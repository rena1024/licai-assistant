"""恢复不退还已消费/未知搜索额度，也不重新外发旧通知。按稳定用户 UUID 重映射。"""
from services.search import SCHEMA as SEARCH_SCHEMA
from services.notifications import SCHEMA as NOTIFICATION_SCHEMA
from services.data_sources import SCHEMA as SOURCE_SCHEMA


def preserve_data_controls(current,restored):
    restored.executescript(SEARCH_SCHEMA+NOTIFICATION_SCHEMA+SOURCE_SCHEMA)
    old_ids=dict(current.execute('SELECT id,uuid FROM users'))
    identities=dict(restored.execute('SELECT uuid,id FROM users'))
    tables={r[0] for r in current.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'search_preferences' in tables:
        for uid,enabled in current.execute('SELECT user_id,enabled FROM search_preferences WHERE enabled=0'):
            target=identities.get(old_ids.get(uid))
            if target:
                restored.execute('INSERT OR REPLACE INTO search_preferences VALUES (?,0)',(target,))
    for uid,value in current.execute("SELECT user_id,value FROM user_settings WHERE key='notification_legacy_imported'"):
        target=identities.get(old_ids.get(uid))
        if target:
            restored.execute("INSERT OR REPLACE INTO user_settings VALUES (?,'notification_legacy_imported',?)",(target,value))
    if 'search_credits' in tables:
        for account,period,used in current.execute('SELECT * FROM search_credits'):
            restored.execute('INSERT INTO search_credits VALUES (?,?,?) ON CONFLICT(account,period) DO UPDATE SET used=MAX(used,excluded.used)',(account,period,used))
        for provider,version,config,paused in current.execute('SELECT * FROM search_channels'):
            # 不回滚已核验凭证或已暂停的状态；恢复后一律重新核验。
            restored.execute('INSERT OR REPLACE INTO search_channels VALUES (?,?,?,1)',(provider,version,config))
        for values in current.execute('SELECT * FROM search_attempts'):
            row=list(values)
            row[1]=identities.get(old_ids.get(row[1]))
            if row[1]:
                if row[6]=='reserved':row[6]='unknown'
                restored.execute('INSERT OR REPLACE INTO search_attempts VALUES (?,?,?,?,?,?,?,?)',row)
    if 'notifications' in tables:
        for table in ('notifications','notification_events','notification_channels','notification_deliveries'):
            columns=[r[1] for r in current.execute(f'PRAGMA table_info({table})')]
            owner=columns.index('user_id')
            for values in current.execute(f'SELECT * FROM {table}').fetchall():
                row=list(values)
                row[owner]=identities.get(old_ids.get(row[owner]))
                if row[owner]:
                    restored.execute(f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",row)
        for row in current.execute('SELECT * FROM notification_channel_versions'):
            if not restored.execute('SELECT 1 FROM notification_channels WHERE id=?',(row[0],)).fetchone():
                continue
            restored.execute('INSERT OR REPLACE INTO notification_channel_versions VALUES (?,?,?)',row)
    restored.execute("UPDATE notification_deliveries SET state='interrupted',error='备份恢复后旧投递不自动重发' WHERE state IN ('pending','retry','sending')")
    restored.execute('DELETE FROM notification_active')
    restored.execute('UPDATE search_channels SET paused=1')
    restored.execute("UPDATE search_attempts SET state='unknown' WHERE state='reserved'")
    restored.execute("INSERT OR REPLACE INTO runtime_metadata VALUES ('data_notifications_schema_version','1')")
