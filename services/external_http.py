"""固定供应商端点的有界 JSON 请求；调用前由业务层核验权限和额度。"""
import json
import requests


class Rejected(RuntimeError):
    """供应商明确未接受请求；不能把超时/5xx归为此类。"""


def post_json(url, *, payload, headers=None):
    with requests.post(url, json=payload, headers=headers, timeout=(5, 15),
                       allow_redirects=False, stream=True) as response:
        if response.status_code in (400, 401, 403, 422, 429):
            raise Rejected('服务明确拒绝请求')
        response.raise_for_status()
        if response.status_code != 200:
            raise RuntimeError('服务返回未知结果')
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 2_000_000:
                raise RuntimeError('服务响应超过限制')
            chunks.append(chunk)
        result = json.loads(b''.join(chunks))
        if not isinstance(result, dict):
            raise ValueError('服务响应格式无效')
        return result
