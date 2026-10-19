"""C 阶段浏览器隔离服务；合成账号、临时库，禁止真实外发。"""
import asyncio
from contextlib import asynccontextmanager
import ipaddress
import os
from pathlib import Path
import socket
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
with tempfile.TemporaryDirectory(prefix='licai-c-browser-') as directory:
    os.environ['LICAI_PROFILE']='test'
    os.environ['LICAI_DATA_DIR']=directory
    from database import init_db
    from services import accounts,notifications,notification_delivery
    from services.user_context import user_scope
    async def prepare():
        await init_db()
        token=await accounts.issue_bootstrap_token()
        admin=await accounts.bootstrap_admin(token=token.read_text().strip(),username='c-admin',password='Synthetic-C-01!')
        other=await accounts.create_user(actor_id=admin.id,username='c-user',password='Synthetic-C-01!')
        from services import auth
        s=await auth.login(username='c-user',password='Synthetic-C-01!')
        await auth.change_password(s.token,current_password='Synthetic-C-01!',new_password='Synthetic-C-02!')
        with user_scope(admin.id):await notifications.emit('personal_budget','fixture',title='管理员本人预算提醒',body='仅管理员本人可见')
        with user_scope(other.id):await notifications.emit('risk','fixture',title='普通用户个人提醒',body='仅普通用户本人可见')
    asyncio.run(prepare())
    connect=socket.socket.connect
    def offline(sock,address):
        if sock.family in (socket.AF_INET,socket.AF_INET6):
            if not ipaddress.ip_address(address[0]).is_loopback:
                raise RuntimeError('浏览器隔离服务禁止外部网络')
        return connect(sock,address)
    socket.socket.connect=offline
    async def synthetic_send(kind,config,summary):
        assert '仅管理员' not in summary and '仅普通用户' not in summary
        return 'accepted'
    notification_delivery.send=synthetic_send
    from run import app
    original=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            await notification_delivery.interrupt_pending()
            async def worker():
                while True:
                    await notification_delivery.deliver_pending()
                    await asyncio.sleep(.1)
            task=asyncio.create_task(worker())
            try:yield
            finally:
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
    app.router.lifespan_context=lifespan
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18778,log_level='warning')
