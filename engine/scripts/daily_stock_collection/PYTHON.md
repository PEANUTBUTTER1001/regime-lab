# 금융위 API 3종 Python 수집

공통 API 키 하나로 주식시세·대표 KOSPI/KOSDAQ 지수·상장종목 기본정보를 받아 같은 MariaDB의
`fsc_stock_daily_raw`, `fsc_index_daily_raw`, `fsc_listed_daily_raw`, `fsc_collection_log`에 저장한다.
수집 서버 PC에서만 실행하며 n8n·프로젝트 웹 서버는 필요하지 않다. 분석용 변환은 후속 작업이다.

## 1. 한 번만 설정

Python 3.12와 uv가 있는 서버에서 저장소 루트의 PowerShell을 연다.

```powershell
& .\engine\scripts\daily_stock_collection\configure.ps1
```

API 키·DB 호스트·DB 사용자·DB 비밀번호 입력. DB 이름 기본 `regime_lab`, 포트 기본3306.
키와 비밀번호 입력은 마스킹한다. 값은 파일에 쓰지 않고 현재 세션 및 **Windows 사용자 환경변수**에 등록한다.
사용자 환경변수는 암호화 비밀 저장소가 아니므로 수집 전용 Windows 계정 사용 권장.
현재 세션에서만 시험하려면 `-SessionOnly`를 사용한다. 이 옵션으로는 이후 예약 작업에 설정이 전달되지 않는다.
PowerShell 스크립트를 별도 프로세스로 실행했다면 그 프로세스가 끝난 뒤 새 PowerShell을 열어 다음 명령을 실행한다.
실행 정책 때문에 `.ps1`이 차단되면 현재 창에서 `Set-ExecutionPolicy -Scope Process Bypass` 후 실행한다.
이는 현재 PowerShell 프로세스에만 적용된다.

환경변수 이름: `REGIME_FSC_SERVICE_KEY`, `REGIME_FSC_DB_HOST`, `REGIME_FSC_DB_USER`,
`REGIME_FSC_DB_PASSWORD`, 선택 `REGIME_FSC_DB_NAME`, `REGIME_FSC_DB_PORT`.
DB TLS를 쓰는 환경은 `REGIME_FSC_DB_SSL_CA`에 CA 인증서 경로를 설정한다(호스트 검증 포함).
API 키는 원문/디코딩 키 권장. `%xx` 인코딩 키는 한 번 디코딩하며 원문의 `+`는 보존한다.

모든 명령은 다음 형태다. uv가 수집기 전용 PyMySQL·PyYAML 환경을 준비하며 engine/api 의존성은 바꾸지 않는다.

```powershell
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc check
```

`check`는 서비스별 API 접근·DB 연결·표 상태를 읽기만 한다. `SHOW GRANTS` 원문·계정·접속 주소는 출력하지 않는다.
권한 표시는 전체 GRANT 문장의 힌트일 뿐 해당 테이블의 실제 쓰기 권한 증명은 아니다. 아래 소량 적재가 최종 확인이다.
키·DB 설정이 없으면 필요한 **환경변수 이름만** 표시한다. 원본 오류 메시지·요청 URL은 출력하지 않는다.

## 2. 신규 표 준비

```powershell
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc init-db
```

기존 DB에 전용 표4개만 생성. 기존 `store_*`·기존 n8n 설정은 접근/수정하지 않는다.
생성 권한이 없으면 서버 관리자(Seonghwanaa)가 같은 DB에서 `schema.sql`을 실행하고 수집 계정에
**전용 표4개의 SELECT·INSERT**를 부여한다. 수집기는 UPDATE·DELETE 권한이 필요하지 않다.
DB 생성·사용자 권한 변경은 이 스크립트가 수행하지 않는다.
DDL은 트랜잭션이 아니므로 일부 표 생성 후 실패할 수 있다. 권한 보완 뒤 다시 실행 가능.
같은 이름에 호환되지 않는 표가 있으면 ALTER·DROP 없이 중단한다.

## 3. 소량 수집 → 전체 적재

먼저 제공된 과거 날짜를 골라 출처별 하루씩 시험한다(예시는 날짜만 교체).

```powershell
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc backfill --source stock --start 2026-10-13 --end 2026-10-13
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc backfill --source index --start 2026-10-13 --end 2026-10-13
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc backfill --source listed --start 2026-10-13 --end 2026-10-13
```

이후 전체 과거 적재:

```powershell
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc backfill
```

- 출처별 `endBasDt` 누적 건수로 과거 경계를 탐색하고 `basDt` 정확 일자 조회로 첫 선택 자료를 확인한다.
  종료 경계 포함/미포함 차이는 인접 날짜로 확인한다. 기본 탐색 하한은1900-01-01이며 하한 이전 자료가 나오면 중단한다.
  기간 인자를 생략하면 KST 어제까지. 오늘/미래 요청은 거부한다. 최초일은 서비스 시작일로 추정하지 않는다.
- 날짜·시장/지수·페이지별 검증 후 해당 출처×날짜를 커밋한다. 같은 명령을 다시 실행하면 완료/빈 응답 날짜를 건너뛴다.
  API 요청 예산/일일 한도에서 종료코드3이면 이후 같은 명령으로 재개한다. 실행 예산은 계정 전체의 일일 한도 보증이 아니다.
