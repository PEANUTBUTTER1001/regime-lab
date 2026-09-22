"""store/ 로딩 (FR-D1~D3, A1). 원본 파일은 읽기만 한다.

본 데이터는 store/ 의 Parquet 파일이다. regime.duckdb 파일은 열지 않고
v_daily·v_universe_base 에 해당하는 조인을 메모리 내 DuckDB 로 재현한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import pandas as pd

PRICE_COLS = ["open", "high", "low", "close"]


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("SET enable_progress_bar = false")
    return con


def _p(path: Path) -> str:
    return path.as_posix()


def _ticker_filter(tickers: Sequence[str] | None, alias: str = "p") -> str:
    if not tickers:
        return ""
    quoted = ",".join(f"'{t}'" for t in tickers if t.isalnum())
    return f"AND {alias}.ticker IN ({quoted})"


def load_daily(
    store: Path,
    tickers: Sequence[str] | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """종목 일봉 통합 프레임.

    수정주가 OHLCV(가격 double 통일), 거래대금(원), 일별 시가총액·시장, KRX 원주가·소속부,
    master 의 종목유형·상장일을 (ticker, date) 기준으로 합친다.
    halted = 원본 시가 결측 (v_daily 와 동일 정의).
    데이터 품질: 원본에 시가·고가·저가가 0 인 행(거래량 > 0, 3건)이 있어 0 이하 가격은 결측으로 읽는다.
    해당일은 체결 불가로 처리된다 (halted 정의는 바꾸지 않음).
    """
    where_end = f"AND p.date <= TIMESTAMP '{end}'" if end else ""
    sql = f"""
        SELECT p.date, p.ticker,
               CAST(NULLIF(GREATEST(p.open, 0), 0) AS DOUBLE) AS open,
               CAST(NULLIF(GREATEST(p.high, 0), 0) AS DOUBLE) AS high,
               CAST(NULLIF(GREATEST(p.low, 0), 0) AS DOUBLE) AS low, CAST(p.close AS DOUBLE) AS close,
               CAST(p.volume AS DOUBLE) AS volume, CAST(p.value AS DOUBLE) AS value,
               CAST(m.marketcap AS DOUBLE) AS marketcap, m.market AS market,
               CAST(k.open_raw AS DOUBLE) AS open_raw, CAST(k.close_raw AS DOUBLE) AS close_raw,
               CAST(k.volume_raw AS DOUBLE) AS volume_raw, k.dept AS dept,
               ms.kind AS kind,
               (p.open IS NULL) AS halted
        FROM read_parquet('{_p(store / "prices.parquet")}') p
        LEFT JOIN read_parquet('{_p(store / "marketcap.parquet")}') m USING (date, ticker)
        LEFT JOIN read_parquet('{_p(store / "raw" / "krx_daily" / "*.parquet")}') k USING (date, ticker)
        LEFT JOIN read_parquet('{_p(store / "master.parquet")}') ms USING (ticker)
        WHERE 1=1 {_ticker_filter(tickers)} {where_end}
        ORDER BY p.ticker, p.date
    """
    df = _con().sql(sql).df()
    df["date"] = df["date"].astype("datetime64[ns]")
    for c in ("market", "dept", "kind"):
        df[c] = df[c].astype("category")
    return df


def load_master(store: Path) -> pd.DataFrame:
    return _con().sql(f"SELECT * FROM read_parquet('{_p(store / 'master.parquet')}')").df()


def load_index(store: Path, end: str | None = None) -> pd.DataFrame:
    """시장 지수 일봉 (A10 초과수익, 시장 국면). 컬럼: date, market, open, high, low, close."""
    where_end = f"WHERE date <= TIMESTAMP '{end}'" if end else ""
    df = _con().sql(f"""
        SELECT date, "index" AS market, open, high, low, close
        FROM read_parquet('{_p(store / 'index.parquet')}') {where_end}
        ORDER BY market, date
    """).df()
    df["date"] = df["date"].astype("datetime64[ns]")
    return df


def load_delisted(store: Path) -> pd.DataFrame:
    return _con().sql(f"SELECT * FROM read_parquet('{_p(store / 'delisted.parquet')}')").df()


def load_sector_snapshots(store: Path) -> pd.DataFrame:
    """월말 업종 스냅샷 (FR-E5 진입 시점 업종). 컬럼: date, ticker, sector."""
    df = _con().sql(f"""
        SELECT date, ticker, sector
        FROM read_parquet('{_p(store / 'raw' / 'sector' / '*.parquet')}')
        ORDER BY date, ticker
    """).df()
    df["date"] = df["date"].astype("datetime64[ns]")
    return df


def load_sample_tickers(store: Path) -> list[str]:
    return sorted(
        _con().sql(f"SELECT DISTINCT ticker FROM read_parquet('{_p(store / 'sample30.parquet')}')")
        .df()["ticker"].tolist()
    )


def input_file_hashes(store: Path) -> dict[str, str]:
    """재현성용 입력 파일 지문 (NFR-10). 대용량 해시 대신 크기+수정시각 기반."""
    import hashlib

    out: dict[str, str] = {}
    targets = [store / f for f in
               ("prices.parquet", "marketcap.parquet", "master.parquet", "index.parquet", "delisted.parquet")]
    for f in targets:
        st = f.stat()
        out[f.name] = f"size={st.st_size};mtime={int(st.st_mtime)}"
    for sub in ("krx_daily", "sector"):
        files = sorted((store / "raw" / sub).glob("*.parquet"))
        h = hashlib.sha256()
        for f in files:
            st = f.stat()
            h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)};".encode())
        out[f"raw/{sub}"] = f"n={len(files)};sha256={h.hexdigest()[:16]}"
    return out
