# regime-lab — 기술 상세

> 이 문서는 2026-09-22 루트 `README.md`에서 옮긴 **기술 상세 문서**입니다. 프로젝트 소개와 사용법은 루트 [`README.md`](../README.md)를 보세요.

> **Historical analysis — not investment advice.**
> 이 프로그램은 과거 데이터 분석 도구이며 투자 권유가 아닙니다. 목표가·비중·종목 추천·매매 신호를 제공하지 않습니다.

KOSPI·KOSDAQ 일봉 데이터로 기술적 패턴 전략을 백테스트하고, 그 성과가 **시장 국면(상승·횡보·하락) × 시장 × 시가총액 그룹**에 따라 어떻게 달라지는지 분석하는 엔진입니다. 결과가 우연인지 가리기 위해 **기간 분할·다중검정 보정(FDR)·무작위 벤치마크** 세 가지 검증을 거칩니다. 세 검증을 모두 통과한 전략만 "분석 대상"으로 표시합니다.

## 현재 범위

| 단계 | 내용 | 상태 |
|---|---|---|
| 1단계 분석 엔진 (`engine/`) | 데이터 로딩 → 워밍업 → 유니버스 → 지표·국면 → 핵심 패턴 5종 → 백테스트 → 집계 → 3중 검증, CLI 실행 | **구현 완료** |
| 2단계 API·LLM 보고서 (`api/`) | 실행 API(`run_id`)·상태·취소, 결과·종목 상세 조회, 예상 대상 종목 수, LLM 해설(1차 OpenAI, 숫자 대조·권유 차단·템플릿 대체) | **구현 완료** |
| 3단계 Web UI (`web/`) | HTML/JavaScript(프로토타입 기반)로 Login, Strategy Builder, Progress, Results, Stock Detail, AI Report, Morning Briefing(데이터 없음 표시) | **구현 완료** |
| Unity Web UI (`unity/`) | 3단계 Web UI를 Unity WebGL로 이관 (추후 과제 T-8, 발표 이후) | 추후 |
| 추후 과제 T-1~T-7 | 야간 시세·뉴스·Morning Briefing·전략 B, TSLA 참조 예제, 후순위 패턴 5종 등 | 제외 (`docs/구현_전_결정사항.md` §8) |

## 주요 기능

- **데이터**: `store/`의 수정주가 일봉·시가총액·지수·KRX 원주가·소속부·업종 스냅샷을 하나의 일봉 프레임으로 합칩니다. 상장폐지 333종목을 포함하므로 생존편향이 없습니다.
- **워밍업**: 2020-09-01 이전 250거래일은 SQL 덤프의 `kor_price` 구간을 종목별 비율로 보정해 연결합니다. 이 구간은 지표 계산에만 쓰고, 신호와 거래는 만들지 않습니다.
- **유니버스**: 매 신호일에 다음 조건을 판정하고, 제외된 경우 사유를 기록합니다.
  - 보통주
  - 첫 거래일로부터 250거래일 이상
  - 20일 평균 거래대금 5억 원 이상
  - 관리종목·투자주의환기종목 아님
- **그룹**: 신호일 기준 시장별 시가총액 30/40/30(대·중·소), 20일 평균 거래대금 30/40/30(유동성 대·중·소)
- **국면**: 200일선 위치와 200일선의 20일 변화율(±1%)로 판정합니다. 시장 국면은 지수 이력 한계로 2021-07-21부터 산출됩니다.
- **핵심 패턴 5종**: `ma_cross_5_20`, `breakout_20d`, `breakout_vol`, `rsi_rebound`, `bb_lower_recover`. 여러 패턴을 AND/OR로 결합할 수 있습니다.
- **백테스트**
  - 진입: 신호 다음 거래일 시가에 매수하고, 상한가(원주가 기준 +30%)면 건너뜁니다.
  - 청산: 손절 -8%, 익절 +20%, 최대 보유 20거래일을 매일 종가로 검사하고, 조건이 충족되면 다음 날 시가에 청산합니다.
  - 체결 불가일: 하한가·거래정지·거래량 0이면 다음 거래일에 다시 시도합니다.
  - 상장폐지 종목은 마지막 거래일 종가로 청산합니다.
  - 왕복 비용 0.30%를 차감하고, 소속 시장 지수 대비 초과수익을 기록합니다.
