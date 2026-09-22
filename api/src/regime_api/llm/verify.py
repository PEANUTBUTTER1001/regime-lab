"""LLM 출력 검증 (FR-L3, NFR-7): 입력에 없는 숫자와 권유 표현을 거부한다.

숫자 대조: 출력에서 모든 수치를 뽑아, 입력 근거(facts)의 수치와 표시 자릿수 반올림 오차 안에서 일치하는지 본다.
부호는 문장("하락", "음수")으로 바뀌어 쓰일 수 있으므로 절댓값 일치도 허용한다.
날짜 구성 숫자(연·월·일)와 1~10 사이의 정수(예: '3중 검증', '2개 셀')는 구조적 표현으로 허용한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUM_RE = re.compile(r"(?<![\w.])[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
FORBIDDEN = [
    r"매수\s*추천", r"매도\s*추천", r"종목\s*추천", r"추천\s*종목", r"매수하세요", r"매도하세요", r"사세요", r"파세요",
    r"목표\s*가", r"목표\s*주가", r"비중을?\s*(늘|줄|확대|축소)", r"투자\s*비중", r"유망", r"수익\s*보장", r"확실히\s*오",
    r"반드시\s*(오|상승)", r"투자하세요", r"매수\s*기회", r"매수\s*타이밍", r"\bbuy\b", r"\bsell\b", r"top\s*pick",
    r"strong\s*signal",
]
# 부정 고지 문맥은 허용 (예: "투자 권유가 아닙니다", "종목 추천이 아닙니다")
NEGATION = re.compile(r"(권유|추천)(가|이)?\s*(아닙니다|아니다|아님|하지\s*않습니다)")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class Verdict:
    ok: bool
    unknown_numbers: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)

    def warnings(self) -> list[str]:
        out = []
        if self.unknown_numbers:
            out.append(f"입력에 없는 수치: {', '.join(self.unknown_numbers[:10])}")
        if self.forbidden:
            out.append(f"권유·예측 표현: {', '.join(self.forbidden)}")
        return out


def _leaves(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _leaves(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _leaves(v)
    else:
        yield x


def allowed_numbers(facts: dict) -> tuple[list[float], set[str]]:
    nums, dates = [], set()
    for v in _leaves(facts):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            nums.append(float(v))
        elif isinstance(v, str):
            for d in DATE_RE.findall(v):
                dates.add(d)
            for n in NUM_RE.findall(DATE_RE.sub(" ", v)):
                nums.append(float(n.replace(",", "").replace("−", "-")))
    return nums, dates


def _decimals(tok: str) -> int:
    return len(tok.split(".")[1]) if "." in tok else 0


def verify(text: str, facts: dict) -> Verdict:
    nums, dates = allowed_numbers(facts)
    date_parts = set()
    for d in dates:
        y, mo, da = d.split("-")
        date_parts |= {float(y), float(mo), float(da)}
    body = text
    for d in DATE_RE.findall(body):  # 날짜는 통째로 대조
        if d not in dates:
            return Verdict(False, unknown_numbers=[d], forbidden=_forbidden(text))
    body = DATE_RE.sub(" ", body)
    unknown = []
    for tok in NUM_RE.findall(body):
        x = float(tok.replace(",", "").replace("−", "-"))
        tol = 0.5 * 10 ** (-_decimals(tok)) + 1e-9
        if float(x).is_integer() and 0 <= abs(x) <= 10:
            continue
        if abs(x) in date_parts:
            continue
        if any(abs(x - a) <= tol or abs(abs(x) - abs(a)) <= tol for a in nums):
            continue
        unknown.append(tok)
    forb = _forbidden(text)
    return Verdict(not unknown and not forb, unknown, forb)


def _forbidden(text: str) -> list[str]:
    t = NEGATION.sub(" ", text)
    return [p for p in FORBIDDEN if re.search(p, t, flags=re.IGNORECASE)]
