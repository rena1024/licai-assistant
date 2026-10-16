"""WebSocket endpoint for real-time price push and alerts."""
from __future__ import annotations
from services.user_context import require_user_id
from dataclasses import dataclass
import asyncio
import json
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from database import get_all_holdings
from services.market_data import (get_realtime_quotes, is_trading_day_active, markets_open,
                                  split_stock_code)
from services import feishu_notify, auth, accounts
from config import config
from services.runtime_paths import database_path, get_runtime_paths

router = APIRouter()

@dataclass(frozen=True)
class Connection:
    user_id: int
    token: str


_clients: dict[WebSocket, Connection] = {}


async def disconnect_user(user_id: int):
    for socket, connection in list(_clients.items()):
        if connection.user_id == user_id:
            _clients.pop(socket, None)
            try:
                await socket.close(code=4401)
            except Exception:
                pass


async def broadcast(message: dict, *, user_id: int):
    from services.user_context import validate_user_id
    validate_user_id(user_id)
    data = json.dumps(message, ensure_ascii=False, default=str)
    for socket, connection in list(_clients.items()):
        if connection.user_id != user_id:
            continue
        try:
            await auth.authenticate(connection.token)
        except accounts.AccountError:
            _clients.pop(socket, None)
            await socket.close(code=4401)
            continue
        try:
            await socket.send_text(data)
        except Exception:
            _clients.pop(socket, None)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    expected_origin = f"{'https' if ws.url.scheme == 'wss' else 'http'}://{ws.url.netloc}"
    if ws.headers.get("origin") != expected_origin:
        await ws.close(code=4403)
        return
    try:
        session = await auth.authenticate(ws.cookies.get(auth.COOKIE_NAME, ""))
        if session.account.must_change_password:
            await ws.close(code=4403)
            return
    except accounts.AccountError:
        await ws.close(code=4401)
        return
    await ws.accept()
    token = ws.cookies.get(auth.COOKIE_NAME, "")
    _clients[ws] = Connection(session.context.user_id, token)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30)
                try:
                    await auth.authenticate(token)
                except accounts.AccountError:
                    await ws.close(code=4401)
                    break
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                try:
                    await auth.authenticate(token)
                except accounts.AccountError:
                    await ws.close(code=4401)
                    break
                try:
                    await ws.send_text(json.dumps({"type": "heartbeat"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        _clients.pop(ws, None)


def _push_interval(opened: dict, holdings: list[dict]) -> int:
    """持仓所在的任一市场正在交易 → 快刷新; 否则慢刷新。A 股不论持仓与否都按 A 股时段快刷(沿用原口径)。"""
    held = {split_stock_code(h.get("stock_code") or "")[0] for h in holdings or []}
    if opened.get("A") or any(opened.get(m) for m in held & {"HK", "US"}):
        return config.refresh_interval
    return config.idle_interval


async def push_prices_once():
    """公共报价按代码去重抓取，再按每个账号的持仓代码裁剪推送。"""
    holdings = {}
    for uid in {c.user_id for c in list(_clients.values())}:
        holdings[uid] = await get_all_holdings(user_id=uid)
    codes = sorted({h['stock_code'] for rows in holdings.values() for h in rows})
    if not codes:
        return
    quotes = await get_realtime_quotes(codes)
    opened = markets_open()
    for uid, rows in holdings.items():
        own_codes = {h['stock_code'] for h in rows}
        own = {code: quote for code, quote in quotes.items() if code in own_codes}
        await broadcast({'type': 'price_update', 'data': own,
                         'market_open': is_trading_day_active(), 'markets_open': opened}, user_id=uid)


async def price_monitor_loop():
    while True:
        try:
            await push_prices_once()
        except Exception:
            print('[monitor] 行情推送失败')
        await asyncio.sleep(config.refresh_interval if any(markets_open().values()) else config.idle_interval)


# --- Daily database backup ---
_backup_done_date: str = ""

async def backup_loop():
    """Backup portfolio.db daily at 20:00 CST."""
    global _backup_done_date
    import shutil
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    paths = get_runtime_paths()
    paths.prepare()
    backup_dir = paths.backups

    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            cst_now = utc_now + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            hour = cst_now.hour

            if hour == 20 and today != _backup_done_date:
                src = database_path(config.db_path)
                if src.exists():
                    dst = get_runtime_paths().file("backups", f"portfolio_{today}.db")
                    import sqlite3
                    with sqlite3.connect(src) as reader, sqlite3.connect(dst) as writer:
                        reader.backup(writer)
                    dst.chmod(0o600)
                    _backup_done_date = today
                    print(f"[backup] Database backed up to {dst}")

                    # Keep only last 30 backups
                    backups = sorted(backup_dir.glob("portfolio_*.db"))
                    for old in backups[:-30]:
                        old.unlink()

            await asyncio.sleep(300)  # check every 5 minutes
        except Exception as e:
            print(f"[backup] Error: {e}")
            await asyncio.sleep(300)
