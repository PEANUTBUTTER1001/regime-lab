"""데이터 준비 파이프라인: 로딩 → 워밍업 → 지표 → 유니버스 → 그룹 → 국면.

전략 실행은 이 프레임을 읽어 신호·체결만 계산한다 (구현_계획 §5 성능 방침).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from regime_lab.config import Paths, config_hash
from regime_lab.data.loader import (
    input_file_hashes,
    load_daily,
    load_delisted,
    load_index,
    load_master,
    load_sector_snapshots,
)
from regime_lab.data.warmup import attach_warmup, first_trade_dates, load_first_dates, load_warmup_source
from regime_lab.indicators import compute_indicators
from regime_lab.regime import attach_regimes
from regime_lab.universe import apply_universe, assign_groups

# 준비 프레임에 영향을 주는 설정 절 (전략·청산·분석 설정은 캐시 무효화 대상 아님)
PREP_SECTIONS = ("data", "universe", "groups", "regime", "indicators", "patterns")
# 준비 프레임 계산 코드가 바뀌면 올린다 (캐시 무효화)
PREP_VERSION = 2


@dataclass
class Prepared:
    frame: pd.DataFrame
    index: pd.DataFrame
    delisted: set[str]
    sectors: pd.DataFrame
    names: dict[str, str]


def prep_hash(cfg: dict) -> str:
    return config_hash({"prep_version": PREP_VERSION, **{k: cfg[k] for k in PREP_SECTIONS}})


def prepare(paths: Paths, cfg: dict, tickers: Sequence[str] | None = None, end: str | None = None,
            use_cache: bool = False) -> Prepared:
    end = end or cfg["data"]["as_of_date"]
    cache_file = None
    if use_cache and tickers is None:
        data_ver = config_hash(input_file_hashes(paths.store))[:8]
        cache_file = paths.cache / "prepared" / f"frame_{end}_{prep_hash(cfg)}_{data_ver}.parquet"
    index = load_index(paths.store, end=end)
    delisted = set(load_delisted(paths.store)["ticker"])
    sectors = load_sector_snapshots(paths.store)
    master = load_master(paths.store)
    names = dict(zip(master["ticker"].astype(str), master["name"]))
    if cache_file is not None and cache_file.exists():
        return Prepared(pd.read_parquet(cache_file), index, delisted, sectors, names)

    daily = load_daily(paths.store, tickers, end=end)
    frame = attach_warmup(daily, load_warmup_source(paths.cache), cfg)
    first = first_trade_dates(frame, load_first_dates(paths.cache), cfg["data"]["backtest_start"])
    frame = compute_indicators(frame, cfg)
    frame = apply_universe(frame, first, cfg)
    frame = assign_groups(frame, cfg)
    frame = attach_regimes(frame, index, cfg)
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(cache_file, index=False)
    return Prepared(frame, index, delisted, sectors, names)


def cache_dir(paths: Paths) -> Path:
    return paths.cache / "prepared"
