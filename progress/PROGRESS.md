# PROGRESS

## 2026-10-07 16:49 KST (+09:00) — hongsungmin0315 백테스트 담당(오후): P3-7 OpenDART 수집기·계약 정렬·실데이터 회귀·팀 PR 리뷰

- 시작 시각: 2026-10-07 오후 (기록 시각 2026-10-07 16:49). Claude Code 작업, Agent Relay friends 채널 재개 — hchee99-codex(P3-4/P3-6 계약·리뷰), codex-01a0fb4b(G2·검증), seonghwan-claude(데이터 서버·P2)와 협업
- 목표: P3-7 을 계약에 맞춰 develop 에 넣고, 원본 없이 막혀 있던 실데이터 회귀를 데이터 서버로 돌린다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. P3-7 OpenDART 공시 목록 수집기 | 🟢 완료 | PR #21 병합. 새 최상위 패키지 `ingest/`(CLI `regime-ingest backfill·run·status`), `<ext_store>`(저장소 밖), `metadata_only`. 결정 §5.3 D-4(공시) A·B·C |
| 2. 리뷰 반영 (hchee99-codex 6건 + codex 2건) | 🟢 완료 | 변경 버전 available_at, 정정 후보는 과거 접수번호만, 잘못된 행 창은 partial·커서 미전진, 재시도 한도, 종목코드 형식, tmp→commit→확정·잠금 안 복구, KST 고정 오프셋(Windows), coverage 기반 창 건너뛰기 |
| 3. P3-6 계약 정렬 | 🟢 완료 | R1 포트(`ports.py`), 식별자·조회 창·시장 검증, 응답 완전성(total_count·고유 접수번호), 리디렉션 거부·서버 메시지 미기록, manifest(schema·policy 버전), `select_as_of`(observed / historical_assumed), coverage_log. ingest 67 passed |
| 4. P3-7.5 순방향 스케줄 | 🟢 완료 | README 에 cron·작업 스케줄러 예시(자동 등록 없음). 순방향 coverage 는 `forward`(일부)로 구분 |
| 5. 계약 문서 대리 게시 | 🟢 완료 | PR #23: `docs/P3_수집_계약.md`·`docs/P3_OpenDART_측정_20261007.md` (작성 hchee99, GitHub 403 으로 대리 게시) |
| 6. P3-8 뉴스 기반(hchee99) 브랜치 준비 | 🟡 PR 대기 | `feat/P3-8-news-bounded-input` 96edccc(작성자 hchee99, 패치 sha256 일치). ingest 105 passed. G2 뒤 develop 기준 PR |
| 7. 실데이터 회귀 (데이터 서버) | 🟢 완료 | Seonghwanaa 데이터 서버(조회 전용 API)에서 sample30 에 필요한 것만 받아 저장소 밖에 엔진 형식으로 저장. develop 3e4b4eb engine **338 passed / 0 skipped**, api **83 passed** |
| 8. 새 매수 패턴 실데이터 (sample30, 기본 청산) | 🟢 완료 | 거래·승률·평균 초과: macd_cross 823·40.3%·+0.69%, high_52w 174·43.7%·+1.78%, disparity_rebound 439·39.2%·−0.34%, stochastic_rebound 1,167·39.2%·−0.26% |
| 9. 팀 PR 리뷰 | 🟢 완료 | #24 GO(병합됨), #25 GO(국면 null 유지 테스트 요청), #26 GO, #27 GO + 빈 결과 dtype 불일치 [P2] |
| 10. X5 후속(실행 기록 지문) | ⚪ 불필요 | #17 `warmup_file_hashes` 가 `cache/warmup/*.parquet` 전체를 지문에 넣어 index_warmup 도 포함 |
| 11. P3-13 자료 보관함 화면 | ⏳ 대기 | P3-11·P3-12(PEANUTBUTTER1001) 선행 |

### 최종 결과

- develop 반영: #21(P3-7), #22(X6-2 정리), #23(계약 문서), #24(gitignore, Seonghwanaa)
- 남은 결정: G2 2차(`ingest/`·news·P2 bars/forecast 위치) — PEANUTBUTTER1001. X6-2 범위 확장·SRS FR-E3(트레일링) 문구 — PEANUTBUTTER1001
- 실수·정정: #21 에 브랜치 전환 실패로 `.DS_Store` 변경 커밋(4a881fe)이 들어가 다음 커밋에서 추적 해제·무시 처리(squash 병합). 채널에 "5년 소급 하루에 끝" 이라고 확정처럼 쓴 것을 단일 월 외삽으로 정정
- 미완료 실측(hchee99 계약 §7): 1거래일 발견 지연, 정정 10건 원관계, 5년 소급·무인 운영

