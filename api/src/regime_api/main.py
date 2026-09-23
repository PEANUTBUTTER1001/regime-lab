"""FastAPI 앱 (2단계). 엔진을 호출만 하며 계산 로직은 engine/ 에만 둔다 (구현 원칙 4).

실행: `uv run regime-api` (기본 http://127.0.0.1:8000, web/ 을 같은 출처로 서빙)
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from regime_api import errors
from regime_api.errors import ApiError
from regime_api.jobs import JobManager
from regime_api.llm.service import ReportService
from regime_api.settings import Settings
from regime_lab.config import load_config
from regime_lab.pipeline import Prepared, prepare

API_VERSION = "0.1.0"


class RevalidatingStaticFiles(StaticFiles):
    """web/ 정적 파일: 매 요청 재검증(no-cache)으로 수정된 스크립트가 캐시에 남지 않게 한다 (ETag 로 304 응답)."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


class AppState:
    def __init__(self, settings: Settings, prep: Prepared | None = None):
        self.settings = settings
        self.cfg = load_config()
        self.paths = settings.paths
        self.paths.runs.mkdir(parents=True, exist_ok=True)
        self.prep: Prepared | None = prep
        self.status = "ready" if prep is not None else "warming_up"
        self.load_error: str | None = None
        self.tickers = None
        if settings.sample:
            from regime_lab.data.loader import load_sample_tickers

            self.tickers = load_sample_tickers(self.paths.store)
        self.jobs = JobManager(self.paths.runs, self.require_prep, self.cfg, self.paths, self.tickers)
        self.reports = ReportService(settings, self.cfg, self.paths)
        # 설정 화면에서 바꾼 LLM 값은 메모리에만 둔다. 지우기 시 시작 시점의 환경변수 값으로 돌아간다 (E10).
        self.llm_env = (settings.llm_model, settings.llm_api_key)
        self.llm_source = "env"

    def load(self) -> None:
        try:
            self.prep = prepare(self.paths, self.cfg, self.tickers, use_cache=self.tickers is None)
            self.status = "ready"
        except Exception as e:  # noqa: BLE001
            self.status, self.load_error = "failed", f"{type(e).__name__}: {e}"

    def require_prep(self) -> Prepared:
        if self.prep is None:
            if self.status == "failed":
                raise ApiError(503, "data_unavailable", "The analysis data could not be loaded.", {"error": self.load_error})
            raise ApiError(503, "warming_up", "The server is still loading analysis data. Try again shortly.", retryable=True)
        return self.prep


def create_app(settings: Settings | None = None, prep: Prepared | None = None, serve_web: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    state = AppState(settings, prep)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state.prep is None:
            threading.Thread(target=state.load, name="prepare", daemon=True).start()
        yield

    app = FastAPI(title="regime-lab API", version=API_VERSION, lifespan=lifespan,
                  description="과거 데이터 분석 API. 투자 권유가 아닙니다 (Historical analysis — not investment advice).")
    app.state.rl = state
    errors.install(app)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                           allow_headers=["*"])

    from regime_api.routes import briefing, llm, meta, report, results, runs

    for r in (meta.router, runs.router, results.router, report.router, briefing.router, llm.router):
        app.include_router(r, prefix="/api")
    if serve_web and settings.web_dir.exists():
        app.mount("/", RevalidatingStaticFiles(directory=settings.web_dir, html=True), name="web")
    return app


def run() -> None:
    import uvicorn

    s = Settings.from_env()
    uvicorn.run(create_app(s), host=s.host, port=s.port)
