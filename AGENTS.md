# regime-lab — 에이전트 작업 규칙
코스피·코스닥 일봉으로 기술적 패턴을 백테스트하고 국면별 성과와 3중 검증을 보여주는 과거 데이터 분석 도구(투자 권유 아님) · 설계 이유와 결정 기록: `docs/구현_계획.md`, `docs/구현_전_결정사항.md`
Source of Truth: `engine/pyproject.toml`, `api/pyproject.toml`, `engine/config/default.yaml` · 이 문서는 사용자 승인을 받아서만 개정한다.

## 1. 명령
- 설치: `uv sync --project engine`, `uv sync --project api`, 데이터 준비는 README "처음 한 번"의 SQL 덤프 추출 뒤 `uv run --project engine regime-lab prepare`
- 실행: `uv run --project api regime-api` (웹 http://127.0.0.1:8000), CLI `uv run --project engine regime-lab run <전략.yaml>`. 8000 포트가 사용 중이면 `REGIME_PORT`로 다른 포트를, 30종목 빠른 확인은 `REGIME_SAMPLE=1`을 쓴다
- 테스트: `uv run --project engine python -m pytest engine`, `uv run --project api python -m pytest api` (스크립트 실행기 방식 uv run pytest는 저장소 이동 후 실패한다)
- 정적 검사: (미정)

## 2. 구조 지도
역할 이름으로 판단한다. 폴더 이름으로 역할을 추측하지 않는다.

| 역할 | 경로 | 들어가는 것 |
|---|---|---|
| 진입 | `engine/src/regime_lab/cli.py`, `api/src/regime_api/routes/`, `api/src/regime_api/schemas.py`, `web/js/views/` | CLI 명령, HTTP 라우트·요청 스키마, 웹 화면 |
| 유스케이스 | `engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `api/src/regime_api/jobs.py`, `api/src/regime_api/llm/service.py` | 실행·결과 저장, 준비 프레임 생성, 작업 관리(1개·취소), 보고서 생성·검증·폴백 |
| 도메인 | `engine/src/regime_lab/indicators.py`, `engine/src/regime_lab/regime.py`, `engine/src/regime_lab/universe.py`, `engine/src/regime_lab/patterns/`, `engine/src/regime_lab/backtest/`, `engine/src/regime_lab/analysis/`, `api/src/regime_api/llm/prompt.py`, `api/src/regime_api/llm/verify.py`, `api/src/regime_api/llm/template.py` | 지표·국면·유니버스·패턴·체결·집계·검증 계산, 보고서 근거 수치·숫자 대조·템플릿 |
| 포트 | `engine/src/regime_lab/context.py`, `api/src/regime_api/llm/provider.py` | 진행·취소 문맥, LLM 공급사 인터페이스 |
| 인프라 | `engine/src/regime_lab/data/`, `engine/src/regime_lab/config.py`, `engine/scripts/`, `api/src/regime_api/settings.py`, `api/src/regime_api/llm/openai.py`, `api/src/regime_api/llm/gemini.py`, `api/src/regime_api/llm/anthropic.py`, `api/src/regime_api/llm/cache.py`, `web/js/api.js`, `web/js/state.js` | Parquet·SQL 덤프 읽기, 설정·환경변수, LLM 공급사 호출(OpenAI만 구현), 보고서 캐시, fetch·브라우저 저장소 |
| 조립 | `api/src/regime_api/main.py`, `web/js/app.js` | 앱 상태·라우터 연결, 웹 해시 라우터·공통 프레임 |
| 공유 | `api/src/regime_api/errors.py`, `web/js/components/`, `web/js/i18n.js`, `web/js/charts.js`, `web/js/config.js` | 공통 오류 형식, UI 부품, 화면 문구, 차트 래퍼 |
| 테스트 | `engine/tests/`, `api/tests/` | 합성 데이터 `engine/tests/synth.py`. 원본이 필요한 테스트는 `data` 마커(없으면 skip) |

## 3. 의존 규칙
규칙은 §2의 역할 이름으로 적는다. 경로는 §2에만 둔다.
- [목표] (R1) 의존은 진입 → 유스케이스 → 도메인 방향으로만 흐른다. 인프라는 포트를 구현하며, 도메인·포트는 진입·인프라·조립을 import하지 않는다.
- [목표] (R2) 파일·DB·네트워크·IPC·시계·환경변수 접근은 인프라와 조립에서만 한다.
- [목표] (R3) 구현체 생성과 연결은 조립에서만 한다. 그 밖의 코드는 협력자를 생성자(또는 DI)로 받는다.
- [목표] (R4) 진입 코드는 입력 검증 → 유스케이스(또는 포트) 호출 → 출력 변환만 한다. 규칙과 계산을 두지 않는다.
- [목표] (R5) API는 엔진을 공개 모듈(`engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `engine/src/regime_lab/config.py`, `engine/src/regime_lab/context.py`, `engine/src/regime_lab/backtest/__init__.py`, `engine/src/regime_lab/analysis/report.py`, `engine/src/regime_lab/data/loader.py`)로만 import한다. 그 밖의 엔진 모듈이 필요하면 §9에 따라 먼저 묻는다. 엔진은 API·웹을 import하지 않는다.
- [목표] (R6) 새 패턴은 `engine/src/regime_lab/patterns/core.py`의 `CORE_PATTERNS`에, 새 화면은 `web/js/app.js`의 `ROUTES`에 항목을 추가해서 넣는다. 분기문을 늘리지 않는다.

