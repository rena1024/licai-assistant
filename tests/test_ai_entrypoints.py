import ast
import asyncio
from pathlib import Path
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope
from services.llm_types import ModelResult,Usage


def test_no_cloud_path_bypasses_runtime(owners,monkeypatch):
    from services import llm_client,llm_runtime
    a,b,_=owners
    captured=[]
    async def fake(context,request,candidate_ids=None):
        captured.append((context,request))
        return ModelResult([{'type':'text','text':'ok'}],Usage(input_tokens=1,output_tokens=1),model_id='selected')
    monkeypatch.setattr(llm_runtime,'generate',fake)
    with user_scope(b):
        assert llm_client.call_claude('q',scene='news')=='ok'
        assert llm_client.call_claude_messages([{'role':'user','content':'q'}],scene='ask')['model_id']=='selected'
    assert [c.scene for c,r in captured]==['news','ask']
    assert all(c.owner_id==b and c.purpose=='user' for c,r in captured)
    source=Path('services/llm_client.py').read_text()
    assert '_post_with_retry' not in source
    assert 'subprocess' not in source
    assert '_resolve_auth' not in source


def test_scene_inventory_is_explicit_and_has_no_vendor_models():
    root=Path('.')
    for name in ['services/stock_agent.py','services/morning_briefing.py','services/fundamental_score.py',
                 'services/coiled_scanner.py','services/multi_lens.py','services/rule_forge.py',
                 'api/news_routes.py','api/market_routes.py','api/sector_routes.py','api/portfolio_routes.py','scripts/rules.py']:
        tree=ast.parse((root/name).read_text())
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute) or node.func.attr!='to_thread' or not node.args:continue
            fn=ast.unparse(node.args[0])
            if fn.endswith(('call_claude','call_claude_messages')):
                assert any(k.arg=='scene' for k in node.keywords),name
                assert not any(isinstance(a,ast.Constant) and isinstance(a.value,str) and a.value.startswith('claude-') for a in node.args),name
