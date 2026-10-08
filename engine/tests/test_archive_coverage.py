"""P3-13 보관함 수집 상태 (FR-N4·N7): 수집기가 내보낸 coverage_log.parquet 로 시점별 공백·수집 상태를 다시 만든다.
엔진은 수집 패키지를 import 하지 않으므로(R5) 같은 열 형식의 파일을 직접 만든다 (ingest/tests 의 내보내기 테스트와 짝)."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from regime_lab import retrieval
from regime_lab.config import load_config
from regime_lab.data.loader import DocsStore

COLS = ["seq", "source", "day", "corp_cls", "state", "run_id", "observed_at"]


@pytest.fixture()
def cfg():
    return load_config()


def _write(root, source, rows):
    d = root / "coverage" / f"source={source}"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([dict(zip(COLS, (i + 1, source, *r))) for i, r in enumerate(rows)]),
                   d / "coverage_log.parquet")


def _store(tmp_path):
    root = tmp_path / "ext"
    _write(root, "opendart", [
        ("2026-10-06", "Y", "collected", "r0", "2026-10-06T23:00:00+09:00"),
        ("2026-10-06", "K", "gap", "r0", "2026-10-06T23:00:00+09:00"),
        ("2026-10-07", "Y", "forward", "r1", "2026-10-07T09:05:00+09:00"),
        ("2026-10-07", "K", "forward", "r1", "2026-10-07T09:05:00+09:00"),
        ("2026-10-06", "K", "collected", "r2", "2026-10-07T23:00:00+09:00"),  # 밤 소급이 공백을 채움
        ("2026-10-07", "Y", "collected", "r2", "2026-10-07T23:00:00+09:00"),
        ("2026-10-07", "K", "partial", "r2", "2026-10-07T23:00:00+09:00"),
        ("2026-10-08", "Y", "forward", "r3", "2026-10-08T09:05:00+09:00"),
    ])
    _write(root, "naver_news", [("2026-10-07", "news", "gap", "n1", "2026-10-07T10:00:00+09:00")])
    return DocsStore(root, tmp_path / "cache")


def test_state_reconstructed_at_as_of(tmp_path, cfg):
    s = _store(tmp_path)
    noon = retrieval.coverage(s, cfg, "2026-10-07T12:00:00+09:00")
    od = noon["sources"]["opendart"]
    assert od["counts"] == {"collected": 1, "forward": 2, "partial": 0, "gap": 1} and od["gap_days"] == ["2026-10-06"]
    assert od["last_day"] == "2026-10-07" and od["markets"] == ["K", "Y"]  # 10-08 은 기준 시각 뒤 날짜라 제외
    assert noon["sources"]["naver_news"]["counts"]["gap"] == 1
    night = retrieval.coverage(s, cfg, "2026-10-08T00:00:00+09:00")["sources"]["opendart"]
    assert night["counts"] == {"collected": 3, "forward": 0, "partial": 1, "gap": 0}
    assert night["gap_days"] == [] and night["partial_days"] == ["2026-10-07"]
    early = retrieval.coverage(s, cfg, "2026-10-06T22:00:00+09:00")
    assert early["status"] == "no_coverage" and early["sources"] == {}


def test_historical_assumed_uses_final_state_up_to_date(tmp_path, cfg):
    s = _store(tmp_path)
    obs = retrieval.coverage(s, cfg, "2026-10-07T00:30:00+09:00")["sources"]["opendart"]
    hist = retrieval.coverage(s, cfg, "2026-10-07T00:30:00+09:00", mode="historical_assumed")["sources"]["opendart"]
    assert obs["gap_days"] == ["2026-10-06"] and hist["gap_days"] == []  # 나중 소급으로 채워진 최종 상태
    assert hist["last_day"] == "2026-10-07"


def test_date_range_and_list_limit(tmp_path, cfg):
    s = _store(tmp_path)
    r = retrieval.coverage(s, cfg, "2026-10-09T00:00:00+09:00", start="2026-10-07", end="2026-10-07")
    assert r["sources"]["opendart"]["first_day"] == "2026-10-07" and r["start"] == "2026-10-07"
    root = tmp_path / "many"
    _write(root, "opendart", [(f"2025-{m:02d}-{d:02d}", "Y", "gap", "r", "2025-12-31T00:00:00+09:00")
                              for m in range(1, 13) for d in range(1, 11)])
    big = retrieval.coverage(DocsStore(root, tmp_path), cfg, "2026-01-01T00:00:00+09:00")["sources"]["opendart"]
    assert big["counts"]["gap"] == 120 and len(big["gap_days"]) == cfg["archive"]["coverage_list_max"]


def test_no_files_and_errors(tmp_path, cfg):
    (tmp_path / "empty").mkdir()
    assert retrieval.coverage(DocsStore(tmp_path / "empty", tmp_path), cfg, "2026-10-08T00:00:00+09:00")["status"] == "no_coverage"
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.coverage(DocsStore(None, tmp_path), cfg, "2026-10-08T00:00:00+09:00")
    assert e.value.code == "data_unavailable"
    for kw, field in (({"as_of": "2026-10-08"}, "as_of"), ({"mode": "x"}, "mode"), ({"start": "2026/10/01"}, "start"),
                      ({"start": "2026-10-08", "end": "2026-10-01"}, "end")):
        args = {"as_of": "2026-10-08T00:00:00+09:00", **kw}
        with pytest.raises(retrieval.RetrievalError) as e:
            retrieval.coverage(_store(tmp_path / field), cfg, **args)
        assert e.value.code == "validation_failed" and field in e.value.detail["fields"]


def test_truncation_invariance(tmp_path, cfg):
    """T 뒤에 기록된 수집 이력을 지워도 T 시점 수집 상태(observed)는 같다."""
    full = _store(tmp_path)
    t = "2026-10-07T12:00:00+09:00"
    rows = [r for r in full.coverage() if r["source"] == "opendart" and r["observed_at"] <= t]
    root = tmp_path / "cut"
    _write(root, "opendart", [tuple(r[c] for c in COLS[2:]) for r in rows])
    _write(root, "naver_news", [("2026-10-07", "news", "gap", "n1", "2026-10-07T10:00:00+09:00")])
    a = retrieval.coverage(full, cfg, t)
    b = retrieval.coverage(DocsStore(root, tmp_path), cfg, t)
    assert a["sources"] == b["sources"]
