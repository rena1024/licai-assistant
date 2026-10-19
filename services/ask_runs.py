"""一次 agent 运行(run): 跑在服务端后台, 不再挂在浏览器那根 fetch 上。

起因: 问一句然后切到别的页, 那一轮就没了 —— 切回来是空的, 历史里也翻不到。实测是两层原因,
两层都得改, 只改一层现象还在:

1) 执行权原来在前端。切页 → 组件卸载 → abort() → 客户端断开 → Starlette 取消响应任务,
   ask_stock_stream 在下一个 yield 处收到 CancelledError 就地死掉。拿一个最小探针实测过:
   客户端一走, 生成器当场 CANCELLED, 后面的 tick 一个都不再出现 —— 所以"后台接着跑"从来没发生。
2) 落库原来在最后一步(前端收到完整答案才 POST /api/ask/messages), 而会话是"存第一条消息"
   时才建的。一轮没答完 → 一行都不写 → 历史里连"我问过什么"都没有, 自然找不到。

现在: 开跑先建会话并把问题写进库(历史里立刻看得见), agent 在独立 task 里跑到底, 事件按序
进内存缓冲, 前端拿游标拉、断了从游标续拉。前端断开只是"不看了", 不影响它跑; 答完由服务端
落库 —— 所以切页 / 刷新 / 甚至关掉页面, 答案照样会出现在历史里。

内存里只留"正在跑 + 刚跑完"的那几条(供重挂和续拉), 原文归 DB。服务重启则内存里的 run 全没,
这时前端续拉会拿到一条明确的提示, 让人去历史里翻原文, 而不是空转。
"""
from __future__ import annotations
from services.user_context import require_user_id
import asyncio
import json as _json
import time
import uuid
import threading
import hashlib
import weakref
from typing import Optional

from database import (create_ask_session, add_ask_message, save_ask_run, update_ask_run,
                      get_ask_run, list_ask_runs, prune_ask_runs)
from services.stock_agent import ask_stock_stream

_KEEP_DONE_SEC = 30 * 60      # 跑完的 run 在内存留半小时: 够切回来把过程重看一遍
_MAX_RUNS = 30
_HARD_WALL_SEC = 20 * 60      # 单条 SSE 连接的兜底上限, 不让它无限挂着
# 事件落盘的合并窗口: 每条都写一次盘, 一轮下来是几十次开关连接; 攒一下再写。
# 收尾必定 flush, 所以最坏也就丢掉最后这半秒的中间步骤(答案在 ask_message 里, 不受影响)。
_FLUSH_SEC = 0.5
_TASK_LIMIT_SEC = 15 * 60
# 过程提示不是工具, 不进历史 meta(否则重开会话会多出假工具胶囊)
_NOT_TOOLS = ("llm_retry", "self_check")

_RUNS: "dict[str, Run]" = {}
_START_LOCKS = weakref.WeakKeyDictionary()


class Run:
    """一问一答的一次执行。events 是只追加的事件流, 下标即游标 —— 前端断开重连靠它对齐。"""

    def __init__(self, question: str, session_id: int, scope: str, title: str):
        self.user_id = require_user_id()
        self.id = uuid.uuid4().hex[:12]
        self.question = question
        self.session_id = session_id
        self.scope = scope                 # market | stock:<code>, 前端按这个认领属于自己的那条
        self.title = title
        self.events: list[dict] = []
        self.done = False
        self.answered = False              # 有没有真答出来(区别于出错/取消)
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self._flushed_at = 0.0             # 上次落盘时间(合并窗口用)
        self.cancel_event = threading.Event()
        self.status = 'running'
        self.request_key = ''
        self.model_id = None
        self.answer_version = 0
        self.stop_fallback = False
        self.queue_status = ''

    def brief(self) -> dict:
        return {"run_id": self.id, "session_id": self.session_id, "question": self.question,
                "scope": self.scope, "done": self.done, "answered": self.answered,
                "n_events": len(self.events), "started_at": self.started_at}

    async def flush(self, force: bool = False) -> None:
        """把事件流写盘。进程没了内存就没了 —— 落盘的这份是重启后唯一还认得这一轮的东西。"""
        if not force and time.time() - self._flushed_at < _FLUSH_SEC:
            return
        self._flushed_at = time.time()
        status = self.status if self.done else 'running'
        try:
            await update_ask_run(self.id, _json.dumps(self.events, ensure_ascii=False),
                                 status, self.answered, self._flushed_at, user_id=require_user_id())
        except Exception:
            pass                            # 落盘失败不该影响正在跑的这一轮


