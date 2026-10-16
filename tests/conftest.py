"""在测试模块导入前隔离文件、凭证与外部副作用。"""
import atexit
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

import pytest


# 不复用调用者的目录、环境凭证或临时目录；旧测试的 mkstemp 也会落到此沙箱。
_sandbox = tempfile.TemporaryDirectory(prefix="licai-tests-")
atexit.register(_sandbox.cleanup)
_root = Path(_sandbox.name).resolve()
_patch = pytest.MonkeyPatch()
_patch.setenv("LICAI_PROFILE", "test")
_patch.setenv("LICAI_DATA_DIR", str(_root))
_patch.setenv("MPLCONFIGDIR", str(_root / "cache" / "matplotlib"))
_patch.setenv("XDG_CACHE_HOME", str(_root / "cache"))
_patch.setattr(tempfile, "tempdir", str(_root))
for _key in list(os.environ):
    if _key.startswith(("LLM_", "ANTHROPIC_", "OPENAI_", "GEMINI_", "DEEPSEEK_",
                        "QWEN_", "ZSXQ_", "OKX_", "FEISHU_", "CRYPTO_")) or _key == "MARKET_PROVIDER":
        _patch.delenv(_key, raising=False)


_connect = sqlite3.connect


def _sandbox_connect(database, *args, **kwargs):
    name = os.fsdecode(database)
    if name != ":memory:":
        if name.startswith("file:"):
            parsed = urlsplit(name)
            # 不允许通过 URI、共享内存或主机名绕过文件边界。
            if parsed.netloc:
                raise RuntimeError("测试禁止访问沙箱外数据库")
            name = unquote(parsed.path)
        if not name or not Path(name).resolve().is_relative_to(_root):
            raise RuntimeError("测试禁止访问沙箱外数据库")
    return _connect(database, *args, **kwargs)


_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex


def _offline_connect(connection, address):
    if connection.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("测试禁止真实网络连接，请使用伪造适配器")
    return _socket_connect(connection, address)


def _offline_connect_ex(connection, address):
    if connection.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("测试禁止真实网络连接，请使用伪造适配器")
    return _socket_connect_ex(connection, address)


def _no_child_process(*args, **kwargs):
    raise PermissionError("测试禁止真实子进程，请替换外部适配器")


_patch.setattr(sqlite3, "connect", _sandbox_connect)
_patch.setattr(socket.socket, "connect", _offline_connect)
_patch.setattr(socket.socket, "connect_ex", _offline_connect_ex)
_patch.setattr(subprocess, "Popen", _no_child_process)
_patch.setattr(os, "system", _no_child_process)


def pytest_configure(config):
    # 即使调用者传了 --basetemp，也不能因此清理或写入真实数据目录。
    config.option.basetemp = str(_root / "pytest")


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    from config import config

    monkeypatch.setattr(config, "db_path", str(tmp_path / "portfolio.db"))


@pytest.fixture
def authenticated_client():
    """旧业务路由测试也通过真实登录门禁，不在生产逻辑添加测试绕过。"""
    import asyncio
    from fastapi.testclient import TestClient
    from database import init_db
    from services import accounts
    from run import app

    password = "Synthetic-route-password-01!"

    async def setup():
        await init_db()
        path = await accounts.issue_bootstrap_token()
        await accounts.bootstrap_admin(token=path.read_text().strip(), username="route-admin", password=password)

    asyncio.run(setup())
    client = TestClient(app)
    result = client.post("/api/auth/login", json={"username": "route-admin", "password": password})
    assert result.status_code == 200
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    yield client
    client.close()


@pytest.fixture(autouse=True)
def synthetic_service_identity():
    """旧服务单元测试显式运行在合成身份中；生产路径没有此默认值。"""
    from services.user_context import user_scope
    with user_scope(1):
        yield
