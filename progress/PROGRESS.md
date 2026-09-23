# PROGRESS

## 2026-09-23 10:55:39 KST (+09:00) — AI 보고서 품질 개선 (프롬프트 report-v2)

- 시작 시각: 2026-09-23 10:55:39 KST (+09:00)
- 목표: 실제 보고서(run 20260923T105058, gpt-6-luna, verified) 점검에서 나온 품질 문제를 고친다 — 내부 코드명·백틱 노출, 국면별 성과 미설명, 천 단위 쉼표 누락. 프롬프트·근거 수치·템플릿·화면 표시만 수정하고 숫자 검증 규칙은 바꾸지 않는다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. `llm/prompt.py`: 한국어 이름 추가, 규칙 보강, `report-v2` | 🟢 완료 | 근거 수치에 `patterns_ko`·`combine_ko`·`markets_ko`·`cap_groups_ko`·`split_judgement_ko`·`fdr_pass_ko`·`random_pass_ko`·`analysis_target_ko`, 셀별 `regime_ko`·`market_ko`·`cap_group_ko`, 충분 셀 수·양수/음수 셀 수 추가. 규칙: 천 단위 쉼표, 국면별 성과 1~2문장(요약 끝, 순위 금지), 코드값·필드명·true/false 금지, 마크다운 강조 금지, 14문장 이내. 3단 형식 유지 |
| 2. `llm/template.py`: 국면별 성과 문장 추가 | 🟢 완료 | 요약 끝에 '충분 셀 N개 중 양수 a개, 음수 b개 (국면·시장·시총 값…)' 입력 순서 인용. 실제 실행 4건 템플릿 verify 통과 |
| 3. `web/js/views/report.js`: 백틱 등 마크다운 기호 제거 | 🟢 완료 | 본문·소제목의 `**`·백틱 제거, 줄 앞 목록 기호 제거 |
| 4. 테스트 추가·전체 회귀 | 🟢 완료 | `test_llm_verify`에 3건 추가(한국어 이름, 템플릿 국면 문장 셀 있음/없음). api 56 passed, engine 111 passed |
| 5. 실제 AI 보고서 재확인 | 🟢 완료 | 사용자가 키 재입력 후 run 20260923T110040_my_strategy_c1a0f85e 보고서 생성: `verified`(gpt-6-luna), 숫자 전부 result.json과 일치, 코드명·백틱 없음, 쉼표 적용, 요약 끝 국면별 성과 문장. 남은 사소한 문제(샤프 '배' 단위, excluded/skipped 뜻 불명확, 분할 기준일 없음)는 `구현_계획.md` §0.6에 기록 |
| 6. 문서 기록 | 🟢 완료 | `구현_계획.md` §6.5에 report-v2 변경 기록 |

### 최종 결과

- 6개 항목 모두 완료.
- 테스트: api 56 passed(신규 3), engine 111 passed. 실제 실행 4건 템플릿 보고서 숫자 검증 통과.


---

## 2026-09-23 10:07:57 KST (+09:00) — 설정 화면 API 키 입력·좌측 사이드바 고정

