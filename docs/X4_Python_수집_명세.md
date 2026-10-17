# X4 Python 금융위 API 3종 수집 계약

2026-10-16 사용자 승인: 수집 우선 SRS → n8n 제외·Python/PyMySQL·공통 키1개·신규 표·13:30 운영안 → $execute.
기존 X4a의 주식 출처에 지수시세·KRX상장종목정보를 이번 수집 범위로 추가 승인.

## 범위와 책임

독립 수집 인프라 `engine/scripts/daily_stock_collection/collect_fsc.py`.
엔진·API·ingest import 없이 urllib·PyMySQL로 수집 서버 PC에서 실행한다.
uv PEP723 메타데이터로 수집 전용 환경을 관리하고 엔진/API 런타임 의존성에는 추가하지 않는다.
숫자 설정은 `engine/config/default.yaml`의 `fsc_collection`, 비밀은 환경변수만 사용.
원본 가격/기간·분석 입력 전환·수정주가 보정·뉴스/LLM·실행 결과 형식 변경은 이번 범위 밖.

## 입력과 저장

주식 `GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`,
지수 `GetMarketIndexInfoService_V2/getStockMarketIndex_V2`,
종목 `GetKrxListedInfoService_V2/getItemInfo_V2`. HTTPS·JSON·serviceKey 공통.
KOSPI/KOSDAQ, 대표 지수명은 설정과 정확 일치. 종목 코드는 변환하지 않고 ISIN 등 원래 필드를 보존.
세 원본 표 + 수집 로그 표는 `schema.sql`, 기존 DB 기본명은 regime_lab. 관리자 DDL 대행 가능.
`check/init-db/backfill/update/status/inspect/schedule-info` CLI. 원본 접근 명령에 `--server-pc` 필수.

## 무결성·재개·품질

- 페이지 번호/크기/전체 건수/각 페이지 행 수/기준일/필터/키 중복 확인 후 출처×날짜 커밋.
- 원본 숫자는 숫자 문자열로 정규화해 자릿수 보존. 0·누락·OHLC 이상은 보정 없이 품질 표시.
- 같은 값은 건너뛰고 변경은 새 revision. A→B→A 재등장도 새 버전. 사라진 키는 삭제로 추정하지 않고 재검사 대기.
- started 로그 선기록 후 원본+완료 로그를 같은 트랜잭션으로 저장. 실패/중단은 다음 실행에서 재개.
- DB 명명 잠금을 같은 연결에서 보유. 동시 수집 차단·자동 재연결 없음.
- empty는 관측만 의미. 일부 시장/지수 누락은 partial. 최근7일과 과거 미완료/빈 날짜를 제한 수씩 순환 재검사.
- 최초일은 누적 건수 탐색+정확 일자 검증. API가 제공하지 않는 기간은 생성하지 않는다.

## 운영과 수용

Windows 설정 도구는 마스킹 입력→현재/사용자 환경변수, 예약 도구는 작업 스케줄러13:30 KST 및 지연 재조회.
실제 서버 점검·키/DB 연결·소량 적재·전체 적재·13:30 로그까지 확인 전 운영 완료로 판정하지 않는다.
AGENTS.md의 신규 명령/설정/의존성 등록은 /agents-md 후속 절차. 사용자 계정/담당 배정 변경 없음.
운영 방법·오류·검증: [PYTHON.md](../engine/scripts/daily_stock_collection/PYTHON.md).