- **집계·검증**
  - 국면 × 시장 × 시총그룹 셀별로 집계하고, 300건 미만 셀은 "표본 부족"으로 표시합니다.
  - 검증 ① 2024-02-29 기준 기간 분할
  - 검증 ② 단측 t-검정 후 Benjamini-Hochberg FDR(q ≤ 0.10)
  - 검증 ③ 무작위 진입 1,000회 벤치마크(시드 고정)
- **재현성**: 실행마다 `run_id` 폴더에 전체 설정·설정 해시·데이터 기준일·입력 파일 지문·시드·거래 내역·요약을 저장합니다. 같은 입력이면 결과도 같습니다.

## 저장소 구조

```text
regime-lab/
├─ docs/                  기획·결정·계획 문서 (SRS, 구현_계획, 구현_전_결정사항, 데이터_검토_결과 등)
├─ engine/                1단계 분석 엔진 (Python 패키지 regime_lab)
│  ├─ config/
│  │  ├─ default.yaml         모든 수치 파라미터
│  │  ├─ paths.example.yaml   데이터 경로 템플릿
│  │  └─ paths.local.yaml     로컬 데이터 경로 (Git 제외)
│  ├─ scripts/extract_sqldump.py   SQL 덤프 1회성 추출 (워밍업·참조값)
│  ├─ src/regime_lab/
│  │  ├─ data/            loader.py(store 로딩), warmup.py(워밍업 보정·첫 거래일)
│  │  ├─ indicators.py    SMA·RSI·볼린저·VWAP_20
│  │  ├─ universe.py      유니버스 제외 규칙, 시총·유동성 그룹
│  │  ├─ regime.py        시장·종목 국면
│  │  ├─ patterns/        패턴 공통 인터페이스 + 핵심 5종
│  │  ├─ backtest/        체결·청산·비용(engine.py), 성과 지표·자산곡선(metrics.py)
│  │  ├─ analysis/        집계(aggregate.py), 3중 검증(validation.py), 화면용 결과(report.py)
│  │  ├─ pipeline.py      데이터 준비 파이프라인과 캐시
│  │  ├─ context.py       실행 단계·진행·취소 알림
│  │  ├─ runs.py          전략 스키마·실행·결과 저장
│  │  └─ cli.py           명령행 도구
│  ├─ strategies/         전략 파일 (YAML)
│  └─ tests/              pytest
├─ api/                   2단계 FastAPI (engine 을 경로 의존성으로 사용)
│  ├─ src/regime_api/     main.py(앱·정적 서빙), jobs.py(실행 관리), schemas.py(입력 검증), routes/, llm/
│  └─ tests/              test_run_contract, test_disclaimer, test_llm_verify, test_llm_config
├─ web/                   3단계 HTML/JS Web UI (빌드 없음, API 서버가 같은 주소로 서빙)
│  ├─ index.html, css/app.css, design-tokens.json
│  ├─ js/                 app.js(라우터), api.js, charts.js(ECharts), views/, components/
│  └─ vendor/             echarts.min.js 6.1.0 (Apache-2.0, ECHARTS_LICENSE.txt)
├─ progress/PROGRESS.md   작업 진행 기록
├─ store/                 원본 데이터 (읽기 전용, Git 제외)
├─ multi_tables_db/       SQL 덤프 (읽기 전용, Git 제외)
├─ cache/                 파생 데이터·준비 프레임 캐시 (Git 제외, 자동 생성)
└─ runs/                  실행 결과 (Git 제외, 자동 생성)
```

## 시작하기

### 1. 요구 사항

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- 메모리 8GB 이상. 전종목 준비 프레임은 약 0.95GB이며(실측), 계산 중에는 임시 사본 때문에 더 씁니다.
- 로컬 원본 데이터
  - `store/`: 약 660MB. 백테스트 본 데이터입니다.
  - `multi_tables_db/multi_tables_backup.sql`: 약 21GB. 워밍업 보정과 지표 참조값에만 씁니다.

