"""OpenAI(ChatGPT) 공급사 — 1차 구현 (E5). 키는 OPENAI_API_KEY, 모델명은 REGIME_LLM_MODEL 설정값."""

from __future__ import annotations

import re

from regime_api.llm.provider import LLMError

# 공급사 오류 문구에 섞여 오는 키 조각(예: "sk-ab*****wxyz")을 가린다. 키 원문·일부 모두 응답에 남기지 않는다.
_MASKED = re.compile(r"\S*\*{3,}\S*")


def redact(message: str, api_key: str | None) -> str:
    if api_key:
        message = message.replace(api_key, "[redacted]")
    return _MASKED.sub("[redacted]", message)


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 30.0, client=None):
        self.model = model
        self._key = api_key
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=1)
        self.client = client

    def generate(self, system: str, user: str) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            text = resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001 — SDK 예외 종류와 무관하게 템플릿으로 대체
            raise LLMError(redact(f"{type(e).__name__}: {e}", self._key)) from None
        if not text or not text.strip():
            raise LLMError("empty response")
        return text.strip()
