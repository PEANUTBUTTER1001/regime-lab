"""공통 오류 형식 {code, message, detail, retryable} (구현_계획 §6.4). 화면 상태와 1:1 대응한다."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail=None, retryable: bool = False):
        self.status, self.code, self.message, self.detail, self.retryable = status, code, message, detail, retryable


def body(code: str, message: str, detail=None, retryable: bool = False) -> dict:
    return {"code": code, "message": message, "detail": detail, "retryable": retryable}


def _loc(loc) -> str:
    parts = [str(p) for p in loc if p not in ("body",)]
    out = ""
    for p in parts:
        out += f"[{p}]" if p.isdigit() else (f".{p}" if out else p)
    return out or "body"


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse(body(e.code, e.message, e.detail, e.retryable), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        fields = {_loc(err["loc"]): err["msg"] for err in e.errors()}
        return JSONResponse(body("validation_failed", "Check the input values.", {"fields": fields}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return JSONResponse(body(code, str(e.detail)), status_code=e.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        return JSONResponse(body("server_error", "An unexpected server error occurred.", {"type": type(e).__name__}, True),
                            status_code=500)
