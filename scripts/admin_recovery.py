"""只在部署机器执行的初始化凭据管理与管理员密码恢复。"""
from __future__ import annotations

import argparse
import asyncio
import getpass
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from database import init_db, get_db, ACCOUNT_SCHEMA_VERSION
from services import accounts
from services.runtime_paths import get_runtime_paths, database_path, RuntimePathError


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse 默认回显未知参数，可能把误传的明文密码写进终端日志。
        self.print_usage(sys.stderr)
        self.exit(2, "参数无效；密码只能通过交互式隐藏输入，不能作为命令行参数。\n")


async def _prepare(*, initialize: bool):
    paths = get_runtime_paths()
    paths.check_legacy_database()
    if initialize:
        paths.prepare()
        await init_db()
        return
    path = database_path(config.db_path)
    if not path.is_file():
        raise accounts.AccountError("account_missing")
    paths.check_database(path)
    db = await get_db()
    try:
        version = await (await db.execute("SELECT value FROM runtime_metadata WHERE key='account_schema_version'")).fetchone()
        if not version or version[0] != ACCOUNT_SCHEMA_VERSION:
            raise RuntimePathError("账号数据库版本不兼容，停止恢复")
    finally:
        await db.close()


async def _issue(renew: bool):
    await _prepare(initialize=True)
    return await accounts.issue_bootstrap_token(renew=renew)


async def _recover(password: str):
    await _prepare(initialize=False)
    await accounts.recover_admin_password(password=password)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="本机初始化凭据管理与唯一管理员恢复；不删除业务数据。")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    issue = commands.add_parser("bootstrap-token", help="生成初始化凭据；只打印文件位置")
    issue.add_argument("--renew", action="store_true", help="轮换凭据，旧凭据立即失效")
    commands.add_parser("recover", help="交互式恢复管理员临时密码并撤销旧会话")
    args = parser.parse_args(argv)
    try:
        if args.command == "bootstrap-token":
            path = asyncio.run(_issue(args.renew))
            print(f"初始化凭据保存在本机文件：{path}" if path else "初始化已完成，不再生成凭据。")
            return 0
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            print("密码恢复需要交互式终端，不接受管道或重定向输入。", file=sys.stderr)
            return 2
        password = getpass.getpass("新的管理员临时密码（12～128 个字符，输入不回显）：")
        confirmation = getpass.getpass("再次输入临时密码：")
        if password != confirmation:
            print("两次输入不一致，未修改密码。", file=sys.stderr)
            return 2
        asyncio.run(_recover(password))
        print("管理员临时密码已更新，所有旧会话已撤销；重新登录后须改密。")
        return 0
    except (accounts.AccountError, RuntimePathError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, sqlite3.DatabaseError):
        print("本机存储操作失败，请检查运行目录与数据库状态。", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("已取消，未继续执行恢复。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