원본 데이터는 저장소에 포함되지 않습니다. 팀 공유 방법은 추후 과제 T-6에서 정합니다.

### 2. 설치

```bash
cd engine
uv sync
```

### 3. 데이터 경로 설정

`engine/config/paths.local.yaml`을 만들고 경로를 적습니다. 파일이 없으면 `paths.example.yaml`을 씁니다. 상대 경로는 저장소 루트 기준입니다.

```yaml
store: store
sql_dump: multi_tables_db/multi_tables_backup.sql
cache: cache
runs: runs
```

### 4. SQL 덤프 추출 (최초 1회, 약 20초)

```bash
uv run python scripts/extract_sqldump.py
```

덤프를 한 번 순차로 읽어 `cache/`에 아래 파일을 만듭니다. 원본은 수정하지 않습니다.

| 파일 | 용도 |
|---|---|
| `cache/warmup/kor_price_warmup.parquet` | 2019-08 ~ 2020-09 워밍업 가격 |
| `cache/warmup/kor_price_first_date.parquet` | 종목별 첫 거래일 (상장 250거래일 판정) |
| `cache/reference/kor_indicators_sample30.parquet` | `test_indicators` 참조 지표 |
| `cache/reference/kor_price_sample30.parquet` | 참조 지표의 원 입력 가격 |

이 단계를 건너뛰면 워밍업 없이 실행됩니다. 그 경우 2020-09-01 이후 250거래일 동안 대부분 종목이 유니버스에서 빠지고, 워밍업·지표 참조값 테스트는 skip됩니다.

## 사용법

모든 명령은 `engine/` 디렉터리에서 실행합니다.

### 전략 실행

```bash
uv run regime-lab run strategies/core_breakout_vol.yaml
```

```text
[prepare] rows=4,383,965 tickers=3,095 0.8s
  [   0.0s] filter
  [   0.0s] load (in-memory prepared frame)
  [   0.0s] indicators (cached)
  [   0.0s] signals_execution
  [   1.8s] regime_join
  [   4.8s] aggregate
  [  11.2s] save
[run] breakout_vol: trades=33,098 ... | split=reversed fdr_p=0.43 random_pct=0.998 -> analysis_target=False
[done] m=1 ... -> runs/20260922T174325_breakout_vol_18ab7d8b
```

실행마다 3중 검증까지 수행합니다. 전종목 기준 단일 전략은 약 13초 걸립니다(캐시 사용, 요구 기준 5분).

### 비교 실행 (여러 전략을 한 요청으로)

```bash
uv run regime-lab run strategies/core_breakout_20d.yaml strategies/core_breakout_vol.yaml
```

전략 여러 개를 한 `run_id`로 실행합니다. 요청에 든 전략 수가 FDR 가족 크기 **m**이 됩니다. 전략을 하나씩 따로 실행해 비교하면 다중검정 보정이 약해지므로, 비교할 전략은 한 요청으로 묶는 것을 권장합니다.

### 핵심 5종 전종목 배치

```bash
uv run regime-lab batch
```

`strategies/core_*.yaml` 5개를 한 요청(m=5)으로 실행합니다. 캐시가 있으면 약 75초, 없으면 준비 시간 약 40초가 더 걸립니다(요구 기준 60분).

### 데이터 준비 캐시만 생성

```bash
uv run regime-lab prepare
```

지표·유니버스·국면을 한 번 계산해 `cache/prepared/`에 저장합니다. 이후 `run`·`batch`는 이 캐시를 읽고 신호·체결만 계산합니다. 캐시 이름에 데이터 기준일·설정 해시·입력 파일 지문이 들어가므로, 설정이나 데이터가 바뀌면 새로 만들어집니다.

### 옵션

| 옵션 | 설명 |
|---|---|
| `--sample` | `store/sample30.parquet`의 30종목만 사용 (빠른 확인용, 캐시 미사용) |
| `--no-cache` | 준비 캐시를 읽거나 쓰지 않고 매번 새로 계산 |

