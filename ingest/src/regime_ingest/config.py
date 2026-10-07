"""설정·경로 읽기 (인프라)."""

from __future__ import annotations

from pathlib import Path

import yaml

INGEST_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = INGEST_ROOT.parent
CONFIG_DIR = INGEST_ROOT / "config"


def load_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_DIR / "default.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# 저장소에서 원본 데이터가 놓이는 곳 (AGENTS.md: 수정·이동·커밋 금지). ext_store 는 이것들과 겹치면 안 된다
PROTECTED = ("data", "multi_tables_db", "store", "cache", "runs")


def check_store_path(p: Path, repo_root: Path = REPO_ROOT) -> Path:
    """해석된 절대 경로가 저장소(추적 파일 영역)·원본 데이터 폴더 안쪽이거나, 그것들을 포함하는 상위 폴더면 거부."""
    p, repo = p.expanduser().resolve(), repo_root.resolve()
    guarded = [repo, *(repo / name for name in PROTECTED)]
    for g in guarded:
        if p == g or g in p.parents or p in g.parents:
            raise ValueError(f"ext_store 가 저장소·원본 데이터 폴더와 겹침: {p} (저장소 밖 별도 폴더를 지정)")
    return p


def load_store_path() -> Path:
    local = CONFIG_DIR / "paths.local.yaml"
    with open(local if local.exists() else CONFIG_DIR / "paths.example.yaml", encoding="utf-8") as f:
        p = Path(yaml.safe_load(f)["ext_store"])
    return check_store_path(p if p.is_absolute() else REPO_ROOT / p)
