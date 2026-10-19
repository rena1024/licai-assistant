"""Settings REST endpoints for notification config and custom alerts."""
from __future__ import annotations
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from typing import Optional

from database import get_config, set_config
from services import feishu_notify, llm_client, tdx_client, proxy_config, zsxq_client

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _assert_local_url(v: str) -> str:
    """TDX 服务只该在本机/私网。限制 base_url 的 host 为 loopback 或 RFC1918 私网,
    拒绝公网与 link-local(169.254, 含云元数据 169.254.169.254) —— 防 SSRF。"""
    if not v:
        return v
    if not v.startswith(("http://", "https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    from urllib.parse import urlparse
    import ipaddress
    host = (urlparse(v).hostname or "").strip()
    if not host:
        raise ValueError("base_url 缺少主机名")
    if host in ("localhost", "localhost.localdomain"):
        return v.rstrip("/")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("TDX base_url 只允许 localhost 或本机/私网 IP(防 SSRF), 不接受公网域名")
    if ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError("base_url 不允许 link-local/保留地址(如 169.254.169.254)")
    if not (ip.is_loopback or ip.is_private):
        raise ValueError("TDX base_url 只允许本机/私网地址(防 SSRF), 不接受公网 IP")
    return v.rstrip("/")


class TDXConfig(BaseModel):
    base_url: str = ""

    @field_validator("base_url")
    @classmethod
    def _v(cls, v: str) -> str:
        return _assert_local_url(v)


@router.get("/tdx")
async def get_tdx_config():
    """TDX 可插拔数据源配置 + 当前是否启用。"""
    return {"base_url": tdx_client._BASE_URL, "enabled": tdx_client.is_enabled()}


@router.post("/tdx")
async def set_tdx_config(data: TDXConfig):
    await set_config("tdx_base_url", data.base_url)
    tdx_client.configure(data.base_url)
    return {"message": "saved", "enabled": tdx_client.is_enabled()}


@router.post("/tdx/test")
async def test_tdx_config(data: TDXConfig):
    """连通性自检: 不改保存值, 用传入(或当前)地址试拉一只票。"""
    return await tdx_client.test_connection(data.base_url or tdx_client._BASE_URL)


# ── 知识星球(可选, 只读观点面) ──────────────────────────────
class ZsxqConfig(BaseModel):
    """MCP 端点 URL(带 api_key) + 选哪些星球。URL 存 DB 不进 config.py, 返回前端一律脱敏。"""
    url: Optional[str] = None
    groups: Optional[list] = None

    @field_validator("url")
    @classmethod
    def _vu(cls, v):
        if v is None:
            return v
        v = (v or "").strip()
        if not v:
            return ""
        if not v.startswith("https://"):
            raise ValueError("MCP 端点必须是 https(URL 里带 api_key, 明文 http 会泄露)")
        return v

    @field_validator("groups")
    @classmethod
    def _vg(cls, v):
        if v is None:
            return v
        out = []
        for g in (v or [])[:20]:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("group_id") or "").strip()
            if not gid.isdigit():
                continue
            out.append({"group_id": gid, "name": str(g.get("name") or gid)[:60],
                        "owner_only": bool(g.get("owner_only", True))})
        return out


# --- 扩展数据源(provider) ---------------------------------------------------
# 本项目自带的行情源都是公开接口, 不要凭证。少数口径(带身份标签的深度龙虎榜、集合竞价异动、
# 机构季度增减仓)只有商业终端才有 —— 那需要用户自己的账号凭证去打对方的接口, 是**用户自己**
# 与对方的关系, 所以本仓库只定义协议、不自带任何实现。装哪个 provider、填不填凭证, 由用户
# 在这里按一次开关; 什么都不配就是 NullProvider, 其余功能照常。
#
# 这三个端点对所有 provider 通用: 表单字段由 provider 自己声明(credential_fields), 设置页
# 照着渲染 —— 不为任何一个 provider 写死表单, 换一个实现不用改后端也不用改前端。

async def _provider_state() -> dict:
    from services.providers import discover, get_provider, load_error
    spec = (await get_config("market_provider")) or ""
    p = await get_provider()
    st = await p.status()
    return {
        "spec": spec,
        # 已装包登记的候选, 给设置页当选项。只读包元数据、不 import 任何 provider ——
        # 否则光是装上某个包, 它的代码就会在每次开设置页时被执行一遍。
        "discovered": discover(),
        "provider": st.get("provider", p.name),
        "display_name": st.get("display_name", p.display_name),
        "capabilities": sorted(p.capabilities),
        # secret 字段永不回传明文; echo 里只放 provider 自己判定可回显的那些(如账号 ID)
        "fields": [{"key": f.key, "label": f.label, "secret": f.secret,
                    "echo": f.echo, "placeholder": f.placeholder}
                   for f in p.credential_fields],
        "echo": st.get("echo", {}),
        "configured": st.get("configured", False),
        "valid": st.get("valid", False),
        "note": st.get("note", ""),
        "load_error": load_error(),
    }


@router.get("/provider")
async def get_provider_config():
    """扩展数据源状态: 装了哪个、要填哪些字段、凭证还有效没。绝不回传 secret 字段明文。"""
    return await _provider_state()


class ProviderConfig(BaseModel):
    spec: Optional[str] = None          # "包名:类名" 或入口点名字; 传空串 = 卸掉, 退回 NullProvider
    values: Optional[dict] = None       # 凭证字段, 键为 provider 声明的 field.key


@router.post("/provider")
async def set_provider_config(data: ProviderConfig, request: Request):
    """装/换 provider, 和/或存凭证。两件事可以一次做完, 也可以分两次。"""
    from services.providers import get_provider, reset_cache
    if data.spec is not None and request.state.user:
        from services import auth
        session = await auth.require_session(request)
        if session.account.role != 'admin':
            raise HTTPException(403, '扩展来源代码由管理员配置')
    if data.spec is not None:
        await set_config("market_provider", (data.spec or "").strip())
        import os
        os.environ.pop("MARKET_PROVIDER", None)   # env 留着旧值会一直盖过刚存的这份
        reset_cache()
    saved = None
    if data.values:
        p = await get_provider()
        if not p.credential_fields:
            return {"ok": False, "note": f"{p.display_name} 不需要凭证", **(await _provider_state())}
        # 只收 provider 声明过的键 —— 别把前端随手多塞的字段原样存进 config 表
        allowed = {f.key for f in p.credential_fields}
        try:
            saved = await p.save_credentials({k: v for k, v in data.values.items() if k in allowed})
        except Exception as e:
            return {"ok": False, "note": "存凭证失败，请检查接入配置", **(await _provider_state())}
    state = await _provider_state()
    return {"ok": bool(saved["ok"]) if saved else True,
            "note": (saved or {}).get("note", ""), **state}


@router.delete("/provider")
async def clear_provider_config(request: Request):
    """清凭证并卸掉 provider。顺序不能反 —— 先卸了就没人知道该清哪些键了。"""
    from services.providers import get_provider, reset_cache
    p = await get_provider()
    if p.credential_fields:
        try:
            await p.clear_credentials()
        except Exception:
            pass
    from services import auth
    session = await auth.require_session(request)
    if session.account.role == 'admin':
        await set_config('market_provider','')
        import os
        os.environ.pop('MARKET_PROVIDER',None)
    reset_cache()
    return {"ok": True, **(await _provider_state())}


@router.get("/zsxq")
async def get_zsxq_config():
    """当前配置 + 端点健康度。绝不回传含 api_key 的 URL, 只给脱敏 host+path。"""
    import asyncio
    h = await asyncio.to_thread(zsxq_client.health)
    # 显式列字段, 不 **h —— health 里也有个同名的计数字段, 盲展开会把 groups 列表覆盖成整数,
    # 前端拿到数字再 .find 就白屏了(实测踩过)
    return {"groups": zsxq_client.configured_groups(),
            "enabled": zsxq_client.is_enabled(),
            "endpoint": zsxq_client.endpoint_label(),
            "configured": h.get("configured", False),
            "ok": h.get("ok", False),
            "account": h.get("account", ""),
            "error": h.get("error", ""),
            "hint": h.get("hint", "")}


@router.get("/zsxq/available")
async def list_zsxq_groups():
    """列出该账号加入的全部星球, 供勾选。"""
    import asyncio
    return await asyncio.to_thread(zsxq_client.list_groups, 50)


@router.post("/zsxq")
async def set_zsxq_config(data: ZsxqConfig):
    import json as _json
    if data.url is not None:
        zsxq_client.configure(url=data.url)
    if data.groups is not None:
        zsxq_client.configure(groups=data.groups)
    return {"message": "saved", "enabled": zsxq_client.is_enabled(),
            "endpoint": zsxq_client.endpoint_label(),
            "groups": zsxq_client.configured_groups()}


# ── 本地代理 (OKX / 外发统一; 东财/新浪直连不受影响) ──────────────
class ProxyConfig(BaseModel):
    proxy: str = ""

    @field_validator("proxy")
    @classmethod
    def _v(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not v.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise ValueError("代理地址须以 http(s):// 或 socks5:// 开头")
        return v


@router.get("/proxy")
async def get_proxy_config():
    """当前生效代理 + DB 存储值。"""
    return {"proxy": proxy_config.get_proxy(), "db_proxy": (await get_config("network_proxy")) or ""}


@router.post("/proxy")
async def set_proxy_config(data: ProxyConfig):
    """保存代理到 DB 并即时应用(通知 OKX/外发 session 更新)。"""
    import asyncio
    await set_config("network_proxy", data.proxy)
    proxy_config.configure(data.proxy)
    ok = await asyncio.to_thread(proxy_config._probe, data.proxy) if data.proxy else False
    return {"message": "saved", "proxy": proxy_config.get_proxy(), "ok": ok}


@router.post("/proxy/detect")
async def detect_proxy_config():
    """自动探测本机可用代理(扫在听端口 + 常见端口, 挑能够到 OKX 的); 命中即应用并存库。"""
    import asyncio
    found = await asyncio.to_thread(proxy_config.auto_detect)
    if found:
        await set_config("network_proxy", found)
        proxy_config.configure(found)
        return {"ok": True, "proxy": found}
    return {"ok": False, "proxy": "", "error": "未探测到可用代理(确认本地代理已开启)"}


@router.post("/proxy/test")
async def test_proxy_config(data: ProxyConfig):
    """连通性自检: 用传入(或当前)代理探一次 OKX, 不改存储值。"""
    import asyncio
    url = data.proxy or proxy_config.get_proxy()
    ok = await asyncio.to_thread(proxy_config._probe, url)
    return {"ok": ok, "proxy": url, "error": "" if ok else "代理连不上或够不到外部接口"}


class FeishuConfig(BaseModel):
    webhook_url: str


@router.get("/feishu")
async def get_feishu_config():
    from services import private_connections
    url = private_connections.get('feishu_webhook', '')
    return {
        "webhook_url": "",
        "configured": bool(url),
        "enabled": feishu_notify.is_enabled(),
        "muted": feishu_notify.is_muted(),
    }


@router.post("/feishu")
async def save_feishu_config(data: FeishuConfig):
    raise HTTPException(410,'请在我的通知中管理飞书渠道，所有事件统一使用当前渠道')


class FeishuMute(BaseModel):
    muted: bool


@router.post("/feishu/mute")
async def set_feishu_mute(data: FeishuMute):
    raise HTTPException(410,'请在我的通知中停用或启用统一外部渠道')


@router.post("/feishu/test")
async def test_feishu():
    raise HTTPException(410,'请在我的通知中确认当前渠道并发送测试')


# --- Risk Config ---

class RiskConfig(BaseModel):
    max_daily_loss: Optional[float] = None


@router.get("/risk")
async def get_risk_config():
    val = await get_config("max_daily_loss")
    return {"max_daily_loss": float(val) if val else 500}


@router.post("/risk")
async def save_risk_config(data: RiskConfig):
    if data.max_daily_loss is not None:
        await set_config("max_daily_loss", str(data.max_daily_loss))
    return {"message": "保存成功"}


# 旧入口只保留明确迁移提示，不能继续保存已失效的全局模型选择。
@router.get('/llm')
async def get_llm_config_api():
    return llm_client.get_llm_config()


@router.post('/llm')
async def save_llm_config_api():
    raise HTTPException(410,'旧模型配置已停用，请使用模型服务和个人场景设置')


@router.post('/llm/test')
async def test_llm_connection():
    raise HTTPException(410,'请选择模型并显式发起受预算控制的能力测试')


@router.get("/rules")
async def rules_list():
    from services import rule_forge as rf
    rules = await rf.list_rules()
    return {
        "rules": rules,
        "n_pending": sum(1 for r in rules if r["status"] == "pending"),
        "n_active": sum(1 for r in rules if r["status"] == "active"),
    }


@router.post("/rules/{rule_id}/{decision}")
async def rules_decide(rule_id: int, decision: str):
    """批准 → 进 system prompt(下一次问答立刻生效); 否决 → 永久留在队列里但不生效。

    否决的不删: 留着才知道"这条提过、被否了", 下次挖掘不会原样再提一遍。
    """
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 只能是 approve / reject")
    from services import rule_forge as rf
    ok = await rf.decide(rule_id, "active" if decision == "approve" else "rejected")
    if not ok:
        raise HTTPException(status_code=404, detail=f"没有 #{rule_id} 这条规则")
    return {"ok": True, "id": rule_id, "status": "active" if decision == "approve" else "rejected"}


@router.post("/rules/mine")
async def rules_mine():
    """立刻挖一次(平时是每周一早上自动跑)。每次最多起草 3 条, 要调模型。"""
    from services import rule_forge as rf
    return await rf.mine_new(max_drafts=3)


@router.get('/data-center')
async def data_center():
    from services.data_sources import overview
    return await overview()


@router.put('/data-center')
async def save_data_center(request: Request):
    from services.data_sources import save_policy
    try:
        data=await request.json()
        version=data.pop('expected_version')
        if type(version) is not int or version<0:
            raise ValueError()
        return await save_policy(data,expected_version=version)
    except (ValueError,KeyError,TypeError,AttributeError):
        raise HTTPException(422,'来源配置无效或版本已变化，请刷新后重试') from None
