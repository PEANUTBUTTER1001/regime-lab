"""검색 조건·토큰화 (P3-11). 완전 조건 판정과 BM25 점수용 2-gram을 구분한다.

한글 연속 구간은 2-gram, ASCII 영숫자 연속 구간은 식별자 전체를 유지한다.
조사는 받침이 맞는 결합만 1회 분리하며, 기본어가 문서의 독립 단어로 확인되어야 한다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_NOT_WORD = re.compile(r"[^0-9a-z가-힣]+")
_SEGMENTS = re.compile(r"[0-9a-z]+|[가-힣]+")
_WORD_CHARS = r"0-9a-z가-힣"
# 수치가 아닌 한국어 결합 규칙: 받침 있음/없음에 따라 허용하는 조사.
_PARTICLE_FINAL = {"이": True, "가": False, "은": True, "는": False,
                   "을": True, "를": False, "과": True, "와": False}


def _normalize(text: str | None) -> str:
    return _NOT_WORD.sub(" ", unicodedata.normalize("NFKC", text or "").lower())


def tokenize(text: str | None) -> list[str]:
    out: list[str] = []
    for w in _SEGMENTS.findall(_normalize(text)):
        if len(w) < 2 or w.isascii():
            out.append(w)
        else:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


def _particles(base: str, allowed: list[str]) -> list[str]:
    """한글로 끝나는 기본어에 붙일 수 있는 조사. 영숫자 식별자 자체는 자르지 않는다."""
    if not base or not "가" <= base[-1] <= "힣":
        return []
    has_final = (ord(base[-1]) - ord("가")) % 28 != 0
    return [p for p in allowed if _PARTICLE_FINAL.get(p) is has_final]


def _body(word: str) -> str:
    # 한글·한글/ASCII 사이 공백은 허용. 별개의 ASCII 식별자(HBM 3E)는 연결하지 않는다.
    return "".join((r"\s*" if i and not (word[i - 1].isascii() and c.isascii()) else "")
                   + re.escape(c) for i, c in enumerate(word))


def query_conditions(q: str, particles: list[str], min_base_chars: int) -> list[re.Pattern[str]]:
    """각 질의 단위의 원형 OR 허용 조사 변형. 단위 사이에는 matches_conditions가 AND 적용.

    원형은 완전 문자열을 찾는다(복합어 안의 '자기주식'도 허용). 조사 분리 대안은 단어 경계를 요구해
    '삼성전자가'의 기본어를 '삼성전자서비스' 일부에서 찾지 않는다. 재귀 분리·별칭·사전 조회 없음.
    """
    out = []
    for word in _normalize(q).split():
        left = r"(?<![0-9a-z])" if word[0].isascii() else ""
        right = r"(?![0-9a-z])" if word[-1].isascii() else ""
        forms = [left + _body(word) + right]
        for particle in particles:
            if particle not in _PARTICLE_FINAL or not word.endswith(particle):
                continue
            base = word[:-len(particle)]
            endings = _particles(base, particles)
            if len(base) >= min_base_chars and particle in endings:
                suffix = "|".join(map(re.escape, endings))
                forms.append(rf"(?<![{_WORD_CHARS}]){_body(base)}(?:{suffix})?(?![{_WORD_CHARS}])")
        out.append(re.compile("(?:" + "|".join(forms) + ")"))
    return out


def matches_conditions(texts: Iterable[str | None], conditions: list[re.Pattern[str]]) -> bool:
    """같은 문서의 허용 필드에서 모든 조건 확인. 한 조건을 필드 사이에서 조립하지 않는다."""
    if not conditions:
        return False
    fields = tuple(_normalize(t) for t in texts)
    return all(any(c.search(t) for t in fields) for c in conditions)


def doc_text(doc: dict, fields: list[str]) -> str:
    """색인할 필드만 이어 붙인다. 없는 필드·None 은 빈 글자."""
    return " ".join(str(doc.get(f) or "") for f in fields)


def name_mention_pattern(name: str, particles: list[str]) -> re.Pattern[str] | None:
    """현재 이름 전체의 텍스트 언급. 별칭/관계 추론 없이 회사명 경계와 조사만 허용.

    삼성전자서비스/삼성전자우는 삼성전자의 언급으로 판정하지 않는다.
    기존 질의 조건/토큰화는 변경하지 않는다.
    """
    name = _normalize(name).strip()
    if not name:
        return None
    body = r"\s*".join(_body(word) for word in name.split())
    # 고정 한국어 문법 어미: 검색 수치나 별칭 목록이 아니다.
    endings = _particles(name, particles) + ["의", "도", "만", "에서", "에게", "으로", "로", "까지", "부터"]
    suffix = "|".join(re.escape(x) for x in sorted(set(endings), key=len, reverse=True))
    return re.compile(rf"(?<![{_WORD_CHARS}]){body}(?:{suffix})?(?![{_WORD_CHARS}])")


def name_mentioned(text: str | None, pattern: re.Pattern[str]) -> bool:
    return bool(pattern.search(_normalize(text)))
