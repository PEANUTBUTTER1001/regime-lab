# regime-lab — 에이전트 작업 규칙
코스피·코스닥 일봉 패턴 백테스트·국면별 성과·3중 검증에 더해 역방향 조건 탐색, 봉 단위 예측·과거 검증, 공시·뉴스 근거 검색과 오프닝 브리핑을 제공하는 과거 데이터 분석 도구(투자 권유 아님) · 범위: `docs/SRS.md`(v2.0) · 설계 이유와 결정 기록: `docs/구현_계획.md`, `docs/구현_전_결정사항.md`
Source of Truth: `engine/pyproject.toml`, `api/pyproject.toml`, `engine/config/default.yaml` · 이 문서는 사용자 승인을 받아서만 개정한다.

## 1. 명령
- 설치: `uv sync --project engine`, `uv sync --project api`, `uv sync --project ingest`, 데이터 준비는 README "처음 한 번"의 SQL 덤프 추출 뒤 `uv run --project engine regime-lab prepare`
- 실행: `uv run --project api regime-api` (웹 http://127.0.0.1:8000), CLI `uv run --project engine regime-lab run <전략.yaml>`. 8000 포트가 사용 중이면 `REGIME_PORT`로 다른 포트를, 30종목 빠른 확인은 `REGIME_SAMPLE=1`을 쓴다
- 공시 수집: `uv run --project ingest regime-ingest run|backfill|status` (키는 환경변수 `DART_API_KEY`로만, `ingest/README.md`)
- 테스트: `uv run --project engine python -m pytest engine`, `uv run --project api python -m pytest api`, `uv run --project ingest python -m pytest ingest` (스크립트 실행기 방식 uv run pytest는 저장소 이동 후 실패한다)
- 정적 검사: (미정)

## 2. 구조 지도
역할 이름으로 판단한다. 폴더 이름으로 역할을 추측하지 않는다.

| 역할 | 경로 | 들어가는 것 |
|---|---|---|
| 진입 | `engine/src/regime_lab/cli.py`, `api/src/regime_api/routes/`, `api/src/regime_api/schemas.py`, `web/js/views/`, `ingest/src/regime_ingest/cli.py` | CLI 명령, HTTP 라우트·요청 스키마, 웹 화면 |
| 유스케이스 | `engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `api/src/regime_api/jobs.py`, `api/src/regime_api/llm/service.py`, `engine/src/regime_lab/search.py`, `ingest/src/regime_ingest/collect.py` | 실행·결과 저장, 준비 프레임 생성, 작업 관리(1개·취소), 보고서 생성·검증·폴백, 역방향 탐색 실행·저장, 공시 수집 흐름 |
| 유스케이스 | `engine/src/regime_lab/forecast_runs.py` (예정) | 과거 as_of 예측 생성·저장 (P2, `docs/분봉_데이터_설계.md` §4) |
| 도메인 | `engine/src/regime_lab/indicators.py`, `engine/src/regime_lab/regime.py`, `engine/src/regime_lab/universe.py`, `engine/src/regime_lab/patterns/`, `engine/src/regime_lab/backtest/`, `engine/src/regime_lab/analysis/`, `api/src/regime_api/llm/prompt.py`, `api/src/regime_api/llm/verify.py`, `api/src/regime_api/llm/template.py`, `ingest/src/regime_ingest/timing.py`, `ingest/src/regime_ingest/normalize.py`, `ingest/src/regime_ingest/dedup.py` | 지표·국면·유니버스·패턴·체결·집계·검증 계산, 보고서 근거 수치·숫자 대조·템플릿, 수집 시각·정규화·중복 판정 |
| 도메인 | `engine/src/regime_lab/bars.py`, `engine/src/regime_lab/forecast/` (예정) | 분봉 정규화·시간봉 집계, 예측 모델·채점 (P2) |
| 포트 | `engine/src/regime_lab/context.py`, `api/src/regime_api/llm/provider.py`, `ingest/src/regime_ingest/ports.py` | 진행·취소 문맥, LLM 공급사 인터페이스, 수집 목록·저장소 경계 |
| 인프라 | `engine/src/regime_lab/data/`, `engine/src/regime_lab/config.py`, `engine/scripts/`, `api/src/regime_api/settings.py`, `api/src/regime_api/llm/openai.py`, `api/src/regime_api/llm/gemini.py`, `api/src/regime_api/llm/anthropic.py`, `api/src/regime_api/llm/cache.py`, `web/js/api.js`, `web/js/state.js`, `engine/src/regime_lab/presets.py`, `ingest/src/regime_ingest/sources/`, `ingest/src/regime_ingest/store.py`, `ingest/src/regime_ingest/config.py` | Parquet·SQL 덤프 읽기, 설정·환경변수, LLM 공급사 호출(OpenAI만 구현), 보고서 캐시, fetch·브라우저 저장소, 찾은 조합 저장소, OpenDART HTTP·수집 저장소 |
| 조립 | `api/src/regime_api/main.py`, `web/js/app.js`, `ingest/src/regime_ingest/app.py` | 앱 상태·라우터 연결, 웹 해시 라우터·공통 프레임, 수집기 시계·키·구현체 연결 |
| 공유 | `api/src/regime_api/errors.py`, `web/js/components/`, `web/js/i18n.js`, `web/js/charts.js`, `web/js/config.js` | 공통 오류 형식, UI 부품, 화면 문구, 차트 래퍼 |
| 테스트 | `engine/tests/`, `api/tests/`, `ingest/tests/` | 합성 데이터 `engine/tests/synth.py`. 원본이 필요한 테스트는 `data` 마커(없으면 skip) |

## 3. 의존 규칙
규칙은 §2의 역할 이름으로 적는다. 경로는 §2에만 둔다.
- [목표] (R1) 의존은 진입 → 유스케이스 → 도메인 방향으로만 흐른다. 인프라는 포트를 구현하며, 도메인·포트는 진입·인프라·조립을 import하지 않는다. 웹 화면(진입)은 인프라 `web/js/api.js`·`web/js/state.js`를 직접 쓴다(웹에는 유스케이스가 없다).
- [목표] (R2) 파일·DB·네트워크·IPC·시계·환경변수 접근은 인프라와 조립에서만 한다.
- [목표] (R3) 구현체 생성과 연결은 조립에서만 한다. 그 밖의 코드는 협력자를 생성자(또는 DI)로 받는다.
- [목표] (R4) 진입 코드는 입력 검증 → 유스케이스(또는 포트) 호출 → 출력 변환만 한다. 규칙과 계산을 두지 않는다.
- [목표] (R5) API는 엔진을 공개 모듈(`engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `engine/src/regime_lab/config.py`, `engine/src/regime_lab/context.py`, `engine/src/regime_lab/backtest/__init__.py`, `engine/src/regime_lab/analysis/report.py`, `engine/src/regime_lab/data/loader.py`, `engine/src/regime_lab/search.py`, `engine/src/regime_lab/presets.py`)로만 import한다. 그 밖의 엔진 모듈이 필요하면 §9에 따라 먼저 묻는다. 엔진은 API·웹을 import하지 않는다. 엔진·API는 `regime_ingest`를 import하지 않고 수집이 확정한 Parquet만 읽는다.
- [목표] (R6) 새 패턴은 `engine/src/regime_lab/patterns/core.py`의 `CORE_PATTERNS`에, 새 화면은 `web/js/app.js`의 `ROUTES`에 항목을 추가해서 넣는다. 분기문을 늘리지 않는다.

