# regime-lab — 에이전트 작업 규칙
코스피·코스닥 일봉 패턴 백테스트·국면별 성과·3중 검증에 더해 역방향 조건 탐색, 봉 단위 예측·과거 검증, 공시·뉴스 근거 검색과 오프닝 브리핑을 제공하는 과거 데이터 분석 도구(투자 권유 아님) · 범위: `docs/SRS.md`(v2.0) · 설계 이유와 결정 기록: `docs/구현_계획.md`, `docs/구현_전_결정사항.md`
Source of Truth: `engine/pyproject.toml`, `api/pyproject.toml`, `engine/config/default.yaml` · 이 문서는 사용자 승인을 받아서만 개정한다.

## 1. 명령
- 설치: `uv sync --project engine`, `uv sync --project api`, `uv sync --project ingest`, 데이터 준비는 README "처음 한 번"의 SQL 덤프 추출(또는 §4 데이터 서버 동기화) 뒤 `uv run --project engine regime-lab prepare`
- 실행: `uv run --project api regime-api` (웹 http://127.0.0.1:8000), CLI `uv run --project engine regime-lab run <전략.yaml>`, 근거 검색 `uv run --project engine regime-lab rag-index|rag-search|rag-eval`, 봉 예측 `uv run --project engine regime-lab forecast-run --timeframe 1d`(KRX 원주가 일봉, D-2b. 통합 모델은 `--with-docs [--docs-mode observed|historical_assumed]`, 예정 P4-3), 오프닝 브리핑 `uv run --project engine regime-lab briefing-run [--at <ISO8601>]`(예정, P4-5). 8000 포트가 사용 중이면 `REGIME_PORT`로 다른 포트를, 30종목 빠른 확인은 `REGIME_SAMPLE=1`을 쓴다
- HTTP 뉴스 갱신: 데이터 서버 PC의 앱 실행 환경에만 `REGIME_NEWS_REFRESH=1` 설정 후 위 API 명령 실행(F1c·F1d). 접속값·운영 절차는 `docs/P3_DB_검색_자동갱신.md`. 팀원 PC에서는 켜지 않는다
- 로그인: 기본은 loopback 전용 데모 모드. 실제 인증은 `REGIME_AUTH_MODE=supabase`와 §4의 인증 환경변수를 설정한 뒤 같은 실행 명령 (README "로그인 모드", `docs/X3_로그인_설계.md`)
- 외부 자료 수집: `uv run --project ingest regime-ingest run|backfill|status` (`--source opendart|naver_news`, RSS는 `--source rss --rss-config <yaml>`(예정, P3-8.1). 키는 환경변수 `DART_API_KEY`·`NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET`로만, `ingest/README.md`)
- 테스트: `uv run --project engine python -m pytest engine`, `uv run --project api python -m pytest api`, `uv run --project ingest python -m pytest ingest` (스크립트 실행기 방식 uv run pytest는 저장소 이동 후 실패한다)
- 정적 검사: (미정). 웹 모듈 문법만 `node --check web/js/<파일>.js`로 확인한다

## 2. 구조 지도
역할 이름으로 판단한다. 폴더 이름으로 역할을 추측하지 않는다.

| 역할 | 경로 | 들어가는 것 |
|---|---|---|
| 진입 | `engine/src/regime_lab/cli.py`, `api/src/regime_api/routes/`, `api/src/regime_api/schemas.py`, `web/js/views/`, `ingest/src/regime_ingest/cli.py` | CLI 명령, HTTP 라우트·요청 스키마(인증 `api/src/regime_api/routes/auth.py` 포함), 웹 화면(로그인·비밀번호 재설정 `web/js/views/password.js` 포함) |
| 유스케이스 | `engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `api/src/regime_api/jobs.py`, `api/src/regime_api/llm/service.py`, `engine/src/regime_lab/search.py`, `engine/src/regime_lab/retrieval.py`, `ingest/src/regime_ingest/collect.py`, `ingest/src/regime_ingest/news_collect.py`, `api/src/regime_api/auth/service.py` | 실행·결과 저장, 준비 프레임 생성, 작업 관리(실행·탐색 공용 FIFO·동시 계산 1개·요청자별 제한·대기 취소, X13·F1b), 보고서·브리핑 문장 생성·검증·폴백, 역방향 탐색 실행·저장, 근거 검색 색인·as_of 검색·평가, 공시·뉴스 수집 흐름, 로그인·세션·CSRF·소유자 확인 |
| 유스케이스 | `engine/src/regime_lab/forecast_runs.py`, `engine/src/regime_lab/influence_runs.py`, `engine/src/regime_lab/briefing_runs.py` (예정) | 과거 as_of 예측 생성·저장(실행 결과 폴더 아래 forecasts, P2-7·P2-8, `docs/분봉_데이터_설계.md` §6.3·§6.4), 영향력 결과 조회(P3-13.3)·학습 실행(P3-12, 예정, 결과 `runs/influence/<model_version>/`), 오프닝 브리핑 스냅샷 생성·발행(같은 기준 시각 1회)·조회 (P4-5, 결정 G2-3·D-5). 저장소는 인프라 `engine/src/regime_lab/data/forecast_store.py`·`engine/src/regime_lab/data/influence_store.py`·`engine/src/regime_lab/data/briefing_store.py`(예정) |
| 도메인 | `engine/src/regime_lab/indicators.py`, `engine/src/regime_lab/regime.py`, `engine/src/regime_lab/universe.py`, `engine/src/regime_lab/patterns/`, `engine/src/regime_lab/backtest/`, `engine/src/regime_lab/analysis/`, `api/src/regime_api/llm/prompt.py`, `api/src/regime_api/llm/verify.py`, `api/src/regime_api/llm/template.py`, `ingest/src/regime_ingest/timing.py`, `ingest/src/regime_ingest/normalize.py`, `ingest/src/regime_ingest/dedup.py`, `ingest/src/regime_ingest/news.py`, `engine/src/regime_lab/rag/` | 지표·국면·유니버스·패턴·체결·집계·검증 계산, 보고서 근거 수치·숫자 대조·템플릿, 수집 시각·정규화·중복 판정, 뉴스 URL 식별·관측 버전·요청 계획·마감 시각 선택, 근거 검색 토큰화·순위·시점 선택·비슷한 제목 묶기·평가 지표 |
| 도메인 | `engine/src/regime_lab/bars.py`, `engine/src/regime_lab/forecast/`, `engine/src/regime_lab/briefing/` (예정), `engine/src/regime_lab/influence/` (예정), `engine/src/regime_lab/fusion/` (예정) | KRX 원주가 일봉 생성·예측 모델·채점 (P2·D-2b, 제품 1d), 분봉 정규화·시간봉 집계는 코드·시험 유지, 브리핑 집계 창·문서 선택·묶기·집계 (P4-5), 영향력 가중치 학습·채점 (P3-12), 가격·자료 특징 결합 `doc_features` (P4-2) |
| 포트 | `engine/src/regime_lab/context.py`, `api/src/regime_api/llm/provider.py`, `ingest/src/regime_ingest/ports.py`, `api/src/regime_api/auth/contracts.py` | 진행·취소 문맥, LLM 공급사 인터페이스, 수집 목록·저장소 경계, 인증 공급사·세션 저장소 인터페이스 |
| 인프라 | `engine/src/regime_lab/data/`(HTTP 뉴스는 `engine/src/regime_lab/data/news_db.py`, 공개 경계 `engine/src/regime_lab/data/loader.py`), `engine/src/regime_lab/config.py`, `engine/scripts/`, `api/src/regime_api/settings.py`, `api/src/regime_api/llm/openai.py`, `api/src/regime_api/llm/gemini.py`, `api/src/regime_api/llm/anthropic.py`, `api/src/regime_api/llm/cache.py`, `web/js/api.js`, `web/js/state.js`, `engine/src/regime_lab/presets.py`, `ingest/src/regime_ingest/sources/`, `ingest/src/regime_ingest/store.py`, `ingest/src/regime_ingest/config.py`, `api/src/regime_api/auth/store.py`, `api/src/regime_api/auth/supabase.py` | Parquet·SQL 덤프 읽기(외부 자료 docs Parquet·근거 검색 색인 캐시 포함), 데이터 서버 API → store Parquet 동기화, 뉴스 스냅샷 HTTP 수신·검증·Parquet 저장, 설정·환경변수, LLM 공급사 호출(OpenAI만 구현), 보고서 캐시, fetch·브라우저 저장소, 찾은 조합 저장소, OpenDART·네이버 뉴스·RSS HTTP·수집 저장소, 인증 세션·소유자 기록(SQLite)·Supabase 인증 HTTP(api 런타임 의존성 `httpx`) |
| 조립 | `api/src/regime_api/main.py`, `web/js/app.js`, `ingest/src/regime_ingest/app.py` | 앱 상태·라우터 연결·뉴스 백그라운드 갱신과 검증 완료 색인 교체, 웹 해시 라우터·공통 프레임, 수집기 시계·키·구현체 연결 |
| 공유 | `api/src/regime_api/errors.py`, `web/js/components/`, `web/js/i18n.js`, `web/js/charts.js`, `web/js/config.js` | 공통 오류 형식, UI 부품, 화면 문구, 차트 래퍼 |
| 테스트 | `engine/tests/`, `api/tests/`, `ingest/tests/` | 합성 데이터 `engine/tests/synth.py`. 원본이 필요한 테스트는 `data` 마커(없으면 skip). 인증 계약·소유자 `api/tests/test_auth_contract.py`·`api/tests/test_auth_ownership.py`는 실제 계정·키 없이 돈다(CI 필수) |

## 3. 의존 규칙
규칙은 §2의 역할 이름으로 적는다. 경로는 §2에만 둔다.
- [목표] (R1) 의존은 진입 → 유스케이스 → 도메인 방향으로만 흐른다. 인프라는 포트를 구현하며, 도메인·포트는 진입·인프라·조립을 import하지 않는다. 웹 화면(진입)은 인프라 `web/js/api.js`·`web/js/state.js`를 직접 쓴다(웹에는 유스케이스가 없다).
- [목표] (R2) 파일·DB·네트워크·IPC·시계·환경변수 접근은 인프라와 조립에서만 한다.
- [목표] (R3) 구현체 생성과 연결은 조립에서만 한다. 그 밖의 코드는 협력자를 생성자(또는 DI)로 받는다.
- [목표] (R4) 진입 코드는 입력 검증 → 유스케이스(또는 포트) 호출 → 출력 변환만 한다. 규칙과 계산을 두지 않는다.
- [목표] (R5) API는 엔진을 공개 모듈(`engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `engine/src/regime_lab/config.py`, `engine/src/regime_lab/context.py`, `engine/src/regime_lab/backtest/__init__.py`, `engine/src/regime_lab/analysis/report.py`, `engine/src/regime_lab/data/loader.py`, `engine/src/regime_lab/search.py`, `engine/src/regime_lab/presets.py`, `engine/src/regime_lab/retrieval.py`, `engine/src/regime_lab/forecast_runs.py`, `engine/src/regime_lab/briefing_runs.py`(예정), `engine/src/regime_lab/influence_runs.py`)로만 import한다. 그 밖의 엔진 모듈이 필요하면 §9에 따라 먼저 묻는다. 엔진은 API·웹을 import하지 않는다. 엔진·API는 `regime_ingest`를 import하지 않고 수집이 확정한 Parquet만 읽는다.
- [목표] (R6) 새 패턴은 `engine/src/regime_lab/patterns/core.py`의 `CORE_PATTERNS`에, 새 화면은 `web/js/app.js`의 `ROUTES`에 항목을 추가해서 넣는다. 분기문을 늘리지 않는다.

