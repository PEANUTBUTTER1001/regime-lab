"""시각 규칙 (도메인, plan/03-04 §4). 입출력·시계 없음 — 현재 시각은 호출자가 넘긴다."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def parse_yyyymmdd(s: str) -> date:
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"YYYYMMDD 형식이 아님: {s!r}")
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def available_at(published: date, first_seen_at: datetime, backfilled: bool) -> datetime:
    """분석·학습에서 이 자료를 쓸 수 있는 시각.

    - 순방향 수집: 실제로 처음 본 시각(first_seen_at). 공개 시각보다 늦을 수는 있어도 이르지 않다.
    - 소급 수집(날짜만 있음): 접수일 다음 날 00:00 KST. 장 마감 뒤 공시를 당일 봉에 붙이는 누출을 막는다
      (봉 연결은 available_at 보다 늦게 시작하는 첫 봉 → 다음 거래일 첫 봉).
    """
    if first_seen_at.tzinfo is None:
        raise ValueError("first_seen_at 은 시간대가 있어야 함")
    if backfilled:
        return datetime.combine(published + timedelta(days=1), time(0, 0), tzinfo=KST)
    return first_seen_at.astimezone(KST)


def month_windows(start: date, end: date, months: int = 1) -> list[tuple[date, date]]:
    """[start, end] 를 달력 기준 months 개월 이하 창으로 나눈다 (양 끝 포함, 겹침·빈틈 없음)."""
    if start > end:
        raise ValueError("start > end")
    if months < 1:
        raise ValueError("months >= 1")
    out, cur = [], start
    while cur <= end:
        y, m = divmod(cur.month - 1 + months, 12)
        nxt = date(cur.year + y, m + 1, 1)
        out.append((cur, min(end, nxt - timedelta(days=1))))
        cur = nxt
    return out


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
