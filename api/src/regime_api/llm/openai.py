"""OpenAI(ChatGPT) 공급사 — 1차 구현 (E5). 키는 OPENAI_API_KEY, 모델명은 REGIME_LLM_MODEL 설정값."""

from __future__ import annotations

from regime_api.llm.provider import LLMError


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 30.0, client=None):
        self.model = model
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
            raise LLMError(f"{type(e).__name__}: {e}") from e
        if not text or not text.strip():
            raise LLMError("empty response")
        return text.strip()
