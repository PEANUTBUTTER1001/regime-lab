"""토큰화 (P3-11). 형태소 분석기 없이 한글은 문자 2-gram으로 잘라 조사·어미가 붙어도 겹치게 한다.

"한빛반도체가" → 한빛 빛반 반도 도체 체가 (조사 없는 "한빛반도체"와 4개 공유). 영문·숫자 단어는 그대로 둔다.
"""

from __future__ import annotations

import re

_NOT_WORD = re.compile(r"[^0-9a-z가-힣]+")


def tokenize(text: str | None) -> list[str]:
    out: list[str] = []
    for w in _NOT_WORD.sub(" ", (text or "").lower()).split():
        if len(w) < 2 or w.isascii():
            out.append(w)
        else:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


def doc_text(doc: dict, fields: list[str]) -> str:
    """색인할 필드만 이어 붙인다. 없는 필드·None 은 빈 글자."""
    return " ".join(str(doc.get(f) or "") for f in fields)
