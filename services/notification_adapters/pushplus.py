"""PushPlus 固定 HTTPS 地址；消息默认仅含固定摘要。"""
from services.external_http import post_json


def send(config, summary):
    data=post_json('https://www.pushplus.plus/send',payload={
        'token':config['token'],'title':'理财助手通知','content':summary,'template':'txt'})
    code=data.get('code')
    # 只有供应商明确确认接受才成功；缺字段/未知业务码不能推断为未投递。
    return 'accepted' if type(code) is int and code==200 else 'unknown'