- 시작 시각: 2026-09-23 10:07:57 KST (+09:00)
- 목표: 설정 탭에서 OpenAI 모델명·키를 입력해 서버 메모리에만 적용(재시작 시 키 소멸, 보고서 캐시는 유지)하고 AI 보고서 생성까지 연결하며, 폭 1050px 초과 화면에서 좌측 사이드바를 헤더 아래에 고정한다. 확정 명세(/srs 최종안) 기준.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. API: `/api/llm/config` 조회·적용·지우기 | 🟢 완료 | `routes/llm.py`(신규, 루프백만 PUT·DELETE, 키 마지막 4자리만 응답), `main.py`(라우터·환경변수 원래 값 보존), `llm/service.py`(사유 문구에 ⚙ 설정 안내) |
| 2. API 테스트 | 🟢 완료 | `api/tests/test_llm_config.py` 15 passed. 참고: `uv run pytest`(스크립트 실행기)는 저장소 이동 후 'trampoline failed to canonicalize script path'로 실패 → `uv run python -m pytest` 사용 |
| 3. Web: 설정 화면 AI 보고서 카드 | 🟢 완료 | `api.js`(호출 3개), `settings.js`(모델명·키(password)·적용·지우기·현재 설정·메모리 보관 안내), `i18n.js`(`set.ai.*` 24개, `rr.llm_*_not_configured`·`set.lead` 문구), `app.css`. i18n 사용 키 누락 0 |
| 4. Web: 좌측 사이드바 고정 | 🟢 완료 | `aside.nav-panel` sticky(top 78px, 높이 100vh−78px, 내부 스크롤). 1050px 이하 `position:static; height:auto`로 기존 유지 |
| 5. 전체 회귀 테스트 | 🟢 완료 | api 52 passed(기존 37 + 신규 15), engine 111 passed (`uv run --project <x> python -m pytest <x>`) |
| 6. 브라우저 확인 | 🟢 완료 | 8001 포트 서버(8000은 기존 서버라 유지). 설정 카드 미설정 표시, 빈 모델·키 없음 오류 표시, 더미 키 적용 → 상태 '화면 입력 · …7777', 보고서 `llm_error` 템플릿, 지우기 → 미설정, 서버 재시작 후 미설정, 브라우저 저장소·서버 로그에 키 없음, 영어 카드 한글 0. **발견·수정**: OpenAI 401 오류 문구에 키 앞 8자리·뒤 4자리가 실려 보고서 `warnings`에 노출 → `llm/openai.py`에서 `[redacted]` 처리 + 테스트 추가. 사이드바: 1920px 스크롤 전·중·후 좌표 차이 0, 1280px 0.03px(소수점 반올림), 맨 아래·중간에서 ⚙ 설정·전략 빌더 클릭 이동, 높이 450px 내부 스크롤, 1000px `static`·가로 줄 유지 |
| 7. 문서 기록 | 🟢 완료 | `구현_전_결정사항.md` E10(잠정)·T-9, `구현_계획.md` §0.1·§0.2·§6.4·§6.5·§8, `README.md`(설정 화면 방법·테스트 명령), `docs/readme-preview.md` |
| 8. AI 보고서 품질 점검 | 🟠 연기 | 사용자가 ⚙ 설정에 실제 모델명·키를 직접 입력해야 진행 가능 (규칙상 키 대리 입력 불가) |

### 최종 결과

- 1~7 완료, 8 연기(사용자 키 입력 대기).
- 테스트: api 53 passed(신규 `test_llm_config` 16), engine 111 passed.
- 계획 외 변경 1건: `api/src/regime_api/llm/openai.py` 오류 문구 키 조각 가림(명세 '응답에 키 원문 없음' 충족을 위해 필요), `.claude/launch.json`에 확인용 `regime-api-8001` 구성 추가.

---

## 2026-09-22 18:59:11 KST (+09:00) — 결과 카드 문구·사이드바 배치·차트 기간 조절 막대

- 시작 시각: 2026-09-22 18:59:11 KST (+09:00)
- 목표: 무작위 백분위 카드 보조 문구를 바꾸고, 사이드바 고지 문구를 설정 탭 아래로 옮기고, 자산곡선 아래 기간 조절 막대 높이를 키운다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 무작위 백분위 카드 보조 문구 | 🟢 완료 | 값 'N 백분위' 유지, 보조 '무작위 1,000회 중 N%보다 높음 · 거래 수' (영어: 'Nth percentile', 'beats N% of 1,000 random runs · n trades') |
| 사이드바 고지·구분선을 설정 탭 아래로 | 🟢 완료 | 순서: 메뉴 → ⚙ 설정 → 구분선·고지 문구(최하단) |
| 자산곡선 기간 조절 막대 높이 (수익률 분포 높이 원복) | 🟢 완료 | dataZoom 슬라이더 16→32px(자산곡선·종목 자산곡선·가격 차트), 해당 차트 330px. 수익률 분포는 300px로 원복 |
| 브라우저 확인 | 🟢 완료 | 사용자 서버(8000)에서 카드 문구·사이드바 순서·슬라이더 32px·차트 높이 측정 확인 |

### 최종 결과