## 4. 무엇을 어디에
| 넣을 것 | 위치 |
|---|---|
| 수치 파라미터(패턴·청산·비용·국면·검증 기준·근거 검색) | `engine/config/default.yaml`. 조사 허용 목록·최소 기본어 길이는 `rag.query_particles`·`rag.particle_min_base_chars` |
| 로컬 데이터 경로 | `engine/config/paths.local.yaml` (Git 제외, 예시 `engine/config/paths.example.yaml`). 근거 검색 외부 자료 `ext_store`·HTTP 사본 `news_snapshots`는 저장소 밖 경로 |
| 작업 대기열 수치 | `engine/config/default.yaml`의 `jobs:` 절(`queue_max`·`per_user`). 준비 캐시와 무관, 동시 계산은 1개 유지 |
| HTTP 뉴스 갱신 설정 | `engine/config/default.yaml`의 `news_refresh:` 절(출처·주기·HTTP 한도). 활성화는 `REGIME_NEWS_REFRESH`, 접속값은 `REGIME_DATA_API_URL`·`REGIME_DATA_API_TOKEN` 환경변수로만 |
| 인증 수치·설정 | `engine/config/default.yaml`의 `auth:` 절. 모드·비밀값은 환경변수 `REGIME_AUTH_MODE`·`REGIME_SUPABASE_URL`·`REGIME_SUPABASE_PUBLISHABLE_KEY`·`REGIME_AUTH_SITE_URL`·`REGIME_AUTH_STATE_DIR`·`REGIME_AUTH_ADMIN_IDS`로만 (파일에 쓰지 않음, 인증 상태 폴더는 저장소 밖) |
| 외부 자료 수집 설정·경로 | `ingest/config/default.yaml`, 뉴스·RSS 예시 `ingest/config/news.example.yaml`·`rss.example.yaml`(예정, 출처 기본 비활성), 로컬 경로는 예시 `ingest/config/paths.example.yaml`을 복사한 paths.local.yaml (Git 제외, 수집 결과 `ext_store`는 저장소 밖, 종목 연결용 엔진 `master.parquet` 경로 `master_parquet`(예정)는 파일로만 읽음) |
| 출처 대장 | `docs/P3_출처_대장.md` (예정) — 출처별 이용 조건·보관·표시·LLM 전송 범위·활성 여부. 수집 활성화의 기준 |
| 브리핑 수치 | `engine/config/default.yaml`의 `briefing:` 절(예정, 기준 시각·집계 창·휴장일·필수 출처, 결정 D-5) |
| 통합 모델 수치 | 결합·가격 단독 비교 판정은 `engine/config/default.yaml`의 `forecast.fusion_compare`, 자료 특징은 `fusion:` 절(예정, 특징 목록·창 길이) — 결정 P4-3a |
| 예측 결과 형식 버전 | `engine/src/regime_lab/forecast_runs.py`의 `FORECAST_RUN_VERSION` (열·형식을 바꾸면 올리고 이전 형식 읽기를 유지한다) |
| 데이터 서버 접속 | 동기화 스크립트 `engine/scripts/sync_data_server.py`, 접속값은 환경변수 `REGIME_DATA_API_URL`·`REGIME_DATA_API_TOKEN`으로만(결정 F1a). 서버 PC 내부에서만 실행(F1d). 원본 store 를 덮어쓰지 않고 새 폴더에 받아 `paths.local.yaml` 로 전환 |
| 전략 파일(YAML) | `engine/strategies/` |
| API 라우트 | `api/src/regime_api/routes/` (라우터 연결은 조립) |
| 준비 프레임 캐시 버전 | `engine/src/regime_lab/pipeline.py`의 `PREP_VERSION` (지표·유니버스·국면·워밍업 계산을 바꾸면 올린다) |
| 근거 검색 색인 캐시 버전 | `engine/src/regime_lab/retrieval.py`의 `INDEX_VERSION` — 캐시는 루트 cache 폴더 아래, 토큰화·순위·시점 선택 규칙을 바꾸면 올린다 |
| LLM 프롬프트·근거 수치 | `api/src/regime_api/llm/prompt.py` (보고서를 바꾸면 `PROMPT_VERSION`, 브리핑을 바꾸면 `BRIEFING_PROMPT_VERSION`(예정)을 올린다) |
| 화면 문구 | `web/js/i18n.js` (한국어·영어 모두) |
| 패턴·조정 수치·청산 이름 [강제] | `web/js/i18n.js`의 `pat.*`·`pp.*`와 `api/src/regime_api/llm/prompt.py`의 `PATTERN_KO`·`PARAM_KO`·`EXIT_KO`에 같은 한국어 이름 (어긋나면 `test_i18n_keys`·`test_llm_verify` 실패) |
| 스타일·토큰 | `web/css/app.css`, `web/design-tokens.json` |
| 차트 색 | `web/js/charts.js`가 그릴 때 `web/css/app.css`의 CSS 토큰(라이트·다크)에서 읽는다. JS에 색 상수를 두지 않는다 (E19) |
| 웹 보기 설정(테마·열 너비·자리·즐겨찾기·최근 조회) | `web/js/state.js`의 브라우저 저장소 `rl.*` 키. 서버에 저장하지 않고, 저장이 막혀도 메모리로 동작 (E19·E20) |
| 삭제한 실행 결과 | `runs/.deleted/<id>/`로 옮긴다(영구 삭제 아님, 실행 중이면 거절, E20) |
| 기획·결정 문서 | `docs/` |
| 활성 진행 기록·지난 기록 보관 | `progress/PROGRESS.md` · `progress/archive/` (날짜별 원문 보관) |
| CI 워크플로 | `.github/workflows/tests.yml` — engine·api·ingest를 원본·키 없이 실행, 핵심 테스트가 빠지거나 skip되면 실패 (F2) |

