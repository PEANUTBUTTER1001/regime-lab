# n8n 워크플로 → 독립 Python 실행 패키지

2026-10-12에 내보낸 `My workflow`(55노드)와 `시트 → Python → DB 자동 동기화`(12노드)를 옮겼습니다. **n8n·Docker·Node.js가 필요 없습니다. Python 3.11 이상과 인터넷은 필요합니다.** Windows 64비트 기준입니다. 기존 regime-lab 제품 코드와 분리한 개인 워크플로 이식본입니다.

이 폴더는 독립 실행 도구입니다. 엔진·API·수집기에서 import하거나 자동 실행하지 않으며, 의존성은 이 폴더의 가상환경에만 설치합니다. 제품 근거 검색·브리핑 통합은 이 변경의 범위에 포함하지 않습니다.

## 가장 먼저

1. GitHub에서 이 작업 브랜치를 선택한 뒤 **Code → Download ZIP**으로 내려받아 압축을 풀어 주세요. `engine/scripts/n8n_python_portable` 폴더를 열면 됩니다. 이 폴더만 다른 PC로 복사해도 됩니다.
2. Python이 없으면 https://www.python.org/downloads/windows/ 에서 64비트 Python 3.11 이상을 설치합니다. 설치 시 `Add Python to PATH`를 선택합니다.
3. `01_setup.bat`를 실행합니다. 필요한 라이브러리를 이 폴더의 `.venv`에 설치합니다.
4. `02_demo.bat`를 실행합니다. **인증정보·인터넷 호출 없이 합성 기사로 전체 처리·DB/Parquet 저장을 확인**하고 결과 화면을 엽니다. 데모 결과는 `demo-output`에만 저장합니다.
5. 아래 인증 설정을 준비한 뒤 `03_run.bat`를 실행합니다. 실제 수집과 Gemini 호출, 시트 저장을 수행합니다. 공급사 요금·호출 한도는 해당 계정에 적용됩니다.

## 인증 설정

API 키는 패키지에 포함되어 있지 않습니다. 기존 n8n의 인증정보를 자동 추출하지 않습니다. `03_run.bat`와 `04_schedule.bat`는 필요한 키를 화면에 숨겨 입력받아 **현재 프로세스 메모리에서만 사용**합니다. 실행 종료 뒤에는 다시 입력합니다. 코드/설정 JSON에 키를 적지 마세요.

| 환경변수 | 용도 |
|---|---|
| `GEMINI_API_KEY` | 연결된 Google Gemini 모델 |
| `NAVER_API_KEY_ID` | NAVER API HUB Client ID |
| `NAVER_API_KEY` | NAVER API HUB Client Secret |
| `DART_API_KEY` | OpenDART 인증키 |
| `GOOGLE_APPLICATION_CREDENTIALS` | Google 서비스 계정 JSON의 파일 경로 |

이미 환경변수가 있으면 입력을 생략합니다. Google 인증은 서비스 계정 JSON의 내용을 `GOOGLE_SERVICE_ACCOUNT_JSON` 환경변수로 제공하는 방식도 지원합니다. 서비스 계정 파일이나 환경변수는 새 PC에서 사용자가 준비합니다. 다른 사람에게 전달하는 ZIP에는 포함하지 마세요.

Google Sheets 연결 준비:

1. Google Cloud 프로젝트에서 **Google Sheets API**를 활성화하고 서비스 계정 인증 파일을 준비합니다.
2. 사용하려는 스프레드시트를 서비스 계정의 `client_email`에 **편집자**로 공유합니다.
3. 실행 시 서비스 계정 JSON 파일 경로를 입력합니다.
4. `config.json`을 `config.local.json`으로 복사하고 `spreadsheet_id`, `sheet_ids`를 설정합니다. 공개 배포본에는 예시 값만 있습니다. `YOUR_SPREADSHEET_ID`를 URL의 문서 ID로, 세 탭 ID를 각 탭 URL의 `gid`로 변경하세요.