전략 입력 값이 허용 범위를 벗어나면 실행 전에 필드별 오류를 출력하고 종료 코드 2로 끝납니다.

## 웹 UI와 API 실행

```bash
uv run --project api regime-api
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. 서버가 시작할 때 준비 프레임을 읽습니다(캐시가 있으면 약 1초, 없으면 약 40초). 그동안 화면은 "Loading data" 상태를 표시합니다.

| 화면 | 내용 |
|---|---|
| Login | UI 흐름만 제공합니다. 실제 인증은 하지 않습니다. **게스트 모드로 진행**을 누르면 가입 없이 바로 사용합니다 |
| Strategy Builder | 패턴·결합 방식·청산 규칙·시장·기간·거래대금 하한·시총 그룹 입력, 예상 대상 종목 수, 입력 검증(전송 전 + 서버) |
| Progress | 서버가 보내는 단계·처리 수/전체 수·경과 시간만 표시하고, 실행을 취소할 수 있습니다 |
| Results | 3중 검증(m 표시), 성과 카드, 자산곡선, 국면 × 시총 히트맵(표본 부족 빗금), 손익 분포, 기간 분할, 종목별 표 |
| Stock Detail | 가격·이동평균·국면 배경·매매 마커, 종목 자산곡선과 단순 보유 비교, 거래 표 |
| AI Report | 검증을 통과한 AI 해설(`verified`) 또는 템플릿(`fallback`), 근거 수치, 데이터 한계 |
| Morning Briefing | 야간·뉴스 입력이 없어 `Data unavailable`과 사유만 표시합니다(추후 과제 T-2) |
| 설정 (⚙) | 화면 언어(한국어 기본 / English), **AI 보고서 모델명·API 키 입력**(서버 메모리에만 보관, 서버 종료 시 소멸 — 결정 E10 잠정, 추후 T-9 재논의), 데이터 기준일·범위 등 서버 데이터 정보 |

**환경변수** (모두 선택)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `REGIME_SAMPLE` | 0 | 1이면 30종목만 읽습니다(빠른 개발용) |
| `REGIME_LLM_MODEL` | 없음 | OpenAI 모델명. 없으면 템플릿 보고서로 동작합니다. ⚙ 설정 화면 입력 값이 우선합니다 |
| `OPENAI_API_KEY` | 없음 | OpenAI 키. 없으면 템플릿 보고서로 동작합니다. ⚙ 설정 화면 입력 값이 우선합니다 |
| `REGIME_LLM_TIMEOUT` | 30 | LLM 호출 제한 시간(초) |
| `REGIME_RUNS_DIR` | `runs/` | 실행 결과 폴더 |
| `REGIME_CORS_ORIGINS` | 없음 | 개발용 추가 허용 출처(쉼표 구분). 기본은 같은 출처만 허용합니다 |
| `REGIME_HOST` / `REGIME_PORT` | 127.0.0.1 / 8000 | 서버 주소 |

- API 명세는 `http://127.0.0.1:8000/docs`(OpenAPI)에서 볼 수 있습니다. 오류 응답은 모두 `{code, message, detail, retryable}` 형식입니다.
- `GET/PUT/DELETE /api/llm/config`: AI 보고서 모델명·키 조회·적용·지우기. 응답에는 키 마지막 4자리만 담고, PUT·DELETE는 이 PC(루프백) 요청만 받습니다(`403 local_only`). 적용한 값은 서버 메모리에만 있고 재시작하면 환경변수 값으로 돌아갑니다. 보고서 캐시(`cache/llm_reports/`)는 유지되며 키를 담지 않습니다.
- 실행은 한 번에 1개만 할 수 있습니다. 실행 중 새 요청은 `409 busy`와 현재 `run_id`를 받습니다.
- 서버는 기본적으로 `127.0.0.1`에만 열립니다. 로그인이 실제 인증이 아니므로 외부에 공개하지 마세요.
- 제품명 표기(`regime-lab`)는 `web/js/config.js` 한 곳에서 관리합니다.
- **화면 언어**: 기본은 한국어입니다. 사이드바 맨 아래 **⚙ 설정**에서 한국어/English를 바꿀 수 있으며, 선택은 이 브라우저에 저장됩니다. AI 보고서 본문은 언어 설정과 관계없이 한국어입니다. 번역 문구는 `web/js/i18n.js`에 있습니다.

