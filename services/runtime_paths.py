"""统一持久目录；路径解析本身不建目录、不读取凭证或修改历史数据。"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_VERSION = "1"


class RuntimePathError(RuntimeError):
    """运行目录配置不安全，必须在写入前停止。"""


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    project_root: Path
    profile: str
    explicit_root: bool

    @property
    def db(self) -> Path:
        return self.root / "portfolio.db"

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def validate(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if not resolved.is_relative_to(self.root):
            label = "测试" if self.profile == "test" else "运行"
            raise RuntimePathError(f"{label}文件必须位于 LICAI_DATA_DIR 目录内")
        return resolved

    def prepare(self) -> None:
        folders = (self.root, self.media, self.cache, self.backups, self.config, self.logs)
        # 先验证所有路径，避免发现后面的软链接越界前已经创建一半目录。
        for folder in folders:
            self.validate(folder)
        self.validate(self.db)
        for folder in folders:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)

    def file(self, area: str, name: str) -> Path:
        if area not in {"media", "cache", "backups", "config", "logs"}:
            raise RuntimePathError("未知运行目录")
        path = self.validate(getattr(self, area) / name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def check_legacy_database(self) -> None:
        old = self.project_root / "portfolio.db"
        if not self.explicit_root and old.is_file() and old.stat().st_size:
            raise RuntimePathError(
                "发现项目根目录的旧数据库；不会自动迁移或清空。"
                "请保留旧库，并通过 LICAI_DATA_DIR 指定新的空目录。"
            )

    def check_database(self, path: Path) -> None:
        path = self.validate(path)
        if not path.exists() or not path.stat().st_size:
            return
        try:
            # 使用只读连接，拒绝初始化时不能改变原库的任何字节。
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute(
                    "SELECT value FROM runtime_metadata WHERE key = 'layout_version'"
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise RuntimePathError("发现旧数据库或无效数据库；请指定新的空运行目录") from exc
        if row != (LAYOUT_VERSION,):
            raise RuntimePathError("数据库运行目录版本不兼容，停止初始化")


def resolve_runtime_paths(
    environ: Mapping[str, str] | None = None, *, project_root: Path | None = None,
) -> RuntimePaths:
    env = os.environ if environ is None else environ
    project = (project_root or PROJECT_ROOT).resolve()
    profile = env.get("LICAI_PROFILE", "production").strip()
    if profile not in {"production", "test"}:
        raise RuntimePathError("LICAI_PROFILE 仅支持 production 或 test")
    raw = env.get("LICAI_DATA_DIR", "").strip()
    if profile == "test" and not raw:
        raise RuntimePathError("测试模式必须显式指定 LICAI_DATA_DIR")
    root = Path(raw).expanduser() if raw else project / "runtime-data"
    if not root.is_absolute():
        root = project / root
    return RuntimePaths(root.resolve(), project, profile, bool(raw))


def get_runtime_paths() -> RuntimePaths:
    return resolve_runtime_paths()


def is_test_profile() -> bool:
    return get_runtime_paths().profile == "test"


def database_path(configured: str) -> Path:
    paths = get_runtime_paths()
    candidate = paths.db if configured == "portfolio.db" else Path(configured)
    candidate = paths.validate(candidate)
    return candidate


def load_runtime_env(paths: RuntimePaths) -> None:
    if paths.profile == "test":
        return
    env_file = paths.validate(paths.config / ".env")
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        # 目录与测试保护必须先于 .env 固定，不能被文件反向修改。
        if key and key not in {"LICAI_PROFILE", "LICAI_DATA_DIR"}:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))