첫 행의 열 이름은 `core.py`의 `HEADERS`와 같아야 합니다. 기존 탭을 복사하면 그대로 사용할 수 있습니다. 이식본은 n8n OAuth 토큰 대신 서비스 계정 인증을 사용합니다. 자동으로 계정을 생성하거나 공유 권한을 바꾸지는 않습니다. OpenAI 노드는 연결되어 있지 않아 OpenAI 키는 필요 없습니다.

## 실행 파일

| 파일 | 역할 |
|---|---|
| `01_setup.bat` | 최초 라이브러리 설치 |
| `02_demo.bat` | 합성 데이터 오프라인 시연 |
| `03_run.bat` | 수집 → 분석 → 시트 → 로컬 DB 한 번 실행 |
| `04_schedule.bat` | 매일 09:10 KST 실행 대기 |
| `05_sync_only.bat` | 기존 시트 3개를 DB로 동기화, 수집·AI 호출 없음 |

예약 실행은 창과 PC가 켜져 있어야 합니다. 시작 시 이미 09:10을 지났고 오늘 시도 기록이 없으면 한 번 실행합니다. 실패 시 자동 반복 과금을 피하려고 중단합니다. 원인 수정 뒤 `03_run.bat`로 수동 재실행하세요. `output/schedule.json`에 성공/실패 시도일을 기록합니다. 같은 폴더의 중복 실행은 OS 잠금으로 막습니다. 다른 PC의 n8n과 같은 시트에 동시에 쓰지 않도록 예약 시간을 조정하세요.

명령줄도 지원합니다:

```powershell
.\.venv\Scripts\python.exe runner.py check --prompt-secrets
.\.venv\Scripts\python.exe runner.py run --prompt-secrets
.\.venv\Scripts\python.exe runner.py sync --prompt-secrets
.\.venv\Scripts\python.exe -m unittest -v portable_checks
```

개인 설정을 Git에 남기지 않으려면 `config.json`을 `config.local.json`으로 복사해 수정하세요. 실행 파일은 이 파일이 있으면 우선 사용합니다.

`check`는 로컬 설정과 라이브러리만 확인합니다. 실제 API 인증/모델 사용 권한 검사는 아닙니다.

## Google Sheets 없이 사용

`config.local.json`에서 `"storage": "local"`로 바꾸면 시트 인증 없이 로컬 DB를 읽고 씁니다. 뉴스 수집·Gemini 인증은 여전히 필요합니다. 기존 시트의 처리 이력을 유지하려면 Google Sheets 모드에서 `05_sync_only.bat`를 먼저 실행한 뒤 local로 바꾸세요. 이식 ZIP에는 과거 기사나 DB를 넣지 않았으므로 새 DB는 비어 있습니다. 원래 DB 전체를 옮기려면 원래 서버를 사용하는 사람이 정상 종료·백업한 DB/Parquet 폴더를 별도로 전달해야 합니다.

`sources`에서 필요 없는 수집원을 명시적으로 `false`로 끌 수 있습니다. 비활성 수집원은 결과에 빠지고 해당 인증키를 요구하지 않습니다. 활성 수집원이 실패하면 정상 0건으로 취급하지 않고 중단합니다.

## 결과와 DB

- `output/report.html`: 브리핑·종목별 집계 보기. 원문은 HTML 이스케이프하여 표시합니다.
- `output/raw.json`, `briefing.json`, `stocks.json`: 3개 시트의 현재 내용.
- `output/db/sheets.sqlite`: 기존 DB 서버와 같은 스키마의 SQLite 이력 DB.
- `output/db/parquet/kind=raw|briefing|stocks/current.parquet`, `history.parquet`: 현재/이력 내보내기.
- `output/last_run.json`: 성공한 실행 집계. 실패 시 이 파일을 새 성공으로 덮어쓰지 않습니다.

기존 서버의 `store.py`, `normalize.py`를 재사용했습니다. `versions`, `current`, `quarantine`, `imports`, `export_state`, `current_records` 구조를 유지하며 변경된 행만 새 버전으로 저장합니다. 최초 관측/이용 가능 시각과 게시일을 구분합니다. 이제 서버 HTTP 호출 없이 같은 저장 함수를 직접 호출하므로 **별도의 8765 서버와 X-Import-Key는 필요 없습니다.** 원래 PC의 DB/서버/키를 건드리지 않습니다. 출력 파일에는 처리한 기사와 분석 내용이 있으므로 공개 저장소에 올리지 마세요.

