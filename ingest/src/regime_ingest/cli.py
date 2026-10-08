"""regime-ingest 명령 (진입). 입력 검증 → 조립 호출 → 출력만 한다 (R4)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"YYYY-MM-DD 형식이 아님: {s}") from None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="regime-ingest", description="외부 자료 수집 (OpenDART 공시 목록, metadata_only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="과거 소급 (1개월 창, 끊기면 커서부터 이어 받음)")
    b.add_argument("--source", choices=["opendart"], default="opendart")
    b.add_argument("--from", dest="start", type=_date, required=True)
    b.add_argument("--to", dest="end", type=_date, default=None, help="기본: KST 오늘")
    b.add_argument("--refetch", action="store_true", help="이미 collected 인 창도 다시 받음 (정정 재확인)")
    r = sub.add_parser("run", help="순방향 수집 (오늘 공시, OS 스케줄러로 주기 실행)")
    r.add_argument("--source", choices=["opendart", "naver_news"], default="opendart")
    r.add_argument("--news-config", type=Path, help="뉴스 설정 YAML (기본: 비활성 예시)")
    s = sub.add_parser("status", help="커서·수집 범위·최근 실행")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--source", choices=["opendart", "naver_news"], default="opendart")
    a = p.parse_args(argv)

    from regime_ingest import app

    if a.cmd == "backfill":
        if a.end is not None and a.start > a.end:
            p.error("--from 이 --to 보다 늦음")
        st = app.backfill(a.start, a.end, a.refetch)
        out = {**st.record(""), "skipped_windows": st.skipped_windows}
    elif a.cmd == "run":
        if a.source == "opendart" and a.news_config is not None:
            p.error("--news-config 는 naver_news 전용")
        out = app.news_forward(a.news_config) if a.source == "naver_news" else app.forward().record("")
    else:
        if a.limit < 1:
            p.error("--limit 은 1 이상")
        out = app.news_status(a.limit) if a.source == "naver_news" else app.status(a.limit)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()
    return 0 if out.get("status", "ok") in ("ok", "skipped", "disabled", "not_initialized") else 1


if __name__ == "__main__":
    raise SystemExit(main())