- 과거 변경까지 다시 확인하려면 `backfill --refresh` 또는 `--start/--end --refresh` 사용.
- `--max-days 3`은 이번 실행의 **출처×날짜** 시도 수를 제한한다. 남은 날짜가 있으면 종료코드3.
- 원본 숫자는 문자열로 보존하므로 21자리 정수·지수 소수가 반올림되지 않는다. 요청별 JSON의 공백/키 순서 등 바이트 형식은 저장 대상이 아니다.
- 종목코드 원형(예: `A000040`)은 보존한다. 상장종목 API가 우선주를 제공하지 않는다고 다른 종목을 보통주로 확정하지 않는다.
- 원본에 구조적으로 올바른0·누락 수치·비정상 OHLC가 있으면 그대로 저장하고 품질 표시한다. 0원 청산·폐지·정상 거래로 해석하지 않는다.

## 4. 상태와 실제 0값 확인

```powershell
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc status
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc inspect --source stock --date 2026-10-13
```

`status`: 원본 버전 수·실제 저장 최초/최신일·날짜 상태·최근 저장 성공일별 품질 집계·미완료 날짜.
최신일 하나로 전체 기간 수집 완료를 판정하지 않는다. `inspect`는 지정일 실제 값과 버전을 서버 터미널에 표시한다.
출력에는 원본 가격이 있으므로 외부 공유·Git 저장을 하지 않는다. 0값의 이유는 실제 종목 이력과 대조한 뒤 별도 판정한다.

## 5. 매일 13:30 KST 갱신

수동 실행:

```powershell
uv run --script engine/scripts/daily_stock_collection/collect_fsc.py --server-pc update
```

최근7일 + 출처별 미완료 최대30일을 다시 조회한다. 과거 빈 응답도 마지막 시도 시각 순으로 순환 확인하므로
7일보다 긴 지연도 복구 대상이다. 빈 날짜를 휴장으로 확정하지 않는다. 최초 적재를 전혀 안 한 기간까지 발견하는 명령은
아니므로 먼저 `backfill`이 필요하다. 다시 빠진 범위는 `backfill --start ... --end ... --refresh`로 확인한다.

세 API 모두 기준일 다음 영업일13시 이후 게시. 13:30에도 미게시/부분 게시일 수 있다.
한 시장/지수만 반환되면 받은 원본은 보존하되 `partial`로 기록하고 재조회한다.
재수집에서 기존 키가 사라지면 `source_rows_disappeared`로 당일 저장을 보류한다(삭제라고 추정하지 않음).

Windows 예약 등록(설정과 같은 Windows 계정):

```powershell
& .\engine\scripts\daily_stock_collection\install_task.ps1 -RunWhenLoggedOff
```

Windows 계정 암호를 대화상자에서 입력한다(DB 암호 아님). 작업 스케줄러가 자격증명을 관리하며 코드/작업 인자에는 비밀을 넣지 않는다.
기본은 KST13:30, 지연 재조회14:00·14:30·15:00. 실제 간격은 설정에서 읽는다.
`-RunWhenLoggedOff` 생략 시 **Windows 로그인 중에만** 실행. 서버 전원·네트워크가 켜져 있어야 한다.
`-PrintOnly`는 등록 없이 XML만 출력한다. 같은 이름의 작업은 덮어쓰지 않는다.
등록 기본 이름은 `regime-lab-fsc-daily`.

작업 스케줄러의 마지막 결과와 `status`의 DB 수집 이력을 함께 확인한다. 콘솔 출력 파일은 자동으로 만들지 않는다.
uv 대신 별도 Python 환경에 PyMySQL·PyYAML을 설치한 서버는 `-PythonPath <python.exe>`로 예약 가능.
별도 설정 파일로 운영할 때는 `-ConfigPath <yaml>`을 함께 지정하면 예약 시각과 실제 수집 모두 같은 파일을 사용한다.
Linux 서버에서는 같은 환경변수를 제공한 실행 계정의 systemd timer/cron으로 `update`를 매일13:30 Asia/Seoul에 실행한다.

## 설정·오류·검증

수치·시장·대표 지수명은 `engine/config/default.yaml`의 `fsc_collection:` 절. CLI의 `--config <yaml>`로 같은 절을 가진 설정 지정 가능.
예약 설정 변경 후에는 예약 작업 재등록 필요. 기존 작업을 수정/삭제하는 명령은 제공하지 않는다.
원본 표는 `(기준일, 원본 키, revision)` 버전 순서로 보존한다. 같은 값 재수집은 신규0건,
A→B→A 정정은 버전1→2→3으로 남는다. 원본 저장과 완료 로그는 같은 트랜잭션이며 단일 DB 세션의 GET_LOCK으로 중복 수집을 차단한다.
이전 값 수정·행 삭제는 없다. 클라이언트의 자동 재연결을 사용하지 않아 잠금 손실 상태로 쓰지 않는다.

종료코드: 0=명령 종료, 1=날짜별 실패 있음, 2=설정/연결/구조 오류, 3=부분 실행·한도·부분 게시, 130=중단.
`empty`는 빈 응답 관측이며 정상 휴장·누락 없는 수집의 증거가 아니다. 모든 날짜의 상태를 함께 확인한다.
DB 접속 오류 원문은 숨기므로 접속·권한 점검은 서버 운영자가 수행한다.

```powershell
uv run --project engine python -m pytest engine/tests/test_fsc_collector.py
```

실제 키/DB 없이 응답·정밀도·페이지·버전·실패·재개·예약 XML을 검사한다. 실서버 수집 성공을 대신하지 않는다.
출처: 제공된 API 가이드3종, [주식시세](https://www.data.go.kr/data/15094808/openapi.do),
[지수시세](https://www.data.go.kr/data/15094807/openapi.do), [상장종목](https://www.data.go.kr/data/15094775/openapi.do).
