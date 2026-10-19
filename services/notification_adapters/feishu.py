"""飞书自定义机器人；由配置校验限定官方 HTTPS 路径。"""
from services.external_http import post_json


def send(config, summary):
    data=post_json(config['webhook_url'],payload={'msg_type':'text','content':{'text':summary}})
    codes=[data[key] for key in ('code','StatusCode') if key in data]
    return 'accepted' if codes and all(type(code) is int and code==0 for code in codes) else 'unknown'
