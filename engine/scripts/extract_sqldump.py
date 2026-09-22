"""SQL 덤프(multi_tables_db) 1회성 추출 스크립트 — 원본은 읽기만 한다.

한 번의 순차 스트리밍으로 다음을 cache/ 에 만든다 (stock_minutes 이후는 읽지 않음).

1. S2 워밍업: kor_price 의 [warmup_source_start, 2020-09-30] 구간 일봉
   → cache/warmup/kor_price_warmup.parquet   (거래대금은 원본 단위 백만원 그대로)
2. U1 첫 거래일: kor_price 종목별 최초 일자
   → cache/warmup/kor_price_first_date.parquet
3. S5 참조값: kor_indicators 의 sample30 종목, 2019-08-01 이후
   → cache/reference/kor_indicators_sample30.parquet
4. S5 참조 입력: 참조 지표가 계산된 원 입력인 kor_price 의 sample30 종목, 2019-08-01 이후
   → cache/reference/kor_price_sample30.parquet

사용: uv run python scripts/extract_sqldump.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regime_lab.config import load_config, load_paths  # noqa: E402
from regime_lab.data.loader import load_sample_tickers  # noqa: E402

KOR_PRICE_COLS = ["date", "open", "high", "low", "close", "volume", "value_mil", "ticker"]
WARMUP_KEEP_END = "2020-09-30"  # 2020-09-01 연결 비율 계산용 여유 구간
REF_START = "2019-08-01"


def _split_row(line: str) -> list[str]:
    body = line.rstrip()
    body = body[1:-2] if body.endswith(("),", ");")) else body[1:-1]
    return [v.strip("'") for v in body.split(",")]



def _price_frame(rows: list[list]) -> pd.DataFrame:
    w = pd.DataFrame(rows, columns=KOR_PRICE_COLS)
    w["date"] = pd.to_datetime(w["date"])
    for c in KOR_PRICE_COLS[1:-1]:
        w[c] = pd.to_numeric(w[c].replace("NULL", None), errors="coerce").astype("float64")
    return w


def main() -> None:
    cfg = load_config()
    paths = load_paths()
    warm_start = cfg["data"]["warmup_source_start"]
    sample = set(load_sample_tickers(paths.store))

    ind_cols: list[str] = []
    warm_rows: list[list] = []
    first_date: dict[str, str] = {}
    ref_rows: list[list] = []
    ref_price_rows: list[list] = []

    section = None  # "kor_price" | "kor_indicators" | "ddl_ind"
    t0 = time.time()
    with open(paths.sql_dump, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            if n % 2_000_000 == 0 and n:
                print(f"  {n:,} lines, {time.time() - t0:.0f}s, section={section}", flush=True)
            if line.startswith("("):
                if section == "kor_price":
                    d = line[2:12]
                    tick = line[line.rindex(",'") + 2: line.rindex("'")]
                    if tick not in first_date:
                        first_date[tick] = d
                    if warm_start <= d <= WARMUP_KEEP_END:
                        warm_rows.append(_split_row(line))
                    if d >= REF_START and tick in sample:
                        ref_price_rows.append(_split_row(line))
                elif section == "kor_indicators":
                    d = line[2:12]
                    if d < REF_START:
                        continue
                    tick = line[15: line.index("'", 15)]
                    if tick in sample:
                        ref_rows.append(_split_row(line))
                continue
            if line.startswith("CREATE TABLE `kor_indicators`"):
                section = "ddl_ind"
            elif section == "ddl_ind" and line.startswith("  `"):
                ind_cols.append(line.split("`")[1])
            elif line.startswith("INSERT INTO `kor_price`"):
                section = "kor_price"
            elif line.startswith("INSERT INTO `kor_indicators`"):
                section = "kor_indicators"
            elif line.startswith("CREATE TABLE `stock_minutes`"):
                break
            elif line.startswith("UNLOCK TABLES"):
                section = None
    print(f"scan done {time.time() - t0:.0f}s: warm={len(warm_rows):,} first={len(first_date):,} ref={len(ref_rows):,}")

    (paths.cache / "warmup").mkdir(parents=True, exist_ok=True)
    (paths.cache / "reference").mkdir(parents=True, exist_ok=True)

    _price_frame(warm_rows).to_parquet(paths.cache / "warmup" / "kor_price_warmup.parquet", index=False)
    _price_frame(ref_price_rows).to_parquet(paths.cache / "reference" / "kor_price_sample30.parquet", index=False)

    fd = pd.DataFrame(sorted(first_date.items()), columns=["ticker", "first_date"])
    fd["first_date"] = pd.to_datetime(fd["first_date"])
    fd.to_parquet(paths.cache / "warmup" / "kor_price_first_date.parquet", index=False)

    r = pd.DataFrame(ref_rows, columns=ind_cols)
    r = r.rename(columns={"기준일": "date", "종목코드": "ticker"})
    r["date"] = pd.to_datetime(r["date"])
    for c in r.columns:
        if c not in ("date", "ticker", "zone"):
            r[c] = pd.to_numeric(r[c].replace("NULL", None), errors="coerce").astype("float64")
    r.to_parquet(paths.cache / "reference" / "kor_indicators_sample30.parquet", index=False)
    print("written to", paths.cache)


if __name__ == "__main__":
    main()
