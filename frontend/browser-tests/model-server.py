"""浏览器回归专用真实后端，仅使用临时数据库和合成账号。"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

with tempfile.TemporaryDirectory(prefix='licai-model-browser-') as directory:
    os.environ['LICAI_PROFILE'] = 'test'
    os.environ['LICAI_DATA_DIR'] = directory
    from database import init_db
    from services import accounts

    async def prepare():
        await init_db()
        token = await accounts.issue_bootstrap_token()
        await accounts.bootstrap_admin(token=token.read_text().strip(), username='model-tester',
                                       password='Synthetic-model-test-01!')

    asyncio.run(prepare())
    import uvicorn
    uvicorn.run('run:app', host='127.0.0.1', port=18768, log_level='warning')
