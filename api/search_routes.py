"""搜索设置；检索只经既有问答工具，不建立新的通用搜索入口。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from services import search

router=APIRouter(prefix='/api/search',tags=['search'])


class Preference(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled: bool


@router.get('/preferences')
async def preferences():
    return await search.preference()


@router.put('/preferences')
async def save_preferences(data: Preference):
    return await search.preference(data.enabled)


@router.get('/channels')
async def channels():
    return {'channels':await search.channels()}


@router.put('/channels')
async def save_channel(request: Request):
    try:
        data=await request.json()
        version=data.pop('expected_version')
        if type(version) is not int or version<0:
            raise ValueError()
        return await search.save_channel(data,expected_version=version)
    except search.SearchConfigError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except (ValueError,KeyError,TypeError,AttributeError):
        raise HTTPException(422,'搜索配置无效，请核对免费条件、额度、有效期及版本') from None
