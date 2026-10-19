import asyncio
import pytest
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope
from services.llm_types import CallContext, Usage
from tests.test_budget_ledger import PRICE


def test_detail_purge_keeps_month_totals_and_pending_reservations(owners):
    from services import budget, usage
    from database import get_db
    a,b,_ = owners
    async def exercise():
        with user_scope(a):
            await budget.set_limits(0, day='1', month='1')
            await budget.set_limits(b, day='1', month='1')
        with user_scope(b):
            ctx = CallContext(b,'ask','task')
            rid = await budget.reserve_attempt(ctx,'m',PRICE,900000,'one')
            await budget.settle_attempt(rid,Usage(input_tokens=10000,output_tokens=40000))
            pending = await budget.reserve_attempt(ctx,'m',PRICE,100,'pending')
            before = await usage.report()
            assert before['alerts']
            assert before['monthly'][0]['calls'] == 1
            with pytest.raises(Exception):
                await usage.report(subject=a)
            await usage.purge_details(now=9999999999)
            after = await usage.report()
            assert after['budget'] == before['budget']
            assert after['monthly'] == before['monthly']
            assert after['alerts'] == before['alerts']
            assert after['details'] == []
            db = await get_db()
            try:
                row = await (await db.execute('SELECT state FROM llm_attempts WHERE id=?',(pending,))).fetchone()
                assert row['state'] == 'reserved'
            finally:
                await db.close()
    asyncio.run(exercise())
