"""1분봉 원본 사본 읽기 (P2-4.1, 인프라). 원본 DB(MariaDB stock_minutes)는 읽지 않는다.

입력: <minutes_dir>/stock_minutes_YYYYMM.parquet (월별, engine/scripts/export_minutes.py 가 만든다)
      <minutes_dir>/minutes_manifest.json (월별 행 수·최소/최대 시각)
컬럼은 원본 그대로 (code, dt, open_p, high_p, low_p, close_p, volume, value) 이다.
`value` 는 당일 누적 거래대금이고 가격 기준이 종목마다 섞여 있으므로, 정규화는 도메인에서 한다
(docs/분봉_데이터_설계.md §2). 이 모듈은 기간·종목으로 골라 읽기만 한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

MINUTE_COLS = ["code", "dt", "open_p", "high_p", "low_p", "close_p", "volume", "value"]
FILE_RE = re.compile(r"^stock_minutes_(\d{6})\.parquet$")
CODE_RE = re.compile(r"^[0-9A-Za-z]{6}$")


def month_files(minutes_dir: Path) -> dict[int, Path]:
    """{YYYYMM: 파일} — 이름 규칙에 맞는 월별 파일만."""
    out = {}
    for f in sorted(minutes_dir.glob("stock_minutes_*.parquet")):
        m = FILE_RE.match(f.name)
        if m:
            out[int(m.group(1))] = f
    return out


def load_manifest(minutes_dir: Path) -> dict | None:
    f = minutes_dir / "minutes_manifest.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def minutes_fingerprint(minutes_dir: Path) -> str:
    """정규화 캐시 키용 데이터 지문 (월별 파일 이름·크기·수정 시각)."""
    h = hashlib.sha256()
    files = month_files(minutes_dir)
    for ym, f in files.items():
        st = f.stat()
        h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)};".encode())
    return f"n={len(files)};sha256={h.hexdigest()[:16]}"


def load_minutes(minutes_dir: Path, codes: Sequence[str] | None = None, start: str | None = None,
                 end: str | None = None, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """기간 [start, end] (양 끝 포함, 날짜만 주면 end 는 그날 전체)·종목으로 1분봉을 읽는다.

    결과는 (code, dt) 오름차순이고 code 는 6자리 문자열이다. 해당 월 파일이 없으면 빈 프레임을 돌려준다.
    """
    cols = list(columns) if columns else list(MINUTE_COLS)
    unknown = set(cols) - set(MINUTE_COLS)
    if unknown:
        raise ValueError(f"없는 컬럼: {sorted(unknown)} (가능: {MINUTE_COLS})")
    for k in ("code", "dt"):
        if k not in cols:
            cols.insert(0 if k == "code" else 1, k)
    if codes is not None:
        codes = list(dict.fromkeys(codes))
        bad = [c for c in codes if not CODE_RE.match(str(c))]
        if bad:
            raise ValueError(f"종목코드는 6자리 영숫자 문자열이어야 합니다: {bad[:5]}")

    lo = pd.Timestamp(start) if start else None
    hi = pd.Timestamp(end) if end else None
    if hi is not None and len(str(end)) <= 10:  # 날짜만 주면 그날 끝까지 포함
        hi = hi + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f"start({start}) 가 end({end}) 보다 늦습니다")

    files = month_files(minutes_dir)
    keep = [f for ym, f in files.items()
            if (lo is None or ym >= lo.year * 100 + lo.month) and (hi is None or ym <= hi.year * 100 + hi.month)]
    if not keep:
        return pd.DataFrame({c: pd.Series(dtype="object" if c == "code" else "float64") for c in cols})

    dataset = ds.dataset([str(f) for f in keep], format="parquet")
    cond = None
    for c in ([ds.field("code").isin(codes)] if codes is not None else []) + \
             ([ds.field("dt") >= lo.to_pydatetime()] if lo is not None else []) + \
             ([ds.field("dt") <= hi.to_pydatetime()] if hi is not None else []):
        cond = c if cond is None else cond & c
    df = dataset.to_table(columns=cols, filter=cond).to_pandas()
    df["code"] = df["code"].astype(str)
    return df.sort_values(["code", "dt"], kind="stable").reset_index(drop=True)
