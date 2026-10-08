"""근거 검색 테스트용 합성 공시 문서와 ext_store 폴더 (P3-11). 실제 수집 자료 없이 docs 계약 필드만 흉내 낸다."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 10, 7, 9, 0, tzinfo=KST)

TITLES = [
    ("한빛반도체", "자기주식취득결정", "005930"),
    ("한빛반도체", "자기주식취득결과보고서", "005930"),
    ("대양중공업", "단일판매ㆍ공급계약체결 LNG 운반선 수주", "010140"),
    ("푸른증권", "영업실적등에대한전망 증권사 실적", "001500"),
    ("새벽바이오", "임상시험계획승인", "068270"),
]


def make_doc(i: int, corp: str, title: str, code: str, seen: datetime, *, version: int = 1,
             available: datetime | None = None, **kw) -> dict:
    rcept = f"20261007{i:06d}"
    d = {
        "doc_id": f"opendart:{rcept}", "version": version, "source": "opendart", "source_type": "disclosure",
        "title": title, "summary": None, "url": f"https://dart.example/{rcept}",
        "published_at": seen.date().isoformat(), "time_precision": "date_only",
        "first_seen_at": seen.isoformat(), "available_at": (available or seen).isoformat(),
        "tickers": [{"code": code, "method": "native", "evidence": "stock_code", "confidence": 1.0}] if code else [],
        "corp_name": corp, "license_scope": "metadata_only",
    }
    d.update(kw)
    return d


def make_docs(start: datetime = T0) -> list[dict]:
    """5건, 1시간 간격으로 처음 관측."""
    return [make_doc(i, c, t, code, start + timedelta(hours=i)) for i, (c, t, code) in enumerate(TITLES)]


def write_store(root: Path, docs: list[dict], source: str = "opendart", schema_version: int = 1) -> Path:
    """<root>/docs/source=<s>/date=YYYY-MM/part-test.parquet 와 _manifest.json 을 쓴다."""
    base = root / "docs" / f"source={source}"
    by_month: dict[str, list[dict]] = {}
    for d in docs:
        by_month.setdefault(d["published_at"][:7], []).append(d)
    for month, rows in by_month.items():
        (base / f"date={month}").mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), base / f"date={month}" / "part-test.parquet")
    (base / "_manifest.json").write_text(json.dumps({"schema_version": schema_version, "policy_version": "test"}),
                                         encoding="utf-8")
    return root