## 4. 무엇을 어디에
| 넣을 것 | 위치 |
|---|---|
| 수치 파라미터(패턴·청산·비용·국면·검증 기준) | `engine/config/default.yaml` |
| 로컬 데이터 경로 | `engine/config/paths.local.yaml` (Git 제외, 예시 `engine/config/paths.example.yaml`) |
| 전략 파일(YAML) | `engine/strategies/` |
| API 라우트 | `api/src/regime_api/routes/` (라우터 연결은 조립) |
| 준비 프레임 캐시 버전 | `engine/src/regime_lab/pipeline.py`의 `PREP_VERSION` (지표·유니버스·국면·워밍업 계산을 바꾸면 올린다) |
| LLM 프롬프트·근거 수치 | `api/src/regime_api/llm/prompt.py` (바꾸면 `PROMPT_VERSION`을 올린다) |
| 화면 문구 | `web/js/i18n.js` (한국어·영어 모두) |
| 스타일·토큰 | `web/css/app.css`, `web/design-tokens.json` |
| 기획·결정 문서 | `docs/` |

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
| R1 | `engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `api/src/regime_api/jobs.py` | 설정·데이터 로딩을 인프라에서 직접 import (구현_계획 §3 파이프라인 구조) |
| R1 | `api/src/regime_api/llm/service.py`, `api/src/regime_api/llm/provider.py` | 보고서 캐시·OpenAI 구현을 직접 import |
| R1 | `engine/src/regime_lab/universe.py` | 상장 거래일 계산을 워밍업 모듈에서 가져옴 |
| R2 | `engine/src/regime_lab/runs.py`, `engine/src/regime_lab/pipeline.py`, `api/src/regime_api/jobs.py` | 결과 폴더·준비 프레임 캐시·작업 상태 파일 기록이 흐름과 한 파일에 있음 (구현_계획 §3, E4) |
| R3 | `api/src/regime_api/llm/service.py`, `api/src/regime_api/llm/provider.py` | 보고서 캐시·OpenAI 공급사를 조립 밖에서 생성 |

## 7. 하지 말 것
- [목표] (R7) 인터페이스·유스케이스·모듈·공통 컴포넌트는 구현이 2개 이상이거나 테스트 대역이 실제로 필요할 때만 만들고, 추가할 때는 해결할 문제(현재의 결합, 중복, 테스트 어려움, 변경 영향)를 밝힌다.
- [목표] (R11) 요청된 구현에 필요한 변경만 한다. 더 큰 리팩터링이 유익해 보여도 요청 범위 밖이면 같은 변경에 섞지 않고 먼저 제안으로 분리한다.
- 유스케이스 위에 서비스 계층을 한 겹 더 두지 않는다.
- "나중을 위한" 인터페이스·기반 클래스·범용 헬퍼를 만들지 않는다.
- 이벤트 버스·CQRS·DI 프레임워크를 도입하지 않는다.
- t일 계산에 t+1일 이후 값을 쓰지 않는다. 국면·그룹은 신호일 값을 쓴다.
- 수치 파라미터를 코드 상수로 넣지 않는다.
- 원본 데이터(`store/`, `multi_tables_db/`)를 수정·이동·커밋하지 않는다. `regime.duckdb`는 열지 않고 Parquet만 읽는다.
- `kor_price`는 워밍업 보정에만, `kor_indicators`는 테스트 참조값에만 쓴다.
- 분석 계산을 API·웹에 두지 않는다. 계산은 엔진에만 두고 웹은 API JSON만 표시한다.
- 목표가·투자 비중·종목 추천·매매 신호 문구를 만들지 않는다. 결과 응답에는 비권유 고지를 넣는다.
- LLM에는 집계 수치만 보낸다. 숫자 대조·권유 표현 차단을 약화하지 않는다. API 키를 파일·브라우저 저장소·로그·응답에 남기지 않는다.
- 화면 문구는 문구 사전 키로만 쓴다. 기본 언어는 한국어, 설정에서 영어를 고른다(E9, 구현_계획 §7.2의 'UI 영어'는 옛 기준). AI 보고서 본문은 언어 설정과 관계없이 한국어다.
- 이관 과제(야간 시세·뉴스·브리핑·전략 B, TSLA 예제, 후순위 패턴, Unity)는 구현하지 않고 `Data unavailable` 자리만 둔다.
- 함정: rolling 계산은 종목별로 한다. 전체 프레임에 한 번에 걸면 부동소수 누적 오차로 같은 종목 값이 입력 범위에 따라 달라진다.
- 함정: RSI는 14일 단순평균(`rsi_smoothing: sma`, A7-1)이다. 계획 문서의 "Wilder"를 보고 바꾸지 않는다.
- 함정: LLM 공급사 오류 문구에 API 키 조각이 섞여 나온다. 보고서 경고·로그로 내보내기 전에 가린다.
- 프로젝트 고유 함정: (사람이 작성)

## 8. 완료 조건
- §1의 명령이 모두 통과한다.
- 새 도메인 규칙에는 I/O 없는 단위 테스트가 있다. 새 지표·패턴·국면·체결 로직은 `engine/tests/test_lookahead.py`의 절단 불변 검사를 받는다.
- §2·§4에 없는 위치에 파일을 만들지 않았다.
- 모듈·의존성·명령·경로를 바꿨다면 AGENTS.md를 직접 고치지 않고 `/agents-md`로 개정을 요청한다.

## 9. 멈추고 물어볼 때
- [목표] (R10) §2에 없는 역할·위치나 새 계층·모듈이 필요하면 구현 전에 멈추고 보고한다.
- [목표] (R12) 설계나 리팩터링을 제안할 때는 어떤 책임·의존성이 어떻게 달라지는지와 어떤 테스트로 동작 보존을 확인할지를 함께 제시한다.
- 새 외부 의존성(라이브러리, 서비스)을 추가할 때. 웹은 빌드 없는 정적 ES 모듈이므로(구현_계획 §7.2) 번들러·프레임워크 도입도 여기에 해당한다
- 저장 형식(실행 결과 폴더·result.json 구조)·API·CLI 인자 같은 공개 계약을 바꿀 때
- `engine/config/default.yaml`의 확정·확인 대기 값(V1·V2·A2-2·U3-1)을 바꿀 때
- 보류된 결정(E7 Should 기능, E8 Unity 전환, T-9 키 입력 방식)에 닿는 구현이 필요할 때 — 결정 전 구현하지 않는다
- AI 보고서 확인에 API 키가 필요할 때 — 사용자가 ⚙ 설정에 직접 입력한다. 서버를 재시작하면 화면 입력 키가 지워지므로 재시작 뒤에는 다시 요청한다
- 사용자가 실행 중인 서버를 끄거나 재시작해야 할 때
- §6 예외를 추가할 때
- 데이터 삭제처럼 되돌릴 수 없는 작업을 할 때
