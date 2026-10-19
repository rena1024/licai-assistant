import asyncio
import sqlite3
from tests.test_ownership_portfolio import owners
from tests.test_budget_ledger import PRICE
from services.user_context import user_scope
from services.llm_types import CallContext,Usage


def test_restored_ledger_keeps_later_fees_and_local_mode_by_uuid(owners):
    from services import backup,budget,model_catalog
    from services.llm_recovery import preserve_finance
    a,b,path = owners
    point = backup.create_backup()
    async def exercise():
        with user_scope(a):
            await budget.set_limits(0,day='1',month='1')
            await budget.set_limits(b,day='1',month='1')
        with user_scope(b):
            await model_catalog.save_preferences(local_only=True,scenes={})
            rid = await budget.reserve_attempt(CallContext(b,'ask','t'),'cloud',PRICE,100,'after-backup')
            await budget.settle_attempt(rid,Usage(input_tokens=1,output_tokens=1))
    asyncio.run(exercise())
    restored_path = backup.validate_backup(point['id'])/'database.sqlite3'
    import shutil
    target = path.parent/'restored.db'
    shutil.copyfile(restored_path,target)
    with sqlite3.connect(path) as current,sqlite3.connect(target) as restored:
        preserve_finance(current,restored)
        assert restored.execute('SELECT local_only FROM llm_preferences WHERE user_id=?',(b,)).fetchone()[0] == 1
        assert restored.execute("SELECT spent FROM llm_budget_totals WHERE subject=0 AND kind='day'").fetchone()[0] == 30
