# DART·ECOS 독립 Python 수집기

금융위 3종 수집과 같은 운영 방식. **기존 주가 수집기·표·예약·ingest는 변경하지 않는다.**
이 도구는 원본/변경/시각/품질의 DB 축적까지 담당하며 학습·가중치·Parquet 연결은 수행하지 않는다.
데이터 서버 PC에서만 실행. 키/비밀번호/실제 자료/접속 주소를 Git·채팅·로그에 남기지 않는다.
계약: [수집 명세](../../../docs/DART_ECOS_수집_명세.md).

## 수집 범위

| 출처 | 데이터 |
|---|---|
| DART | 회사 고유번호 ZIP/XML, 기업개황, 공시 목록, 사건12종, 전체 재무제표 |
| ECOS | 통계표/항목 메타, 기준금리·국고채3년/10년·회사채AA-3년·원/달러 매매기준율 |

회사 식별정보 ZIP만 받는다. 공시 원문 ZIP·기사 본문·LLM은 범위 밖.
대상 회사 = 현재 종목코드가 있는 회사와 과거 유가/코스닥 공시 회사의 합집합.
현재 코넥스/비상장 회사의 프로필은 범위 확인을 위해 조회할 수 있으나, 상세/재무 대상은 현재 또는 과거 유가/코스닥 회사다.
과거 회사가 현재 E로 바뀌거나 종목코드가 비어도 과거 공시 회사면 제외하지 않는다.

## 1. 서버 준비

- Python3.12·uv, MariaDB 접속 가능한 기존 주가 수집 환경.
- DART 키는 `DART_API_KEY`, ECOS는 별도 발급한 `ECOS_API_KEY`.
- DB는 `REGIME_FSC_DB_HOST/USER/PASSWORD/NAME/PORT` 재사용. 선택 `REGIME_FSC_DB_SSL_CA`.
- 호스트: MariaDB가 같은 PC이면 보통 `127.0.0.1`, 사용자: 권한을 받은 **MariaDB 계정**, 암호: 그 DB 계정 암호.
- 기본 DB명 regime_lab·포트3306은 실제 서버 값 확인 후 사용.
- 서버 담당자는 [schema.sql](schema.sql)로 **기존 대상 DB 안에 fund_* 8개 표** 생성.
  기존 표가 있으면 구조 비교 후 실행. 불일치는 ALTER/DROP 없이 중단.
- 수집 계정은 신규8개 표 SELECT/INSERT. 기존 fsc_* 권한만으로 새 표 권한을 보장하지 않는다.
  init-db를 사용하려면 CREATE가 추가로 필요하지만 관리자가 DDL을 대행하면 수집 계정의 CREATE는 불필요.
- 파일은 이 폴더 전체(잠금 파일 포함)와 추가된 `fundamental_collection` 설정이 있는
  `engine/config/default.yaml`을 서버의 같은 프로젝트 구조에 배치. 배치 전 원격 최신과 로컬 변경을 확인.

이하 모든 명령은 **서버 PC의 프로젝트 루트 PowerShell**에서 실행한다.

## 2. 마스킹 설정

```powershell
& .\engine\scripts\fundamental_collection\configure.ps1
```

이미 등록한 DB값·DART키·ECOS키는 값을 표시하지 않고 재사용. 없는 값만 질문한다.
키를 아직 안 받았으면 빈 입력으로 해당 출처를 미설정 상태로 두고 다른 출처만 실행 가능.
환경변수는 현재 프로세스와 Windows 사용자 환경에 저장되며 동일 사용자 권한으로 접근 가능하다.
`-SessionOnly`는 현재 창에만, `-Reconfigure`는 기존 값도 다시 입력(빈 입력은 기존 값 유지).
DB값은 주가 수집과 공유하므로 변경 시 두 도구에 영향을 준다. 기존 금융위 API 키는 건드리지 않는다.
스크립트를 별도 프로세스에서 실행했다면 새 터미널에서 후속 명령 실행.
스크립트 실행 정책으로 차단된 경우 현재 창에만 `Set-ExecutionPolicy -Scope Process Bypass` 후 실행.

## 3. 테이블/접근 점검

관리자가 표를 이미 만들었다면 바로 check. 직접 생성 권한이 있는 경우에만 init-db.

```powershell
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc init-db
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc check
```

