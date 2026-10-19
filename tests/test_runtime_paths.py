"""运行目录与测试保护必须在加载业务模块之前生效。"""
import asyncio
import sqlite3

import pytest


def test_all_runtime_files_live_under_data_dir(tmp_path):
    from services.runtime_paths import resolve_runtime_paths

    root = tmp_path / "理财 data"
    paths = resolve_runtime_paths({"LICAI_DATA_DIR": str(root)}, project_root=tmp_path)
    paths.prepare()
    assert paths.db == root / "portfolio.db"
    for folder in (paths.media, paths.cache, paths.backups, paths.config, paths.logs):
        assert folder.is_dir()
        assert folder.parent == root


def test_test_profile_cannot_open_real_database(tmp_path, monkeypatch):
    from services.runtime_paths import resolve_runtime_paths, RuntimePathError
    from config import config
    from database import get_db

    production = tmp_path / "production" / "portfolio.db"
    production.parent.mkdir()
    with sqlite3.connect(production) as db:
        db.execute("CREATE TABLE private_data (value TEXT)")
        db.execute("INSERT INTO private_data VALUES ('keep-me')")
    before = production.read_bytes()
    paths = resolve_runtime_paths({"LICAI_PROFILE": "test", "LICAI_DATA_DIR": str(tmp_path / "test")})
    monkeypatch.setenv("LICAI_DATA_DIR", str(paths.root))
    monkeypatch.setattr(config, "db_path", str(production))
    with pytest.raises(RuntimePathError, match="测试|目录"):
        asyncio.run(get_db())
    assert production.read_bytes() == before
    assert paths.db != production


def test_test_profile_requires_explicit_directory():
    from services.runtime_paths import resolve_runtime_paths, RuntimePathError

    with pytest.raises(RuntimePathError, match="LICAI_DATA_DIR"):
        resolve_runtime_paths({"LICAI_PROFILE": "test"})


def test_legacy_database_is_rejected_without_modification(tmp_path):
    from services.runtime_paths import resolve_runtime_paths, RuntimePathError

    old = tmp_path / "portfolio.db"
    old.write_bytes(b"legacy data must survive")
    paths = resolve_runtime_paths({}, project_root=tmp_path)
    with pytest.raises(RuntimePathError, match="旧|历史"):
        paths.check_legacy_database()
    assert old.read_bytes() == b"legacy data must survive"
    assert not paths.db.exists()


def test_explicit_fresh_directory_does_not_adopt_legacy_database(tmp_path):
    from services.runtime_paths import resolve_runtime_paths

    (tmp_path / "portfolio.db").write_bytes(b"legacy")
    paths = resolve_runtime_paths({"LICAI_DATA_DIR": str(tmp_path / "fresh")}, project_root=tmp_path)
    paths.check_legacy_database()
    paths.prepare()
    assert not paths.db.exists()
    assert (tmp_path / "portfolio.db").read_bytes() == b"legacy"


def test_test_profile_does_not_load_dotenv(tmp_path, monkeypatch):
    from services.runtime_paths import resolve_runtime_paths, load_runtime_env

    paths = resolve_runtime_paths({"LICAI_PROFILE": "test", "LICAI_DATA_DIR": str(tmp_path)})
    paths.prepare()
    (paths.config / ".env").write_text("LICAI_TEST_SENTINEL=secret\n")
    monkeypatch.delenv("LICAI_TEST_SENTINEL", raising=False)
    load_runtime_env(paths)
    import os
    assert "LICAI_TEST_SENTINEL" not in os.environ


def test_symlink_cannot_escape_runtime_directory(tmp_path):
    from services.runtime_paths import resolve_runtime_paths, RuntimePathError

    root, outside = tmp_path / "runtime", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "media").symlink_to(outside, target_is_directory=True)
    paths = resolve_runtime_paths({"LICAI_DATA_DIR": str(root)})
    with pytest.raises(RuntimePathError, match="目录"):
        paths.prepare()


def test_existing_unmarked_database_is_not_initialized(tmp_path):
    from services.runtime_paths import resolve_runtime_paths, RuntimePathError

    paths = resolve_runtime_paths({"LICAI_DATA_DIR": str(tmp_path)})
    with sqlite3.connect(paths.db) as db:
        db.execute("CREATE TABLE holdings (secret TEXT)")
        db.execute("INSERT INTO holdings VALUES ('private')")
    before = paths.db.read_bytes()
    with pytest.raises(RuntimePathError, match="旧|历史"):
        paths.check_database(paths.db)
    assert paths.db.read_bytes() == before


def test_unknown_profile_is_rejected():
    from services.runtime_paths import resolve_runtime_paths, RuntimePathError

    with pytest.raises(RuntimePathError, match="LICAI_PROFILE"):
        resolve_runtime_paths({"LICAI_PROFILE": "tset"})


def test_test_process_blocks_network_and_child_processes():
    import socket
    import subprocess

    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="测试.*网络"):
            connection.connect(("127.0.0.1", 9))
    with pytest.raises(PermissionError, match="测试.*子进程"):
        subprocess.run(["curl", "https://example.com"], check=True)


def test_test_process_blocks_database_outside_sandbox(tmp_path):
    sandbox = tmp_path.parent.parent
    outside = sandbox.parent / f"{sandbox.name}-forbidden.db"
    with pytest.raises(RuntimeError, match="测试.*数据库"):
        sqlite3.connect(outside)
    assert not outside.exists()


def test_test_profile_never_reads_keychain(monkeypatch):
    from services import llm_client
    # 新客户端已彻底移除认证回退，环境变量也不能恢复这些路径。
    monkeypatch.setenv('ANTHROPIC_API_KEY','synthetic-ignored')
    assert not hasattr(llm_client,'_resolve_auth')
    assert not hasattr(llm_client,'_call_via_cli')
    assert not llm_client.get_llm_config()['using_oauth_fallback']


