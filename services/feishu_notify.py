"""旧调用签名兼容层；所有外发统一交给消息中心和当前活动渠道。"""
from services import private_connections


def configure(webhook_url):
    from services.notification_delivery import validate_config
    if webhook_url:
        validate_config('feishu',{'webhook_url':webhook_url})
    private_connections.save('feishu_webhook',webhook_url.strip())


def set_muted(muted):
    private_connections.save('feishu_muted',bool(muted))


def is_muted():
    return private_connections.get('feishu_muted',False)


def is_enabled():
    return bool(private_connections.get('feishu_webhook','') and not is_muted())


async def send_text(text):
    from services.notification_delivery import migrate_legacy
    from services.notifications import emit
    import uuid
    await migrate_legacy()
    return bool(await emit('legacy',uuid.uuid4().hex,title='理财提醒',body=text))


async def send_alert_card(stock_code,stock_name,alert_type,price,message,buy_zone='',sell_zone=''):
    from services.notifications import emit
    from services.notification_delivery import migrate_legacy
    from datetime import date
    await migrate_legacy()
    return bool(await emit('risk',f'{date.today()}:{stock_code}:{alert_type}',title='个人风险提醒',
        body=f'{stock_name}({stock_code}) {message}'))


async def send_test():
    from services.notification_delivery import migrate_legacy
    from services.notifications import emit
    import uuid
    await migrate_legacy()
    return bool(await emit('test',uuid.uuid4().hex,title='通知测试',body='本人主动发送的测试摘要'))