def _gc() -> None:
    now = time.time()
    for rid, r in list(_RUNS.items()):
        if r.done and r.finished_at and now - r.finished_at > _KEEP_DONE_SEC:
            _RUNS.pop(rid, None)
    if len(_RUNS) > _MAX_RUNS:                     # 还超, 就按开始时间丢最老的已完成
        old = sorted((r for r in _RUNS.values() if r.done), key=lambda r: r.started_at)
        for r in old[:len(_RUNS) - _MAX_RUNS]:
            _RUNS.pop(r.id, None)


async def _drive(run: Run, events, *, finalize=True) -> bool:
    """把一条事件流跑完, 事件塞进缓冲, 收尾落库。这里不碰任何请求对象 —— 前端在不在都一样跑。

    events 是一个**已经创建好的异步生成器**, 不再写死 ask_stock_stream: 多视角对抗那条链
    (四层并行 + 综合, 实测一轮 3 分半)同样需要"后台跑 + 切页不断 + 历史里能翻", 那正是本
    模块存在的理由, 没必要为它再造一套。只要产出同一套事件(step/answer/done)就能挂进来。
    """
    answer = None
    sources: list = []
    steps: list = []
    charts: list = []
    cancelled = False
    failed = False
    partial = ''
    try:
        async for ev in events:
            if run.cancel_event.is_set():
                raise asyncio.CancelledError()
            from services.task_context import ensure_active
            await ensure_active()
            if ev.get('type') != 'done':
                run.events.append(ev)
            await run.flush()               # 合并窗口内不重复写
            t = ev.get("type")
            if t == "answer":
                answer = ev.get("text")
            elif t == 'partial':
                partial = ev.get('text','')
            elif t == 'error':
                failed = True
                if ev.get('code') in {'queue_timeout','queue_full','cancelled','task_timeout','inactive_user'}:
                    run.stop_fallback = True
            elif t == "sources":
                sources.extend(ev.get("sources") or [])
            elif t == "step":
                steps.append(ev.get("tool"))
            elif t == "chart":
                charts.append(ev.get("url"))
    except asyncio.CancelledError:
        cancelled = True
        run.cancel_event.set()
        run.status = 'cancelled'
        run.events.append({"type": "error", "error": "已取消"})
    except Exception as e:
        failed = True
        run.events.append({"type": "error", "error": str(e)})
    # 落库放在这儿而不是前端: 答出来就存, 哪怕早就没人在看了 —— 这一步就是"切走也不丢"
    text = answer or partial
    if text:
        meta = {"tools_used": [s for s in steps if s and s not in _NOT_TOOLS],
                "sources": sources, "charts": charts,
                'model_id':run.model_id, 'answer_version':run.answer_version,
                'status':'cancelled' if cancelled else 'incomplete' if failed or not answer else 'complete'}
        try:
            await add_ask_message(run.session_id, "assistant", text,
                                  _json.dumps(meta, ensure_ascii=False), user_id=require_user_id())
            run.answered = bool(answer and not cancelled and not failed)
        except Exception as e:
            run.events.append({"type": "error", "error": f"答案没能落库: {e}"})
    if finalize:
        await _finish(run, cancelled=cancelled)
    return bool(run.answered and not failed and not cancelled)


async def _finish(run, *, cancelled=False):
    run.done = True
    run.status = 'cancelled' if cancelled or run.cancel_event.is_set() else 'done' if run.answered else 'failed'
    run.finished_at = time.time()
    if not run.events or run.events[-1].get('type') != 'done':
        run.events.append({'type':'done','status':run.status})
    await run.flush(force=True)


