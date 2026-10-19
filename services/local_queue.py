"""单进程全局本地推理队列；用户轮转、不抢占，并给老化后台任务执行机会。"""
import asyncio
import threading
import time
import uuid
from contextlib import contextmanager
from database import get_db
from services.llm_types import LLMError

SCHEMA='''CREATE TABLE IF NOT EXISTS llm_local_jobs (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, task_id TEXT NOT NULL,
 background INTEGER NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL
);'''


async def _persist(job,context,status):
    db=await get_db()
    try:
        from services.user_context import require_user_id
        # 系统诊断用管理员身份参与排队，但财务上下文仍为系统归属。
        actor=context.owner_id if context.owner_id is not None else require_user_id()
        await db.execute('INSERT INTO llm_local_jobs VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status',
                         (job,actor,context.task_id,int(context.background),status,int(time.time())))
        await db.commit()
    finally:await db.close()


class LocalQueue:
    def __init__(self,*,max_per_user=2,clock=time.monotonic):
        self.condition=threading.Condition()
        self.waiters=[]
        self.active=0
        self.max_active=0
        self.last_user=None
        self.interactive_streak=0
        self.limit=1
        self.max_per_user=max_per_user
        self.clock=clock

    def configure(self,limit):
        if type(limit) is not int or not 1<=limit<=8:raise ValueError('本地并发必须为 1～8')
        with self.condition:
            self.limit=limit
            self.condition.notify_all()

    def wake(self):
        with self.condition:self.condition.notify_all()

    def wait_for_size(self,size,timeout):
        with self.condition:return self.condition.wait_for(lambda:len(self.waiters)>=size,timeout)

    def _next(self):
        now=self.clock()
        background=[w for w in self.waiters if w['context'].background]
        interactive=[w for w in self.waiters if not w['context'].background]
        if background and self.interactive_streak>=5 and now-background[0]['at']>=120:
            pool=background
        else:pool=interactive or background
        return next((w for w in pool if w['context'].owner_id!=self.last_user),pool[0]) if pool else None

    @contextmanager
    def slot(self,context,*,stop=None,timeout=300):
        job=uuid.uuid4().hex
        waiter={'id':job,'context':context,'at':self.clock()}
        asyncio.run(_persist(job,context,'queued'))
        entered=False
        state='failed'
        try:
            with self.condition:
                own=sum(w['context'].owner_id==context.owner_id for w in self.waiters)
                if len(self.waiters)>=20 or own>=self.max_per_user:raise LLMError('queue_full','本地队列已满')
                self.waiters.append(waiter);self.condition.notify_all()
                while True:
                    from services.task_context import ensure_active_sync
                    ensure_active_sync()
                    if stop and stop.is_set():raise LLMError('cancelled','排队已取消')
                    remaining=timeout-(self.clock()-waiter['at'])
                    if remaining<=0:raise LLMError('queue_timeout','本地排队超时，请稍后重试')
                    if self.active<self.limit and self._next() is waiter:
                        self.waiters.remove(waiter)
                        self.active+=1;entered=True
                        self.max_active=max(self.max_active,self.active)
                        self.last_user=context.owner_id
                        self.interactive_streak=0 if context.background else self.interactive_streak+1
                        break
                    self.condition.wait(min(remaining,0.1))
            asyncio.run(_persist(job,context,'running'))
            yield
            state='done'
        except LLMError as exc:
            state='cancelled' if exc.code=='cancelled' else 'failed'
            raise
        finally:
            with self.condition:
                if waiter in self.waiters:self.waiters.remove(waiter)
                if entered:self.active-=1
                self.condition.notify_all()
            asyncio.run(_persist(job,context,state))


queue=LocalQueue()


async def configure_from_db():
    db=await get_db()
    try:
        row=await (await db.execute("SELECT value FROM app_config WHERE key='local_llm_concurrency'")).fetchone()
        queue.configure(int(row[0]) if row else 1)
    finally:await db.close()


async def task_status(task_id,user_id):
    db=await get_db()
    try:
        row=await (await db.execute('SELECT status FROM llm_local_jobs WHERE task_id=? AND user_id=? ORDER BY rowid DESC LIMIT 1',(task_id,user_id))).fetchone()
        return row[0] if row else None
    finally:await db.close()


async def interrupt_pending():
    db=await get_db()
    try:
        await db.execute("UPDATE llm_local_jobs SET status='interrupted' WHERE status IN ('queued','running')")
        # 退出进程的云端请求可能已计费；不释放预留。
        await db.execute("INSERT OR IGNORE INTO llm_service_holds SELECT COALESCE(service_id,model_id) FROM llm_attempts WHERE state='reserved'")
        await db.execute("UPDATE llm_attempts SET state='unknown' WHERE state='reserved'")
        await db.execute("UPDATE llm_attempts SET state='settled',cost=0 WHERE state='local_pending'")
        configured=await (await db.execute("SELECT value FROM app_config WHERE key='local_llm_concurrency'")).fetchone()
        queue.configure(int(configured[0]) if configured else 1)
        await db.commit()
    finally:await db.close()
