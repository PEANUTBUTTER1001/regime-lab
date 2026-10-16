"""예측 실행 저장소 (P2-7, 인프라). KRX 원주가 일봉·종목 마스터(·시간봉이면 1분봉 사본)를 읽고 예측 결과 폴더를 쓴다.
일봉 예측(P2-11, 결정 D-2b)은 1분봉 폴더 없이 동작한다 — 1분봉은 load_minutes 를 부를 때만 읽는다.

결과 형식 (공개 계약, 2026-10-12 Seonghwanaa 승인):
  runs/forecasts/<run_id>/meta.json         실행 정보·설정 해시·데이터 지문·분할 날짜
  runs/forecasts/<run_id>/predictions.parquet code·as_of·h·split·base_close·q…·p_up·direction·reason·y·cross_day
  runs/forecasts/<run_id>/scores.json       split×h 별 모델·기준선 지표
  자료 결합 형식5: evidence.parquet (문서 식별자/버전/종목/봉/유효 구간; 원문 없음).
  meta.fusion_kind·comparison_runs·influence_versions; 예측에 bar_ordinal/결측/자료 건수.
  기존 형식2~4는 원래 세 파일만으로 읽는다 (P4-3b, 2026-10-16 사용자 승인).
저장은 runs/forecasts/.tmp_<run_id>/ 에 쓴 뒤 완료 시 한 번에 옮긴다 (runs.py 와 같은 방식).
유스케이스(forecast_runs.py)는 이 객체를 인자로 받아 계산만 한다 (R1·R2).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from regime_lab.data.minutes import load_minutes, minutes_fingerprint

FILES = ("meta.json", "predictions.parquet", "scores.json")


class ForecastStore:
    def __init__(self, store: Path, minutes_dir: Path | None, runs: Path):
        self.store, self.root = Path(store), Path(runs) / "forecasts"
        self.minutes_dir = Path(minutes_dir) if minutes_dir is not None else None

    # 읽기 (입력)
    def common_codes(self) -> list[str]:
        """N8: 종목 마스터의 보통주 코드."""
        from regime_lab.data.loader import load_master  # loader 가 이 모듈을 다시 내보내므로 함수 안에서 import

        m = load_master(self.store)
        return sorted(m.loc[m["kind"].astype(str) == "보통주", "ticker"].astype(str))

    def load_minutes(self, codes: list[str]) -> pd.DataFrame:
        if self.minutes_dir is None:
            raise FileNotFoundError("1분봉 폴더가 지정되지 않았습니다 (시간봉 예측에만 필요)")
        return load_minutes(self.minutes_dir, codes=codes)

    def load_krx_raw(self, codes: list[str]) -> pd.DataFrame:
        """(code, date, open_raw, high_raw, low_raw, close_raw, volume_raw, value) — 일봉 예측 입력 (P2-11)."""
        from regime_lab.data.loader import load_krx_raw

        return load_krx_raw(self.store, tickers=codes)

    def load_daily_raw(self, codes: list[str]) -> pd.DataFrame:
        """(code, date, close_raw) — KRX 원주가 종가 (N2 판정용)."""
        from regime_lab.data.loader import load_daily

        d = load_daily(self.store, tickers=codes)
        return d.assign(code=d["ticker"].astype(str))[["code", "date", "close_raw"]]

    def fingerprint(self, minutes: bool = True) -> dict[str, str]:
        """입력 데이터 지문. 일봉 예측은 1분봉을 읽지 않으므로 minutes=False 로 1분봉 지문을 넣지 않는다."""
        from regime_lab.data.loader import input_file_hashes

        krx = input_file_hashes(self.store)
        out = {"raw/krx_daily": krx["raw/krx_daily"], "master.parquet": krx["master.parquet"]}
        return {"minutes": minutes_fingerprint(self.minutes_dir), **out} if minutes else out

    # 쓰기·읽기 (결과)
    def write_run(self, run_id: str, meta: dict, predictions: pd.DataFrame, scores: dict, evidence=None) -> Path:
        out, tmp = self.root / run_id, self.root / f".tmp_{run_id}"
        if out.exists():
            raise FileExistsError(f"이미 있는 예측 실행입니다: {out}")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            predictions.to_parquet(tmp / "predictions.parquet", index=False)
            (tmp / "scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if evidence is not None:
                evidence.to_parquet(tmp / "evidence.parquet", index=False)
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

    def read_evidence(self, run_id: str, code: str) -> pd.DataFrame:
        return pd.read_parquet(self.root / run_id / "evidence.parquet", filters=[("code", "==", code)])

    def read_predictions(self, run_id: str, code: str) -> pd.DataFrame:
        """한 종목의 예측 행만 (Parquet 필터로 읽음)."""
        return pd.read_parquet(self.root / run_id / "predictions.parquet", filters=[("code", "==", code)])

    def as_of_range(self, run_id: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        """저장된 예측의 as_of 최소·최대 — Parquet 행 그룹 통계만 읽음 (전 종목 run 도 가볍게)."""
        md = pq.ParquetFile(self.root / run_id / "predictions.parquet").metadata
        col = md.schema.to_arrow_schema().get_field_index("as_of")
        stats = [md.row_group(i).column(col).statistics for i in range(md.num_row_groups)]
        if not stats or any(s is None or not s.has_min_max for s in stats):
            return None
        return pd.Timestamp(min(s.min for s in stats)), pd.Timestamp(max(s.max for s in stats))

    def read_run(self, run_id: str) -> tuple[dict, pd.DataFrame, dict]:
        d = self.root / run_id
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        scores = json.loads((d / "scores.json").read_text(encoding="utf-8"))
        return meta, pd.read_parquet(d / "predictions.parquet"), scores
