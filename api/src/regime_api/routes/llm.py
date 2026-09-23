"""AI 보고서 공급사 설정 (설정 화면, 결정 E10 잠정).

설정 화면에서 받은 모델명·키는 서버 메모리(Settings)에만 둔다. 파일·로그·응답에 키 원문을 쓰지 않으며,
서버를 다시 켜면 사라지고 시작 시 환경변수 값으로 돌아간다. 보고서 캐시(cache/llm_reports)는 그대로 유지한다.
적용·지우기는 이 PC(루프백)에서 온 요청만 받는다.
"""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from regime_api.errors import ApiError

router = APIRouter(tags=["llm"])

MAX_MODEL, MAX_KEY = 100, 300


class LLMConfigRequest(BaseModel):
    model: str = Field(max_length=MAX_MODEL)
    api_key: str | None = Field(default=None, max_length=MAX_KEY)

    @field_validator("model", "api_key")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("must not contain control characters")
        return v

    @field_validator("model")
    @classmethod
    def _model_required(cls, v: str) -> str:
        if not v:
            raise ValueError("model is required")
        return v


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _require_local(request: Request) -> None:
    if not _is_local(request):
        raise ApiError(403, "local_only", "LLM settings can only be changed from this computer.")


def config_view(s) -> dict:
    """현재 설정. 키 원문은 넣지 않고 마지막 4자리만 보여 준다."""
    st = s.settings
    key = st.llm_api_key
    if s.llm_source == "ui":
        source = "ui"
    elif st.llm_model or key:
        source = "env"
    else:
        source = "none"
    return {
        "provider": st.llm_provider,
        "model": st.llm_model,
        "key_set": bool(key),
        "key_hint": f"…{key[-4:]}" if key and len(key) >= 8 else ("…" if key else None),
        "source": source,
    }


@router.get("/llm/config")
def get_config(request: Request):
    return config_view(request.app.state.rl)


@router.put("/llm/config")
def put_config(body: LLMConfigRequest, request: Request):
    _require_local(request)
    s = request.app.state.rl
    key = body.api_key or s.settings.llm_api_key
    if not key:
        raise ApiError(422, "validation_failed", "Check the input values.", {"fields": {"api_key": "API key is required"}})
    s.settings.llm_model, s.settings.llm_api_key = body.model, key
    s.llm_source = "ui"
    return config_view(s)


@router.delete("/llm/config")
def delete_config(request: Request):
    _require_local(request)
    s = request.app.state.rl
    s.settings.llm_model, s.settings.llm_api_key = s.llm_env
    s.llm_source = "env"
    return config_view(s)
