"""영향력 가중치 결과 저장소 (P3-12 가 쓰고 P3-13.3 이 읽음, 인프라). 결정 P3-12a (2026-10-12).

결과 형식 (docs/P3_영향분석_화면_요구.md §8 확정 형식):
  runs/influence/<model_version>/meta.json        모델 정보·기간·기준선·표본 부족 기준·입력 지문·형식 버전
  runs/influence/<model_version>/weights.parquet  가중치 표 (행 = 자료 묶음)
  runs/influence/<model_version>/events.parquet   사건 (자료 버전 × 종목)
저장은 runs/influence/.tmp_<model_version>/ 에 쓴 뒤 완료 시 한 번에 옮긴다 (forecast_store 와 같은 방식).
세 파일이 다 있는 폴더만 결과로 본다. 폴더가 없으면 빈 목록.
오류는 내장 예외로 낸다 — 유스케이스가 이 모듈을 import하지 않고도 구분할 수 있게:
  ValueError = meta 를 읽을 수 없거나 지원하지 않는 형식 버전.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

FILES = ("meta.json", "weights.parquet", "events.parquet")
SUPPORTED_FORMAT_VERSIONS = (1,)


class InfluenceStore:
    def __init__(self, runs: Path):
        self.root = Path(runs) / "influence"

    def _dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")
                      and all((p / f).exists() for f in FILES))

    def list_models(self) -> list[dict]:
        """완성된 결과 폴더의 meta 목록. 형식 버전이 다르거나 읽을 수 없으면 ValueError (조용히 건너뛰지 않음)."""
        out = []
        for d in self._dirs():
            try:
                m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise ValueError(f"meta.json 을 읽을 수 없음: {d.name} ({type(e).__name__})") from None
            if m.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
                raise ValueError(f"지원하지 않는 format_version {m.get('format_version')}: {d.name}")
            if m.get("model_version") != d.name:
                raise ValueError(f"폴더 이름과 model_version 이 다름: {d.name}")
            out.append(m)
        return out

    def read_weights(self, model_version: str) -> pd.DataFrame:
        return pd.read_parquet(self.root / model_version / "weights.parquet")

    def read_events(self, model_version: str, ticker: str) -> pd.DataFrame:
        """한 종목의 사건만 (Parquet 필터로 읽음)."""
        return pd.read_parquet(self.root / model_version / "events.parquet", filters=[("ticker", "==", ticker)])

    def write_model(self, model_version: str, meta: dict, weights: pd.DataFrame, events: pd.DataFrame) -> Path:
        """P3-12 학습 결과 저장 (테스트의 합성 결과도 이 함수로 쓴다). 같은 이름이 있으면 덮어쓰지 않는다."""
        final = self.root / model_version
        if final.exists():
            raise FileExistsError(f"이미 있는 model_version: {model_version}")
        tmp = self.root / f".tmp_{model_version}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            weights.to_parquet(tmp / "weights.parquet", index=False)
            events.to_parquet(tmp / "events.parquet", index=False)
            tmp.rename(final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return final