## 4. 무엇을 어디에
| 넣을 것 | 위치 |
|---|---|
| 수치 파라미터(패턴·청산·비용·국면·검증 기준) | `engine/config/default.yaml` |
| 로컬 데이터 경로 | `engine/config/paths.local.yaml` (Git 제외, 예시 `engine/config/paths.example.yaml`) |
| 공시 수집 설정·경로 | `ingest/config/default.yaml`, 로컬 경로는 예시 `ingest/config/paths.example.yaml`을 복사한 paths.local.yaml (Git 제외, 수집 결과 `ext_store`는 저장소 밖) |
| 전략 파일(YAML) | `engine/strategies/` |
| API 라우트 | `api/src/regime_api/routes/` (라우터 연결은 조립) |
| 준비 프레임 캐시 버전 | `engine/src/regime_lab/pipeline.py`의 `PREP_VERSION` (지표·유니버스·국면·워밍업 계산을 바꾸면 올린다) |
| LLM 프롬프트·근거 수치 | `api/src/regime_api/llm/prompt.py` (바꾸면 `PROMPT_VERSION`을 올린다) |
| 화면 문구 | `web/js/i18n.js` (한국어·영어 모두) |
| 패턴·조정 수치·청산 이름 [강제] | `web/js/i18n.js`의 `pat.*`·`pp.*`와 `api/src/regime_api/llm/prompt.py`의 `PATTERN_KO`·`PARAM_KO`·`EXIT_KO`에 같은 한국어 이름 (어긋나면 `test_i18n_keys`·`test_llm_verify` 실패) |
| 스타일·토큰 | `web/css/app.css`, `web/design-tokens.json` |
| 기획·결정 문서 | `docs/` |
| CI 워크플로 | `.github/workflows/tests.yml` — engine·api·ingest를 원본·키 없이 실행, 핵심 테스트가 빠지거나 skip되면 실패 (F2) |

