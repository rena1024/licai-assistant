import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.test_ownership_portfolio import owners
from services.llm_types import CallContext,LLMError
from services.user_context import user_scope


def test_global_concurrency_and_background_fairness(owners):
    from services.local_queue import LocalQueue
    a,b,_=owners
    clock=[0.0]
    queue=LocalQueue(max_per_user=8,clock=lambda:clock[0])
    order=[]
    entered=threading.Event()
    release=threading.Event()
    def task(uid,name,background=False):
        with user_scope(uid),queue.slot(CallContext(uid,'ask',name,background=background)):
            order.append(name)
            if name=='first':entered.set();assert release.wait(5)
    with ThreadPoolExecutor(max_workers=7) as pool:
        first=pool.submit(task,a,'first')
        assert entered.wait(5)
        bg=pool.submit(task,b,'background',True)
        jobs=[pool.submit(task,a,f'interactive{i}') for i in range(5)]
        assert queue.wait_for_size(6,5)
        assert order==['first']
        clock[0]=121
        release.set()
        for job in [first,bg,*jobs]:job.result(5)
    assert order.index('background')<=5
    assert queue.max_active==1


def test_queued_cancellation_exits_without_acquiring(owners):
    from services.local_queue import LocalQueue
    a,b,_=owners
    queue=LocalQueue()
    stop=threading.Event()
    with user_scope(a),queue.slot(CallContext(a,'ask','one')):
        def waiting():
            with user_scope(b),pytest.raises(LLMError,match='取消'):
                with queue.slot(CallContext(b,'ask','two'),stop=stop):
                    raise AssertionError('已取消任务不能执行')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(waiting)
            assert queue.wait_for_size(1,5)
            stop.set();queue.wake()
            future.result(5)


def test_administrator_can_adjust_concurrency_without_preempting_work(owners):
    from services.local_queue import LocalQueue
    a,b,_=owners
    queue=LocalQueue()
    queue.configure(2)
    with user_scope(a),queue.slot(CallContext(a,'ask','a')):
        with user_scope(b),queue.slot(CallContext(b,'ask','b')):
            assert queue.active==2
            queue.configure(1)
            assert queue.active==2
    assert queue.active==0 and queue.max_active==2


def test_per_user_waiting_limit_is_enforced(owners):
    from services.local_queue import LocalQueue
    a,b,_=owners
    queue=LocalQueue()
    release=threading.Event()
    def waiter(name):
        with user_scope(b),queue.slot(CallContext(b,'ask',name)):pass
    with ThreadPoolExecutor(max_workers=2) as pool:
        with user_scope(a),queue.slot(CallContext(a,'ask','active')):
            one=pool.submit(waiter,'one');two=pool.submit(waiter,'two')
            assert queue.wait_for_size(2,5)
            with user_scope(b),pytest.raises(LLMError,match='队列已满'):
                with queue.slot(CallContext(b,'ask','third'),timeout=0.2):pass
        one.result(5);two.result(5)
