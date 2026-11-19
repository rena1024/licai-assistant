"""新请求取得当前代际；维护期间拒绝业务请求，健康探针始终可读。"""
import sqlite3
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from services import runtime_state
from services import llm_activity


class RuntimeSafetyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        if request.url.path=='/api/health':return await call_next(request)
        try:
            with runtime_state.runtime_scope(), llm_activity.request_scope(request.headers.get('X-LLM-Activity', '')):
                return await call_next(request)
        except runtime_state.RuntimeUnavailable as exc:
            return JSONResponse({'detail':{'code':exc.code,'message':str(exc)}},status_code=503,headers={'Cache-Control':'no-store','Retry-After':'5'})
        except sqlite3.DatabaseError:
            if runtime_state.read_state().get('maintenance'):
                return JSONResponse({'detail':{'code':'maintenance','message':'系统维护中，请稍后重试'}},status_code=503)
            raise