- 4개 항목 모두 완료.
- GitHub `PEANUTBUTTER1001/regime-lab` 에 `Initial commit`, `feat: 분석 엔진·실행 API·웹 UI 첫 구현`을 커밋·푸시. 현재 브랜치 `develop`.
- 이후(2026-09-22): 루트 `README.md`를 소개·사용법 중심으로 재작성하고, 이전 README 를 `docs/readme-preview.md`(기술 상세)로 이관.
- 2026-09-23 인수인계 정리: `docs/구현_계획.md` §0·§2·§8, `docs/구현_전_결정사항.md` 갱신 (문서만 수정, 테스트 재실행 없음).

---

## 2026-09-22 18:47:02 KST (+09:00) — 로그인 화면 게스트 모드

- 시작 시각: 2026-09-22 18:47:02 KST (+09:00)
- 목표: 로그인 화면의 '계정 만들기' 옆에 '게스트 모드로 진행' 버튼을 추가해 가입 없이 사용할 수 있게 한다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 게스트 진입 버튼·상태·헤더 표시 | 🟢 완료 | 로그인 화면 '계정 만들기' 옆 '게스트 모드로 진행'(+안내 문구), sessionStorage `rl.guest`, 헤더 '게스트 모드' 배지·'로그인' 버튼. i18n 키 누락 0. 로그인 화면 하단 고지가 사이드바 폭만큼 밀리던 문제도 수정 |
| 브라우저 확인 | 🟢 완료 | 사용자 서버(8000)로 확인: 게스트 버튼 → 전략 빌더 진입, 배지 표시, 헤더 '로그인' → 로그인 화면·게스트 해제, 로그인 전 다른 화면 접근 시 로그인으로 이동 |

---

## 2026-09-22 18:30:08 KST (+09:00) — 설정 화면·언어 선택(한국어 기본, 영어)

- 시작 시각: 2026-09-22 18:30:08 KST (+09:00)
- 목표: 사이드바 최하단 설정 아이콘과 설정 화면을 추가하고, 화면 언어를 한국어(기본)·영어로 바꿀 수 있게 한다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| i18n 모듈·사전 (한국어·영어) | 🟢 완료 | `web/js/i18n.js` 키 389개, 기본 ko, localStorage `rl.lang`. 점검 스크립트: 사용 키 누락 0, 영어 값 한글 0 |
| 전 화면 문구 적용 (공통 프레임·로그인·빌더·진행·결과·종목·보고서·브리핑·차트) | 🟢 완료 | 오류 제목·문구는 코드별 번역, 지표 정의·패턴명·국면·청산 사유 번역. AI 보고서 본문은 한국어 유지(FR-L1) |
| 설정 화면·사이드바 설정 아이콘 | 🟢 완료 | 사이드바 최하단 ⚙ 설정(`#/settings`), 언어 라디오(한국어/English), 데이터 정보(읽기 전용) |
| 결정 기록 (design.md §3.3, 결정 문서) | 🟢 완료 | 결정 E9 추가, design.md §3.3 갱신, README |
| 브라우저 확인 (두 언어 전환·유지) | 🟢 완료 | 저장값 없을 때 한국어, 설정에서 English 선택 시 즉시 전환·저장, 새로고침 후 유지, 한국어로 결과·종목·보고서·설정 확인 |

### 최종 결과

- 5개 항목 모두 완료. 사용자 터미널에서 실행 중인 서버(8000)로 확인했으며 서버 재시작은 필요 없음(정적 파일만 변경).

---

## 2026-09-22 17:58:21 KST (+09:00) — 2단계 2B~2D API·LLM + 3단계 3A\~3F Web UI

