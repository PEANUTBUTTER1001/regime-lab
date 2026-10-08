"""외부 자료 docs Parquet 과 근거 검색 색인 캐시 (P3-11, 인프라).

수집(ingest)이 확정한 <ext_store>/docs/source=<s>/date=*/part-*.parquet 만 읽는다. 이름을 아직 못 바꾼 *.tmp 는
보지 않는다(수집 쪽 read_docs 와 다른 점. 복구 전 잠깐 생기는 차이). 원본·수집 결과는 읽기만 한다.
오류는 내장 예외로 낸다 — 유스케이스가 이 모듈을 import하지 않고도 구분할 수 있게:
  FileNotFoundError = 경로 없음·문서 없음, ValueError = manifest 스키마가 다름.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pyarrow.parquet as pq

SUPPORTED_SCHEMA_VERSIONS = (1,)  # docs/P3_수집_계약.md §3 schema_version


class DocsStore:
    def __init__(self, ext_store: Path | None, cache_dir: Path):
        self.ext_store = ext_store
        self.cache_dir = cache_dir / "rag"

    def _sources(self) -> list[Path]:
        if self.ext_store is None:
            raise FileNotFoundError("ext_store 경로가 설정되지 않음 (engine/config/paths.local.yaml)")
        base = self.ext_store / "docs"
        if not base.is_dir():
            raise FileNotFoundError(f"docs 폴더 없음: {base}")
        return sorted(p for p in base.glob("source=*") if p.is_dir())

    def manifests(self) -> dict[str, dict]:
        out = {}
        for d in self._sources():
            p = d / "_manifest.json"
            if not p.is_file():
                raise ValueError(f"manifest 없음: {p}")
            m = json.loads(p.read_text(encoding="utf-8"))
            if m.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                raise ValueError(f"지원하지 않는 schema_version {m.get('schema_version')}: {p}")
            out[d.name.removeprefix("source=")] = m
        return out

    def files(self) -> list[Path]:
        files = [f for d in self._sources() for f in sorted(d.rglob("*.parquet"))]
        if not files:
            raise FileNotFoundError("확정된 docs Parquet 이 없음")
        return files

    def fingerprint(self, files: list[Path]) -> str:
        h = hashlib.sha256()
        for f in files:
            h.update(f.relative_to(self.ext_store).as_posix().encode())
            h.update(str(f.stat().st_size).encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
        return h.hexdigest()[:16]

    def coverage(self) -> list[dict]:
        """수집 범위 이력 (P3-13 수집 상태 표시, FR-N4). 수집기가 커밋마다 내보낸
        <ext_store>/coverage/source=<s>/coverage_log.parquet 를 읽기만 한다. 파일이 없으면 빈 목록(기록 없음)."""
        if self.ext_store is None:
            raise FileNotFoundError("ext_store 경로가 설정되지 않음 (engine/config/paths.local.yaml)")
        base = self.ext_store / "coverage"
        files = sorted(base.glob("source=*/coverage_log.parquet")) if base.is_dir() else []
        return [r for f in files for r in pq.read_table(f).to_pylist()]

    def read(self, files: list[Path]) -> list[dict]:
        return [r for f in files for r in pq.read_table(f).to_pylist()]

    def read_cache(self, key: str) -> dict | None:
        p = self.cache_dir / f"index_{key}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def write_cache(self, key: str, payload: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self.cache_dir / f"index_{key}.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