## 5. 새 기능 레시피
- [목표] (R8) 작업 전에 관련 코드의 책임·의존성·기존 관례와, 작업의 WBS ID·결정 ID가 나오는 `docs/` 문서(결정 기록 `docs/구현_전_결정사항.md`, 해당 작업의 로드맵·계약 문서)를 확인하고 아래 순서를 따른다. 일반적인 모범 사례를 이유로 현재 구조를 일괄 교체하지 않는다.
1. 수치 파라미터를 설정 파일에 추가
2. 엔진 도메인 계산과 단위 테스트(미래참조 절단 불변 포함)
3. 엔진 유스케이스에 연결하고 결과 파일에 필드 추가
4. API 라우트·스키마
5. 웹 화면·문구(한국어·영어)
6. 조립에서 연결
7. 테스트: 도메인은 합성 데이터로, API는 계약 테스트로

## 6. 알려진 예외
- [목표] (R9) 아래 예외는 늘리지 않는다. 수정하는 파일에 예외가 있어도 요청 범위 밖이면 고치지 않고 별도 작업으로 제안한다. 예외 추가는 §9에 따라 승인받는다.

| 규칙 | 파일 | 사유 |
|---|---|---|
| R1 | `engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `api/src/regime_api/jobs.py`, `engine/src/regime_lab/search.py` | 설정·데이터 로딩을 인프라에서 직접 import (구현_계획 §3 파이프라인 구조) |
| R1 | `api/src/regime_api/llm/service.py`, `api/src/regime_api/llm/provider.py` | 보고서 캐시·OpenAI 구현을 직접 import |
| R1 | `engine/src/regime_lab/universe.py` | 상장 거래일 계산을 워밍업 모듈에서 가져옴 |
| R1 | `ingest/src/regime_ingest/cli.py` | 진입이 조립(`ingest/src/regime_ingest/app.py`)을 호출해 시계·기본 종료일을 맡김 (`docs/P3_수집_계약.md` §2) |
| R1 | `web/js/components/workspace.js` | 공유 부품이 인프라 `web/js/state.js`로 열 너비·자리 배치를 저장 (E19, 배치 저장이 이 부품의 책임) |
| R2 | `engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `api/src/regime_api/jobs.py`, `engine/src/regime_lab/search.py` | 결과 폴더·준비 프레임 캐시·작업 상태·탐색 결과 파일 기록이 흐름과 한 파일에 있음 (구현_계획 §3, E4) |
| R2 | `api/src/regime_api/routes/results.py`, `api/src/regime_api/routes/searches.py` | 라우트가 실행·탐색 결과 파일을 직접 읽음 |
| R3 | `api/src/regime_api/llm/service.py`, `api/src/regime_api/llm/provider.py` | 보고서 캐시·OpenAI 공급사를 조립 밖에서 생성 |

