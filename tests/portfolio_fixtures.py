"""仅供历史业务单元测试创建固定的合成 owner，不建立登录会话。"""
import sqlite3


def seed_business_owner(path):
    with sqlite3.connect(path) as db:
        db.execute("INSERT OR IGNORE INTO users (id, uuid, username, password_hash, role, must_change_password, created_at) "
                   "VALUES (1, 'synthetic-business-owner', 'synthetic-business-owner', 'not-a-login-hash', 'user', 0, 0)")