- 시작 시각: 2026-09-22 17:58:21 KST (+09:00)
- 목표: `구현_계획.md` §6·§7에 따라 실행·결과 API, LLM 보고서(1차 OpenAI), 프로토타입 기반 HTML/JS Web UI를 구현하고 단계별 테스트로 검증한다. (ECharts 다운로드·파일 목록 일괄 승인: 2026-09-22 사용자)
- 생성 파일 목록(승인): `api/`(pyproject.toml, src/regime_api/{main,settings,errors,schemas,jobs,results}.py, routes/, llm/{provider,openai,gemini,anthropic,prompt,verify,template,cache}.py, tests/), `web/`(index.html, design-tokens.json, css/, js/{app,api,state,charts}.js, js/components/, js/views/, vendor/echarts.min.js)

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 2B-1 API 골격 (준비 프레임 적재, /api/meta) | 🟢 완료 | `api/` FastAPI, 백그라운드 적재(warming_up 503), 공통 오류 형식, OpenAPI |
| 2B-2 실행 API (작업 관리자·상태·취소) | 🟢 완료 | `jobs.py` 스레드 1개·409 busy·status 파일·재시작 복구·협조적 취소 |
| 2B-3 서버 측 입력 검증 | 🟢 완료 | pydantic 엄격 모드 + 엔진 범위 검증, 422 필드별 경로(strategies[i].field) |
| 2B-4 예상 대상 종목 수 | 🟢 완료 | `POST /api/universe/preview` |
| 2C-1 결과·종목 API | 🟢 완료 | result.json 제공, 종목 상세(준비 프레임 해시 불일치 시 409 stale_run) |
| 2C-2 브리핑 자리 | 🟢 완료 | `data_unavailable` + 사유 |
| 2D LLM 어댑터·보고서 검증 | 🟢 완료 | OpenAI 어댑터(모델·키 없으면 템플릿), 숫자 대조·권유 표현 차단·캐시·폴백. api pytest 37 passed (run_contract·disclaimer·llm_verify) |
| 3A 공통 프레임·토큰·Login | 🟢 완료 | `web/` 프로토타입 CSS 기반, design-tokens.json, 해시 라우터·로그인 게이트(UI만), 제목 포커스 이동, ECharts 6.1.0 로컬 포함 |
| 3B Strategy Builder·Progress | 🟢 완료 | /api/meta 기본값, 청산 신호·ATR 제거, trailing Off 고정, 시총 다중 선택, 예상 종목 수, 전송 전·서버 검증 표시, 서버 단계·처리 수만 표시, 취소 |
| 3C Results | 🟢 완료 | 3중 검증(m), 성과 카드 10개, 자산곡선, 국면×시총 히트맵(표본 부족 빗금), 손익 분포, 기간 분할, 종목 표(거래 수 순). 엔진 result.json 에 equity 추가 |
| 3D Stock Detail | 🟢 완료 | 가격·MA·국면 배경·마커, 종목 자산곡선 vs 단순 보유, 거래 표. 요약 통계는 엔진에서 계산(stats) |
| 3E AI Report·Morning Briefing | 🟢 완료 | verified/fallback 구분·근거 수치·한계, 브리핑 Data unavailable |
| 3F QA (브라우저 확인·키보드·상태) | 🟢 완료 | 내장 브라우저 확인: 로그인 검증, 빌더 검증 오류, 실행→진행→결과 자동 이동, 취소, 결과·종목·보고서·브리핑, run_not_found·No results 상태, 1920/1280/1000px 가로 넘침 없음, Tab 순서·포커스 외곽선. 수정: 캐시로 옛 JS 로드(no-cache), 차트 라벨 겹침, 영어 UI 문구 통일(API·엔진 메시지), 접근성 이름. 한계: 자동화 도구로 Space/Enter 활성화는 검증 못함 |

### 최종 결과

- 13개 항목 모두 완료. 엔진 pytest 111 passed, API pytest 37 passed.
- 전종목 실제 실행(bb_lower_recover) 결과가 CLI 배치와 일치(43,338건, +0.32%, 무작위 백분위 39.7).
- 제품명: 2026-09-22 사용자 결정으로 `regime-lab` 적용 (web/js/config.js, index.html).
- 남은 사용자 조치: OpenAI 모델명·키(REGIME_LLM_MODEL·OPENAI_API_KEY) 설정 후 실제 보고서 확인, V1·V2·A2-2·U3-1 확인, E7·E8 결정. 스크린리더 실사용 검수는 미실시.

---

## 2026-09-22 17:32:56 KST (+09:00) — 2단계 2A 엔진 보강 (E1~E4)

