"""도메인 (입출력 없음): 시각 규칙·창 나누기·정규화·중복/버전·정정 연결."""

from datetime import date, datetime

import pytest

from regime_ingest.dedup import Seen, classify, link_amendments
from regime_ingest.normalize import normalize, split_report_name
from regime_ingest.timing import KST, available_at, month_windows

SEEN_AT = datetime(2026, 10, 7, 14, 5, tzinfo=KST)
ITEM = {"corp_code": "00126380", "corp_name": "합성전자", "stock_code": "005930", "corp_cls": "Y",
        "report_nm": "[기재정정]사업보고서 (2023.12)", "rcept_no": "20240312000736", "flr_nm": "합성전자",
        "rcept_dt": "20240312", "rm": "연"}


def _norm(item=ITEM, backfilled=True):
    return normalize(item, first_seen_at=SEEN_AT, backfilled=backfilled, ingest_run_id="r1",
                     viewer_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=", license_scope="metadata_only")


def test_available_at_backfill_is_next_day_midnight():
    """날짜만 있는 소급분은 접수일 다음 날 00:00 KST — 장 마감 뒤 공시가 당일 봉에 붙지 않는다."""
    got = available_at(date(2024, 3, 12), SEEN_AT, backfilled=True)
    assert got == datetime(2024, 3, 13, 0, 0, tzinfo=KST)
    assert available_at(date(2024, 12, 31), SEEN_AT, True) == datetime(2025, 1, 1, tzinfo=KST)  # 연도 넘김


def test_available_at_forward_is_first_seen():
    assert available_at(date(2026, 10, 7), SEEN_AT, backfilled=False) == SEEN_AT
    with pytest.raises(ValueError):
        available_at(date(2026, 10, 7), datetime(2026, 10, 7, 14, 5), False)  # 시간대 없는 시각 거부


def test_month_windows_cover_range_without_gaps():
    w = month_windows(date(2021, 1, 15), date(2021, 4, 3))
    assert w == [(date(2021, 1, 15), date(2021, 1, 31)), (date(2021, 2, 1), date(2021, 2, 28)),
                 (date(2021, 3, 1), date(2021, 3, 31)), (date(2021, 4, 1), date(2021, 4, 3))]
    assert month_windows(date(2024, 2, 1), date(2024, 2, 29)) == [(date(2024, 2, 1), date(2024, 2, 29))]  # 윤년
    w3 = month_windows(date(2021, 11, 1), date(2022, 6, 30), months=3)
    assert w3[0] == (date(2021, 11, 1), date(2022, 1, 31)) and w3[-1][1] == date(2022, 6, 30)
    with pytest.raises(ValueError):
        month_windows(date(2021, 2, 1), date(2021, 1, 1))


def test_split_report_name():
    assert split_report_name("[기재정정]사업보고서 (2023.12)") == (["기재정정"], "사업보고서 (2023.12)")
    assert split_report_name(" [첨부추가] [기재정정] 증권신고서(지분증권)") == (["첨부추가", "기재정정"], "증권신고서(지분증권)")
    assert split_report_name("분기보고서 (2024.03)") == ([], "분기보고서 (2024.03)")


def test_normalize_metadata_only():
    d = _norm()
    assert d["doc_id"] == "opendart:20240312000736" and d["version"] == 1
    assert d["published_at"] == "2024-03-12" and d["time_precision"] == "date_only"
    assert d["published_at_basis"] == "dart_receipt_date" and d["backfilled"] is True
    assert d["available_at"] == "2024-03-13T00:00:00+09:00"
    assert d["body_ref"] is None and d["summary"] is None and d["license_scope"] == "metadata_only"
    assert d["url"].endswith("rcpNo=20240312000736")
    assert d["tickers"] == [{"code": "005930", "method": "native", "evidence": "stock_code", "confidence": 1.0}]
    assert d["is_amendment"] and d["report_base"] == "사업보고서 (2023.12)"
    no_code = _norm({**ITEM, "stock_code": " "})
    assert no_code["tickers"] == [] and no_code["ticker_status"] == "none"


@pytest.mark.parametrize("bad", [{"rcept_no": ""}, {"rcept_dt": "2024-03-12"}, {"rcept_no": "A123"}, {"report_nm": None}])
def test_normalize_rejects_bad_items(bad):
    with pytest.raises(ValueError):
        _norm({**ITEM, **bad})


def test_classify_new_duplicate_changed():
    d = _norm()
    assert classify(d, {}) == ("new", d)
    assert classify(d, {d["doc_id"]: Seen(1, d["content_hash"])}) == ("duplicate", None)
    changed = _norm({**ITEM, "rm": "연정"})
    kind, row = classify(changed, {d["doc_id"]: Seen(2, d["content_hash"])})
    assert kind == "changed" and row["version"] == 3


def test_link_amendments_by_company_and_base_name():
    orig = _norm({**ITEM, "report_nm": "사업보고서 (2023.12)", "rcept_no": "20240311000100", "rcept_dt": "20240311"})
    amend = _norm()
    other = _norm({**ITEM, "corp_code": "00000002", "rcept_no": "20240312000999"})  # 다른 회사 정정 → 원공시 없음
    chain = link_amendments([amend, other, orig], {})
    assert amend["amends_doc_id"] == orig["doc_id"] and other["amends_doc_id"] is None
    assert orig["amends_doc_id"] is None
    assert chain[("00126380", "사업보고서 (2023.12)")] == amend["doc_id"]
    # 이전 실행에서 이어 받은 원공시
    amend2 = _norm({**ITEM, "rcept_no": "20240401000001", "rcept_dt": "20240401"})
    link_amendments([amend2], chain)
    assert amend2["amends_doc_id"] == amend["doc_id"]
