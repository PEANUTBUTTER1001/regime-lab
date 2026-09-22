"""보고서 캐시 (FR-L4). 키 = 근거 수치(전략·기준일 결과) + 프롬프트 버전 + 공급사 + 모델. 검증을 통과한 보고서만 저장한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cache_key(facts: dict, prompt_version: str, provider: str, model: str) -> str:
    blob = json.dumps({"facts": facts, "prompt": prompt_version, "provider": provider, "model": model},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class ReportCache:
    def __init__(self, root: Path):
        self.root = root

    def get(self, key: str) -> dict | None:
        f = self.root / f"{key}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def put(self, key: str, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
