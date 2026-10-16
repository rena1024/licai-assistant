"""个人业务快照导入导出；认证、公共配置及费用账本不在业务白名单中。"""
from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from database import get_db
from services.user_context import require_user_id

router = APIRouter(prefix='/api/data', tags=['data'])
# 顺序决定父子引用重建与反向清理顺序，外部 ID 永不直接写回。
TABLES = ('holdings', 'external_assets', 'unwind_plans', 'unwind_tranches',
          'position_actions', 'external_asset_actions', 'dca_schedules',
          'trade_log', 'custom_alerts', 'alert_config', 'cashflow_monthly',
          'brokers', 'watchlist', 'watchlist_group', 'position_thesis',
          'thesis_revision', 'portfolio_snapshots', 'morning_briefings',
          'ask_session', 'ask_message', 'ask_run', 'tool_gap', 'prompt_rule', 'user_settings')
REFERENCES = {'external_asset_actions': {'asset_id': 'external_assets'},
              'dca_schedules': {'asset_id': 'external_assets'},
              'position_actions': {'tranche_id': 'unwind_tranches'},
              'ask_message': {'session_id': 'ask_session'},
              'ask_run': {'session_id': 'ask_session'},
              'prompt_rule': {'session_id': 'ask_session'}}
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _scope(table):
    if table == 'dca_schedules':
        return 'asset_id IN (SELECT id FROM external_assets WHERE user_id=?)'
    return 'user_id=?'


@router.get('/export')
async def export_all():
    uid = require_user_id()
    db = await get_db()
    try:
        await db.execute('BEGIN')
        tables = {}
        for table in TABLES:
            rows = await (await db.execute(f'SELECT * FROM {table} WHERE {_scope(table)}', (uid,))).fetchall()
            tables[table] = [{k: row[k] for k in row.keys() if k != 'user_id'} for row in rows if table != 'user_settings' or not (row['key'].startswith('task_done:') or row['key'] == 'automatic_ai_enabled')]
        await db.commit()
    finally:
        await db.close()
    body = json.dumps({'_meta': {'version': 2, 'scope': 'personal_business', 'complete_backup': False,
                               'exported_at': datetime.now().isoformat()}, 'tables': tables}, ensure_ascii=False)
    return Response(body, media_type='application/json', headers={
        'Content-Disposition': 'attachment; filename="licai-personal.json"', 'X-Export-Version': '2'})


@router.post('/import')
async def import_all(file: UploadFile = File(...), mode: str = 'replace'):
    if mode not in {'replace', 'merge'}:
        raise HTTPException(400, 'mode 必须是 replace 或 merge')
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, '导入文件不能超过 10 MB')
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, '导入文件不是有效 JSON') from None
    tables = payload.get('tables') if isinstance(payload, dict) else None
    if not isinstance(tables, dict) or not tables or set(tables) - set(TABLES):
        raise HTTPException(400, '只允许导入个人业务白名单中的表')
    uid = require_user_id()
    _backup_current_db()
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        columns = {}
        for table, rows in tables.items():
            columns[table] = {r['name'] for r in await (await db.execute(f'PRAGMA table_info({table})')).fetchall()}
            if not isinstance(rows, list) or any(not isinstance(row, dict) or 'user_id' in row or set(row)-columns[table] for row in rows):
                raise ValueError('数据行包含非法字段或伪造身份')
        if mode == 'replace':
            for table in reversed(TABLES):
                scope = _scope(table)
                if table == 'user_settings':
                    scope += " AND key NOT LIKE 'task_done:%' AND key != 'automatic_ai_enabled'"
                await db.execute(f'DELETE FROM {table} WHERE {scope}', (uid,))
        mapping, summary = {}, {}
        for table in TABLES:
            if table not in tables:
                continue
            mapping[table] = {}
            count = 0
            for original in tables[table]:
                row = dict(original)
                if table == 'user_settings' and (row.get('key','').startswith('task_done:') or row.get('key') == 'automatic_ai_enabled'):
                    raise ValueError('不能导入后台执行状态')
                old_id = row.pop('id', None)
                for key, parent in REFERENCES.get(table, {}).items():
                    value = row.get(key)
                    if value is not None:
                        if value not in mapping.get(parent, {}):
                            raise ValueError('关联资源必须在同一文件中提供，不能引用已有或他人的 ID')
                        row[key] = mapping[parent][value]
                if table != 'dca_schedules':
                    row['user_id'] = uid
                if table == 'portfolio_snapshots' and row.get('by_asset'):
                    values = json.loads(row['by_asset'])
                    row['by_asset'] = json.dumps({f"EXT:{mapping['external_assets'][int(key[4:])]}" if key.startswith('EXT:') else key:value for key,value in values.items()})
                if table == 'ask_run':
                    row['run_id'] = uuid.uuid4().hex
                    row['status'] = 'interrupted'
                keys = list(row)
                if not keys:
                    raise ValueError('不能导入空数据行')
                # merge 只按本人自然键合并；主键 ID 全部由数据库重新生成。
                conflict = ' ON CONFLICT DO UPDATE SET ' + ','.join(f'{k}=excluded.{k}' for k in keys if k != 'user_id') if mode == 'merge' else ''
                cursor = await db.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)}){conflict}", tuple(row[k] for k in keys))
                if old_id is not None:
                    if type(old_id) is not int or old_id in mapping[table]:
                        raise ValueError('资源 ID 无效或重复')
                    mapping[table][old_id] = cursor.lastrowid
                count += 1
            summary[table] = count
        await db.commit()
    except (ValueError, sqlite3.DatabaseError, TypeError, KeyError, AttributeError):
        await db.rollback()
        raise HTTPException(400, '个人数据格式或关联关系无效，导入已全部撤销') from None
    finally:
        await db.close()
    return {'message': '导入完成', 'mode': mode, 'imported': summary}



def _backup_current_db():
    """SQLite 在线备份保留一致视图，并验证目标路径不越出运行目录。"""
    from services.runtime_paths import get_runtime_paths, database_path
    from config import config
    source = database_path(config.db_path)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target = get_runtime_paths().file('backups', f'pre-import-{stamp}.db')
    with sqlite3.connect(source) as reader, sqlite3.connect(target) as writer:
        reader.backup(writer)
    target.chmod(0o600)
    return target
