"""摘要任务脱离浏览器请求运行；同一账号、参数和运行代际只执行一次。"""
import asyncio
import hashlib
import inspect
import json
import uuid
import weakref
from functools import wraps

from pydantic import BaseModel

from services import llm_activity, llm_runtime, runtime_state
from services.task_context import _version
from services.user_context import require_user_id

_jobs = weakref.WeakKeyDictionary()


def cancel_user(user_id):
    tasks = [entry[0] for entry in _jobs.get(asyncio.get_running_loop(), {}).values() if entry[2] == user_id and not entry[0].done()]
    for task in tasks:
        task.cancel()
    return tasks


def _json_value(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode='json')
    raise TypeError('分析参数不能序列化')


def shared_ai_job(function=None, *, daily=False):
    """保留原 API 签名和缓存语义；force 只在没有同类在途任务时触发重算。"""
    if function is None:
        return lambda target: shared_ai_job(target, daily=daily)
    signature = inspect.signature(function, eval_str=True)

    @wraps(function)
    async def run(*args, **kwargs):
        from config import config
        from services.runtime_paths import database_path
        arguments = signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        parameters = {k: v for k, v in arguments.arguments.items() if k != 'force'}
        from services import daily_ai_cache
        day = daily_ai_cache.today() if daily else None
        identity = [function.__module__, function.__qualname__, parameters]
        key = hashlib.sha256(json.dumps([
            str(database_path(config.db_path)), require_user_id(), _version.get(), runtime_state.check_generation(),
            *identity, day,
        ], sort_keys=True, default=_json_value).encode()).hexdigest()
        jobs = _jobs.setdefault(asyncio.get_running_loop(), {})
        entry = jobs.get(key)
        if entry is None or entry[0].done():
            task_id = uuid.uuid4().hex

            async def execute():
                task_token = llm_runtime._task.set(task_id)
                try:
                    if daily:
                        cache_key = 'daily_ai:' + hashlib.sha256(json.dumps(
                            identity, sort_keys=True, default=_json_value).encode()).hexdigest()
                        return await daily_ai_cache.run_daily(function, arguments, cache_key, day)
                    return await function(*args, **kwargs)
                finally:
                    llm_runtime._task.reset(task_token)

            task = asyncio.create_task(execute())
            entry = (task, task_id, require_user_id())
            jobs[key] = entry

            def finished(completed):
                if jobs.get(key) is entry:
                    jobs.pop(key, None)
                llm_activity.clear_task_requests(task_id)
                # 所有页面都离开时也须收取异常，避免无人等待的任务异常警告。
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        task, task_id, _ = entry
        llm_activity.bind_request(task_id)
        # 取消查看者只取消这个 await，不取消任务或正在排队的物理推理请求。
        return await asyncio.shield(task)

    # FastAPI 必须在原函数命名空间解析请求模型，不能在装饰器模块解析前向注解。
    run.__signature__ = signature
    return run