- 시작 시각: 2026-09-22 17:32:56 KST (+09:00)
- 목표: 승인된 권장안 E1~E4를 결정 문서에 기록하고, 엔진에 전략 입력 확장·진행/취소 훅·단일 실행 3중 검증·결과 화면용 데이터를 구현한다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 결정 기록 (E1~E4) | 🟢 완료 | `구현_전_결정사항.md` §5.1 E1~E4 ✅ 확정 |
| 2A-1 전략 입력 확장 | 🟢 완료 | `runs.py` Strategy에 markets·period·min_avg_value_krw(≥5억)·cap_groups, 필드별 오류(StrategyError.errors), 신호일 기준 entry_mask |
| 2A-2 진행·취소 훅 | 🟢 완료 | `context.py`(RunContext·RecordingContext·RunCancelled, 7단계), 종목 루프·무작위 반복 진행 알림, 임시 폴더 → 완료 시 이동 |
| 2A-3 단일 실행 3중 검증 | 🟢 완료 | 실행마다 validate, m=요청 전략 수(비교 실행 지원), not_applicable, 무작위 진입일 기간 제한 |
| 2A-4 결과 화면용 데이터 | 🟢 완료 | `analysis/report.py` 손익 분포(고정 구간)·종목 표(거래 수 순)·분할 비교·종목 상세, `result.json` |
| 회귀·실측 (CLI 배치, 메모리) | 🟢 완료 | pytest 111 passed. 전종목 단일 전략 12.9초, 핵심 5종 비교 실행(m=5) 74.9초, 최대 메모리 2.39GB. m=5 검증 값이 이전 배치와 동일. CLI 비교 실행 확인. YAML 날짜 객체 입력 버그 수정. README·구현_계획 갱신 |

### 최종 결과

- 6개 항목 모두 완료. 결과 폴더 구조 변경: `runs/<run_id>/{meta,result,universe}.json`, `validation.parquet`, `strategies/<name>/*` (한 run_id = 전략 1개 이상, m = 전략 수).
- 남은 결정: E5~E8 (LLM 모델·키, 웹 치수, Should 기능 범위, Unity 전환 시점), V1·V2·A2-2·U3-1.

---

## 2026-09-22 16:24:18 KST (+09:00) — 0단계 준비 및 1단계 분석 엔진 S0~S12