---

## 2026-10-07 12:32 KST (+09:00) — hongsungmin0315 백테스트 담당: 재현성 보강·조건 확장(패턴 수치·선택 청산·매수 패턴 4종)

- 시작 시각: 2026-10-07 (기록 시각 2026-10-07 12:32). Claude Code 작업. 이날부터 Agent Relay 채널은 읽지 않고 사용자와만 진행
- 목표: 정방향 백테스트(조합 만들기)에서 고를 수 있는 조건을 늘리고, 실행 기록의 재현성을 보강한다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. 탐색 기본값 실측 조정 | 🟢 완료 | PR #16: 목표 승률 기본 40%(실데이터 탐색 구간 최고 42.8%), 후보당 2.5초(#14 이후 실측 2.27초) |
| 2. NFR-10 워밍업 파일 지문 | 🟡 리뷰 대기 | PR #17: 실행·탐색 기록의 입력 지문에 워밍업 원본 파일 포함 |
| 3. 핵심 5종 기간·배수 + 선택 청산 | 🟡 리뷰 대기 | PR #18: 이평 fast·slow, 돌파 lookback, RSI window, 볼린저 window·k 조정(기본값이면 결과 비트 단위 동일). 트레일링·본전·이평 이탈 청산(기본 꺼짐). 결정 §5.3 P1-4b·A8-1 |
| 4. 매수 패턴 4종 추가 | 🟡 리뷰·팀 승인 대기 | PR #19: macd_cross·high_52w·disparity_rebound·stochastic_rebound (10 → 14종). SRS FR-P1 확장이라 팀 승인 필요(결정 §5.3 X6-2). prep_hash 변경 |
| 5. 병합 순서 점검 | 🟢 완료 | #17 → #18 → #19 시험 병합: #19 에서 문서 2곳·import 1줄 충돌(양쪽 유지로 해결). 합친 상태 engine 300·API 30 passed |
| 6. 새 패턴 간이 실데이터 점검 | 🟢 완료 | sample30 `prices.parquet` 신호 수: macd 1,424·52주 224·이격도 640·스토캐스틱 3,328 (PR #19 댓글). 로컬 원본에 `raw/` 가 없어 `-m data` 테스트는 서버에서 필요 |
| 7. P3-7 OpenDART | ⏳ 대기 | D-4d(코드 위치)·D-4e(저장 위치)·P3-4·P3-6(hchee99) 선행. 범위는 C안(metadata_only)에 사용자 동의 |
| 8. P3-13 자료 보관함 화면 | ⏳ 대기 | P3-11·P3-12(PEANUTBUTTER1001) 선행 |

### 최종 결과

- 백테스트 담당 WBS 중 P1 전체·X2·X6 완료. 남은 P3-7·P3-13 은 선행 작업 대기
- 병합 요청 순서: #17 → #18 → #19. #18 병합 뒤 #19 를 develop 위로 rebase 예정
- 테스트(로컬, 원본 없음): #19 브랜치 engine 247 passed·39 skipped, api 29 passed·53 skipped

---

## 2026-10-02 17:40 KST (+09:00) — hongsungmin0315 백테스트 담당: D-3·P1 역방향 백테스트 전체·X2·X6·탐색 성능

- 시작 시각: 2026-10-01 10:00 KST (+09:00) (기록 시각 2026-10-02 17:40). Claude Code(seongmin-claude) 작업, Agent Relay friends 채널에서 PEANUTBUTTER1001·Seonghwanaa 에이전트와 협업
- 목표: WBS의 백테스트 담당 작업을 develop에 반영하고, 원본 데이터가 있는 PC에서 실데이터 검증을 받는다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. D-3 분봉 체결 여부 | 🟢 완료 | 일봉 다음 거래일 시가 체결 유지, 분봉 체결은 §8 T-10 (결정 문서 §5.3) |
| 2. P1-1 현 계약·승률 회귀·평가 시간 | 🟢 완료 | `test_win_rate`(승률 정의 경계 + 핵심 5종 고정값), 합성 시장 `make_market`, 후보당 시간 실측 |
| 3. P1-2 D-1 탐색 범위 | 🟢 완료 | 패턴 조합·결합·청산 이산 값 전수 탐색, 후보 200개 상한, 분할일 기준 탐색/평가 구간, FDR m = 시도한 전체 후보 수 (결정 문서 §5.3 D-1, plan/01 정렬) |
| 4. P1-3 공개 계약 | 🟢 완료 | 기존 계약 유지 + `/searches`·`/presets` 추가. 설계 문서 `docs/역방향_백테스트_설계.md` §4 |
| 5. P1-5·P1-6 탐색 엔진·실행·취소·기록 | 🟢 완료 | `search.py`: 탐색 구간은 분할일까지 데이터만, 상태 5종, 가장 가까운 후보, 취소 시 처리분 기록 `runs/searches/` |
| 6. P1-9 조합 저장 | 🟢 완료 | `presets.py`: `runs/presets/`, 기본값까지 채운 스냅샷, revision 충돌 방지 |
| 7. P1-7 API | 🟢 완료 | 탐색·조합 저장 API, 실행·탐색 작업 슬롯 공유, 합성 데이터 계약 테스트(원본 없이 실행) |
| 8. P1-8·P1-10·X2 화면 | 🟢 완료 | 역방향 탐색(입력·진행·결과), 저장 목록·빌더 연결, 실행 기록 목록. i18n 중복 키 검사 테스트 |
| 9. P1-4 패턴 수치 | 🟢 완료 | `pattern_params`(준비 프레임 밖 수치만, PREP_VERSION 영향 없음), 탐색 수치 축, 정수 검증 |
| 10. X6 후순위 패턴 5종 | 🟢 완료 | 정의 r1(PEANUTBUTTER1001 에이전트 초안), 2026-10-02 hongsungmin0315 승인. 참조 구현과 전체 신호 일치, 절단 불변. `patterns` 절 변경으로 prep_hash 변경 |
| 11. P1-11 수용 테스트 | 🟢 완료 | 합성 + 실데이터(sample30) 재현성·정방향 일치·기존 성과 불변 |
| 12. 실데이터 검증 | 🟢 완료 | PEANUTBUTTER1001 에이전트가 원본 PC에서 실행: engine 253·API 82 passed(실패·skip 0). 발견한 결함(거래 0건 거래 표 열 누락) 수정 |
| 13. 탐색 성능 (NFR-14) | 🟢 완료 | PR #14: 거래별 지수 수익률 조회를 한 번에 → 거래 결합 4.07→0.70초(옛 구현과 값 비트 단위 동일). 실데이터 57후보 실측 5.08초/후보(개선 전), 개선 후 재측정 대기 |
| 14. P3-7 OpenDART | ⏳ 대기 | D-4 미결정, P3-4·P3-5·P3-6(hchee99) 선행 |
| 15. P3-13 자료 보관함 화면 | ⏳ 대기 | P3-11·P3-12(PEANUTBUTTER1001) 선행 |

### 최종 결과

- develop 반영: PR #6(P1 엔진·API), #13(#7~#12 내용 재반영 — stacked PR이 중간 브랜치로만 병합된 것을 develop 기준으로 다시 올림), #14(성능). 중간 브랜치 8개 삭제
- 테스트(develop, 로컬 원본 없음): engine 215 passed·38 skipped, api 29 passed·53 skipped. 실데이터(원본 PC): engine 253·API 82 passed
- 남은 결정·조치: `PREP_VERSION` 3(X5 PR에서 X6와 함께 올리기로 Seonghwanaa 에이전트와 합의 중), 탐색 화면 기본 목표 승률(실데이터 기본 57후보 중 승률 50% 이상 0개 → 하향 검토), 실데이터 파일(raw·cache) 공유(F1)

---

## 2026-10-01 12:44:42 KST (+09:00) — SRS v2.0 승인 기록·G2 AGENTS.md 개정(1차)·팀원 공용 시작 안내

- 시작 시각: 2026-10-01 12:44:42 KST (+09:00) (기록 시각. 작업은 12:28부터 진행)
- 목표: 팀 승인 결과를 문서에 기록하고, 승인된 SRS v2.0 부록 A로 `AGENTS.md`를 개정하며, 팀원이 클론 직후 같은 순서로 작업을 시작할 수 있게 안내를 고친다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. SRS v2.0 승인 기록 | 🟢 완료 | 2026-10-01 팀 전원(hongsungmin0315·Seonghwanaa·hchee99·PEANUTBUTTER1001) 승인, PEANUTBUTTER1001 전달. `SRS.md` 머리말·§9, `구현_전_결정사항.md` E12·머리말, `구현_계획.md` §0.2·§10, `plan/README.md`에서 '승인 대기' 표기 제거 |
| 2. G2 `AGENTS.md` 개정 1차 (`/agents-md`) | 🟢 완료 | 점검 FAIL 0·WARN 38(기존 예외·웹 화면 패턴, 새 위반 없음). 제안 15개 전체 승인: 목적 문장·SRS 링크, §4 `.github/workflows/`(예정), §7 이관·보류 범위·LLM 입력(D-7)·예측 표시(E17)·as_of 이용 가능 시각·조합별 수치·역방향 백테스트·R1 고정·외부 자료 권한, §8 절단 불변 범위, §9 외부 출처·D-1~D-7·확정 값. lint FAIL 0, 109줄. Claude Code 2.1.283(AGENTS.md 직접 로딩, CLAUDE.md 없음) |
| 3. G2 2차 예정 | 🟠 연기 | 새 모듈의 §2·§4 역할·위치는 P2-3(Seonghwanaa)·P3-4(hchee99) 제안 뒤 10-03에 `/agents-md` 재실행(확인 질문 16: A) |
| 4. `구현_계획.md` §0.5 팀원 공용 안내 | 🟢 완료 | 설치·데이터 없을 때의 범위, 읽기, 일정 파일에서 자기 이름 작업 찾기, 브랜치→PR, 결정 기록, 멈춤, 검증, API 키 규칙, 에이전트 시작 문구. §0.3 읽기 순서(§5.2·SRS v2.0·협업 규칙)와 §0.4 이관 문구를 새 `AGENTS.md`와 맞춤 |
| 5. 문서 링크 점검 | 🟢 완료 | `docs/`·README·`AGENTS.md`의 상대 링크 깨짐 0. `requirements.txt`는 만들지 않음(의존성 기준은 `pyproject.toml`·`uv.lock`, 필요 시 `uv export`) |
| 6. 인수인계 정리 (`/handoff`) | 🟢 완료 | `구현_계획.md` 머리말·§0.1(10-01 완료 6행, Git·검증 줄)·§0.2(F4·M8·D-6, G2 1·2차, 다음 작업 M2) 갱신, `plan/README.md`의 '프로젝트 2~4는 기존 범위 아님' 문장을 승인 후 상태로 정정. 문서만 수정 |

### 최종 결과

- 4개 완료, 1개 연기(G2 2차, 10-03). 코드 변경 없음(문서만), 테스트 재실행 없음(직전 기록 engine 111·api 56 passed).
- 사용자 조치: 변경 파일 커밋·푸시·PR(직접). 일정 파일 상태(G1·D-6 완료 등)는 플래너에서 갱신 후 다시 내보냄.

---

## 2026-10-01 11:48:44 KST (+09:00) — 10/01 결정 반영·PEANUTBUTTER1001 첫날 작업 (M1·M8·G1·F4·D-6)

- 시작 시각: 2026-10-01 11:48:44 KST (+09:00)
- 목표: 확정 명세(/srs 최종안)에 따라 10/01 결정을 문서·설정·화면에 반영하고, 압축 일정(`docs/plan/regime-lab-wbs-20261001.csv`)의 PEANUTBUTTER1001 첫날 작업(G1 SRS v2.0 개정안 + M7, F4 협업 규칙, M8 문서 정리, D-6 기록)을 마친다. 수치·API·결과 형식은 바꾸지 않는다. GitHub 설정과 커밋·푸시·PR은 실행 직전에 사용자 확인을 받는다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. `00-explainer.md.md` → `00-explainer.md` 이름 변경 (B-2) | 🟢 완료 | 내용 그대로. `README.md:83`·`01-presets…md:3` 링크 대상 존재 확인 |
| 2. `default.yaml` 확정 주석 (A-2, 값 불변) | 🟢 완료 | 주석 2줄만 변경. `load_config()` 결과 변경 전후 동일, `prep_hash` 3aef37372fef9069 유지 |
| 3. 설정 › 데이터 정보 U3-1 고지 (`i18n.js`·`settings.js`) (A-3) | 🟢 완료 | 키 2개(`set.limits`·`set.limitU3`, 한·영)와 행 1개 추가. 8001 서버에서 한국어·영어 표시 확인, 콘솔 오류 0 |
| 4. `구현_전_결정사항.md` §2.2 확정 + §5.2 E11~E17 (A-1·C-1·H) | 🟢 완료 | §2.2 4건 ✅, 머리말 10-01 행, §5.2 E11~E17(D-6 기본안 = E17), §6 이관 행 보충. '⏳ 확인 대기'는 범례 1곳만 남음 |
| 5. `readme-preview.md` 알려진 제한 정리 (A-4) | 🟢 완료 | 절 제목 '알려진 제한', 검증 기준 '(확정 2026-10-01)', `status: proposed` 안내 문장 교체, U3-1 행에 화면 고지 추가. README.md는 이미 U3-1 문구가 있어 그대로 |
| 6. `구현_계획.md` 정정 (B-1) | 🟢 완료 | 머리말 상태·§0 제목·§0.1 AGENTS 행·Git 줄의 '미커밋' 정정(`7f9700f`, PR #2), §0.2를 10-01 결정·압축 일정 표로 교체, §0.5 시작 순서 갱신, §10에 'SRS v2.0 반영' 표시 |
| 7. `SRS.md` v2.0 개정안 + M7 (D-1) | 🟢 완료 | 상태 '팀 전원 승인 대기'. R1 고정 절(§1.4), 포함/보류/제외, C-1~C-8, FR-R·F·N·B·S 요약 FR, FR-L7, 이관·보류 표시, NFR-14·15, §6 테스트·수용 기준 R1/R2, §8 실명·계정, 부록 A(G2 항목)·B(M7 반영 10건) |
| 8. `docs/협업_규칙.md` (E-1) | 🟢 완료 | 팀·계정(hongsungmin0315 `hongsungmin0315`·Seonghwanaa `Seonghwanaa`·hchee99 `hchee99`·PEANUTBUTTER1001 `PEANUTBUTTER1001`, 참고인 `chordJE` 제외), 브랜치·커밋·PR·리뷰·squash, 공용 파일 주인 표(초안), 버전 상수 알림, 커밋 금지 항목, CI, 저장소 설정 |
| 9. `docs/plan/README.md` 갱신 (B-3) | 🟢 완료 | 10-01 상태 안내(일정 CSV·간트 SVG·협업 규칙 링크), 결정 대기 목록에 D-6(확정 E17)·D-7, 변경 이력 |
| 10. 검증 (engine·api 테스트, 설정 불변, 화면) | 🟢 완료 | engine 111 passed, api 56 passed. `load_config()` 동일·`prep_hash` 유지. i18n 사용 키 누락 0(한 줄 다중 키는 수동 확인), 영어 값 한글 0. '미커밋'·'status: proposed' 잔존 없음(E16의 '미커밋 문서'는 의도된 문구) |
| 11. GitHub 설정 적용 (F-1, 실행 직전 확인) | 🟢 완료 | 사용자 확인 후 적용: `develop` 보호(승인 1명, 관리자 미적용), squash만 허용. 사용자 결정으로 자동 삭제는 꺼진 채 유지, 병합 브랜치 2개도 유지. `gh api` 재조회로 확인. `협업_규칙.md`·E15를 이 결정에 맞게 수정 |
| 12. 공개 커밋 후보 점검 보고 → 선택 후 PR (G-1) | 🟢 완료 | 점검 보고 완료(민감 패턴 없음, SVG 전화번호 패턴은 좌표값 오탐). 사용자가 `docs/` 안의 HTML을 모두 지움. 커밋·푸시·PR은 사용자가 직접 하기로 함(범위 밖) |


### 최종 결과

- 12개 단계 모두 종료(🟢). 테스트 engine 111 passed, api 56 passed. 수치·API·결과 형식 변경 없음.
- 사용자 조치: 변경 파일 커밋·푸시·PR(직접), SRS v2.0 팀 전원 승인 결과 전달(SRS §9 기록).
- 참고: 사용자가 `docs/` 안의 HTML을 지웠다가 `docs/regime-lab_prototype.html`은 같은 날 복구함(커밋본과 동일, 변경 없음). 이 파일을 근거로 적은 기록(E6, `구현_계획.md` §7.1, `app.css`·`design-tokens.json` 주석)은 그대로 유효하다.
---

## 2026-09-30 15:58:31 KST (+09:00) — 작업 스킬트리 확장 (4인 배정·WBS·간트·보고서)

- 시작 시각: 2026-09-30 15:58:31 KST (+09:00)
- 목표: 확정 명세(대화 내 최종 /srs)에 따라 `docs/plan/260930skilltree.html` 한 파일에 인원 1~4 체계, 작업 세분화·쉬운 설명, WBS·간트(CSV·SVG), 시작일, 손/선택 도구(H/C), 1초 설명창, 인원별 보고서(.md·인쇄)를 추가한다. 저장소 코드는 변경하지 않는다.

### 단계 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. 데이터: 세부 작업·쉬운 설명, 작업 가중치 = 세부 합 | 🟢 완료 | 작업 70·세부 209·쉬운 설명 70. 작업별 합계가 이전 가중치와 전부 일치, 보류 제외 총량 63.0 |
| 2. 상태·저장 v2 (v1 비율 이전, 시작일·펼침) | 🟢 완료 | v1 샘플(P2-4 → 4.0) 가져오기 시 세부 비율 이전·배정·메모 유지 확인 |
| 3. 인원 1~4 표기·단축키(물리 키, H/C) | 🟢 완료 | 붓·카드·노드·WBS·간트·보고서 번호 통일. 한글 입력(ㅗ/ㅊ)·손 도구 중 숫자키·Ctrl+C·입력칸 무시 확인 |
| 4. 일정: 세부 구간·날짜 변환 | 🟢 완료 | 제안 배치 전체 기간 22.7주 유지, 시작일 변경 시 날짜 재계산 확인 |
| 5. 보기 탭·WBS(CSV)·간트(SVG)·상세 칸 | 🟢 완료 | CSV 287행(8+70+209)·BOM, 선행 미배정 시 `선행 끊김`/`일정 제외`·간트 제외 표시, 10-21 마감선·오늘 선. SVG는 문자열 생성까지 확인(파일 열기는 미확인) |
| 6. 손·선택 도구 | 🟢 완료 | 커서 grab/grabbing, 드래그 스크롤, 손 도구 클릭 시 배정 불변, C로 직전 붓 복귀 |
| 7. 1초 설명창 | 🟢 완료 | 0.5초 미표시·1.2초 표시, 벗어남·Esc·클릭 시 닫힘, 가장자리 화면 안, 간트·WBS 동작, 실제 Tab 초점 표시 |
| 8. 인원별 보고서(.md·인쇄)·회의록 확장 | 🟢 완료 | 인원별 인주·작업 수 = 카드, 받을 선행 = 선행 관계 직접 계산과 일치, 빈 인원 "배정된 작업 없음". 검증 중 담당자 미입력 시 제목 중복 버그 발견·수정. 인쇄 CSS·출력 영역 구조 확인(실제 인쇄 미리보기는 미실행) |
| 9. 브라우저 검증(수용 기준) | 🟢 완료 | 콘솔 오류 0, 1280px·665px 폭 페이지 가로 넘침 없음. 저장소 테스트는 코드 변경이 없어 미실행 |

### 최종 결과

- 9개 항목 모두 완료. 변경 파일: `docs/plan/260930skilltree.html`(재작성), `progress/PROGRESS.md`. 저장소 코드·설정 변경 없음, 커밋하지 않음.
- 사용자 확인 필요: 실제 브라우저에서 CSV(엑셀)·SVG 파일 열기, 인쇄 미리보기(A4, 인원별 새 쪽).

---

## 2026-09-28 — 작업 재개 점검·AGENTS.md 생성·인수인계 정리 (문서만)

| 단계 | 상태 | 비고 |
|---|---|---|
| 작업 재개 점검 (`/resume`) | 🟢 완료 | 문서와 Git 대조: 09-23 작업은 `0a1b8da`·`7949491`(PR #1)로 병합 완료인데 `구현_계획.md`는 '미커밋'으로 남아 있었음(충돌 → 정정) |
| 루트 `AGENTS.md` 생성·1차 개정 (`/agents-md`) | 🟢 완료 | 사용자 결정: R5(API는 엔진 공개 모듈 7개로만 import) 채택, R1–R3는 목표로 두고 기존 위반은 §6 예외, 진입 역할 경고는 유지, 웹 번들러 도입은 §9 멈춤 조건. 개정 10개 항목 반영. lint FAIL 0, 104줄. 미커밋 |
| 인수인계 문서 정리 (`/handoff`) | 🟢 완료 | `구현_계획.md` §0 커밋 상태·읽기 순서·시작 순서 갱신, §7.2 언어를 E9(기본 한국어 + 영어 선택)로 정정 |

- 테스트는 실행하지 않음(문서만 수정). 마지막 검증 결과는 2026-09-23 engine 111 passed, api 56 passed.
- 환경(저장소 밖): 터미널 Claude Code CLI 2.1.220 → 2.1.283 업데이트(`AGENTS.md` 직접 로딩 지원).

---

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