check는 원본 표에 자료를 넣지 않지만 **API 시험 요청의 예산 로그는 추가**한다.
표가 누락되면 API 시험 전 종료. 키 누락·한도·표 불일치는 출처별 고정 코드로만 표시.
GRANT 표시만으로 실제 원본 쓰기 권한을 증명하지 않는다. 아래 소량 시험까지 수행.

## 4. DART 한 회사 소량 시험

`--corp-code`는 주식6자리 코드가 아닌 DART 회사8자리 고유번호다.
아래 00126380은 공식 가이드의 회사 코드 예시다. 실제 시험 회사로 바꿔도 된다.

```powershell
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc backfill --source dart --corp-code 00126380 --start 2025-01-01 --end 2025-12-31 --max-requests 100 --max-units 80
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc status
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc inspect --table fund_financial_raw --corp-code 00126380 --limit 5
```

같은 backfill 명령 재실행 → 완료/정상 빈 작업은 캐시 사용, 신규 버전0건 확인.
실제 API 재조회까지 검증하려면 같은 인자에 `--refresh` 추가 → 내용 동일 시 신규0건.
실제 원본은 서버에서만 inspect로 확인하며 채팅에 붙이지 않는다.
빈 자료가 하나라도 있거나 상한으로 중단하면 종료코드3이 가능하다. 실패와 정상 빈 관측을 status에서 구분.

## 5. ECOS 소량 시험

```powershell
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc backfill --source ecos --start 2025-10-01 --end 2025-10-31 --max-requests 100 --max-units 30
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc inspect --table fund_ecos_meta_raw --limit 10
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc inspect --table fund_ecos_value_raw --date 2025-10-13 --limit 10
```

메타에서 통계표·D주기·검색가능 여부·항목명·단위·기간을 확인한다.
임의 항목코드 추정/이름 부분일치로 수집하지 않는다. 허용 이름이 다르면 `ecos_item_ambiguous`로 중단하고
공식 항목 정의를 확인한 뒤 설정의 이름/단위를 명시적으로 보완한다. 다른 단위·여러 차원·중복 후보는 거부.
원/달러는 매매기준율 계열이며 종가와 혼합하지 않는다.

## 6. 전체 과거 수집과 재개

```powershell
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc backfill
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc status
```

기본 DART 목록 시작2015-01-01, 재무2015사업연도부터. 사건은 발견된 선정 공시의 회사/월만 상세조회.
DART 목록의 더 과거가 필요하면 `--source dart --start YYYY-MM-DD`로 명시. 전용 재무의2015제한은 유지.
ECOS는 메타에 기록된 각 항목의 최초일~최신일까지, `--start`를 주면 해당 범위와 교집합.
금융위 수집기와 달리 오늘까지 요청 가능하며 미래 종료일은 거부한다.
범위를 과거로 지정해도 재무 API가 반환하는 값이 **그 당시의 최초 발표본이라는 보장은 없다.**

요청 예산으로 중단하면 다음 허용 시간/날짜에 **동일 backfill 명령** 재실행. 완료 단위부터 이어 받는다.
아직 시도하지 않은 전체 과거 작업은 일별 update가 대신 전부 생성하지 않으므로 최초 backfill은 완료까지 재실행 필요.
--max-requests는 설정 상한을 낮추는 용도, --max-units는 이번 실행의 실제 작업 수 상한.
전체 수집은 회사 수·과거 보고서 수·일 한도에 따라 여러 날 소요될 수 있다.
양쪽 출처 선택 시 작은 공통 경제 자료가 대량 DART 작업에 밀리지 않도록 ECOS부터 실행한다.

## 7. 일별 갱신·예약

```powershell
uv run --script engine/scripts/fundamental_collection/collect_fundamentals.py --server-pc update
& .\engine\scripts\fundamental_collection\install_task.ps1 -PrintOnly
& .\engine\scripts\fundamental_collection\install_task.ps1 -RunWhenLoggedOff
```

