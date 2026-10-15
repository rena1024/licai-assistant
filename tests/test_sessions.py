"""固定会话期限、设备撤销及网页认证边界，全部使用合成账号。"""
import asyncio
import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

NOW = 1_792_022_400
PASSWORD = "Synthetic-password-01!"
NEW_PASSWORD = "Synthetic-password-02!"


@pytest.fixture
def session_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LICAI_DATA_DIR", str(tmp_path))
    from database import init_db
    from services import accounts

    async def setup():
        await init_db()
        path = await accounts.issue_bootstrap_token(now=NOW)
        admin = await accounts.bootstrap_admin(token=path.read_text().strip(), username="admin", password=PASSWORD, now=NOW)
        user = await accounts.create_user(actor_id=admin.id, username="alice", password=PASSWORD, now=NOW)
        await accounts.create_user(actor_id=admin.id, username="bob", password=PASSWORD, now=NOW)
        return admin, user

    admin, user = asyncio.run(setup())
    return tmp_path / "portfolio.db", admin, user


def test_fixed_expiry_and_logout_scope(session_db):
    from services import auth

    async def exercise():
        a = await auth.login(username="admin", password=PASSWORD, now=NOW)
        b = await auth.login(username="admin", password=PASSWORD, now=NOW + 1)
        assert a.token != b.token
        assert a.expires_at == NOW + 30 * 86400
        for t in (NOW + 1, NOW + 86400, a.expires_at - 1):
            assert (await auth.authenticate(a.token, now=t)).expires_at == a.expires_at
        await auth.logout(a.token, now=NOW + 3)
        with pytest.raises(auth.AuthError):
            await auth.authenticate(a.token, now=NOW + 4)
        assert (await auth.authenticate(b.token, now=NOW + 4)).account.username == "admin"
        with pytest.raises(auth.AuthError):
            await auth.authenticate(b.token, now=b.expires_at)
        return a

    a = asyncio.run(exercise())
    with sqlite3.connect(session_db[0]) as db:
        assert db.execute("SELECT token_hash FROM sessions WHERE token_hash=?", (hashlib.sha256(a.token.encode()).hexdigest(),)).fetchone()
    assert a.token.encode() not in session_db[0].read_bytes()


def test_password_change_revokes_all_devices_not_other_users(session_db):
    from services import auth

    async def exercise():
        a = await auth.login(username="alice", password=PASSWORD, now=NOW)
        b = await auth.login(username="alice", password=PASSWORD, now=NOW)
        other = await auth.login(username="bob", password=PASSWORD, now=NOW)
        assert a.account.must_change_password
        with pytest.raises(auth.AuthError):
            await auth.change_password(a.token, current_password="incorrect", new_password=NEW_PASSWORD, now=NOW + 1)
        assert (await auth.authenticate(a.token, now=NOW + 1)).account.must_change_password
        await auth.change_password(a.token, current_password=PASSWORD, new_password=NEW_PASSWORD, now=NOW + 2)
        for token in (a.token, b.token):
            with pytest.raises(auth.AuthError):
                await auth.authenticate(token, now=NOW + 3)
        assert (await auth.authenticate(other.token, now=NOW + 3)).account.username == "bob"
        fresh = await auth.login(username="alice", password=NEW_PASSWORD, now=NOW + 3)
        assert not fresh.account.must_change_password
        with pytest.raises(auth.AuthError):
            await auth.login(username="alice", password=PASSWORD, now=NOW + 4)

    asyncio.run(exercise())


def test_reset_and_disabled_account_reject_existing_sessions(session_db):
    from services import auth, accounts

    async def exercise():
        session = await auth.login(username="alice", password=PASSWORD, now=NOW)
        await accounts.reset_user_password(actor_id=session_db[1].id, user_id=session_db[2].id, password=NEW_PASSWORD, now=NOW + 1)
        with pytest.raises(auth.AuthError):
            await auth.authenticate(session.token, now=NOW + 2)
        fresh = await auth.login(username="alice", password=NEW_PASSWORD, now=NOW + 2)
        with sqlite3.connect(session_db[0]) as db:
            db.execute("UPDATE users SET active=0 WHERE id=?", (session_db[2].id,))
        with pytest.raises(auth.AuthError):
            await auth.authenticate(fresh.token, now=NOW + 3)
        with pytest.raises(auth.AuthError):
            await auth.login(username="alice", password=NEW_PASSWORD, now=NOW + 3)

    asyncio.run(exercise())


def test_http_cookie_csrf_origin_and_anonymous_gate(session_db):
    from run import app
    from services import auth
    with TestClient(app) as c:
        assert c.get("/api/auth/me").status_code == 401
        assert c.get("/api/portfolio").status_code == 401
        assert c.post("/api/auth/login", data={"username": "admin", "password": PASSWORD}).status_code == 415
        assert c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}, headers={"Origin": "https://evil.invalid"}).status_code == 403
        bad = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD, "extra": PASSWORD})
        assert bad.status_code == 422 and PASSWORD not in bad.text
        result = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert result.status_code == 200
        cookie = result.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=2592000" in cookie
        assert "token" not in result.json().get("account", {})
        me = c.get("/api/auth/me")
        csrf = me.json()["csrf_token"]
        assert me.headers["cache-control"] == "no-store"
        assert "set-cookie" not in me.headers
        assert c.post("/api/auth/logout").status_code == 403
        assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "null"}).status_code == 403
        assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert c.get("/api/auth/me").status_code == 401
        assert auth.COOKIE_NAME not in c.cookies


def test_temporary_password_only_allows_change_and_logout(session_db):
    from run import app
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        me = c.get("/api/auth/me").json()
        assert me["account"]["must_change_password"]
        assert c.get("/api/portfolio").status_code == 403
        headers = {"X-CSRF-Token": me["csrf_token"]}
        bad = c.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD}, headers=headers)
        assert bad.status_code == 422
        result = c.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers)
        assert result.status_code == 204
        assert c.get("/api/auth/me").status_code == 401


def test_bad_login_is_generic_and_audit_hides_passwords(session_db):
    from run import app
    with TestClient(app) as c:
        first = c.post("/api/auth/login", json={"username": "admin", "password": "incorrect"})
        second = c.post("/api/auth/login", json={"username": "absent", "password": "incorrect"})
        assert first.status_code == second.status_code == 401
        assert first.json() == second.json()
    with sqlite3.connect(session_db[0]) as db:
        events = repr(db.execute("SELECT * FROM audit_events").fetchall())
    assert PASSWORD not in events and "incorrect" not in events


def test_websocket_requires_session_and_same_origin(session_db):
    from run import app
    from starlette.websockets import WebSocketDisconnect
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('/ws', headers={'Origin': 'http://testserver'}):
                pass
        c.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD})
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('/ws', headers={'Origin': 'https://evil.invalid'}):
                pass
        with c.websocket_connect('/ws', headers={'Origin': 'http://testserver'}) as ws:
            ws.send_json({'type': 'ping'})
            assert ws.receive_json() == {'type': 'pong'}