## 보존한 로직과 차이

보존: 한경·매경 RSS, 네이버 검색어 4개×30건, DART Y/K×B/I 각 10건, 월요일 금요일부터/다른 날 전일부터 수집, 원자료·처리 이력, 새 뉴스 30·공시 10건, 5건 묶음, 공시 전용 묶음 AI 생략, 기사 본문 허용 도메인, 숫자·출처·관련성·상장종목·영향 근거 검증, 원문 숫자 검증 실패 시 제공 요약 사용, 결과 저장 후 완료 상태 갱신, 종목별 합산, DB 버전/격리/Parquet 복구.

차이/한계:

- 모든 실행 코드는 Python입니다. 원래 n8n Code 노드 JS는 배포하지 않습니다.
- Gemini를 REST structured output으로 직접 호출합니다. 원래 프롬프트/JSON 스키마를 사용하지만, n8n의 에이전트/형식 복구 모델 호출은 재현하지 않습니다. 출력 스키마 실패 시 추가 모델 호출 없이 중단합니다. LLM 출력이 이전 실행과 문자 단위로 같다고 보장하지 않습니다.
- 모델명은 원래 설정 `gemini-3.5-flash-lite`를 유지했습니다. 새 PC에서 사용하는 계정에 이 모델이 없으면 `config.json`의 `gemini_model`을 이용 가능한 모델명으로 바꿔야 합니다.
- 상장사 목록은 원래 **2026-10-01 스냅샷**입니다. 자동 갱신하지 않습니다. DART의 숫자 6자리 종목 필터도 원본대로 유지해 영문자 포함 종목코드는 제외됩니다.
- 미래 게시 시각은 제외하도록 추가했습니다. 공시 접수일은 KST 날짜로 파싱하며 실제 공개 시각으로 주장하지 않습니다.
- 기사 집계 정렬은 Python 문자열 순서라 원래 JS 한국어 locale 정렬과 순서가 다를 수 있습니다.
- Google Sheets는 서비스 계정 인증으로 바꾸고, 고정 행 주소의 RAW batchUpdate로 재시도 시 중복 추가를 막습니다. 같은 식별자가 여러 행에 있으면 덮어쓰지 않고 중단합니다. 시트 동시 편집·다른 PC 실행까지 분산 잠금으로 막지는 않습니다.
- 원래 노드 설정에는 RSS 제한값이 없어, 메모의 한경/매경 각 50건을 `rss_limit_per_source`로 명시했습니다. 나머지 상한/대기시간은 config.json에 있습니다.
- DB에 시트에서 삭제된 행을 자동 삭제하지 않는 원래 버전 저장 동작을 유지합니다. 외부 본문/AI 호출의 실제 운영 검증은 이 패키지 생성 중 수행하지 않았습니다.

뉴스·공시를 정리한 참고 자료이며 실제 가격 전망이나 매매 신호가 아닙니다.

## 검증과 참고

전체 테스트는 이 폴더에서 다음 명령으로 실행합니다. 저장소 엔진 테스트와 별도 의존성으로 실행하며, CI에도 별도 작업으로 등록했습니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest portable_checks.py bridge_checks.py -q
```

`portable_checks.py`는 시간대·월요일 경계, 미래 시각 제외, 중복/재실행, 출처·숫자·근거·권유 표현 차단, 공시 AI 생략, 실패 시 완료 상태 미갱신, DB 재수입/복구 등을 합성 입력으로 검사합니다. `bridge_checks.py`는 원래 저장 코드의 테스트입니다. 검증 상세는 `VALIDATION.md`에 있습니다.

- Gemini structured output: https://ai.google.dev/gemini-api/docs/generate-content/structured-output
- Google Sheets batchUpdate: https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate
- NAVER API HUB 인증 헤더: https://guide.ncloud-docs.com/docs/apihub-migration

원본 추적 정보는 `provenance.json`에 있습니다. 키·원래 실행 데이터·원본 DB는 이 배포본에 포함되지 않습니다.
