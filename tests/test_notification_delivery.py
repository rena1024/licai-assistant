import asyncio
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_unknown_result_and_channel_change_do_not_duplicate(owners,monkeypatch):
    from services import notification_delivery as d, notifications as n
    a,b,_=owners
    sent=[]
    async def send(kind,config,summary):
        sent.append(kind)
        raise TimeoutError()
    monkeypatch.setattr(d,'send',send)
    async def exercise():
        with user_scope(b):
            first=await d.save_channel({'kind':'pushplus','name':'微信','config':{'token':'fake'}},expected_version=0)
            await d.activate(first['id'])
            msg=await n.emit('risk','1',title='风险',body='private')
            second=await d.save_channel({'kind':'feishu','name':'飞书','config':{'webhook_url':'https://open.feishu.cn/open-apis/bot/v2/hook/fake'}},expected_version=0)
            await d.activate(second['id'])
        await d.deliver_pending()
        await d.deliver_pending()
        assert sent==['pushplus']
        with user_scope(b):
            assert (await n.list_messages())[0]['delivery_state']=='unknown'
            assert not (await n.list_messages())[0]['read_at']
            await d.resend(msg,second['id'],confirmed=True)
        from services import accounts
        await accounts.set_user_active(actor_id=a,user_id=b,active=False)
        await d.deliver_pending()
        assert sent==['pushplus']
    asyncio.run(exercise())


def test_explicit_failure_retries_same_frozen_channel_and_restart_unknown(owners,monkeypatch):
    from services import notifications as n,notification_delivery as d
    a,b,path=owners
    calls=[]
    async def send(kind,config,summary):
        calls.append(config['token'])
        return 'failed'
    monkeypatch.setattr(d,'send',send)
    async def exercise():
        with user_scope(b):
            c=await d.save_channel({'kind':'pushplus','name':'微信','config':{'token':'v1'}},expected_version=0)
            await d.activate(c['id'])
            await n.emit('risk','retry',title='风险')
            await d.save_channel({'id':c['id'],'kind':'pushplus','name':'微信新版','config':{'token':'v2'}},expected_version=1)
        import time
        now=int(time.time())
        await d.deliver_pending(now=now)
        await d.deliver_pending(now=now+59)
        assert calls==['v1']
        await d.deliver_pending(now=now+60)
        await d.deliver_pending(now=now+360)
        await d.deliver_pending(now=now+1000)
        assert calls==['v1','v1','v1']
    asyncio.run(exercise())