async def _start(question: str, *, agent_question: Optional[str] = None,
                history: Optional[list] = None, images: Optional[list] = None,
                session_id: Optional[int] = None, title: Optional[str] = None,
                scope: str = "market", user_meta: Optional[dict] = None,
                events=None, regenerate=False) -> Run:
    """建会话(如需) → 先把问题落库 → 起后台 task。返回后这一轮的命运已经跟前端无关了。

    question 是给人看的原话; agent_question 是真正喂给 agent 的(抽屉会前缀上"名称(代码): ")。
    events 可传一条自定义事件流(如多视角对抗); 不传就走默认的问答 agent。
    """
    from services.task_context import ensure_active
    version = await ensure_active()
    _gc()
    from config import config
    key = hashlib.sha256(_json.dumps([config.db_path,require_user_id(),scope,question,agent_question,session_id,history,images],sort_keys=True).encode()).hexdigest()
    if not regenerate:
        existing = next((r for r in _RUNS.values() if not r.done and r.request_key==key and r.user_id==require_user_id()),None)
        if existing:
            if events is not None and hasattr(events,'aclose'):await events.aclose()
            return existing
    if session_id:
        from database import get_ask_session
        if not await get_ask_session(session_id, user_id=require_user_id()):
            from fastapi import HTTPException
            raise HTTPException(404, '会话不存在')
    if not session_id:
        session_id = await create_ask_session((title or question)[:80], user_id=require_user_id())
    await add_ask_message(session_id, "user", question,
                          _json.dumps(user_meta, ensure_ascii=False) if user_meta else "", user_id=require_user_id())
    run = Run(question, session_id, scope, title or question)
    run.request_key = key
    _RUNS[run.id] = run
    try:                                    # 先建流水行: 之后每次 flush 都是 UPDATE
        await save_ask_run(run.id, session_id, scope, question, run.started_at, user_id=require_user_id())
        await prune_ask_runs(user_id=require_user_id())
    except Exception:
        pass
    factory = (events if callable(events) else lambda:events) if events is not None else lambda:ask_stock_stream(agent_question or question, history or [], images)
    async def owned_drive():
        from services.task_context import _version, ensure_active
        token = _version.set(version)
        from services import llm_runtime, model_catalog
        task_token = llm_runtime._task.set(run.id)
        cancel_token = llm_runtime._cancel.set(run.cancel_event)
        try:
            await ensure_active()
            scene = 'multi_lens' if scope.startswith('deep:') else 'ask'
            required = {'text'} if scene=='multi_lens' else {'text','tools'}
            if images:required.add('image')
            choices = await model_catalog.candidates(scene,required)
            ids = [m['id'] for m in choices] or [None]
            if events is not None and not callable(events):ids=ids[:1]
            for index,identifier in enumerate(ids):
                if run.cancel_event.is_set():break
                await ensure_active()
                # 每个备用版本都从完整原始问题重建，不能续接前一模型的半段内容。
                run.model_id=identifier;run.answer_version=index+1
                if identifier is not None:
                    run.events.append({'type':'version','version':index+1,'model_id':identifier,
                                       'reason':'上一版本失败，重新完整生成' if index else ''})
                candidate_token=llm_runtime._candidate.set(identifier)
                scene_token=llm_runtime._scene.set(scene)
                try:
                    if await _drive(run,factory(),finalize=False) or run.stop_fallback:break
                finally:
                    llm_runtime._candidate.reset(candidate_token)
                    llm_runtime._scene.reset(scene_token)
            await _finish(run)
        except asyncio.CancelledError:
            run.cancel_event.set()
            await _finish(run,cancelled=True)
        except Exception:
            run.events.append({'type':'error','error':'任务已失效或执行失败，请重新登录后检查配置'})
            await _finish(run)
        finally:
            llm_runtime._task.reset(task_token)
            llm_runtime._cancel.reset(cancel_token)
            _version.reset(token)
    async def timed_drive():
        timer=None
        try:
            async with asyncio.timeout(_TASK_LIMIT_SEC) as timer:
                await owned_drive()
        except TimeoutError:
            pass
        if timer and timer.expired():
            run.cancel_event.set()
            run.answered=False
            run.done=True
            run.status='timeout'
            run.finished_at=time.time()
            if run.events and run.events[-1].get('type')=='done':run.events.pop()
            run.events.extend([{'type':'error','error':'任务等待超时，未自动重跑','code':'task_timeout'},{'type':'done','status':'timeout'}])
            await run.flush(force=True)
    run.task = asyncio.create_task(timed_drive())
    return run


