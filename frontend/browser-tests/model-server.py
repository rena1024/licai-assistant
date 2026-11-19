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
        admin = await accounts.bootstrap_admin(token=token.read_text().strip(), username='model-tester',
                                               password='Synthetic-model-test-01!')
        await accounts.create_user(actor_id=admin.id, username='scroll-user', password='Synthetic-scroll-01!')

    asyncio.run(prepare())
    from services import llm_runtime

    def synthetic_inference(model, payload, path, headers):
        # 浏览器用例只验证测试结果的展示与持久化，不访问真实 Ollama 或云端。
        if not model.local:
            raise AssertionError('浏览器回归禁止真实云端推理')
        return {'message': {'role': 'assistant', 'content': 'OK'}, 'done': True,
                'prompt_eval_count': 1, 'eval_count': 1}

    llm_runtime.send_http = synthetic_inference
    import uvicorn
    uvicorn.run('run:app', host='127.0.0.1', port=18768, log_level='warning')
