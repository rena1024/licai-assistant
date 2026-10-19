import asyncio
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_admin_alert_never_contains_other_user_content(owners):
    from services import notifications as n
    a,b,_=owners
    async def exercise():
        with user_scope(b):
            first=await n.emit('personal_budget','day:2026-10-19:80',title='个人预算提醒',body='仅本人可见')
            assert await n.emit('personal_budget','day:2026-10-19:80',title='个人预算提醒',body='重复')==first
            await n.system_event('source_failed','sina:1')
            mine=await n.list_messages()
            assert len(mine)==1 and mine[0]['body']=='仅本人可见'
        with user_scope(a):
            mine=await n.list_messages()
            assert len(mine)==1 and mine[0]['event']=='source_failed'
            assert '仅本人可见' not in str(mine)
            assert not await n.mark_read(first)
        with user_scope(b):
            assert await n.mark_read(first)
            assert (await n.list_messages())[0]['read_at'] is not None
    asyncio.run(exercise())


def test_existing_feishu_configuration_migrates_once_without_overriding_channel(owners):
    from services import notification_delivery as d,private_connections
    a,b,_=owners
    async def exercise():
        with user_scope(b):
            private_connections.save('feishu_webhook','https://open.feishu.cn/open-apis/bot/v2/hook/legacy-test')
            rows=await d.channels()
            assert len(rows)==1 and rows[0]['active']
            await d.activate(None)
            assert not (await d.channels())[0]['active']
            await d.delete_channel(rows[0]['id'])
            assert await d.channels()==[]
    asyncio.run(exercise())


def test_message_retention_preserves_event_dedup_and_owner_cursor(owners):
    from services import notifications as n
    import sqlite3
    a,b,path=owners
    async def exercise():
        with user_scope(b):
            ids=[await n.emit('risk',str(i),title='提醒') for i in range(3)]
            rows=await n.list_messages(limit=1)
            rest=await n.list_messages(limit=10,cursor=rows[0]['id'])
            assert len(rest)==2 and rows[0]['id'] not in [r['id'] for r in rest]
        with user_scope(a):
            await n.set_retention(1)
        with sqlite3.connect(path) as db:
            db.execute('UPDATE notifications SET created_at=1')
        await n.cleanup(now=200000)
        with user_scope(b):
            assert await n.list_messages()==[]
            assert await n.emit('risk','0',title='不应重复')==ids[0]
            assert await n.list_messages()==[]
    asyncio.run(exercise())