## 전략 파일

`engine/strategies/`에 YAML로 작성합니다.

```yaml
name: example_breakout_and_rsi           # 영문·숫자·_·- 1~64자
patterns: [breakout_20d, rsi_rebound]   # 핵심 5종 중 선택
combine: or                              # and | or
exit:                                    # 생략하면 config 기본값 (-8% / +20% / 20거래일)
  stop_loss_pct: -8          # -50 ~ -1, 또는 null
  take_profit_pct: 20        # +1 ~ +200, 또는 null
  max_hold_days: 10          # 1 ~ 250 거래일 정수, 필수
  trailing_stop_pct: null    # 이번 릴리스는 null만 허용
# 아래는 선택 입력 (생략하면 전체). 모두 신호일 값으로 진입 후보만 거릅니다.
markets: [KOSDAQ]                        # KOSPI, KOSDAQ
period: {start: 2021-01-04, end: 2025-12-30}   # YYYY-MM-DD, 2020-09-01 ~ 2026-09-18
min_avg_value_krw: 1000000000            # 20일 평균 거래대금 하한(원), 5억 원 이상
cap_groups: [mid, small]                 # large, mid, small
```

- 입력 필터는 **진입 후보만** 거릅니다. 지표·국면·시총·유동성 그룹은 전종목 기준으로 계산한 값을 그대로 씁니다.
- `period`는 진입 신호를 받는 기간입니다. 기간 끝에 보유 중인 거래는 데이터 기준일까지 청산 규칙대로 진행합니다.
- 기간이 분할일(2024-02-29)을 사이에 두지 않으면 기간 분할 검증은 `not_applicable`이 되어 분석 대상에서 빠집니다. 무작위 벤치마크의 진입일도 입력 기간 안에서만 뽑습니다.

패턴 파라미터(이동평균 기간, 거래량 배수 등)는 전략 파일에서 바꿀 수 없습니다(SRS C-3). 허용되지 않은 키가 있으면 실행 전에 오류가 납니다.

### 핵심 패턴 정의 (신호일 t)

| 패턴 | 매수 신호 조건 |
|---|---|
| `ma_cross_5_20` | SMA5(t-1) ≤ SMA20(t-1) 이고 SMA5(t) > SMA20(t) |
| `breakout_20d` | 종가(t) > 직전 20거래일 고가 최댓값 |
| `breakout_vol` | `breakout_20d` 이고 거래량(t) ≥ 직전 20거래일 평균 × 2.0 |
| `rsi_rebound` | RSI14(t-1) < 30 이고 RSI14(t) ≥ 30 |
| `bb_lower_recover` | 종가(t-1) < 볼린저 하단(t-1) 이고 종가(t) ≥ 볼린저 하단(t) (20일, 2σ) |

거래정지일에는 신호를 만들지 않습니다.

## 실행 결과

`runs/<run_id>/` 폴더에 다음 파일이 저장됩니다. 실행 중에는 `runs/.tmp_<run_id>/`에 쓰고 완료 시 옮기므로, 취소·실패한 실행은 결과를 남기지 않습니다.