## 7. 하지 말 것
- [목표] 답변과 문서의 담당자·리뷰어·작성자·승인 기록에서는 팀원을 GitHub 계정 아이디로만 지목하고 실명은 쓰지 않는다. 계정·역할은 협업 규칙 §1을 기준으로 한다.
- [목표] (R7) 인터페이스·유스케이스·모듈·공통 컴포넌트는 구현이 2개 이상이거나 테스트 대역이 실제로 필요할 때만 만들고, 추가할 때는 해결할 문제(현재의 결합, 중복, 테스트 어려움, 변경 영향)를 밝힌다.
- [목표] (R11) 요청된 구현에 필요한 변경만 한다. 더 큰 리팩터링이 유익해 보여도 요청 범위 밖이면 같은 변경에 섞지 않고 먼저 제안으로 분리한다.
- 유스케이스 위에 서비스 계층을 한 겹 더 두지 않는다.
- "나중을 위한" 인터페이스·기반 클래스·범용 헬퍼를 만들지 않는다.
- 이벤트 버스·CQRS·DI 프레임워크를 도입하지 않는다.
- 벡터 DB·임베딩 모델 의존성을 engine·api에 넣지 않는다. 비교 실험은 저장소 밖에서 한다.
- t일 계산에 t+1일 이후 값을 쓰지 않는다. 국면·그룹은 신호일 값을 쓴다. 예측·영향력·통합 모델과 외부 자료 결합은 as_of 시점에 이용 가능했던 입력만 쓰고(게시 시각이 아님), 학습·검증·평가를 시간 순서로 나눈다. 근거 검색은 순위 통계(IDF·평균 길이)도 as_of에 보이는 문서로만 계산한다.
- 수치 파라미터를 코드 상수로 넣지 않는다. 조합별 패턴 수치는 허용 범위를 검증한 뒤 실행 메타데이터에 기록하고, 기본값은 `engine/config/default.yaml`에 둔다.
- 역방향 백테스트는 평가 구간을 후보 선택·재탐색에 쓰지 않고, 결과에 시도 후보 수·두 구간 성과·3중 검증을 함께 둔다. 승률만으로 '추천' 순위를 만들지 않는다.
- SRS v2.0 §1.4 R1(체결 규칙·승률 정의·3중 검증 기준·실행 결과 형식)의 의미는 R2를 구현하면서 바꾸지 않는다.
- 원본 데이터 (`data`, `multi_tables_db/`)를 수정·이동·커밋하지 않는다. `regime.duckdb`는 열지 않고 Parquet만 읽는다. 1분봉 원본·외부 자료 원문과 그것을 담은 평가셋·실험 데이터도 커밋하지 않는다 (공개 저장소). 원본·뉴스 사본의 수신·보관은 데이터 서버 PC만, 팀원 PC 자동 다운로드 제외(F1c·F1d). 팀원은 서버 웹·합성 데이터로 확인하며 기존 로컬 데모 사본 사용과 새 배포를 구분한다.
- 외부 자료는 출처 대장에서 활성인 출처만 수집하고, 원문은 권한 범위 안에서만 보관·표시·색인한다(지금 제품 근거 검색은 제목·회사명만). 수집 실패를 빈 정상 데이터로 남기지 않는다.
- `kor_price`는 워밍업 보정에만, `kor_indicators`는 테스트 참조값에만 쓴다.
- 분석 계산을 API·웹에 두지 않는다. 계산은 엔진에만 두고 웹은 API JSON만 표시한다.
- 목표가·투자 비중·종목 추천·매매 신호 문구를 만들지 않는다. 결과 응답에는 비권유 고지를 넣는다. 예측은 회색 구간과 방향으로만 표시하고 예상 OHLC 캔들은 그리지 않으며, 고정 문구 '과거 데이터로 계산한 통계적 예상 범위이며 실제 가격 전망이나 매매 신호가 아닙니다.'를 함께 표시한다(E17).
- LLM에는 집계 수치와 D-7에서 허용한 범위의 외부 자료 근거만 보낸다(D-7 결정 전에는 집계 수치만, 브리핑 v1도 집계 수치만이며 기사 제목·요약·URL은 화면 근거 목록으로만 보인다 — D-7 (v1)). 숫자·출처 대조와 권유 표현 차단을 약화하지 않는다. API 키를 파일·브라우저 저장소·로그·응답에 남기지 않는다.
- 화면 문구는 문구 사전 키로만 쓴다. 기본 언어는 한국어, 설정에서 영어를 고른다(E9, 구현_계획 §7.2의 'UI 영어'는 옛 기준). AI 보고서·브리핑 본문은 언어 설정과 관계없이 한국어다.
- 이관·보류 과제(야간 시세·전략 B, TSLA 예제, Should 기능 4종(E7), Unity(E8))는 구현하지 않고 `Data unavailable` 자리만 둔다. 뉴스·공시·브리핑·후순위 패턴은 SRS v2.0 R2 범위다.
- 함정: 엔진은 수집의 시점 선택 함수를 import할 수 없어 같은 규칙을 다시 둔다. 두 구현은 같은 경계 사례 테스트로 묶는다.
- 함정: 시각 문자열을 잘라 표시·비교하지 않는다. 출처마다 UTC·KST·날짜만인 값이 섞여 오므로 시간대를 붙여 파싱하고, 화면은 KST로 표시한다.
- 근거 검색의 비슷한 제목 묶음은 글자 유사도이므로 화면·문서에서 '같은 사건'으로 단정하지 않는다.
- [목표] 근거 검색의 반환 자격과 BM25 점수를 구분한다. 질의 원형 또는 받침·경계·최소 길이를 충족한 조사 1회 분리형으로 모든 조건을 만족한 문서만 후보 제한·묶기·MMR에 넘긴다(대표·similar 모두). 회사명 본체·혼합 식별자 전체를 보존하고 gram 부분 일치·중간 단어 생략으로 자격을 부여하지 않는다. 공백 차이 허용, `matched_tokens`는 점수 근거이며 완전 조건 증명 아님. 상세: `docs/RAG_검색_품질_개선_명세_20261015.md`.
- 함정: rolling 계산은 종목별로 한다. 전체 프레임에 한 번에 걸면 부동소수 누적 오차로 같은 종목 값이 입력 범위에 따라 달라진다.
- 함정: RSI는 14일 단순평균(`rsi_smoothing: sma`, A7-1)이다. 계획 문서의 "Wilder"를 보고 바꾸지 않는다.
- 함정: LLM 공급사 오류 문구에 API 키 조각이 섞여 나온다. 보고서 경고·로그로 내보내기 전에 가린다.
- 함정: 보고서·화면 문구에 설정·데이터에 따라 바뀌는 값(국면 산출 시작일 등)을 고정 문장으로 쓰지 않는다. 실행 결과나 설정에서 만든다(X5 뒤 '2021-07-21' 문장이 틀어짐).
- 함정: pytest `--basetemp`·`cache_dir`는 루트 `cache/` 아래에 둔다. `engine/`·`api/` 아래에 남은 임시 폴더는 `python -m pytest engine` 수집을 막는다. 상위 폴더가 없으면 먼저 만든다(없으면 임시 폴더 생성이 실패한다). Windows에서는 짧은 `cache/<실행별 이름>` 권장(중첩 fixture 경로가 260자 경계를 넘으면 파일 생성 실패).
- 함정: WBS CSV(`docs/plan/regime-lab-wbs-20261001.csv`) 실적은 대상 행만 바꾸고 나머지 줄은 원문을 유지한다(BOM·CRLF 포함). csv 라이브러리로 통째로 다시 쓰면 따옴표가 바뀌어 상관없는 줄까지 diff가 생긴다.

