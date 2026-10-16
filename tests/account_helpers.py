"""仅供旧数据库单元测试创建合成 owner，不在生产中提供默认身份。"""
async def seed_owner():
    from database import get_db, DEFAULT_BROKERS
    db = await get_db()
    try:
        await db.execute("INSERT OR IGNORE INTO users(id,uuid,username,password_hash,role,created_at) VALUES (1,'synthetic-owner','synthetic-owner','unused','admin',0)")
        for values in DEFAULT_BROKERS:
            await db.execute('INSERT OR IGNORE INTO brokers(user_id,name,stock_rate,stock_min,etf_rate,etf_min,is_default) VALUES (1,?,?,?,?,?,?)', values)
        await db.commit()
    finally:
        await db.close()
