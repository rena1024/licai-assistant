"""Feishu (飞书) webhook notification service."""
from __future__ import annotations
import asyncio
import json
import requests as _requests

from services import private_connections


def configure(webhook_url: str):
    private_connections.save('feishu_webhook', webhook_url.strip())


def set_muted(muted: bool):
    private_connections.save('feishu_muted', bool(muted))


def is_muted() -> bool:
    return private_connections.get('feishu_muted', False)


def is_enabled() -> bool:
    return bool(private_connections.get('feishu_webhook', '') and not is_muted())


def _send_sync(payload: dict) -> bool:
    if not is_enabled():
        return False
    from services.task_context import ensure_active_sync
    ensure_active_sync()
    try:
        resp = _requests.post(
            private_connections.get('feishu_webhook', ''),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        data = resp.json()
        if data.get("code") != 0 and data.get("StatusCode") != 0:
            print("[feishu] 推送服务拒绝请求")
            return False
        return True
    except Exception as e:
        print("[feishu] 推送连接失败")
        return False


async def send_text(text: str) -> bool:
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    return await asyncio.to_thread(_send_sync, payload)


async def send_alert_card(
    stock_code: str,
    stock_name: str,
    alert_type: str,
    price: float,
    message: str,
    buy_zone: str = "",
    sell_zone: str = "",
) -> bool:
    """Send a rich card message for T-trade alerts."""
    is_buy = "BUY" in alert_type
    color = "green" if is_buy else "red"
    title = f"{'买入' if is_buy else '卖出'}提醒 {stock_name}({stock_code})"

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**当前价:** {price:.2f}\n{message}",
            },
        },
    ]

    if buy_zone:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**买入区间:** {buy_zone}",
            },
        })

    if sell_zone:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**卖出区间:** {sell_zone}",
            },
        })

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }
    return await asyncio.to_thread(_send_sync, payload)


async def send_test() -> bool:
    """Send a test message to verify webhook is working."""
    return await send_text("理财助手已连接，飞书推送测试成功！")
