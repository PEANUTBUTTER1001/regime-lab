"""보고서 생성 흐름 (FR-L1~L4, NFR-7).

근거 수치 → (캐시) → 공급사 호출 → 숫자·권유 표현 검증 → 통과 시 verified, 아니면 템플릿(fallback).
검증에 실패한 LLM 문장은 사용자에게 보내지 않는다.
"""

from __future__ import annotations

from regime_api.llm.cache import ReportCache, cache_key
from regime_api.llm.prompt import PROMPT_VERSION, SYSTEM, build_facts, build_user
from regime_api.llm.provider import LLMError, get_provider
from regime_api.llm.template import render
from regime_api.llm.verify import verify
from regime_lab.runs import DISCLAIMER

REASONS = {
    "llm_model_not_configured": "LLM 모델명이 설정되지 않아 템플릿 보고서를 표시합니다 (REGIME_LLM_MODEL).",
    "llm_key_not_configured": "LLM API 키가 없어 템플릿 보고서를 표시합니다 (OPENAI_API_KEY).",
    "gemini_not_implemented": "Gemini 어댑터가 아직 구현되지 않아 템플릿 보고서를 표시합니다.",
    "anthropic_not_implemented": "Anthropic 어댑터가 아직 구현되지 않아 템플릿 보고서를 표시합니다.",
    "llm_provider_unknown": "알 수 없는 LLM 공급사라 템플릿 보고서를 표시합니다.",
    "llm_error": "LLM 호출에 실패해 템플릿 보고서를 표시합니다.",
    "verification_failed": "AI 문장이 숫자·표현 검증을 통과하지 못해 템플릿 보고서로 대체했습니다.",
}


class ReportService:
    def __init__(self, settings, cfg: dict, paths, provider_factory=get_provider):
        self.settings, self.cfg = settings, cfg
        self.cache = ReportCache(paths.cache / "llm_reports")
        self._provider_factory = provider_factory

    def generate(self, run_id: str, result: dict, strategy: str) -> dict:
        facts = build_facts(result, strategy)
        base = {"run_id": run_id, "strategy": strategy, "prompt_version": PROMPT_VERSION, "facts": facts,
                "disclaimer": DISCLAIMER}
        provider, reason = self._provider_factory(self.settings)
        if provider is None:
            return self._fallback(base, reason)
        key = cache_key(facts, PROMPT_VERSION, provider.name, provider.model)
        hit = self.cache.get(key)
        if hit:
            return {**base, **hit, "cached": True}
        try:
            text = provider.generate(SYSTEM, build_user(facts))
        except LLMError as e:
            return self._fallback(base, "llm_error", provider, [str(e)[:200]])
        v = verify(text, facts)
        if not v.ok:
            return self._fallback(base, "verification_failed", provider, v.warnings())
        out = {"status": "verified", "text": text, "warnings": [], "reason": None,
               "provider": provider.name, "model": provider.model}
        self.cache.put(key, out)
        return {**base, **out, "cached": False}

    def _fallback(self, base: dict, reason: str, provider=None, warnings: list[str] | None = None) -> dict:
        return {**base, "status": "fallback", "text": render(base["facts"]), "reason": reason,
                "reason_message": REASONS.get(reason, reason), "warnings": warnings or [],
                "provider": getattr(provider, "name", self.settings.llm_provider),
                "model": getattr(provider, "model", self.settings.llm_model), "cached": False}
