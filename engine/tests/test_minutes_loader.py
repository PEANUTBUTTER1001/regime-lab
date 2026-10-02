"""P2-4.1 1분봉 사본 로더 — 월 파일 선택, 기간·종목 필터, 정렬, 지문."""

import pandas as pd
import pytest

from regime_lab.data.minutes import load_manifest, load_minutes, minutes_fingerprint, month_files


def _write(d, ym, rows):
    df = pd.DataFrame(rows, columns=["code", "dt", "open_p", "high_p", "low_p", "close_p", "volume", "value"])
    df["dt"] = pd.to_datetime(df["dt"])
    df.to_parquet(d / f"stock_minutes_{ym}.parquet", index=False)


@pytest.fixture()
def mdir(tmp_path):
    _write(tmp_path, 202508, [
        ("005930", "2025-08-29 15:30:00", 100, 101, 99, 100, 10, 1000),
        ("000660", "2025-08-29 09:00:00", 200, 201, 199, 200, 5, 1000),
    ])
    _write(tmp_path, 202509, [
        ("005930", "2025-09-01 09:01:00", 101, 102, 100, 101, 7, 2707),
        ("005930", "2025-09-01 09:00:00", 100, 101, 99, 100, 20, 2000),
        ("0161M0", "2025-09-01 09:00:00", 50, 51, 49, 50, 3, 150),
        ("005930", "2025-09-02 09:00:00", 102, 103, 101, 102, 8, 816),
    ])
    (tmp_path / "not_a_month.parquet").write_bytes(b"")  # 이름 규칙 밖 파일은 무시
    return tmp_path


def test_month_files_and_sorting(mdir):
    assert list(month_files(mdir)) == [202508, 202509]
    df = load_minutes(mdir)
    assert len(df) == 6
    assert list(df.columns) == ["code", "dt", "open_p", "high_p", "low_p", "close_p", "volume", "value"]
    assert df[["code", "dt"]].equals(df.sort_values(["code", "dt"])[["code", "dt"]].reset_index(drop=True))
    assert "0161M0" in set(df["code"])  # 영문이 섞인 종목코드·앞자리 0 보존


def test_period_and_code_filters(mdir):
    df = load_minutes(mdir, codes=["005930"], start="2025-09-01", end="2025-09-01")
    assert df["dt"].dt.strftime("%Y-%m-%d %H:%M").tolist() == ["2025-09-01 09:00", "2025-09-01 09:01"]
    df2 = load_minutes(mdir, codes=["005930"], start="2025-09-01T09:01", end="2025-09-02T09:00")
    assert df2["dt"].dt.strftime("%m-%d %H:%M").tolist() == ["09-01 09:01", "09-02 09:00"]
    assert load_minutes(mdir, start="2025-08-29", end="2025-08-29")["code"].tolist() == ["000660", "005930"]


def test_columns_empty_and_validation(mdir):
    df = load_minutes(mdir, codes=["005930"], start="2025-09-02", end="2025-09-02", columns=["close_p"])
    assert list(df.columns) == ["code", "dt", "close_p"] and len(df) == 1
    assert load_minutes(mdir, start="2026-01-01", end="2026-01-31").empty
    with pytest.raises(ValueError):
        load_minutes(mdir, codes=["5930"])
    with pytest.raises(ValueError):
        load_minutes(mdir, columns=["bogus"])
    with pytest.raises(ValueError):
        load_minutes(mdir, start="2025-09-02", end="2025-09-01")


def test_fingerprint_changes_with_files(mdir):
    a = minutes_fingerprint(mdir)
    _write(mdir, 202510, [("005930", "2025-10-01 09:00:00", 1, 1, 1, 1, 1, 1)])
    assert minutes_fingerprint(mdir) != a
    assert load_manifest(mdir) is None


@pytest.mark.data
def test_real_minutes_copy(paths):
    d = paths.store.parent / "minutes"
    if not month_files(d):
        pytest.skip(f"{d} 에 1분봉 사본이 없음 — engine/scripts/export_minutes.py 로 만든다")
    m = load_manifest(d)
    df = load_minutes(d, codes=["005930"], start="2026-09-01", end="2026-09-01")
    assert len(df) > 0 and df["dt"].dt.date.nunique() == 1
    assert df["value"].is_monotonic_increasing  # 당일 누적 거래대금 (docs/분봉_데이터_설계.md §1.3)
    if m:
        assert sum(v["rows"] for v in m["months"].values()) == m["total_rows"]
