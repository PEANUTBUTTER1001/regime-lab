# DART·ECOS Python 수집 계약

2026-10-17 사용자 SRS 승인 후 `$execute`. 서버 전달 SQL 8개 표를 저장 계약으로 사용한다.
기존 X4 금융위 수집과 같은 독립 Python·MariaDB·환경변수·Windows 예약 구조다.

## 범위

- DART: corpCode·company·공시 목록·사건12종·전체 재무제표(CFS 우선, 정상 자료 없음일 때 OFS).
- ECOS: 통계표/항목 정의, 기준금리·국고채3년/10년·회사채AA-3년·원/달러 매매기준율.
- 원본·시각·변경 버전·품질·수집 이력만 저장. 학습·특징·Parquet 연결·기존 ingest/주가 변경 제외.
- corpCode ZIP은 회사 식별정보이며 공시 원문 ZIP이 아니다. 공시 원문·뉴스·LLM 전송 제외.
- 이번 승인은 기존 D-4의 목록에 더해 사건 상세·재무·기업개황의 서버 DB 보관과 ECOS 출처를 포함.
  출처 활성/이용 범위는 이 승인된 API 업무 데이터에 한정. 서비스 공개·제3자 재배포 확대 없음.

## 저장

`engine/scripts/fundamental_collection/schema.sql`은 사용자가 서버 에이전트에게 전달한 DDL과 같은 열·형식·색인이다.
7개 원본 표는 동일 구조, 1개 추가 전용 로그 표. 기존 DB·표·계정 변경/삭제 없음.
수집 계정 SELECT/INSERT, 관리자가 DDL 대행 가능. init-db는 누락 표만 만들고 기존 구조 불일치는 거부.

업무별 identity를 정규 JSON으로 해시하여 entity_key로 사용한다. source+entity_key+revision 고유키.
재무는 조회 응답 배열 전체, 사건은 같은 접수번호의 복수 행 전체를 보존한다.
정상 최신 값 동일 시 추가0건, A→B→A는 버전3개. 관측 시각과 기준일·공개 시각을 구분한다.
available_at은 실제 관측 시각, availability_basis=observed. 과거 최신 정정값의 당시 공개 여부는 미확인으로
품질에 표시하고 과거로 소급하지 않는다. 원본 날짜만 있으면 source_published_at=NULL.
UTC DATETIME(6), 원본 숫자는 문자열/원본 자릿수 보존. 결측/0/음수 구분. API 키/오류 원문 저장 금지.

## 수집/복구

회사·공시 대장 → 회사 대상 확인 → 사건/재무, ECOS 메타 검증 → 시계열 순서.
현재 상장 회사뿐 아니라 과거 유가/코스닥 공시 회사도 대상. 현재 고유번호의 빈 종목코드는 삭제 근거 아님.
API별 작업 단위·조회 조건·설정 지문을 기록한다. 완료 단위만 건너뛰고 실패/빈/부분 상태는 재검사한다.
최근 공시와 미완료 작업을 갱신하며, 빈 응답을 휴장·영구 부재로 확정하지 않는다.
요청 예산은 전송 전에 로그에 예약, 재시도도 차감. KST 일 예산은 이 수집기만의 값이며 공유 키 전체 한도 보장 아님.
일부 페이지/잘못된 필드/필터 불일치/키 중복은 완료로 확정하지 않는다. 잠금 손실·DB 오류는 중단.
실제 원본 저장+완료 로그 같은 트랜잭션, started 별도 기록. GET_LOCK과 동일 연결 소유 확인, 자동 재연결 없음.

## 운영/수용

키 DART_API_KEY/ECOS_API_KEY, DB는 기존 REGIME_FSC_DB_* 재사용. 소스 한쪽 미설정은 나머지와 분리 보고.
명령 check/init-db/backfill/update/status/inspect/schedule-info. 원본 접근 --server-pc 필수.
13:30 KST 및14:00/14:30/15:00 별도 예약, 기존 fsc 예약 덮어쓰기 없음. 장중 실시간 공시 수집 아님.
실서버 실제 소량 적재·재실행·DB 잠금/원자성·무인 예약 로그 확인 전 운영 완료 판정 금지.
새 경로/명령은 승인된 인프라 engine/scripts 역할 안에 추가, AGENTS.md 직접 수정 제외.

실행 방법·CLI·설정의 단일 안내: [README](../engine/scripts/fundamental_collection/README.md).