## 5. 새 기능 레시피
- [목표] (R8) 작업 전에 관련 코드의 책임·의존성·기존 관례를 확인하고 아래 순서를 따른다. 일반적인 모범 사례를 이유로 현재 구조를 일괄 교체하지 않는다.
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
- t일 계산에 t+1일 이후 값을 쓰지 않는다. 국면·그룹은 신호일 값을 쓴다. 예측·영향력·통합 모델과 외부 자료 결합은 as_of 시점에 이용 가능했던 입력만 쓰고(게시 시각이 아님), 학습·검증·평가를 시간 순서로 나눈다.
- 수치 파라미터를 코드 상수로 넣지 않는다. 조합별 패턴 수치는 허용 범위를 검증한 뒤 실행 메타데이터에 기록하고, 기본값은 `engine/config/default.yaml`에 둔다.
- 역방향 백테스트는 평가 구간을 후보 선택·재탐색에 쓰지 않고, 결과에 시도 후보 수·두 구간 성과·3중 검증을 함께 둔다. 승률만으로 '추천' 순위를 만들지 않는다.
- SRS v2.0 §1.4 R1(체결 규칙·승률 정의·3중 검증 기준·실행 결과 형식)의 의미는 R2를 구현하면서 바꾸지 않는다.
- 원본 데이터 (`data`, `multi_tables_db/`)를 수정·이동·커밋하지 않는다. `regime.duckdb`는 열지 않고 Parquet만 읽는다. 1분봉 원본·외부 자료 원문도 커밋하지 않는다 (공개 저장소).
- 외부 자료는 출처 대장에서 활성인 출처만 수집하고, 원문은 권한 범위 안에서만 보관·표시한다. 수집 실패를 빈 정상 데이터로 남기지 않는다.
- `kor_price`는 워밍업 보정에만, `kor_indicators`는 테스트 참조값에만 쓴다.
- 분석 계산을 API·웹에 두지 않는다. 계산은 엔진에만 두고 웹은 API JSON만 표시한다.
- 목표가·투자 비중·종목 추천·매매 신호 문구를 만들지 않는다. 결과 응답에는 비권유 고지를 넣는다. 예측은 회색 구간과 방향으로만 표시하고 예상 OHLC 캔들은 그리지 않으며, 고정 문구 '과거 데이터로 계산한 통계적 예상 범위이며 실제 가격 전망이나 매매 신호가 아닙니다.'를 함께 표시한다(E17).
- LLM에는 집계 수치와 D-7에서 허용한 범위의 외부 자료 근거만 보낸다(D-7 결정 전에는 집계 수치만). 숫자·출처 대조와 권유 표현 차단을 약화하지 않는다. API 키를 파일·브라우저 저장소·로그·응답에 남기지 않는다.
- 화면 문구는 문구 사전 키로만 쓴다. 기본 언어는 한국어, 설정에서 영어를 고른다(E9, 구현_계획 §7.2의 'UI 영어'는 옛 기준). AI 보고서·브리핑 본문은 언어 설정과 관계없이 한국어다.
- 이관·보류 과제(야간 시세·전략 B, TSLA 예제, Should 기능 4종(E7), Unity(E8))는 구현하지 않고 `Data unavailable` 자리만 둔다. 뉴스·공시·브리핑·후순위 패턴은 SRS v2.0 R2 범위다.
- 함정: rolling 계산은 종목별로 한다. 전체 프레임에 한 번에 걸면 부동소수 누적 오차로 같은 종목 값이 입력 범위에 따라 달라진다.
- 함정: RSI는 14일 단순평균(`rsi_smoothing: sma`, A7-1)이다. 계획 문서의 "Wilder"를 보고 바꾸지 않는다.
- 함정: LLM 공급사 오류 문구에 API 키 조각이 섞여 나온다. 보고서 경고·로그로 내보내기 전에 가린다.
- 함정: 보고서·화면 문구에 설정·데이터에 따라 바뀌는 값(국면 산출 시작일 등)을 고정 문장으로 쓰지 않는다. 실행 결과나 설정에서 만든다(X5 뒤 '2021-07-21' 문장이 틀어짐).
- 함정: pytest `--basetemp`·`cache_dir`는 루트 `cache/` 아래에 둔다. `engine/`·`api/` 아래에 남은 임시 폴더는 `python -m pytest engine` 수집을 막는다.

