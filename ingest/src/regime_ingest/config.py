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


def load_store_path() -> Path:
    local = CONFIG_DIR / "paths.local.yaml"
    with open(local if local.exists() else CONFIG_DIR / "paths.example.yaml", encoding="utf-8") as f:
        p = Path(yaml.safe_load(f)["ext_store"])
    return p if p.is_absolute() else REPO_ROOT / p
