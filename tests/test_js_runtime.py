"""首次 JavaScript 上下文必须在并行行情调用前准备完毕。"""
import asyncio
from dataclasses import replace

import pytest


def test_startup_prepares_js_before_bootstrap_and_serving(tmp_path, monkeypatch):
    import run
    from services import accounts
    from services.runtime_paths import get_runtime_paths

    monkeypatch.setenv("LICAI_DATA_DIR", str(tmp_path))
    paths = replace(get_runtime_paths(), profile="production")
    monkeypatch.setattr(run, "get_runtime_paths", lambda: paths)
    events = []

    def prepare():
        events.append("js-ready")

    async def bootstrap():
        assert events == ["js-ready"]
        events.append("bootstrap-ready")
        return tmp_path / "synthetic-token"

    # 真实应用生命周期保留，只替换底层运行库与账号边界；不读取真实数据/凭据。
    monkeypatch.setattr(run, "initialize_js_runtime", prepare, raising=False)
    monkeypatch.setattr(accounts, "issue_bootstrap_token", bootstrap)

    async def exercise():
        async with run.lifespan(run.app):
            assert events == ["js-ready", "bootstrap-ready"]

    asyncio.run(exercise())


def test_js_initialization_is_once_and_failure_is_retryable(monkeypatch):
    from services import js_runtime
    import py_mini_racer

    monkeypatch.setattr(js_runtime, "_initialized", False)
    attempts = []

    class NativeRuntime:
        def __enter__(self):
            attempts.append("open")
            return self

        def eval(self, source):
            assert source == "0"
            if attempts.count("open") == 1:
                raise RuntimeError("synthetic engine failure")
            return 0

        def __exit__(self, *args):
            attempts.append("closed")

    monkeypatch.setattr(py_mini_racer, "MiniRacer", NativeRuntime)
    with pytest.raises(RuntimeError, match="synthetic"):
        js_runtime.initialize_js_runtime()
    js_runtime.initialize_js_runtime()
    js_runtime.initialize_js_runtime()
    assert attempts == ["open", "closed", "open", "closed"]