## 8. 완료 조건
- §1의 명령이 모두 통과한다.
- 웹 모듈을 바꿨으면 develop 최신과 합친 상태에서 앱을 열어 콘솔 오류가 0인지 확인한다(§1의 웹 문법 검사는 정의되지 않은 이름을 못 잡는다).
- 새 도메인 규칙에는 I/O 없는 단위 테스트가 있다. 새 지표·패턴·국면·체결 로직, 예측·영향력·통합 모델, 외부 자료 결합·근거 검색은 절단 불변 검사를 받는다(지표·패턴·국면·체결은 `engine/tests/test_lookahead.py`).
- §2·§4에 없는 위치에 파일을 만들지 않았다.
- 모듈·의존성·명령·경로를 바꿨다면 AGENTS.md를 직접 고치지 않고 `/agents-md`로 개정을 요청한다.

## 9. 멈추고 물어볼 때
- [목표] (R10) §2에 없는 역할·위치나 새 계층·모듈이 필요하면 구현 전에 멈추고 보고한다.
- [목표] (R12) 설계나 리팩터링을 제안할 때는 어떤 책임·의존성이 어떻게 달라지는지와 어떤 테스트로 동작 보존을 확인할지를 함께 제시한다.
- 새 외부 자료 출처를 추가하거나 출처의 보관·전송 범위를 바꿀 때
- 새 외부 의존성(라이브러리, 서비스)을 추가할 때. 웹은 빌드 없는 정적 ES 모듈이므로(구현_계획 §7.2) 번들러·프레임워크 도입도 여기에 해당한다
- 저장 형식(실행 결과 폴더·result.json 구조)·API·CLI 인자 같은 공개 계약을 바꿀 때
- `engine/config/default.yaml`의 확정 값(V1·V2·A2-2·U3-1, 2026-10-01 확정)을 바꿀 때
- 보류된 결정(E7 Should 기능, E8 Unity 전환, T-9 키 입력 방식)과 미결정 D-4·D-7(출처·권한, LLM 입력 확대)에 닿는 구현이 필요할 때 — 결정 전 구현하지 않는다
- AI 보고서 확인에 API 키가 필요할 때 — 사용자가 ⚙ 설정에 직접 입력한다(Supabase 모드에서는 `REGIME_AUTH_ADMIN_IDS`에 등록된 관리자만 가능). 서버를 재시작하면 화면 입력 키가 지워지므로 재시작 뒤에는 다시 요청한다
- 사용자가 실행 중인 서버를 끄거나 재시작해야 할 때
- §6 예외를 추가할 때
- 데이터 삭제처럼 되돌릴 수 없는 작업을 할 때
