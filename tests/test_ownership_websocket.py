import asyncio
import json
from tests.test_ownership_portfolio import owners


def test_shared_quote_does_not_broadcast_private_holdings(owners):
    from api import ws
    from services import auth
    a,b,_=owners
    class Socket:
        def __init__(self):self.messages=[];self.closed=False
        async def send_text(self,data):self.messages.append(json.loads(data))
        async def close(self,code):self.closed=True
    async def exercise():
        sa=await auth.login(username='owner-a',password='Synthetic-password-01!')
        sb=await auth.login(username='owner-b',password='Synthetic-password-02!')
        wa,wb=Socket(),Socket()
        ws._clients[wa]=ws.Connection(a,sa.token)
        ws._clients[wb]=ws.Connection(b,sb.token)
        try:
            await ws.broadcast({'type':'private','data':{'cost':100}},user_id=a)
            assert wa.messages and not wb.messages
            await auth.logout(sa.token)
            await ws.broadcast({'type':'private'},user_id=a)
            assert wa.closed and len(wa.messages)==1
        finally:
            ws._clients.clear()
    asyncio.run(exercise())