| 파일 | 내용 |
|---|---|
| `meta.json` | 전략(기본값 채움), FDR 가족 크기 m, 전체 설정과 해시, 데이터 기준일, 입력 파일 지문, 시드, 버전, 단계별 소요 시간 |
| `result.json` | 화면용 결과: 전략별 요약, 지표 정의(이름·단위·계산식), 3중 검증, 셀 집계, 손익 분포(-30%~+30% 2%p 고정 구간), 종목별 표본 표(거래 수 순), 기간 분할 비교, 비권유 고지 |
| `validation.parquet` | 전략별 검증: p-value·`fdr_pass`, 전·후반 성과·`split_judgement`, 무작위 백분위·`random_pass`, `fdr_family_size`, 최종 `analysis_target` |
| `universe.json` | 유니버스 제외 사유별 종목·일 수 |
| `strategies/<name>/trades.parquet` | 거래별 진입·청산 일자와 가격, 청산 사유, 재시도 횟수, 수익률, 비용, 초과수익, 신호일의 시장·종목 국면, 시총·유동성 그룹, 업종 |
| `strategies/<name>/skipped.parquet` | 진입을 건너뛴 신호와 사유 (`limit_up`, `halted_entry`, `no_next_day`) |
| `strategies/<name>/equity.parquet` | 동시 보유 종목 동일가중 일별 자산곡선 |
| `strategies/<name>/cells_market.parquet` / `cells_stock.parquet` | 시장 국면 / 종목 국면 기준 셀 집계, `sample_insufficient` 표시 |
| `strategies/<name>/summary.json` | 전략 입력과 성과 요약 |

기간 분할 판정 값: `maintained`·`weakened`(통과), `reversed`, `negative_both`, `sample_insufficient`, `not_applicable`(입력 기간이 분할일을 포함하지 않음). 전략이 1개면 순위 비교가 없어 `weakened`는 나오지 않습니다.

### 성과 지표

| 지표 | 정의 |
|---|---|
| 승률, 평균·중앙값 수익률 | 비용 차감 후 거래별 수익률(`net_ret`) 기준 |
| 평균 초과수익 | `net_ret` − 같은 보유기간 소속 시장 지수 수익률 |
| 손익비 | 평균 이익 / \|평균 손실\| |
| 샤프 | 거래별 `net_ret`의 평균/표준편차 (무위험수익률 0, 연율화 없음) |
| MDD | 동일가중 일별 자산곡선의 최대 낙폭 |
| 제외 거래 | 데이터 기준일에 아직 보유 중인 거래 (성과 집계에서 제외) |

## 설정 (`engine/config/default.yaml`)

모든 수치 파라미터는 이 파일에서 관리하며, 코드에 상수를 두지 않습니다. 값의 출처는 `docs/구현_전_결정사항.md`의 `결정` 열입니다. 바꾸려면 결정 문서를 먼저 갱신하세요. 실행마다 설정 전체가 `meta.json`에 저장됩니다.

| 절 | 주요 값 |
|---|---|
| `data` | 기준일 2026-09-18, 백테스트 시작 2020-09-01, 워밍업 250거래일 |
| `universe` | 보통주, 상장 250거래일, 20일 평균 거래대금 5억 원, 관리종목·투자주의환기 제외 |
| `groups` | 시총·유동성 30/40/30 |
| `regime` | 200일선, 20일 변화율 ±1%, 시장 국면 2021-07-21부터 |
| `indicators` | RSI 14 (단순평균 방식), 볼린저 20일·2σ·ddof 0, VWAP 20 |
| `exit` / `exit_limits` | 손절 -8, 익절 +20, 최대 보유 20 / 허용 범위 |
| `execution` | 상·하한가 ±30% (원주가), 왕복 비용 0.30% |
| `analysis` | 표본 기준 300건, 분할일 2024-02-29, FDR q 0.10, 무작위 1,000회, 시드 |

`status: proposed`로 표시된 값은 결정 문서에 기준이 없어 잠정 적용한 값입니다. 아래 "알려진 제한과 확인 대기 사항"을 참고하세요.

## 테스트

```bash
uv run --project engine python -m pytest engine
```

```bash
uv run --project api python -m pytest api
```

> 저장소 폴더를 옮긴 뒤에는 `uv run pytest`가 `trampoline failed to canonicalize script path`로 실패할 수 있어 `python -m pytest` 형식을 씁니다.

API 테스트는 30종목 준비 프레임과 임시 폴더로 서버를 띄우고, LLM은 가짜 공급사로 대신합니다(키 불필요).