## 8. 완료 조건
- §1의 명령이 모두 통과한다.
- 새 도메인 규칙에는 I/O 없는 단위 테스트가 있다. 새 지표·패턴·국면·체결 로직, 예측·영향력·통합 모델, 외부 자료 결합은 절단 불변 검사를 받는다(지표·패턴·국면·체결은 `engine/tests/test_lookahead.py`).
- §2·§4에 없는 위치에 파일을 만들지 않았다.
- 모듈·의존성·명령·경로를 바꿨다면 AGENTS.md를 직접 고치지 않고 `/agents-md`로 개정을 요청한다.

## 9. 멈추고 물어볼 때
- [목표] (R10) §2에 없는 역할·위치나 새 계층·모듈이 필요하면 구현 전에 멈추고 보고한다.
- [목표] (R12) 설계나 리팩터링을 제안할 때는 어떤 책임·의존성이 어떻게 달라지는지와 어떤 테스트로 동작 보존을 확인할지를 함께 제시한다.
- 새 외부 자료 출처를 추가하거나 출처의 보관·전송 범위를 바꿀 때
- 새 외부 의존성(라이브러리, 서비스)을 추가할 때. 웹은 빌드 없는 정적 ES 모듈이므로(구현_계획 §7.2) 번들러·프레임워크 도입도 여기에 해당한다
- 저장 형식(실행 결과 폴더·result.json 구조)·API·CLI 인자 같은 공개 계약을 바꿀 때
- `engine/config/default.yaml`의 확정 값(V1·V2·A2-2·U3-1, 2026-10-01 확정)을 바꿀 때
- 보류된 결정(E7 Should 기능, E8 Unity 전환, T-9 키 입력 방식)과 미결정 D-1~D-5·D-7(탐색 범위, 예측 출력, 분봉 체결, 출처·권한, 브리핑 운영, LLM 입력)에 닿는 구현이 필요할 때 — 결정 전 구현하지 않는다
- AI 보고서 확인에 API 키가 필요할 때 — 사용자가 ⚙ 설정에 직접 입력한다. 서버를 재시작하면 화면 입력 키가 지워지므로 재시작 뒤에는 다시 요청한다
- 사용자가 실행 중인 서버를 끄거나 재시작해야 할 때
- §6 예외를 추가할 때
- 데이터 삭제처럼 되돌릴 수 없는 작업을 할 때