- 시작 시각: 2026-09-22 16:24:18 KST (+09:00)
- 목표: `구현_계획.md` 0단계(준비)와 1단계 분석 엔진 S0~S12를 확정 파라미터로 구현하고 단계별로 `uv run pytest` 결과를 보고한다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| S0 저장소·환경 준비 | 🟢 완료 | git init(커밋 없음), uv sync(pandas 3.0.6·numpy 2.5.3·pyarrow 25·duckdb 1.5.5), `uv run pytest` 3 passed, store/·multi_tables_db/ git-ignored 확인 |
| S1 데이터 로더 | 🟢 완료 | `data/loader.py`. 전종목 3,865,943행 6.9초·410MB, 가격 double 통일, 키 중복 0. pytest 8 passed |
| S2 워밍업 보정 | 🟢 완료 | `data/warmup.py`, `scripts/extract_sqldump.py`(18초, cache/ 출력). 연결 비율 5~95% 분위 0.9997~1.0 (검토 문서와 일치). 워밍업 내 >35% 점프 8종목은 연결 제외 → `warmup_max_abs_daily_ret: 0.35` 추가(보고). 연결 1,979종목. pytest 12 passed |
| S3 유니버스 | 🟢 완료 | `universe.py` apply_universe·사유 기록. 2026-09-18 유동성 통과 KOSPI 430/KOSDAQ 694 (검토 431/696, 차이는 상장 20일 미만 3종목 — U1로 어차피 제외). U3는 dept가 KOSDAQ만 채워져 있어 KOSPI 관리종목 미식별(보고). pytest 18 passed |
| S4 시총·유동성 그룹 | 🟢 완료 | 시총 30/40/30 (percent_rank) 검토 문서 §3.5 수치와 정확히 일치. 유동성 그룹: 2026-09-22 사용자 결정으로 시장별 20일 평균 거래대금 30/40/30 (`groups.liquidity_split`). 2026-09-18 KOSPI 241/320/241, KOSDAQ 521/694/521. 배치 재실행 120초, pytest 80 passed |
| S5 지표 | 🟢 완료 | `indicators.py`. kor_price 동일 입력 기준 MA·RSI·VWAP 100% 1e-2 이내, BB 35,185/35,186 (1행은 참조값 이상치). 확정: `rsi_smoothing: sma`(Wilder 불일치), `bb_ddof: 0`. 참조값은 cache/(Git 제외), 없으면 skip. pytest 31 passed |
| S6 국면 | 🟢 완료 | `regime.py`. 지수 국면 첫 산출일 KOSPI·KOSDAQ 모두 2021-07-21 (A2-1 일치). test_lookahead 3개 절단점 불변. rolling 을 종목별 독립 계산으로 변경(전체 프레임 rolling 의 누적 부동소수 오차가 입력 범위에 따라 달라지는 문제 수정). 전종목 지표 15.8초·국면 4.0초. pytest 37 passed |
| S7 패턴 5종 | 🟢 완료 | `patterns/` 공통 인터페이스 `signal(frame)->bool Series`, AND/OR 결합, 파라미터는 config만. 거래정지일 신호 없음(추가 규칙, 보고). 패턴별 수기 사례 + 절단 불변 테스트. pytest 50 passed |
| S8 백테스트 엔진 | 🟢 완료 | `backtest/engine.py`·`metrics.py`, `pipeline.py`(준비 프레임 캐시). test_execution 11·test_cost 3·백테스트 절단 불변 통과. 경계값 부동소수 오차(정확히 -8%) 수정. 전종목 ma_cross 52,722건 백테스트 6.7초+요약 1.2초, 준비 40초(캐시 후 재사용). 해석 항목(진입일 정지 스킵, end_of_data 집계 제외, 초과수익=net-지수, 샤프 거래 단위) 보고. pytest 67 passed |
| S9 실행 저장 | 🟢 완료 | `runs.py`·`cli.py`(`uv run regime-lab prepare/run/batch`), `strategies/` 핵심 5종+결합 예시. runs/<run_id>/ 에 meta·summary·trades·skipped·equity·universe 저장. 2회 실행 결과 동일 테스트 통과. pytest 69 passed |
| S10 집계 | 🟢 완료 | `analysis/aggregate.py` 시장/종목 국면 × 시장 × 시총그룹, 300건 미만 sample_insufficient, 국면 미산출 'unavailable' 셀. run 결과에 cells_*.parquet 저장. pytest 71 passed |
| S11 검증 | 🟢 완료 | `analysis/validation.py` 기간 분할(진입일 기준)·단측 t→BH(q 0.10)·무작위 1,000회(시드 고정). 판정 기준 미정 2건은 잠정안(`split_pass_judgements`, `random_bench_pass_percentile: 0.95`, proposed)으로 추가·보고. pytest 78 passed |
| S12 전종목 배치 | 🟢 완료 | `uv run regime-lab batch --no-cache`: 준비 36초 + 5종 각 7.5~10초 + 검증 24초 = 총 107초 (NFR-1 60분 충족). 단일 전략 캐시 없이 44초 (NFR-2 5분 충족). 결과 runs/ (Git 제외). 원본 시가·고가·저가 0 인 3행 발견 → 결측 처리(체결 불가), 무작위 벤치마크 inf 수정, 준비 캐시 키에 PREP_VERSION 추가. pytest 79 passed |

### 최종 결과 (2026-09-22)

- S0~S12 종료: 13단계 모두 완료 (S4 유동성 그룹은 사용자 결정 후 완료).
- 핵심 5종 검증 결과: 분석 대상(3중 검증 통과) 0종. breakout_20d·breakout_vol 은 무작위 벤치마크 통과, 기간 분할 역전·FDR 미통과.
- 사용자 확인 대기: 잠정 판정 기준 2건(split_pass_judgements, random_bench_pass_percentile), warmup_max_abs_daily_ret 0.35, RSI 평활(sma), KOSPI 관리종목 미식별.
