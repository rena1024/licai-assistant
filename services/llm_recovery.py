"""恢复数据库时保留费用事实及稳定 UUID 对应的仅本地偏好。"""
import json
from services.model_catalog import SCHEMA as MODEL_SCHEMA
from services.budget import SCHEMA as BUDGET_SCHEMA
from services.local_queue import SCHEMA as QUEUE_SCHEMA


def preserve_finance(current, restored):
    # 旧恢复点可能早于 B；只新增表，不修改业务 schema。
    restored.executescript(MODEL_SCHEMA + BUDGET_SCHEMA + QUEUE_SCHEMA)
    restored.execute("UPDATE llm_local_jobs SET status='interrupted' WHERE status IN ('queued','running')")
    restored.execute("INSERT OR REPLACE INTO runtime_metadata VALUES ('llm_schema_version','1')")
    if not current.execute("SELECT 1 FROM sqlite_master WHERE name='llm_attempts'").fetchone():
        return
    identities = dict(restored.execute('SELECT uuid,id FROM users'))
    old_ids = dict(current.execute('SELECT id,uuid FROM users'))
    # 两个版本都坚持仅本地时才有意义；恢复不得放宽恢复前的安全偏好。
    for uid,scenes in current.execute('SELECT user_id,scenes FROM llm_preferences WHERE local_only=1'):
        target = identities.get(old_ids.get(uid))
        if target:
            restored.execute('INSERT INTO llm_preferences VALUES (?,1,1,?) ON CONFLICT(user_id) DO UPDATE SET local_only=1,version=version+1', (target,scenes))
    # 账本独立保留，不随旧备份回退。重叠 ID 使用恢复前事实，仍在途的尝试转待核实。
    columns = [r[1] for r in current.execute('PRAGMA table_info(llm_attempts)')]
    for values in current.execute('SELECT * FROM llm_attempts').fetchall():
        record = dict(zip(columns,values))
        record['owner_id'] = identities.get(record['user_uuid']) if record['user_uuid'] else None
        if record['state'] == 'reserved':
            record['state'] = 'unknown'
        restored.execute(f"INSERT OR REPLACE INTO llm_attempts ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(record[c] for c in columns))
    # 恢复点中未出现在当前库的在途记录同样不能自动重试。
    restored.execute("UPDATE llm_attempts SET state='unknown' WHERE state='reserved'")
    restored.execute("INSERT OR IGNORE INTO llm_service_holds SELECT COALESCE(service_id,model_id) FROM llm_attempts WHERE state='unknown'")
    restored.execute('DELETE FROM llm_budget_totals')
    restored.execute('DELETE FROM llm_usage_monthly')
    for r in restored.execute('SELECT owner_id,day,month,cost,reserved,state,model_id,scene,input_tokens,output_tokens FROM llm_attempts').fetchall():
        owner,day,month,cost,reserved,state,model,scene,inputs,outputs = r
        for subject in [0] + ([owner] if owner is not None else []):
            for kind,period in [('day',day),('month',month)]:
                restored.execute('INSERT INTO llm_budget_totals VALUES (?,?,?,?,?) ON CONFLICT(subject,kind,period) DO UPDATE SET spent=spent+excluded.spent,reserved=reserved+excluded.reserved',
                                 (subject,kind,period,cost or 0,reserved if state != 'settled' else 0))
            if state == 'settled':
                restored.execute('INSERT INTO llm_usage_monthly VALUES (?,?,?,?,1,?,?) ON CONFLICT(subject,month,model_id,scene) DO UPDATE SET calls=calls+1,input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens', (subject,month,model,scene,inputs or 0,outputs or 0))
        if state == 'unknown':
            restored.execute('INSERT OR IGNORE INTO llm_service_holds VALUES (?)', (model,))
    for subject,kind,period,threshold in current.execute('SELECT * FROM llm_budget_alerts'):
        target = 0 if subject == 0 else identities.get(old_ids.get(subject))
        if target is not None:
            restored.execute('INSERT OR IGNORE INTO llm_budget_alerts VALUES (?,?,?,?)', (target,kind,period,threshold))
