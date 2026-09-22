"""설정 로딩: config/default.yaml + 선택적 override, 로컬 데이터 경로(paths.local.yaml)."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ENGINE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_DIR.parent
CONFIG_DIR = ENGINE_DIR / "config"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(override: dict | None = None, path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_DIR / "default.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return _deep_merge(cfg, override) if override else cfg


def config_hash(cfg: dict) -> str:
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Paths:
    store: Path
    sql_dump: Path
    cache: Path
    runs: Path


def load_paths() -> Paths:
    local = CONFIG_DIR / "paths.local.yaml"
    src = local if local.exists() else CONFIG_DIR / "paths.example.yaml"
    with open(src, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    def resolve(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else REPO_ROOT / q

    return Paths(**{k: resolve(v) for k, v in raw.items()})
