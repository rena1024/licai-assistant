import asyncio
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_all_events_use_one_active_channel(owners, monkeypatch):
    from services import notification_delivery as d, notifications as n
    a,b,_=owners
    sent=[]
    async def send(kind,config,summary):
        sent.append((kind,config,summary))
        return 'accepted'
    monkeypatch.setattr(d,'send',send)
    async def exercise():
        with user_scope(a):
            channel=await d.save_channel({'kind':'pushplus','name':'我的微信','config':{'token':'synthetic-token'}},expected_version=0)
            await d.activate(channel['id'])
            second=await d.save_channel({'kind':'feishu','name':'飞书','config':{'webhook_url':'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic'}},expected_version=0)
            await n.emit('risk','1',title='风险提醒',body='私有数量1000股，成本12345元')
            await n.emit('summary','2',title='收盘小结',body='私有正文')
            assert len(await d.channels())==2
        with user_scope(b):
            assert await d.channels()==[]
            await n.emit('risk','1',title='B提醒',body='B正文')
            assert len(await n.list_messages())==1
        await d.deliver_pending()
        assert len(sent)==2 and all(s[0]=='pushplus' for s in sent)
        assert all('12345' not in s[2] and '私有' not in s[2] for s in sent)
        with user_scope(a):
            assert all(not c.get('config') for c in await d.channels())
    asyncio.run(exercise())
