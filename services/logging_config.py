"""受限结构运行事件；不接收消息正文、异常对象、凭证或任意 metadata。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import math
import os
import re
from threading import RLock
import time

from services.runtime_paths import get_runtime_paths
from services.runtime_state import read_state, update_state


EVENTS = frozenset({
    "health_check", "startup", "shutdown", "backup_created", "backup_failed",
    "restore_completed", "restore_failed", "retention_completed", "retention_failed",
    "trace_enabled", "trace_disabled", "runtime_unavailable",
})
OUTCOMES = frozenset({"success", "failure", "denied"})
DEFAULT_LOG_RETENTION_DAYS = 7
TRACE_DURATION_SECONDS = 30 * 60
_LOG_NAME = re.compile(r"events-(\d{4}-\d{2}-\d{2})\.jsonl\Z")
_lock = RLock()


def _timestamp(now: int | None) -> int:
    value = int(time.time()) if now is None else now
    if type(value) is not int or value < 0:
        raise ValueError("运行日志时间必须是有效整数")
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ValueError("运行日志时间超出范围") from None
    return value


def _retention_days(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 3650:
        raise ValueError("运行日志保留天数必须是1到3650的整数")
    return value


def trace_status(*, now: int | None = None) -> dict:
    timestamp = _timestamp(now)
    until = read_state().get("trace_until", 0)
    if type(until) is not int or until < 0:
        raise ValueError("运行日志 Trace 状态无效")
    return {"enabled": until > timestamp, "trace_until": until}


def set_trace(enabled: bool, *, now: int | None = None) -> dict:
    if type(enabled) is not bool:
        raise ValueError("运行日志 Trace 开关必须为布尔值")
    timestamp = _timestamp(now)
    # 只在明确设置时改变到期时间；读取和进程重启均不续期。
    update_state(trace_until=timestamp + TRACE_DURATION_SECONDS if enabled else 0)
    return trace_status(now=timestamp)


def set_log_retention_days(days: int) -> None:
    update_state(log_retention_days=_retention_days(days))


def safe_metadata(*, now: int | None = None) -> dict:
    return {
        **trace_status(now=now),
        "log_retention_days": _retention_days(read_state().get("log_retention_days", DEFAULT_LOG_RETENTION_DAYS)),
    }


def _logs_directory():
    paths = get_runtime_paths()
    # 即使软链接目标仍在运行目录，也不能借 logs 别名清理媒体或配置。
    if paths.logs.is_symlink():
        raise ValueError("运行日志目录不能是软链接")
    folder = paths.validate(paths.logs)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def record_event(*, event: str, outcome: str, elapsed_ms: int | float,
                 actor_id: int | None = None, trace: bool = False,
                 now: int | None = None) -> bool:
    if type(event) is not str or event not in EVENTS or type(outcome) is not str or outcome not in OUTCOMES:
        raise ValueError("运行日志只接受已定义事件与结果代码")
    if type(elapsed_ms) not in (int, float) or not math.isfinite(elapsed_ms) or not 0 <= elapsed_ms <= 10**12:
        raise ValueError("运行日志耗时必须是有限非负数")
    if actor_id is not None and (type(actor_id) is not int or not 1 <= actor_id <= 2**63 - 1):
        raise ValueError("运行日志操作者必须是有效内部标识")
    if type(trace) is not bool:
        raise ValueError("运行日志 Trace 标识必须为布尔值")
    timestamp = _timestamp(now)
    if trace and not trace_status(now=timestamp)["enabled"]:
        return False
    record = {"created_at": timestamp, "event": event, "outcome": outcome,
              "elapsed_ms": elapsed_ms, "actor_id": actor_id, "trace": trace}
    payload = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
    with _lock:
        folder = _logs_directory()
        path = folder / f"events-{day}.jsonl"
        if path.is_symlink():
            raise ValueError("运行日志文件不能是软链接")
        # POSIX 下同时阻止检查后替换文件软链接；Windows 仍保留显式路径检查。
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "ab") as target:
            target.write(payload)
    return True


def cleanup_logs(*, now: int | None = None) -> int:
    timestamp = _timestamp(now)
    days = _retention_days(read_state().get("log_retention_days", DEFAULT_LOG_RETENTION_DAYS))
    # 按 UTC 文件日期保留今天及前 days-1 天；不依据可被复制改变的 mtime。
    cutoff = datetime.fromtimestamp(timestamp, timezone.utc).date() - timedelta(days=days - 1)
    removed = 0
    with _lock:
        folder = _logs_directory()
        for path in folder.iterdir():
            match = _LOG_NAME.fullmatch(path.name)
            if not match or path.is_symlink() or not path.is_file():
                continue
            try:
                day = date.fromisoformat(match[1])
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
                removed += 1
    return removed
