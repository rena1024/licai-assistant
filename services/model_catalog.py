"""管理员开放模型，用户只配置本人场景选择；禁用不覆盖已保存选择。"""
import ipaddress
import json
import re
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from database import get_db
from services.accounts import _require_admin
from services.user_context import require_user_id
from services.llm_types import Capability, LLMError

SCENES = ('ask', 'briefing', 'fundamental', 'scanner', 'multi_lens', 'rules',
          'news', 'portfolio', 'market', 'sector')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS llm_models (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_preferences (
 user_id INTEGER PRIMARY KEY REFERENCES users(id), version INTEGER NOT NULL,
 local_only INTEGER NOT NULL DEFAULT 0, scenes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_model_checks (
 model_id TEXT NOT NULL, version INTEGER NOT NULL, capability TEXT NOT NULL,
 success INTEGER NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(model_id,version,capability)
);
'''


class Price(BaseModel):
    model_config = ConfigDict(extra='forbid')
    currency: Literal['CNY', 'USD'] = 'CNY'
    exchange_rate: Decimal = Field(gt=0, le=10000)
    input: Decimal = Field(ge=0, le=100000)
    output: Decimal = Field(ge=0, le=100000)
    cache_read: Decimal = Field(ge=0, le=100000)
    cache_write: Decimal = Field(ge=0, le=100000)
    cache_write_1h: Decimal | None = Field(default=None,ge=0,le=100000)


class DeepSeekRates(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: Decimal = Field(ge=0, le=100000)
    output: Decimal = Field(ge=0, le=100000)
    cache_read: Decimal = Field(ge=0, le=100000)


class TimePricing(BaseModel):
    model_config = ConfigDict(extra='forbid')
    off_peak: DeepSeekRates


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    protocol: Literal['ollama', 'anthropic', 'openai', 'deepseek', 'qwen', 'gemini', 'openai_compatible']
    endpoint: str = Field(max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    local: bool = False
    enabled: bool = True
    api_key: SecretStr = SecretStr('')
    capabilities: set[Capability] = Field(default_factory=lambda: {'text'})
    context_limit: int = Field(default=32768, ge=1024, le=2000000)
    price: Price | None = None
    time_pricing: TimePricing | None = None

    @model_validator(mode='after')
    def validate_config(self):
        if self.time_pricing and (self.protocol != 'deepseek' or self.local or self.price is None):
            raise ValueError('分时计价仅适用于已配置高峰价格的 DeepSeek 云端模型')
        if self.time_pricing and (self.price.currency != 'CNY' or self.price.exchange_rate != 1):
            raise ValueError('DeepSeek 分时价格须使用人民币，汇率固定为 1')
        u = urlsplit(self.endpoint)
        try:
            port = u.port
        except ValueError:
            raise ValueError('无效端口') from None
        if (not u.hostname or u.username or u.password or u.query or u.fragment
                or u.scheme not in {'http', 'https'} or (port is not None and port == 0)):
            raise ValueError('端点必须为不含凭证、查询或片段的 HTTP(S) 地址')
        host = u.hostname.lower()
        loopback = host in {'localhost', 'host.docker.internal'}
        try:
            address = ipaddress.ip_address(host)
            loopback = loopback or address.is_loopback
            if not self.local and not address.is_global:
                raise ValueError('云端地址不能指向本机或内网')
        except ValueError as exc:
            if str(exc) == '云端地址不能指向本机或内网':
                raise
        if self.local:
            if self.protocol != 'ollama' or not loopback or u.path.rstrip('/'):
                raise ValueError('本地模型仅允许本机 Ollama 根端点')
            if re.search(r'cloud|://', self.model, re.I):
                raise ValueError('本地模型不能引用云端模型')
        elif self.protocol == 'ollama' or loopback or u.scheme != 'https':
            raise ValueError('云端服务必须使用 HTTPS，Ollama 必须为本地')
        if not self.local and self.enabled and (self.price is None or not self.api_key.get_secret_value()):
            raise ValueError('开放云端模型需要完整价格与凭证')
        return self


def _public(row):
    config = json.loads(row['config'])
    config['has_key'] = bool(config.pop('api_key', ''))
    return {**config, 'version': row['version']}


async def list_models():
    db = await get_db()
    try:
        models=[_public(r) for r in await (await db.execute('SELECT * FROM llm_models ORDER BY id')).fetchall()
                if not json.loads(r['config']).get('_deleted')]
        for model in models:
            checks=await (await db.execute('SELECT capability,success,reason,created_at FROM llm_model_checks WHERE model_id=? AND version=?',(model['id'],model['version']))).fetchall()
            model['checks']=[dict(r) for r in checks]
            model['validation_status']='已有能力实测记录' if checks else '尚未真实联调'
        return models
    finally:
        await db.close()


async def save_model(data, *, expected_version):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await _require_admin(db, require_user_id())
        old = await (await db.execute('SELECT * FROM llm_models WHERE id=?', (data.get('id'),))).fetchone()
        if old and json.loads(old['config']).get('_deleted'):
            raise LLMError('model_deleted', '模型服务已删除，请使用新的模型标识')
        if (old['version'] if old else 0) != expected_version:
            raise LLMError('config_conflict', '模型配置已更新，请刷新')
        data = dict(data)
        # 空密钥表示保留；停用服务仍可保存，配置版本只能成功后增长。
        if old and not data.get('api_key'):
            data['api_key'] = json.loads(old['config']).get('api_key', '')
        model = ModelConfig(**data)
        raw = model.model_dump(mode='json')
        raw['api_key'] = model.api_key.get_secret_value()
        version = expected_version + 1
        await db.execute('INSERT INTO llm_models VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,config=excluded.config',
                         (model.id, version, json.dumps(raw)))
        from services import audit, accounts
        await audit.record(db, action='configuration_changed', outcome='success', created_at=accounts._now(None), actor_id=require_user_id())
        await db.commit()
        return _public({'config': json.dumps(raw), 'version': version})
    finally:
        await db.close()


async def _model_for_update(db, identifier, expected_version):
    await _require_admin(db, require_user_id())
    row = await (await db.execute('SELECT * FROM llm_models WHERE id=?', (identifier,))).fetchone()
    if not row or json.loads(row['config']).get('_deleted'):
        raise LLMError('model_missing', '模型服务不存在或已删除')
    if row['version'] != expected_version:
        raise LLMError('config_conflict', '模型配置已更新，请刷新')
    return json.loads(row['config'])


async def set_model_enabled(identifier, *, enabled, expected_version):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        data = await _model_for_update(db, identifier, expected_version)
        data['enabled'] = enabled
        ModelConfig(**data)
        await db.execute('UPDATE llm_models SET version=?,config=? WHERE id=?',
                         (expected_version + 1, json.dumps(data), identifier))
        from services import audit, accounts
        await audit.record(db, action='configuration_changed', outcome='success',
                           created_at=accounts._now(None), actor_id=require_user_id())
        await db.commit()
        return _public({'config': json.dumps(data), 'version': expected_version + 1})
    finally:
        await db.close()


async def delete_model(identifier, *, expected_version):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        data = await _model_for_update(db, identifier, expected_version)
        if data['enabled']:
            raise LLMError('model_enabled', '请先停用模型服务，再删除')
        # 保留标识和版本防止旧任务、旧个人选择绑定到新服务；移除凭证与其余配置。
        tombstone = {'_deleted': True, 'endpoint': data['endpoint']}
        await db.execute('UPDATE llm_models SET version=?,config=? WHERE id=?',
                         (expected_version + 1, json.dumps(tombstone), identifier))
        from services import audit, accounts
        await audit.record(db, action='configuration_changed', outcome='success',
                           created_at=accounts._now(None), actor_id=require_user_id())
        await db.commit()
        return {'deleted': True, 'id': identifier}
    finally:
        await db.close()


async def preferences():
    db = await get_db()
    try:
        row = await (await db.execute('SELECT * FROM llm_preferences WHERE user_id=?', (require_user_id(),))).fetchone()
        automatic = await (await db.execute("SELECT value FROM user_settings WHERE user_id=? AND key='automatic_ai_enabled'",(require_user_id(),))).fetchone()
        return {**({'version': row['version'], 'local_only': bool(row['local_only']), 'scenes': json.loads(row['scenes'])} if row else {'version': 0, 'local_only': False, 'scenes': {}}), 'automatic_ai_enabled':bool(automatic and automatic[0]=='1')}
    finally:
        await db.close()


async def save_preferences(*, local_only, scenes, expected_version=None, automatic_ai_enabled=None):
    if type(local_only) is not bool or not isinstance(scenes, dict) or set(scenes) - set(SCENES):
        raise ValueError('无效场景或模式')
    for ids in scenes.values():
        if not isinstance(ids, list) or len(ids) > 3 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('模型候选必须是最多三项、不重复的有序清单')
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        from services.task_context import ensure_active
        await ensure_active()
        old = await (await db.execute('SELECT version FROM llm_preferences WHERE user_id=?', (require_user_id(),))).fetchone()
        version = old[0] if old else 0
        if expected_version is not None and version != expected_version:
            raise LLMError('config_conflict', '个人配置已更新，请刷新')
        await db.execute('INSERT INTO llm_preferences VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,local_only=excluded.local_only,scenes=excluded.scenes',
                         (require_user_id(), version+1, int(local_only), json.dumps(scenes)))
        if automatic_ai_enabled is not None:
            if type(automatic_ai_enabled) is not bool:raise ValueError('自动任务开关无效')
            await db.execute("INSERT INTO user_settings VALUES (?,'automatic_ai_enabled',?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",(require_user_id(),'1' if automatic_ai_enabled else '0'))
        from services.notifications import insert_event
        await insert_event(db,require_user_id(),'model_switch',f'preferences:{version+1}','模型偏好已更新','本人模型场景偏好已保存，后续调用按当前选择执行。')
        await db.commit()
        return {'version': version+1, 'local_only': local_only, 'scenes': scenes, 'automatic_ai_enabled':automatic_ai_enabled}
    finally:
        await db.close()


async def candidates(scene, required):
    prefs = await preferences()
    ids = prefs['scenes'].get(scene, [])
    models = {m['id']: m for m in await list_models()}
    return [models[i] for i in ids if i in models and models[i]['enabled']
            and (not prefs['local_only'] or models[i]['local'])
            and set(required) <= set(models[i]['capabilities'])]


async def private_model(identifier):
    db = await get_db()
    try:
        row = await (await db.execute('SELECT * FROM llm_models WHERE id=?', (identifier,))).fetchone()
        if not row or json.loads(row['config']).get('_deleted'):
            raise LLMError('model_missing', '模型不存在')
        return ModelConfig(**json.loads(row['config'])), row['version']
    finally:
        await db.close()
