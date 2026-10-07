"""OpenDART 공시 목록 항목 → 공통 문서 스키마 (도메인, plan/03-04 §5). 입출력 없음.

license_scope = metadata_only: 보고서명·회사·접수번호·날짜만 남기고 원문(ZIP)은 받지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from regime_ingest.timing import available_at, parse_yyyymmdd

SOURCE, SOURCE_TYPE = "opendart", "disclosure"
REQUIRED = ("rcept_no", "corp_code", "corp_name", "report_nm", "rcept_dt")
# 보고서명 앞의 대괄호 표시 중 원공시를 고치거나 보탠 것 (OpenDART 보고서명 관례). 정정 연결은 1-4 실측(10건)으로 확정 예정
AMEND_TAGS = frozenset({"기재정정", "첨부정정", "첨부추가", "변경등록", "연장결정", "발행조건확정", "정정명령부과", "정정제출요구"})
_TAG = re.compile(r"^\s*\[([^\]]+)\]\s*")
_STOCK_CODE = re.compile(r"^[0-9A-Z]{6}$")  # KRX 단축코드 6자리 (영문 포함 코드 있음)


def split_report_name(report_nm: str) -> tuple[list[str], str]:
    """'[기재정정]사업보고서 (2023.12)' → (['기재정정'], '사업보고서 (2023.12)'). 앞쪽 대괄호를 모두 뗀다."""
    tags, rest = [], report_nm
    while (m := _TAG.match(rest)):
        tags.append(m.group(1).strip())
        rest = rest[m.end():]
    return tags, " ".join(rest.split())


def content_hash(item: dict) -> str:
    """같은 접수번호의 내용이 바뀌었는지 판별하는 지문 (보고서명·제출인·비고·종목코드)."""
    key = {k: (item.get(k) or "").strip() for k in ("report_nm", "flr_nm", "rm", "stock_code", "corp_name")}
    return hashlib.sha256(json.dumps(key, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def normalize(item: dict, *, first_seen_at: datetime, backfilled: bool, ingest_run_id: str,
              viewer_url: str, license_scope: str) -> dict:
    missing = [k for k in REQUIRED if not (item.get(k) or "").strip()]
    if missing:
        raise ValueError(f"필수 필드 없음: {missing}")
    rcept_no = item["rcept_no"].strip()
    if not rcept_no.isdigit():
        raise ValueError(f"접수번호 형식이 아님: {rcept_no!r}")
    published = parse_yyyymmdd(item["rcept_dt"].strip())
    tags, base = split_report_name(item["report_nm"])
    raw_code = (item.get("stock_code") or "").strip()
    code = raw_code if _STOCK_CODE.match(raw_code) else ""
    url = viewer_url + rcept_no
    return {
        "doc_id": f"{SOURCE}:{rcept_no}",
        "version": 1,  # 같은 접수번호의 내용 변경 시 dedup 이 올린다
        "source": SOURCE,
        "source_type": SOURCE_TYPE,
        "title": item["report_nm"].strip(),
        "summary": None,
        "body_ref": None,  # metadata_only: 원문 미보관
        "url": url,
        "original_url": url,
        "published_at": published.isoformat(),
        "published_at_basis": "dart_receipt_date",
        "time_precision": "date_only",
        "modified_at": None,
        "first_seen_at": first_seen_at.isoformat(),
        "available_at": available_at(published, first_seen_at, backfilled).isoformat(),
        "backfilled": backfilled,
        "tickers": [{"code": code, "method": "native", "evidence": "stock_code", "confidence": 1.0}] if code else [],
        "ticker_status": "resolved" if code else ("invalid_code" if raw_code else "none"),
        "stock_code_raw": raw_code,
        "content_hash": content_hash(item),
        "cluster_id": None,
        "license_scope": license_scope,
        "ingest_run_id": ingest_run_id,
        # 공시 고유 필드
        "rcept_no": rcept_no,
        "corp_code": item["corp_code"].strip(),
        "corp_name": item["corp_name"].strip(),
        "corp_cls": (item.get("corp_cls") or "").strip(),
        "flr_nm": (item.get("flr_nm") or "").strip(),
        "rm": (item.get("rm") or "").strip(),
        "report_tags": tags,
        "report_base": base,
        "is_amendment": any(t in AMEND_TAGS for t in tags),
        "amends_candidate_doc_id": None,  # dedup.link_amendments 가 채운다 (후보, 확정 아님)
        "amends_basis": None,
    }
