"""Anthropic 공급사 자리 (C1). E5에 따라 1차 구현 대상이 아니다."""

from __future__ import annotations


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *_, **__):
        raise NotImplementedError("Anthropic 어댑터는 아직 구현되지 않았습니다 (E5: 1차 공급사는 OpenAI).")
