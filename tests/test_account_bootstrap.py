"""账号初始化、恢复及其审计只使用隔离临时库。"""
import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest


NOW = 1_792_022_400
PASSWORD = "Synthetic-password-01!"
NEW_PASSWORD = "Synthetic-password-02!"


@pytest.fixture
def account_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LICAI_DATA_DIR", str(tmp_path))
    from database import init_db
    asyncio.run(init_db())
    return tmp_path / "portfolio.db"


def rows(path, query, params=()):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query, params)]


async def bootstrap(now=NOW):
    from services import accounts
    path = await accounts.issue_bootstrap_token(now=now)
    token = path.read_text().strip()
    user = await accounts.bootstrap_admin(token=token, username="Admin", password=PASSWORD, now=now)
    return user, token


def test_bootstrap_race_has_one_admin(account_db):
    from services import accounts

    async def exercise():
        path = await accounts.issue_bootstrap_token(now=NOW)
        token = path.read_text().strip()
        results = await asyncio.gather(*(
            accounts.bootstrap_admin(token=token, username=name, password=PASSWORD, now=NOW)
            for name in ("AdminA", "AdminB")
        ), return_exceptions=True)
        successes = [result for result in results if not isinstance(result, Exception)]
        assert len(successes) == 1
        assert isinstance(next(result for result in results if isinstance(result, Exception)), accounts.AccountError)
        assert not path.exists()
        with pytest.raises(accounts.AccountError):
            await accounts.bootstrap_admin(token=token, username="Again", password=PASSWORD, now=NOW)

    asyncio.run(exercise())
    admins = rows(account_db, "SELECT * FROM users WHERE role='admin'")
    assert len(admins) == 1
    assert admins[0]["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in account_db.read_bytes().decode(errors="ignore")


def test_issuing_token_is_idempotent_and_database_stores_only_hash(account_db):
    from services import accounts

    async def exercise():
        paths = await asyncio.gather(*(accounts.issue_bootstrap_token(now=NOW) for _ in range(2)))
        assert paths[0] == paths[1]
        token = paths[0].read_text().strip()
        assert len(token) == 43
        assert token.encode() not in account_db.read_bytes()
        if os.name == "posix":
            assert paths[0].stat().st_mode & 0o777 == 0o600
        assert rows(account_db, "SELECT token_hash FROM bootstrap_state")[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()

    asyncio.run(exercise())


def test_bootstrap_expiry_and_local_renewal(account_db):
    from services import accounts

    async def exercise():
        path = await accounts.issue_bootstrap_token(now=NOW)
        old = path.read_text().strip()
        with pytest.raises(accounts.AccountError, match="凭据"):
            await accounts.bootstrap_admin(token=old, username="Admin", password=PASSWORD, now=NOW + 86400)
        path = await accounts.issue_bootstrap_token(renew=True, now=NOW + 86400)
        new = path.read_text().strip()
        assert new != old
        with pytest.raises(accounts.AccountError):
            await accounts.bootstrap_admin(token=old, username="Admin", password=PASSWORD, now=NOW + 86400)
        await accounts.bootstrap_admin(token=new, username="Admin", password=PASSWORD, now=NOW + 86400)
        assert await accounts.issue_bootstrap_token(renew=True, now=NOW + 86401) is None

    asyncio.run(exercise())


def test_admin_creates_only_ordinary_users_and_checks_actor(account_db):
    from services import accounts

    async def exercise():
        admin, _ = await bootstrap()
        a = await accounts.create_user(actor_id=admin.id, username="UserA", password=PASSWORD, now=NOW)
        b = await accounts.create_user(actor_id=admin.id, username="UserB", password=PASSWORD, now=NOW)
        assert a.role == b.role == "user"
        assert a.must_change_password and b.must_change_password
        assert len({admin.uuid, a.uuid, b.uuid}) == 3
        with pytest.raises(accounts.AccountError):
            await accounts.create_user(actor_id=a.id, username="Intruder", password=PASSWORD, now=NOW)
        with pytest.raises(accounts.AccountError):
            await accounts.create_user(actor_id=999999, username="Intruder", password=PASSWORD, now=NOW)
        with pytest.raises(accounts.AccountError):
            await accounts.create_user(actor_id=admin.id, username="USERA", password=PASSWORD, now=NOW)
        return a

    a = asyncio.run(exercise())
    assert a.username == "usera"
    assert len(rows(account_db, "SELECT id FROM users")) == 3


def test_recovery_revokes_sessions_and_preserves_business_data(account_db):
    from services import accounts

    async def exercise():
        admin, _ = await bootstrap()
        user = await accounts.create_user(actor_id=admin.id, username="UserA", password=PASSWORD, now=NOW)
        with sqlite3.connect(account_db) as db:
            db.execute("INSERT INTO holdings (user_id, stock_code, shares, cost_price) VALUES (1, 'FAKE', 2, 3)")
            for owner, tag in ((admin.id, "device-a"), (admin.id, "device-b"), (user.id, "other-device")):
                db.execute(
                    "INSERT INTO sessions (session_id, token_hash, user_id, auth_version, created_at, expires_at) VALUES (?, ?, ?, 1, ?, ?)",
                    (tag, hashlib.sha256(tag.encode()).hexdigest(), owner, NOW, NOW + 2592000),
                )
        recovered = await accounts.recover_admin_password(password=NEW_PASSWORD, now=NOW + 10)
        assert recovered.id == admin.id and recovered.uuid == admin.uuid
        assert recovered.auth_version == 2 and recovered.must_change_password
        return admin, user

    admin, user = asyncio.run(exercise())
    assert all(row["revoked_at"] == NOW + 10 for row in rows(account_db, "SELECT * FROM sessions WHERE user_id=?", (admin.id,)))
    assert rows(account_db, "SELECT * FROM sessions WHERE user_id=?", (user.id,))[0]["revoked_at"] is None
    assert rows(account_db, "SELECT * FROM holdings")[0]["shares"] == 2
    stored = rows(account_db, "SELECT password_hash FROM users WHERE id=?", (admin.id,))[0]["password_hash"]
    from pwdlib import PasswordHash
    assert PasswordHash.recommended().verify(NEW_PASSWORD, stored)
    assert not PasswordHash.recommended().verify(PASSWORD, stored)


def test_reset_user_password_is_admin_only_and_revokes_target_sessions(account_db):
    from services import accounts

    async def exercise():
        admin, _ = await bootstrap()
        a = await accounts.create_user(actor_id=admin.id, username="UserA", password=PASSWORD, now=NOW)
        b = await accounts.create_user(actor_id=admin.id, username="UserB", password=PASSWORD, now=NOW)
        with sqlite3.connect(account_db) as db:
            db.execute("INSERT INTO sessions VALUES ('device-a', ?, ?, 1, ?, ?, NULL)", (hashlib.sha256(b'a').hexdigest(), a.id, NOW, NOW + 2592000))
        with pytest.raises(accounts.AccountError):
            await accounts.reset_user_password(actor_id=b.id, user_id=a.id, password=NEW_PASSWORD, now=NOW)
        with pytest.raises(accounts.AccountError):
            await accounts.reset_user_password(actor_id=admin.id, user_id=admin.id, password=NEW_PASSWORD, now=NOW)
        changed = await accounts.reset_user_password(actor_id=admin.id, user_id=a.id, password=NEW_PASSWORD, now=NOW)
        assert changed.must_change_password and changed.auth_version == 2

    asyncio.run(exercise())
    assert rows(account_db, "SELECT revoked_at FROM sessions")[0]["revoked_at"] == NOW


def test_existing_unowned_business_data_blocks_bootstrap(account_db):
    from services import accounts

    with sqlite3.connect(account_db) as db:
        db.execute("DROP TABLE cashflow_monthly")
        db.execute("CREATE TABLE cashflow_monthly(month TEXT, income REAL, fixed_cost REAL)")
        db.execute("INSERT INTO cashflow_monthly (month, income, fixed_cost) VALUES ('2026-01', 20, 5)")
    before = rows(account_db, "SELECT * FROM cashflow_monthly")
    with pytest.raises(accounts.AccountError, match="无归属|历史"):
        asyncio.run(accounts.issue_bootstrap_token(now=NOW))
    assert rows(account_db, "SELECT * FROM cashflow_monthly") == before
    assert not rows(account_db, "SELECT id FROM users")


@pytest.mark.parametrize("password", ["123", "x" * 129])
def test_invalid_password_does_not_consume_bootstrap_token(account_db, password):
    from services import accounts

    async def exercise():
        path = await accounts.issue_bootstrap_token(now=NOW)
        token = path.read_text().strip()
        with pytest.raises(accounts.AccountError):
            await accounts.bootstrap_admin(token=token, username="Admin", password=password, now=NOW)
        await accounts.bootstrap_admin(token=token, username="Admin", password=PASSWORD, now=NOW)

    asyncio.run(exercise())


def test_token_publication_failure_rolls_back_state(account_db, monkeypatch):
    from services import accounts

    def fail_publish(*args, **kwargs):
        raise PermissionError("synthetic failure")

    monkeypatch.setattr(os, "replace", fail_publish)
    with pytest.raises(PermissionError):
        asyncio.run(accounts.issue_bootstrap_token(now=NOW))
    assert not rows(account_db, "SELECT * FROM bootstrap_state")
    assert not rows(account_db, "SELECT id FROM users")


def test_bootstrap_api_hides_secrets_and_has_no_public_recovery(account_db):
    from services import accounts
    from fastapi.testclient import TestClient
    from run import app

    path = asyncio.run(accounts.issue_bootstrap_token())
    token = path.read_text().strip()
    with TestClient(app) as client:
        assert client.get("/api/auth/bootstrap/status").json() == {"initialized": False, "credential_available": True}
        payload = {"token": token, "username": "Admin", "password": PASSWORD}
        bad = client.post("/api/auth/bootstrap", json={**payload, "role": "admin"})
        assert bad.status_code == 422
        assert token not in bad.text and PASSWORD not in bad.text
        cross = client.post("/api/auth/bootstrap", json=payload, headers={"Origin": "https://attacker.invalid"})
        assert cross.status_code == 403
        result = client.post("/api/auth/bootstrap", json=payload)
        assert result.status_code == 201
        assert result.json()["role"] == "admin"
        assert "password_hash" not in result.text and token not in result.text and PASSWORD not in result.text
        assert client.post("/api/auth/bootstrap", json=payload).status_code == 409
        assert client.post("/api/auth/recover", json={"password": NEW_PASSWORD}).status_code == 404
        assert client.get("/api/auth/bootstrap/status").json() == {"initialized": True, "credential_available": False}


def test_cli_recovery_never_accepts_password_arguments(account_db, monkeypatch, capsys):
    from scripts import admin_recovery

    with pytest.raises(SystemExit) as stopped:
        admin_recovery.main(["recover", "--password", NEW_PASSWORD])
    assert stopped.value.code == 2
    # argparse 的未知参数错误也不得回显密码。
    assert NEW_PASSWORD not in capsys.readouterr().err


def test_cli_recovers_using_hidden_prompt_only(account_db, monkeypatch, capsys):
    from scripts import admin_recovery
    import sys

    asyncio.run(bootstrap())
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(admin_recovery.getpass, "getpass", lambda prompt: NEW_PASSWORD)
    assert admin_recovery.main(["recover"]) == 0
    captured = capsys.readouterr()
    assert NEW_PASSWORD not in captured.out + captured.err
    assert rows(account_db, "SELECT auth_version FROM users")[0]["auth_version"] == 2


def test_audit_contains_only_structural_fields(account_db, caplog):
    from services import accounts

    async def exercise():
        admin, token = await bootstrap()
        await accounts.create_user(actor_id=admin.id, username="UserA", password=PASSWORD, now=NOW)
        await accounts.recover_admin_password(password=NEW_PASSWORD, now=NOW)
        return token

    token = asyncio.run(exercise())
    events = rows(account_db, "SELECT * FROM audit_events")
    assert {event["action"] for event in events} >= {"bootstrap_issued", "bootstrap_completed", "user_created", "password_recovered"}
    serialized = json.dumps(events) + caplog.text
    assert all(secret not in serialized for secret in (PASSWORD, NEW_PASSWORD, token, "password_hash"))


def test_audit_failure_rolls_back_admin_and_token_consumption(account_db, monkeypatch):
    from services import accounts, audit

    async def exercise():
        path = await accounts.issue_bootstrap_token(now=NOW)
        token = path.read_text().strip()
        original = audit.record

        async def fail_audit(db, **event):
            if event["action"] == "bootstrap_completed":
                raise OSError("synthetic audit storage failure")
            await original(db, **event)

        with monkeypatch.context() as patch:
            patch.setattr(audit, "record", fail_audit)
            with pytest.raises(OSError):
                await accounts.bootstrap_admin(token=token, username="Admin", password=PASSWORD, now=NOW)
        assert not rows(account_db, "SELECT id FROM users")
        assert rows(account_db, "SELECT consumed_at FROM bootstrap_state")[0]["consumed_at"] is None
        await accounts.bootstrap_admin(token=token, username="Admin", password=PASSWORD, now=NOW)

    asyncio.run(exercise())


def test_future_account_schema_is_rejected_without_altering_data(account_db):
    from database import init_db
    from services.runtime_paths import RuntimePathError

    with sqlite3.connect(account_db) as db:
        db.execute("UPDATE runtime_metadata SET value='999' WHERE key='account_schema_version'")
    before = account_db.read_bytes()
    with pytest.raises(RuntimePathError, match="版本"):
        asyncio.run(init_db())
    assert account_db.read_bytes() == before


def test_modified_broker_templates_are_not_adopted_by_first_admin(account_db):
    from services import accounts

    with sqlite3.connect(account_db) as db:
        db.execute("DROP TABLE brokers")
        db.execute("CREATE TABLE brokers(name TEXT, stock_rate REAL, stock_min REAL, etf_rate REAL, etf_min REAL, is_default INTEGER)")
        db.execute("INSERT INTO brokers VALUES ('招商证券',0.123,5,0.001,5,1)")
    with pytest.raises(accounts.AccountError, match="无归属|历史"):
        asyncio.run(accounts.issue_bootstrap_token(now=NOW))
    assert not rows(account_db, "SELECT id FROM users")


def test_bootstrap_rejects_oversize_body_before_password_processing(account_db):
    from fastapi.testclient import TestClient
    from run import app

    with TestClient(app) as client:
        result = client.post("/api/auth/bootstrap", content=b"x" * 16385, headers={"Content-Type": "application/json"})
        assert result.status_code == 413
    assert not rows(account_db, "SELECT id FROM users")


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_token_renewal_failure_preserves_previous_credential(account_db, monkeypatch, failure):
    import aiosqlite
    from services import accounts, audit

    async def exercise():
        path = await accounts.issue_bootstrap_token(now=NOW)
        original = path.read_bytes()
        original_state = rows(account_db, "SELECT * FROM bootstrap_state")

        async def fail(*args, **kwargs):
            raise OSError("synthetic persistence failure")

        with monkeypatch.context() as patch:
            if failure == "audit":
                patch.setattr(audit, "record", fail)
            else:
                patch.setattr(aiosqlite.Connection, "commit", fail)
            with pytest.raises(OSError):
                await accounts.issue_bootstrap_token(renew=True, now=NOW + 1)
        assert path.read_bytes() == original
        assert rows(account_db, "SELECT * FROM bootstrap_state") == original_state
        await accounts.bootstrap_admin(token=original.decode().strip(), username="Admin", password=PASSWORD, now=NOW + 1)

    asyncio.run(exercise())


def test_cli_recovery_does_not_run_business_migrations(account_db, monkeypatch):
    from scripts import admin_recovery
    import sys

    asyncio.run(bootstrap())
    with sqlite3.connect(account_db) as db:
        db.execute("INSERT INTO holdings (user_id, stock_code, shares, cost_price) VALUES (1, 'SYNTHETIC', 2, 3)")
    original_actions = rows(account_db, "SELECT * FROM position_actions")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(admin_recovery.getpass, "getpass", lambda prompt: NEW_PASSWORD)
    assert admin_recovery.main(["recover"]) == 0
    assert rows(account_db, "SELECT * FROM position_actions") == original_actions


def test_cancel_after_token_commit_keeps_current_credential_file(account_db, monkeypatch):
    import aiosqlite
    from services import accounts

    async def exercise():
        original_commit = aiosqlite.Connection.commit

        async def commit_then_cancel(db):
            await original_commit(db)
            raise asyncio.CancelledError()

        with monkeypatch.context() as patch:
            patch.setattr(aiosqlite.Connection, "commit", commit_then_cancel)
            with pytest.raises(asyncio.CancelledError):
                await accounts.issue_bootstrap_token(now=NOW)
        # 下次启动/本机命令可以取回已经生效的这一代，而不是得到丢失文件错误。
        path = await accounts.issue_bootstrap_token(now=NOW + 1)
        await accounts.bootstrap_admin(token=path.read_text().strip(), username="Admin", password=PASSWORD, now=NOW + 1)

    asyncio.run(exercise())


def test_system_proxy_does_not_block_repeated_first_startup(account_db, monkeypatch):
    from dataclasses import replace
    from database import set_config
    from services.runtime_paths import get_runtime_paths
    import run

    asyncio.run(set_config("network_proxy", "http://127.0.0.1:17890"))
    original = rows(account_db, "SELECT * FROM app_config ORDER BY key")
    paths = replace(get_runtime_paths(), profile="production")
    monkeypatch.setattr(run, "get_runtime_paths", lambda: paths)

    async def forbid_restoring_external_settings(*args, **kwargs):
        raise AssertionError("初始化前不应恢复外部配置或探测代理")

    monkeypatch.setattr(run, "get_config", forbid_restoring_external_settings)

    async def exercise():
        async with run.lifespan(run.app):
            first = rows(account_db, "SELECT * FROM bootstrap_state")
            assert len(first) == 1
        async with run.lifespan(run.app):
            assert rows(account_db, "SELECT * FROM bootstrap_state") == first

    asyncio.run(exercise())
    assert rows(account_db, "SELECT * FROM app_config ORDER BY key") == original
    assert not rows(account_db, "SELECT id FROM users")


def test_private_configuration_still_blocks_bootstrap_with_system_proxy(account_db):
    from database import set_config
    from services import accounts

    asyncio.run(set_config("network_proxy", "http://127.0.0.1:17890"))
    asyncio.run(set_config("feishu_webhook_url", "synthetic-private-notification-target"))
    original = rows(account_db, "SELECT * FROM app_config ORDER BY key")
    with pytest.raises(accounts.AccountError, match="无归属|历史"):
        asyncio.run(accounts.issue_bootstrap_token(now=NOW))
    assert rows(account_db, "SELECT * FROM app_config ORDER BY key") == original
    assert not rows(account_db, "SELECT id FROM users")


def test_bootstrap_accepts_four_character_password(account_db):
    from services import accounts, auth

    async def exercise():
        path = await accounts.issue_bootstrap_token(now=NOW)
        await accounts.bootstrap_admin(token=path.read_text().strip(), username='Admin', password='1234', now=NOW)
        assert (await auth.login(username='admin', password='1234', now=NOW)).account.role == 'admin'

    asyncio.run(exercise())