예약 이름 `regime-lab-fundamentals-daily`. 기존 주가 예약을 덮어쓰지 않는다. 동일 이름이 있으면 거부.
기본13:30 KST, 14:00/14:30/15:00 재확인. 최신7일과 출처별 미완료 작업30개 재조회.
회사정보는 식별표 변경 또는 기본7일 경과 시 재조회하며 매번 모든 회사를 다시 호출하지 않는다.
재무는 최근 발견된 정기/정정 보고서의 사업연도 후보를 재조회하고, 오래된 실패/빈 응답도 순환 재확인.
일별 수집이므로 마지막 실행 후 게시된 공시는 다음 실행에 반영. 공시 실시간 수집 아님.
설정의 수치는 예약 XML 생성 시 반영된다. 설정을 바꿨다고 이미 등록된 예약이 자동 변경되지는 않는다.

로그아웃 실행 등록에서 요구하는 암호는 **Windows 계정 암호**이며 DB 암호가 아니다.
현재 환경변수를 등록한 동일 Windows 계정 사용. 서버 PC·DB 가동 필요, 터미널 상시 개방 불필요.
`-PythonPath/-UvPath/-ConfigPath` 지원. 직접 Python 실행은 해당 환경에 PyMySQL/PyYAML을 별도 준비해야 한다.
등록 후 다음 실행 시각·작업 결과·DB의 최근 수집 로그까지 확인. 예약 등록 성공만으로 무인 성공 판정 금지.

## 저장·품질·복구 계약

- 서버 전달 DDL 8개 표의 열·형식·NULL·기본값·색인·문자셋을 검증. 구조 불일치는 중단, 자동 변경 없음.
- 원본 숫자 문자열·계정 배열 전체·API 추가 업무 필드 보존. 금융업 계정을 일반 제조업 지표로 강제 변환하지 않는다.
- 같은 최신 값은 추가0건, 변경은 새 revision. 수신 확인은 로그에 남고 원본을 갱신하지 않는다.
- first_seen_at/observed_at/available_at은 실제 버전 관측 UTC, availability_basis=observed.
  날짜만 있는 공시의 source_published_at=NULL, time_precision=date_only. 현재 기업정보·과거 재무 원본 불확실성은 품질 표시.
- started 선기록 후 원본+완료 로그 같은 트랜잭션. 실패/중단은 rollback, started/failed 작업은 재개.
- 정상 empty는 관측일 뿐 영구 자료 부재·휴장 확정이 아님. status의 범위는 관측 범위이며 과거 전체 완전성 보증 아님.
- 기존 키 소실은 source_rows_disappeared로 실패시켜 운영자 확인 전 삭제하지 않는다.
- 재무 CFS가 정상013일 때만 OFS 조회. 연결 오류·권한 오류에서 별도 재무로 조용히 대체하지 않는다.
- GET_LOCK은 fund 전용이므로 기존 fsc 수집과 독립, 새 수집기끼리는 중복 차단. 연결/잠금 손실에서 자동 재연결 없음.
- 요청 직전 로그 예약·commit. 재시도·중단도 예산 소진. 기본 실행8,000/출처별KST일8,000은 이 도구의 로컬 한도.
  기존 ingest 등 공유 키의 다른 사용량을 포함한 계정 전체 잔여량 보장 아님.
- 오류 원문·URL·키·비밀번호 비노출. 로컬 상태와 원본을 임의 삭제하지 않는다.

종료코드: 0 종료/점검통과, 1 출처 또는 작업 실패, 2 설정/구조/연결 오류,
3 부분/요청예산/빈 관측, 130 사용자 중단. check의 missing tables는2.

## 검증

```powershell
uv run --project engine python -m pytest engine/tests/test_fundamental_collector.py engine/tests/test_fsc_collector.py
```

합성 API·SQLite 어댑터로 원본 저장 SQL/트랜잭션·재개를 검사한다. MariaDB DDL/메타데이터/명명잠금은
별도 계약 대역 검사이며 실제 서버 수용을 대체하지 않는다. 실제 키·서버 적재·예약 실행은 서버에서 확인해야 한다.

### 2026-10-17 로컬 검증 결과

- 전체 engine777통과/원본 의존8skip, API289통과/기존경고1, ingest147통과.
- 이후 수집기 경계 보완은 신규60+기존FSC57=117통과로 재검증.
- PowerShell5/현재 PowerShell·Python/uv·사용자 설정 예약 XML 통과, 실제 작업 등록 없음.
- 실API·MariaDB·무인 운영 수용은 접속 환경이 없어 미실행. 위 결과로 서버 검증을 대신하지 않는다.
