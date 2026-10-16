"""固定时钟验证结构日志与持久 Trace，不捕获任意业务正文。"""
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def logging_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("LICAI_DATA_DIR", str(tmp_path))
    assert (Path(__file__).resolve().parents[1] / "services/logging_config.py").is_file(), "缺少结构化运行日志模块"
    from services import logging_config
    return logging_config, tmp_path


def test_trace_expiry_and_redaction(logging_runtime):
    logs, root = logging_runtime
    now = 1_800_000_000
    assert logs.trace_status(now=now) == {"enabled": False, "trace_until": 0}
    logs.set_trace(True, now=now)
    assert logs.trace_status(now=now + 1799)["enabled"] is True
    reloaded = importlib.reload(logs)
    assert reloaded.trace_status(now=now + 1800) == {"enabled": False, "trace_until": now + 1800}
    assert not reloaded.record_event(event="health_check", outcome="success", elapsed_ms=2, trace=True, now=now + 1800)
    assert reloaded.record_event(event="health_check", outcome="success", elapsed_ms=2, actor_id=7, now=now)
    secret = RuntimeError("synthetic-secret private正文")
    for field in ("event", "outcome", "elapsed_ms", "actor_id"):
        fields = dict(event="health_check", outcome="failure", elapsed_ms=1, actor_id=7, now=now)
        fields[field] = secret
        with pytest.raises(ValueError, match="运行日志"):
            logs.record_event(**fields)
    with pytest.raises(TypeError):
        logs.record_event(event="health_check", outcome="failure", elapsed_ms=1, error=secret, now=now)
    with pytest.raises(ValueError):
        logs.record_event(event="synthetic-secret private正文", outcome="failure", elapsed_ms=1, now=now)
    body = "".join(p.read_text() for p in (root / "logs").iterdir())
    assert "synthetic-secret" not in body and "private正文" not in body
    record = json.loads(body)
    assert record == {"created_at": now, "event": "health_check", "outcome": "success", "elapsed_ms": 2, "actor_id": 7, "trace": False}


def test_trace_settings_preserve_maintenance_and_do_not_extend_on_read(logging_runtime):
    logs, _ = logging_runtime
    from services import runtime_state
    runtime_state.update_state(maintenance=True, generation=8, cloud_hold=True)
    now = 1_800_000_000
    logs.set_trace(True, now=now)
    assert logs.trace_status(now=now + 5)["trace_until"] == now + 1800
    logs.set_log_retention_days(3)
    state = runtime_state.read_state()
    assert state["maintenance"] and state["cloud_hold"] and state["generation"] == 8
    assert logs.safe_metadata(now=now)["log_retention_days"] == 3
    logs.set_trace(False, now=now + 6)
    assert not logs.trace_status(now=now + 6)["enabled"]
    for invalid in (0, -1, True, "7"):
        with pytest.raises(ValueError):
            logs.set_log_retention_days(invalid)


def test_cleanup_only_known_logs_and_never_follows_symlinks(logging_runtime):
    logs, root = logging_runtime
    from datetime import datetime, timezone
    now = int(datetime(2026, 10, 16, 12, tzinfo=timezone.utc).timestamp())
    folder = root / "logs"
    folder.mkdir()
    old = folder / "events-2026-10-09.jsonl"
    old.write_text("old")
    kept = folder / "events-2026-10-10.jsonl"
    kept.write_text("recent")
    foreign = folder / "unknown-2020-01-01.jsonl"
    foreign.write_text("foreign")
    outside = root / "media"
    outside.mkdir()
    victim = outside / "events-2020-01-01.jsonl"
    victim.write_text("protected")
    (folder / "events-2020-01-01.jsonl").symlink_to(victim)
    (folder / "nested").symlink_to(outside, target_is_directory=True)
    assert logs.cleanup_logs(now=now) == 1
    assert not old.exists() and kept.exists() and foreign.exists()
    assert victim.read_text() == "protected"
    with pytest.raises(ValueError):
        logs.record_event(event="health_check", outcome="success", elapsed_ms=float("nan"), now=now)


def test_log_destination_symlinks_fail_closed(logging_runtime):
    logs, root = logging_runtime
    from datetime import datetime, timezone
    now = int(datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp())
    folder = root / "logs"
    folder.mkdir()
    outside = root.parent / (root.name + "-outside.log")
    outside.write_text("protected")
    (folder / "events-2026-10-16.jsonl").symlink_to(outside)
    with pytest.raises((OSError, ValueError)):
        logs.record_event(event="health_check", outcome="success", elapsed_ms=1, now=now)
    assert outside.read_text() == "protected"
    (folder / "events-2026-10-16.jsonl").unlink()
    folder.rmdir()
    folder.symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        logs.cleanup_logs(now=now)
