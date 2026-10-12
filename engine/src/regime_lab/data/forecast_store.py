"""예측 실행 저장소 (P2-7, 인프라). 1분봉 사본·KRX 원주가·종목 마스터를 읽고 예측 결과 폴더를 쓴다.

결과 형식 (공개 계약, 2026-10-12 Seonghwanaa 승인):
  runs/forecasts/<run_id>/meta.json         실행 정보·설정 해시·데이터 지문·분할 날짜
  runs/forecasts/<run_id>/predictions.parquet code·as_of·h·split·base_close·q…·p_up·direction·reason·y·cross_day
  runs/forecasts/<run_id>/scores.json       split×h 별 모델·기준선 지표
저장은 runs/forecasts/.tmp_<run_id>/ 에 쓴 뒤 완료 시 한 번에 옮긴다 (runs.py 와 같은 방식).
유스케이스(forecast_runs.py)는 이 객체를 인자로 받아 계산만 한다 (R1·R2).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from regime_lab.data.minutes import load_minutes, minutes_fingerprint

FILES = ("meta.json", "predictions.parquet", "scores.json")


class ForecastStore:
    def __init__(self, store: Path, minutes_dir: Path, runs: Path):
        self.store, self.minutes_dir, self.root = Path(store), Path(minutes_dir), Path(runs) / "forecasts"

    # 읽기 (입력)
    def common_codes(self) -> list[str]:
        """N8: 종목 마스터의 보통주 코드."""
        from regime_lab.data.loader import load_master  # loader 가 이 모듈을 다시 내보내므로 함수 안에서 import

        m = load_master(self.store)
        return sorted(m.loc[m["kind"].astype(str) == "보통주", "ticker"].astype(str))

    def load_minutes(self, codes: list[str]) -> pd.DataFrame:
        return load_minutes(self.minutes_dir, codes=codes)

    def load_daily_raw(self, codes: list[str]) -> pd.DataFrame:
        """(code, date, close_raw) — KRX 원주가 종가 (N2 판정용)."""
        from regime_lab.data.loader import load_daily

        d = load_daily(self.store, tickers=codes)
        return d.assign(code=d["ticker"].astype(str))[["code", "date", "close_raw"]]

    def fingerprint(self) -> dict[str, str]:
        from regime_lab.data.loader import input_file_hashes

        krx = input_file_hashes(self.store)
        return {"minutes": minutes_fingerprint(self.minutes_dir), "raw/krx_daily": krx["raw/krx_daily"],
                "master.parquet": krx["master.parquet"]}

    # 쓰기·읽기 (결과)
    def write_run(self, run_id: str, meta: dict, predictions: pd.DataFrame, scores: dict) -> Path:
        out, tmp = self.root / run_id, self.root / f".tmp_{run_id}"
        if out.exists():
            raise FileExistsError(f"이미 있는 예측 실행입니다: {out}")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            predictions.to_parquet(tmp / "predictions.parquet", index=False)
            (tmp / "scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp.rename(out)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return out

    def list_runs(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")
                      and all((p / f).exists() for f in FILES))

    def has_run(self, run_id: str) -> bool:
        return run_id in self.list_runs()

    def read_meta(self, run_id: str) -> dict:
        return json.loads((self.root / run_id / "meta.json").read_text(encoding="utf-8"))

    def read_scores(self, run_id: str) -> dict:
        return json.loads((self.root / run_id / "scores.json").read_text(encoding="utf-8"))

    def read_predictions(self, run_id: str, code: str) -> pd.DataFrame:
        """한 종목의 예측 행만 (Parquet 필터로 읽음)."""
        return pd.read_parquet(self.root / run_id / "predictions.parquet", filters=[("code", "==", code)])

    def read_run(self, run_id: str) -> tuple[dict, pd.DataFrame, dict]:
        d = self.root / run_id
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        scores = json.loads((d / "scores.json").read_text(encoding="utf-8"))
        return meta, pd.read_parquet(d / "predictions.parquet"), scores
