"""问答与媒体资源必须拒绝猜中标识的其他用户。"""
import asyncio
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_foreign_run_media_and_cached_analysis_denied(owners):
    import database as db
    from services import ask_runs
    a,b,_=owners
    async def events():
        yield {'type':'answer','text':'A 的私人回答'}
    async def exercise():
        with user_scope(a):
            run=await ask_runs.start('私人问题',events=events())
            await run.task
            assert await db.get_ask_session(run.session_id,user_id=a)
        with user_scope(b):
            assert await db.get_ask_session(run.session_id,user_id=b) is None
            assert await ask_runs.live() == []
            assert ask_runs.get(run.id) is None
            assert not ask_runs.cancel(run.id)
            response=[ev async for ev in ask_runs.follow(run.id)]
            assert not any('A 的私人回答' in str(ev) for ev in response)
    asyncio.run(exercise())


def test_cannot_append_message_to_foreign_session(owners):
    import database as db
    import pytest
    a,b,_=owners
    async def exercise():
        sid=await db.create_ask_session('A',user_id=a)
        with pytest.raises(ValueError):
            await db.add_ask_message(sid,'user','B 注入',user_id=b)
        assert (await db.get_ask_session(sid,user_id=a))['messages']==[]
    asyncio.run(exercise())


def test_private_media_is_not_readable_by_other_user(owners):
    from fastapi.testclient import TestClient
    from run import app
    with TestClient(app) as client:
        result=client.post('/api/auth/login',json={'username':'owner-a','password':'Synthetic-password-01!'})
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        msg=client.post('/api/ask/messages',json={'role':'user','content':'A','meta':{'images':['data:image/png;base64,aGVsbG8=']}})
        sid=msg.json()['session_id']
        url=client.get(f'/api/ask/sessions/{sid}').json()['messages'][0]['meta']['images'][0]
        assert client.get(url).status_code==200
        result=client.post('/api/auth/login',json={'username':'owner-b','password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        assert client.get(url).status_code==404


def test_private_cli_clear_requires_selected_owner(owners,monkeypatch):
    from scripts import rules,tool_gaps
    from services import rule_forge as rf
    import database as db
    a,b,_=owners
    async def seed():
        for uid in (a,b):
            with user_scope(uid):
                await rf.add_candidate('rule','body','evidence')
        connection=await db.get_db()
        try:
            for uid in (a,b):
                await connection.execute("INSERT INTO tool_gap(user_id,tool,kind,market,hits) VALUES (?,'test','empty','A',1)",(uid,))
            await connection.commit()
        finally:await connection.close()
    asyncio.run(seed())
    monkeypatch.setattr('sys.argv',['rules.py','--user-id',str(b),'--clear-pending'])
    assert asyncio.run(rules.main())==0
    monkeypatch.setattr('sys.argv',['tool_gaps.py','--user-id',str(b),'--clear'])
    assert asyncio.run(tool_gaps.main())==0
    async def check():
        with user_scope(a):assert len(await rf.list_rules())==1
        with user_scope(b):assert await rf.list_rules()==[]
        connection=await db.get_db()
        try:assert [r['user_id'] for r in await (await connection.execute('SELECT user_id FROM tool_gap')).fetchall()]==[a]
        finally:await connection.close()
    asyncio.run(check())
