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
import json
import math
import ipaddress
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path

from regime_lab.config import REPO_ROOT, Paths, load_paths


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _news_db_config() -> dict | None:
    path = os.environ.get('REGIME_NEWS_DB_CONFIG')
    if not path:
        return None
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    interval = config['poll_seconds']
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        raise ValueError('Invalid news polling interval')
    runtime = Path(config['runtime_dir'])
    if not runtime.is_absolute() or runtime.resolve().is_relative_to(REPO_ROOT.resolve()):
        raise ValueError('News snapshots must be outside the repository')
    if sorted(config['sources']) != ['n8n_disclosure', 'n8n_news']:
        raise ValueError('Only the approved C3 sources are supported')
    if not all(isinstance(config.get(key), str) and config[key] for key in ('env_file', 'client_executable')):
        raise ValueError('Missing database client configuration')
    return config


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

    auth_mode: str = "demo"
    supabase_url: str | None = None
    supabase_publishable_key: str | None = field(default=None, repr=False)
    auth_site_url: str = "http://127.0.0.1:8000"
    auth_state_dir: Path | None = None
    auth_admin_ids: list[str] = field(default_factory=list)

    news_db: dict | None = field(default=None, repr=False)

    def validate_auth(self):
        if self.auth_mode not in {"demo", "supabase"}:
            raise ValueError("REGIME_AUTH_MODE must be demo or supabase")
        if self.auth_mode == "demo":
            try:
                local = ipaddress.ip_address(self.host).is_loopback
            except ValueError:
                local = self.host == "localhost"
            if not local:
                raise ValueError("Demo authentication is only allowed on loopback")
            return
        url = urlsplit(self.supabase_url or "")
        site = urlsplit(self.auth_site_url)
        if url.scheme != "https" or not (url.hostname or "").endswith(".supabase.co") or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"} or url.port not in {None, 443}:
            raise ValueError("Set a valid HTTPS REGIME_SUPABASE_URL")
        if not self.supabase_publishable_key or not self.supabase_publishable_key.startswith("sb_publishable_") or any(c.isspace() for c in self.supabase_publishable_key):
            raise ValueError("Set REGIME_SUPABASE_PUBLISHABLE_KEY")
        if self.auth_state_dir is None:
            raise ValueError("Set REGIME_AUTH_STATE_DIR")
        try:
            local_site = ipaddress.ip_address(site.hostname or "").is_loopback
        except ValueError:
            local_site = site.hostname == "localhost"
        if not site.netloc or site.username or site.password or site.path not in {"", "/"} or site.query or site.fragment or not (site.scheme == "https" or (site.scheme == "http" and local_site)):
            raise ValueError("REGIME_AUTH_SITE_URL requires HTTPS or a loopback development URL")

    @classmethod
    def from_env(cls) -> "Settings":
        paths = load_paths()
        runs = os.environ.get("REGIME_RUNS_DIR")
        if runs:
            paths = Paths(paths.store, paths.sql_dump, paths.cache, Path(runs), paths.ext_store)
        provider = os.environ.get("REGIME_LLM_PROVIDER", "openai").strip().lower()
        key_env = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        return cls(
            paths=paths,
            news_db=_news_db_config(),
            sample=_flag("REGIME_SAMPLE"),
            llm_provider=provider,
            llm_model=os.environ.get("REGIME_LLM_MODEL") or None,
            llm_api_key=os.environ.get(key_env.get(provider, "OPENAI_API_KEY")) or None,
            llm_timeout=float(os.environ.get("REGIME_LLM_TIMEOUT", "30")),
            cors_origins=[o.strip() for o in os.environ.get("REGIME_CORS_ORIGINS", "").split(",") if o.strip()],
            host=os.environ.get("REGIME_HOST", "127.0.0.1"),
            port=int(os.environ.get("REGIME_PORT", "8000")),
            auth_mode=os.environ.get("REGIME_AUTH_MODE") or ("supabase" if os.environ.get("REGIME_SUPABASE_URL") or os.environ.get("REGIME_SUPABASE_PUBLISHABLE_KEY") else "demo"),
            supabase_url=os.environ.get("REGIME_SUPABASE_URL"),
            supabase_publishable_key=os.environ.get("REGIME_SUPABASE_PUBLISHABLE_KEY"),
            auth_site_url=os.environ.get("REGIME_AUTH_SITE_URL", "http://127.0.0.1:" + os.environ.get("REGIME_PORT", "8000")),
            auth_state_dir=Path(os.environ["REGIME_AUTH_STATE_DIR"]) if os.environ.get("REGIME_AUTH_STATE_DIR") else None,
            auth_admin_ids=[v.strip() for v in os.environ.get("REGIME_AUTH_ADMIN_IDS", "").split(",") if v.strip()],
        )
