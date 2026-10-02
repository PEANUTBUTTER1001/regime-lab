"""찾은 조합 저장 (P1-9, plan/01 작업 ③): 이름 붙인 전략 조합의 저장·불러오기·수정·삭제.

저장 위치: runs/presets/<preset_id>.json (이 PC 전체에서 공유, 브라우저·사용자 구분 없음 — 데모 로그인이라 권한을 전제하지 않음).

- 조합은 기존 전략 입력 형식 그대로 저장하되, 기본값을 모두 채운 값(effective)으로 고정한다(값 스냅샷, P1-9.3).
  나중에 설정 기본값이 바뀌어도 저장한 조합의 값은 바뀌지 않는다.
- 과거 실행 결과(runs/<run_id>/meta.json)와 탐색 기록(runs/searches/)은 실행 시점 값을 따로 담고 있으므로
  조합을 수정·삭제해도 바뀌지 않는다.
- 출처: 탐색 기록에서 저장(search_id·후보 id) 또는 직접 작성(manual).
- 동시 수정은 revision 으로 막는다. 다른 곳에서 먼저 고쳤으면 version_conflict.
- 파일은 임시 파일에 쓴 뒤 교체한다(쓰다 끊겨도 깨진 파일이 남지 않음).
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

from regime_lab.config import Paths, config_hash
from regime_lab.runs import Strategy, StrategyError

FORMAT = 1
NAME_MAX = 60
PRESET_ID_RE = re.compile(r"^p_[0-9a-f]{12}$")
SEARCH_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class PresetError(Exception):
    """code = API 오류 코드 (validation_failed·preset_not_found·name_conflict·version_conflict·
    search_not_found·candidate_not_found)."""

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


def presets_dir(paths: Paths) -> Path:
    return paths.runs / "presets"


def clean_name(name) -> str:
    """앞뒤 공백 제거·NFC 정규화. 1~60자, 제어 문자 금지. 한글 등 자유 문자 허용."""
    if not isinstance(name, str):
        raise PresetError("validation_failed", "Check the input values.", {"fields": {"name": "Must be text"}})
    n = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(n) <= NAME_MAX or any(unicodedata.category(c).startswith("C") for c in n):
        raise PresetError("validation_failed", "Check the input values.",
                          {"fields": {"name": f"1-{NAME_MAX} characters, no control characters"}})
    return n


def snapshot_strategy(strategy: dict, preset_id: str, cfg: dict) -> dict:
    """전략 입력을 검증하고 기본값을 채운 값으로 고정한다. 전략 이름은 preset_id 로 둔다(실행 결과 표시용)."""
    try:
        s = Strategy.from_dict({**strategy, "name": preset_id})
        s.validate(cfg)
    except StrategyError as e:
        raise PresetError("validation_failed", "Check the input values.",
                          {"fields": {f"strategy.{k}" if k and k != "_" else "strategy": v
                                      for k, v in e.errors.items()}}) from None
    return s.effective(cfg)


class PresetStore:
    """runs/presets/ 파일 저장소. 같은 프로세스 안의 동시 요청은 잠금으로 직렬화한다."""

    def __init__(self, root: Path, cfg: dict, clock=None):
        self.root, self.cfg = root, cfg
        self._now = clock or (lambda: datetime.now().isoformat(timespec="seconds"))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 파일
    def _path(self, preset_id: str) -> Path:
        if not isinstance(preset_id, str) or not PRESET_ID_RE.match(preset_id):
            raise PresetError("preset_not_found", f"Preset not found: {preset_id}")
        return self.root / f"{preset_id}.json"

    def _read(self, preset_id: str) -> dict:
        f = self._path(preset_id)
        if not f.exists():
            raise PresetError("preset_not_found", f"Preset not found: {preset_id}")
        return json.loads(f.read_text(encoding="utf-8"))

    def _write(self, p: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        f = self._path(p["id"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, f)

    def _all(self) -> list[dict]:
        if not self.root.exists():
            return []
        return [json.loads(f.read_text(encoding="utf-8")) for f in self.root.glob("p_*.json")]

    def _check_name_free(self, name: str, except_id: str | None = None) -> None:
        key = name.casefold()
        for p in self._all():
            if p["id"] != except_id and p["name"].casefold() == key:
                raise PresetError("name_conflict", "A preset with this name already exists.",
                                  {"name": name, "id": p["id"]})

    # ------------------------------------------------------------------ 조회
    def list(self) -> list[dict]:
        """최근 수정 순. 목록용 요약(전략 전체 포함)."""
        items = sorted(self._all(), key=lambda p: (p["updated_at"], p["id"]), reverse=True)
        return [{k: p[k] for k in ("id", "name", "source", "strategy", "created_at", "updated_at", "revision")}
                for p in items]

    def get(self, preset_id: str) -> dict:
        return self._read(preset_id)

    # ------------------------------------------------------------------ 저장·수정·삭제
    def create(self, name, strategy: dict, source: dict | None = None) -> dict:
        name = clean_name(name)
        now = self._now()
        with self._lock:
            self._check_name_free(name)
            pid = "p_" + config_hash({"name": name, "at": now, "n": len(self._all())})[:12]
            p = {"format": FORMAT, "id": pid, "name": name,
                 "strategy": snapshot_strategy(strategy, pid, self.cfg),
                 "source": source or {"kind": "manual"},
                 "data_as_of": self.cfg["data"]["as_of_date"], "config_hash": config_hash(self.cfg),
                 "created_at": now, "updated_at": now, "revision": 1}
            self._write(p)
        return p

    def update(self, preset_id: str, revision: int, name=None, strategy: dict | None = None) -> dict:
        """덮어쓰기·이름 바꾸기. revision 이 현재 값과 다르면 version_conflict (다른 곳에서 먼저 고침)."""
        with self._lock:
            p = self._read(preset_id)
            if revision != p["revision"]:
                raise PresetError("version_conflict", "This preset was changed elsewhere. Reload it and try again.",
                                  {"revision": p["revision"]})
            if name is not None:
                name = clean_name(name)
                self._check_name_free(name, except_id=preset_id)
                p["name"] = name
            if strategy is not None:
                p["strategy"] = snapshot_strategy(strategy, preset_id, self.cfg)
                p["data_as_of"], p["config_hash"] = self.cfg["data"]["as_of_date"], config_hash(self.cfg)
            p["updated_at"], p["revision"] = self._now(), p["revision"] + 1
            self._write(p)
        return p

    def delete(self, preset_id: str) -> None:
        with self._lock:
            f = self._path(preset_id)
            if not f.exists():
                raise PresetError("preset_not_found", f"Preset not found: {preset_id}")
            f.unlink()

    def create_from_search(self, searches_root: Path, search_id: str, candidate_id: str, name) -> dict:
        """탐색 기록의 후보를 저장한다. 출처에 탐색 id·후보 id·그 후보의 상태를 남긴다."""
        f = searches_root / search_id / "search.json" if SEARCH_ID_RE.match(search_id) else None
        if f is None or not f.exists():
            raise PresetError("search_not_found", f"Search not found: {search_id}")
        sj = json.loads(f.read_text(encoding="utf-8"))
        row = next((r for r in sj["rows"] if r["id"] == candidate_id), None)
        if row is None:
            raise PresetError("candidate_not_found", f"Candidate not found: {candidate_id}",
                              {"search_id": search_id})
        # 후보의 기간은 탐색 구간이다. 기간은 탐색 축이 아니므로 탐색 요청의 기간(없으면 전체)으로 저장한다.
        strategy = {k: v for k, v in row["strategy"].items() if k != "period"}
        if sj["request"]["filters"].get("period"):
            strategy["period"] = sj["request"]["filters"]["period"]
        source = {"kind": "search", "search_id": search_id, "candidate_id": candidate_id, "status": row["status"]}
        return self.create(name, strategy, source)