| 테스트 | 검증 내용 |
|---|---|
| `test_lookahead` | 입력 끝을 잘라도 앞 구간의 지표·국면·신호·거래가 변하지 않음 (미래참조 방지) |
| `test_execution` | 진입·청산 가격, 상한가 스킵, 하한가·정지·거래량 0 재시도, 상장폐지 청산, 초과수익 |
| `test_indicators` | 수기 계산 사례 + `kor_indicators` 참조값 대조 (허용 오차 1e-2) |
| `test_cost` | 비용 차감액 = 거래 수 × 비용률, 동일가중 자산곡선·MDD |
| `test_split_fdr_random` | 분할 중복 없음, BH-FDR 수기 대조, 시드 고정 무작위 벤치마크 재현 |
| `test_run_contract` (api) | `run_id` 일관성, 상태·취소·결과, 입력 단위·필드별 422, busy, 재시작 복구, 예상 종목 수 |
| `test_disclaimer` (api) | 모든 결과 응답의 고지·거래 수, 브리핑 `data_unavailable` |
| `test_llm_verify` (api) | 입력에 없는 숫자·권유 표현 거부, 템플릿 대체, 캐시, OpenAI 어댑터 |
| `test_llm_config` (api) | 설정 화면 모델명·키 적용·조회·지우기, 키 원문 비노출(응답·캐시·오류 문구), 재시작 시 소멸, 422·403 |
| 그 외 | 로더, 워밍업, 유니버스·그룹, 국면, 패턴, 집계, 실행 저장·재현성 |

- `store/`가 없으면 데이터가 필요한 테스트는 skip되고 합성 데이터 테스트만 실행됩니다.
- `cache/`가 없으면 워밍업·참조값 테스트가 skip됩니다. 먼저 `scripts/extract_sqldump.py`를 실행하세요.

## 설계 원칙

1. **미래참조 금지**: t일 계산에는 t일까지의 값만 씁니다. 국면·그룹도 신호일 값을 기록하고, 모든 모듈을 `test_lookahead`로 검증합니다.
2. **데이터 소스 분리**: 본 데이터는 `store/`만 씁니다. `kor_price`는 워밍업 보정에만, `kor_indicators`는 테스트 참조값에만 씁니다.
3. **원본 읽기 전용**: `store/`, `multi_tables_db/`는 수정·이동·커밋하지 않습니다. `regime.duckdb`도 열지 않고 Parquet만 읽습니다.
4. **엔진 독립성**: 분석 엔진은 CLI만으로 동작합니다. 2단계 API는 엔진을 호출만 합니다.

## 알려진 제한과 확인 대기 사항

| 항목 | 내용 |
|---|---|
| 시장 국면 기간 | 지수 데이터가 2020-09부터라 2021-07-21 이전 진입 거래는 시장 국면이 `unavailable`입니다 (T-5) |
| KOSPI 관리종목 | `raw/krx_daily.dept`가 KOSPI에서 비어 있어 관리종목·투자주의환기 제외가 KOSDAQ에만 적용됩니다 |
| 워밍업 연결 제외 | 보정 구간에 하루 35%를 넘는 가격 점프가 있는 8종목은 워밍업을 연결하지 않습니다 (`warmup_max_abs_daily_ret`) |
| RSI 계산 방식 | 참조 테이블과 맞춰 14일 단순평균 방식으로 고정했습니다 (계획 문서의 "Wilder"와 다름) |
| 검증 통과 기준 (잠정) | 기간 분할: 양쪽 반기 평균 초과수익이 모두 양수 / 무작위 벤치마크: 95 백분위 이상 |
| 데이터 품질 | 원본에 시가·고가·저가가 0인 행 3건은 결측으로 읽어 체결 불가로 처리합니다 |
| 재수집 | `collect.py`·`build_db.py`가 없어 현재 `store/` 스냅샷으로만 진행합니다 (T-4) |

## 참고 문서

- `docs/SRS.md`: 요구사항 명세
- `docs/구현_계획.md`: 단계·작업 분해·완료 기준 (활성 인수인계 문서)
- `docs/구현_전_결정사항.md`: 확정 파라미터와 추후 과제
- `docs/데이터_검토_결과.md`: 데이터 근거
- `store/데이터_컬럼_정의서.md`: 컬럼 정의

---

*이 시스템은 과거 데이터 분석 도구이며 투자 권유가 아닙니다.*
