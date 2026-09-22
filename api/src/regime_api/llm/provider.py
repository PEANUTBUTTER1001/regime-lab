"""LLMProvider 인터페이스와 공급사 선택 (C1). 1차 구현은 OpenAI (E5)."""

from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """공급사 호출 실패 (네트워크·시간 초과·인증·응답 없음). 호출자는 템플릿으로 대체한다."""


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, system: str, user: str) -> str: ...


def get_provider(settings) -> tuple[LLMProvider | None, str | None]:
    """(공급사, None) 또는 (None, 사용 불가 사유 코드)."""
    if not settings.llm_model:
        return None, "llm_model_not_configured"
    if not settings.llm_api_key:
        return None, "llm_key_not_configured"
    if settings.llm_provider == "openai":
        from regime_api.llm.openai import OpenAIProvider

        return OpenAIProvider(settings.llm_api_key, settings.llm_model, settings.llm_timeout), None
    if settings.llm_provider in ("gemini", "anthropic"):
        return None, f"{settings.llm_provider}_not_implemented"
    return None, "llm_provider_unknown"
