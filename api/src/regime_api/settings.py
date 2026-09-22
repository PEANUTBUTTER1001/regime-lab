"""API 설정. 데이터 경로는 engine/config/paths.local.yaml 을 재사용하고, 나머지는 환경변수로 받는다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| REGIME_SAMPLE | 0 | 1 이면 store/sample30 종목만 적재 (개발·테스트) |
| REGIME_RUNS_DIR | paths.runs | 실행 결과 폴더 |
| REGIME_LLM_PROVIDER | openai | LLM 공급사 (E5: openai 1차, gemini·anthropic 자리만) |
| REGIME_LLM_MODEL | (없음) | LLM 모델명. 없으면 템플릿 보고서로 동작 |
| OPENAI_API_KEY | (없음) | OpenAI 키. 없으면 템플릿 보고서로 동작 |
| REGIME_LLM_TIMEOUT | 30 | LLM 호출 제한 시간(초) |
| REGIME_CORS_ORIGINS | (없음) | 개발용 추가 허용 출처(쉼표 구분). 기본은 같은 출처만 |
| REGIME_HOST / REGIME_PORT | 127.0.0.1 / 8000 | 서버 바인딩 |
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from regime_lab.config import REPO_ROOT, Paths, load_paths


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


@dataclass
class Settings:
    paths: Paths
    sample: bool = False
    llm_provider: str = "openai"
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout: float = 30.0
    cors_origins: list[str] = field(default_factory=list)
    web_dir: Path = REPO_ROOT / "web"
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        paths = load_paths()
        runs = os.environ.get("REGIME_RUNS_DIR")
        if runs:
            paths = Paths(paths.store, paths.sql_dump, paths.cache, Path(runs))
        provider = os.environ.get("REGIME_LLM_PROVIDER", "openai").strip().lower()
        key_env = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        return cls(
            paths=paths,
            sample=_flag("REGIME_SAMPLE"),
            llm_provider=provider,
            llm_model=os.environ.get("REGIME_LLM_MODEL") or None,
            llm_api_key=os.environ.get(key_env.get(provider, "OPENAI_API_KEY")) or None,
            llm_timeout=float(os.environ.get("REGIME_LLM_TIMEOUT", "30")),
            cors_origins=[o.strip() for o in os.environ.get("REGIME_CORS_ORIGINS", "").split(",") if o.strip()],
            host=os.environ.get("REGIME_HOST", "127.0.0.1"),
            port=int(os.environ.get("REGIME_PORT", "8000")),
        )