def test_test_lifespan_initializes_only_temporary_database(tmp_path, monkeypatch):
    import run
    from config import config

    monkeypatch.setattr(config, "db_path", str(tmp_path / "portfolio.db"))

    async def exercise():
        current = asyncio.current_task()
        before = asyncio.all_tasks() - {current}
        async with run.lifespan(run.app):
            assert asyncio.all_tasks() - {current} == before
            assert (tmp_path / "portfolio.db").exists()

    asyncio.run(exercise())


def test_media_and_cache_writes_use_runtime_directory(tmp_path, monkeypatch):
    from services import chart_render, fund_nav_model, market_data
    from api.ask_routes import _persist_images

    monkeypatch.setenv("LICAI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fund_nav_model, "_models", {"synthetic": {"value": 1}})
    url = chart_render.save_png(b"synthetic-chart")
    assert (tmp_path / "media" / "1" / url.rsplit("/", 1)[-1]).read_bytes() == b"synthetic-chart"
    meta = _persist_images({"images": ["data:image/png;base64,dGVzdA=="]})
    assert (tmp_path / "media" / "1" / meta["images"][0].rsplit("/", 1)[-1]).read_bytes() == b"test"
    fund_nav_model._save()
    assert (tmp_path / "cache" / "fund_nav_model.json").exists()
    market_data._fx_remember("USD", 7.1)
    assert (tmp_path / "cache" / "fx_last.json").exists()


def test_production_env_file_cannot_redirect_runtime(tmp_path, monkeypatch):
    from services.runtime_paths import resolve_runtime_paths, load_runtime_env

    paths = resolve_runtime_paths({"LICAI_DATA_DIR": str(tmp_path)})
    paths.prepare()
    (paths.config / ".env").write_text(
        "LICAI_DATA_DIR=/should-not-change\nLICAI_PROFILE=production\n"
        "LICAI_TEST_SETTING=file-value\nLICAI_TEST_EXISTING=file-value\n", encoding="utf-8",
    )
    monkeypatch.setenv("LICAI_TEST_EXISTING", "env-value")
    monkeypatch.delenv("LICAI_TEST_SETTING", raising=False)
    import os
    root, profile = os.environ["LICAI_DATA_DIR"], os.environ["LICAI_PROFILE"]
    try:
        load_runtime_env(paths)
        assert os.environ["LICAI_DATA_DIR"] == root
        assert os.environ["LICAI_PROFILE"] == profile
        assert os.environ["LICAI_TEST_SETTING"] == "file-value"
        assert os.environ["LICAI_TEST_EXISTING"] == "env-value"
    finally:
        os.environ.pop("LICAI_TEST_SETTING", None)


def test_initialized_runtime_database_can_be_reopened(tmp_path, monkeypatch):
    from services.runtime_paths import resolve_runtime_paths
    from config import config
    from database import init_db

    monkeypatch.setattr(config, "db_path", str(tmp_path / "portfolio.db"))
    asyncio.run(init_db())
    paths = resolve_runtime_paths({"LICAI_DATA_DIR": str(tmp_path)})
    paths.check_database(paths.db)


def test_default_database_path_used_by_rule_and_tool_ledgers(tmp_path, monkeypatch):
    from config import config
    from database import init_db
    from services import rule_forge, tool_gaps

    monkeypatch.setenv("LICAI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "db_path", "portfolio.db")

    async def exercise():
        await init_db()
        from tests.account_helpers import seed_owner
        await seed_owner()
        await tool_gaps.record("fake_tool", {}, {"error": "synthetic failure"})
        assert (await tool_gaps.report())[0]["tool"] == "fake_tool"
        await rule_forge.add_candidate("synthetic title", "synthetic body", "synthetic evidence")
        rules = await rule_forge.list_rules()
        assert rules[0]["title"] == "synthetic title"
        await rule_forge.decide(rules[0]["id"], "active")
        assert rule_forge.active_rules_sync()[0]["body"] == "synthetic body"

    asyncio.run(exercise())


def test_import_backup_cannot_follow_symlink_outside_data_dir(tmp_path, monkeypatch):
    from datetime import datetime
    from api import export_routes
    from config import config
    from services.runtime_paths import RuntimePathError

    root = tmp_path / "runtime"
    backups = root / "backups"
    backups.mkdir(parents=True)
    source = root / "portfolio.db"
    source.write_bytes(b"synthetic database")
    outside = tmp_path / "keep.txt"
    outside.write_bytes(b"must survive")
    (backups / "pre-import-20261015-120000.db").symlink_to(outside)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 15, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(export_routes, "datetime", FixedDateTime)
    monkeypatch.setenv("LICAI_DATA_DIR", str(root))
    monkeypatch.setattr(config, "db_path", str(source))
    with pytest.raises(RuntimePathError, match="目录"):
        export_routes._backup_current_db()
    assert outside.read_bytes() == b"must survive"


def test_missing_eval_database_never_falls_back_to_private_ledger(monkeypatch):
    from evals import run as evaluation
    from evals.cases import CASES
    import sys

    monkeypatch.setattr(sys, "argv", ["evals/run.py", "--db", "missing.demo.db", "--case", CASES[0]["name"]])

    async def forbid_external_evaluation(*args, **kwargs):
        raise AssertionError("缺少评测库时不应执行评测")

    monkeypatch.setattr(evaluation, "run_one", forbid_external_evaluation)
    assert asyncio.run(evaluation.main()) == 2