async def start(question, **kwargs):
    lock = _START_LOCKS.setdefault(asyncio.get_running_loop(),asyncio.Lock())
    async with lock:
        return await _start(question,**kwargs)


async def follow(run_id: str, cursor: int = 0):
    """从 cursor 起把事件吐出去, 追平了就等新的。每条带回 cursor, 断线后照它续拉。

    内存里没有就回盘上找: 重启之后那一轮的过程还在(状态已标 interrupted), 至少能看到它
    走到哪一步, 而不是一句"不在内存里了"。
    """
    run = get(run_id)
    if not run:
        async for ev in _follow_from_db(run_id, cursor):
            yield ev
        return
    if cursor < 0:
        cursor = 0
    deadline = time.time() + _HARD_WALL_SEC
    next_queue_check = 0
    while True:
        from services.runtime_state import check_generation
        check_generation()
        if not run.done and time.monotonic()>=next_queue_check:
            from services.local_queue import task_status
            status=await task_status(run.id,run.user_id)
            next_queue_check=time.monotonic()+1
            if status in {'queued','running'} and status != run.queue_status:
                run.queue_status=status
                run.events.append({'type':'queue','status':status})
                await run.flush()
        if cursor < len(run.events):
            ev = run.events[cursor]
            cursor += 1
            yield {**ev, "cursor": cursor}
            if ev.get("type") == "done":
                return
            continue
        if run.done:                      # done 事件已经发过了(或缓冲被清), 别干等
            return
        if time.time() > deadline:
            return
        await asyncio.sleep(0.12)


async def _follow_from_db(run_id: str, cursor: int):
    """重放盘上那一轮(它一定已经结束了 —— 进程都换了, 原来的 task 不可能还在)。"""
    try:
        row = await get_ask_run(run_id, user_id=require_user_id())
    except Exception:
        row = None
    if not row:
        yield {"type": "error", "error": "这一轮找不着了(流水已清理), 原文去历史里翻", "gone": True}
        return
    try:
        evs = _json.loads(row.get("events") or "[]")
    except Exception:
        evs = []
    for i, ev in enumerate(evs):
        if i >= max(0, cursor):
            yield {**ev, "cursor": i + 1}
    if row.get("status") == "interrupted":
        yield {"type": "error", "error": "这一轮跑到一半服务重启了, 没跑完", "cursor": len(evs) + 1}
    if not evs or evs[-1].get("type") != "done":
        yield {"type": "done", "cursor": len(evs) + 2}


def get(run_id: str) -> Optional[Run]:
    run = _RUNS.get(run_id)
    return run if run and run.user_id == require_user_id() else None


async def live(scope: Optional[str] = None) -> list[dict]:
    """能重挂的 run: 内存里在跑/刚跑完的, 再补上盘里最近几条(重启前的, 都已收尾)。

    盘上那些一律 done=true —— 它们不可能再产出新事件了, 前端不该去"接着等"。
    """
    _gc()
    out = [r.brief() for r in _RUNS.values() if r.user_id == require_user_id() and (not scope or r.scope == scope)]
    seen = {x["run_id"] for x in out}
    try:
        for row in await list_ask_runs(limit=20, user_id=require_user_id()):
            if row["run_id"] in seen or (scope and row.get("scope") != scope):
                continue
            out.append({"run_id": row["run_id"], "session_id": row.get("session_id"),
                        "question": row.get("question") or "", "scope": row.get("scope") or "",
                        "done": True, "answered": bool(row.get("answered")),
                        "interrupted": row.get("status") == "interrupted",
                        "n_events": 0, "started_at": row.get("started_at") or 0})
    except Exception:
        pass
    out.sort(key=lambda x: x["started_at"])
    return out


def cancel(run_id: str) -> bool:
    """真取消(点"新对话"/明确停掉时用)。跟"前端走开"区分开: 走开不取消。"""
    run = get(run_id)
    if not run or run.done or not run.task:
        return False
    run.cancel_event.set()
    run.status='cancelled'
    run.task.cancel()
    if not run.done:
        asyncio.create_task(_finish(run,cancelled=True))
    return True
