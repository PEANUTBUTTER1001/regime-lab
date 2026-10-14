# 다른 PC에서 뉴스 DB 자동 조회하기 (P3-11)

이 기능은 각 PC의 웹 서버가 공통 뉴스 DB를 읽는 기능이다. 작성자의 PC 경로, 8001 포트, 수집기 실행 파일을 복사할 필요는 없다. 30초는 설정 예시이며 실제 주기는 `poll_seconds`를 따른다. `REGIME_NEWS_DB_CONFIG`가 없으면 기존 로컬 Parquet 방식으로 실행된다.

## 기능별 준비 범위

| 필요한 기능 | 코드/설정 |
|---|---|
| Google·카카오 로그인 | PR #71과 팀의 Supabase 프로젝트 URL·공개 키. 뉴스 DB 설정은 불필요 |
| DB의 최신 공시·뉴스 검색 | PR #81과 아래 DB 읽기 설정 |
| n8n 수집 자료를 DB에 넣기 | PR #78의 수집 PC 운영 설정. 조회만 하는 팀원 PC에서는 실행하지 않음 |

각 PR 병합 전에는 해당 기능 브랜치만 확인할 수 있다. 로그인과 자동 조회를 함께 쓰려면 두 PR이 반영된 develop을 받는다. Supabase 공개 키와 뉴스 DB 접속 계정은 서로 다른 용도다.

## 1. 코드 받기

Git·uv·Python 3.12를 준비하고 원하는 폴더에 저장소를 clone한다. 이미 있으면 저장소 루트에서 실행한다.

```bash
git clone https://github.com/PEANUTBUTTER1001/regime-lab.git
cd regime-lab
```

PR #81 병합 전에는 다음 브랜치를 받는다. 로컬 수정이 있는 저장소는 별도 clone을 이용한다.

```bash
git fetch origin
git switch fix/P3-11-project-news-refresh
git pull --ff-only origin fix/P3-11-project-news-refresh
```

병합 후에는 위 브랜치 선택 대신 `git switch develop`, `git pull --ff-only origin develop`을 실행한다.

```bash
uv sync --project engine
uv sync --project api
```

## 2. DB 관리자에게 받을 것

`Seonghwanaa`에게 해당 PC에서 접근 가능한 DB 주소·포트, `regime_lab` 접속 계정과 접속 설정 파일을 요청한다. 방화벽/네트워크 경로와 해당 PC의 DB 접속 권한도 확인한다. 인터넷에 DB를 새로 공개할 필요는 없다.

이 웹 조회 기능에 필요한 테이블은 `ext_document_manifests`, `ext_document_versions`, `ext_document_snapshot_members`, `ext_document_coverage`이며 SELECT 권한을 사용한다. 승인된 자료가 DB에 이미 적재되어 있어야 한다.

접속 파일은 저장소 밖에 두며 다음 이름의 값이 필요하다. 아래는 형식만 보여 주는 예시이며 그대로는 연결되지 않는다. 값에 따옴표를 붙이지 않는다.

```text
DB_HOST=<접속 가능한 DB 서버 주소>
DB_PORT=<DB 포트>
DB_NAME=regime_lab
DB_USER=<발급받은 계정>
DB_PASSWORD=<발급받은 비밀번호>
```

DB 접속 파일을 Git·PR에 올리지 않는다. 다른 팀원의 세션 폴더나 작성자 PC의 개인 설정 파일을 복사하지 않는다.

## 3. 이 PC용 설정 만들기 (Windows PowerShell)

MariaDB 명령행 클라이언트가 필요하다. `Get-Command mariadb.exe`가 설치 위치를 찾을 수 있어야 한다. PATH에 없다면 아래 `$newsClientPath`에 **이 PC에서 확인한** `mariadb.exe` 전체 경로를 넣는다. 버전이나 설치 경로를 작성자 PC와 맞출 필요는 없다.

```powershell
$newsLocalRoot = Join-Path $env:LOCALAPPDATA "regime-lab/news"
New-Item -ItemType Directory -Force -Path $newsLocalRoot | Out-Null
$newsClientPath = (Get-Command mariadb.exe -ErrorAction Stop).Source
$newsConnectionFile = "<관리자에게 받은 이 PC의 DB 접속 파일 전체 경로>"
$newsConfigPath = Join-Path $newsLocalRoot "news-db.json"
@{
    poll_seconds = 30
    sources = @("n8n_news", "n8n_disclosure")
    env_file = $newsConnectionFile
    client_executable = $newsClientPath
    runtime_dir = (Join-Path $newsLocalRoot "runtime")
} | ConvertTo-Json | Set-Content -LiteralPath $newsConfigPath -Encoding utf8
```

설정 파일에는 비밀번호 값 대신 접속 파일의 경로만 들어간다. 자료 저장 폴더는 저장소 밖이어야 한다. macOS/Linux도 같은 JSON 필드를 쓰되 접속 파일·클라이언트·runtime_dir을 각 PC의 절대 경로로 설정하고 `REGIME_NEWS_DB_CONFIG`를 export한다.

## 4. 서버 실행

위 설정을 만든 **같은 PowerShell**에서 저장소 루트로 이동해 실행한다. 8000은 예시이며 8001로 고정된 기능이 아니다.

```powershell
$env:REGIME_NEWS_DB_CONFIG = $newsConfigPath
$env:REGIME_HOST = "127.0.0.1"
$env:REGIME_PORT = "8000"
uv run --project api regime-api
```

로그인도 사용하려면 먼저 PR #71 실행 안내의 인증 환경변수를 같은 터미널에 설정한다. 그때 브라우저 주소·REGIME_PORT·REGIME_AUTH_SITE_URL·Supabase callback 허용 포트가 모두 같아야 한다. 기본 접속은 `http://127.0.0.1:8000`이다. 기존 서버는 설정을 자동으로 다시 읽지 않으므로 운영자가 확인한 뒤 재시작한다.

## 5. 확인 방법

1. 새 터미널에서는 경로 변수를 다시 지정한다. 위 터미널에서는 다음으로 갱신 상태를 읽는다.

```powershell
Get-Content (Join-Path $newsLocalRoot "runtime/status.json")
```

2. `status: ok`, 최근 `checked_at`, `last_success`의 `snapshot_id`·문서 수를 확인한다. 문서 수는 DB 수집에 따라 변하므로 특정 숫자와 같아야 하는 것은 아니다.
3. 공시·뉴스 검색 화면의 기준 시각을 현재로 맞추고 검색한다. DB 새 스냅샷이 반영된 뒤 다시 검색하면 새 자료를 사용한다. 이미 표시된 결과가 저절로 다시 그려지는 기능은 아니다.
4. `stale` 또는 `db_refresh_unavailable`이면 DB 접근 권한·네트워크·접속 파일·클라이언트 경로·완성된 스냅샷을 확인한다. 마지막 정상 색인이 있으면 유지한다. 최초 적재까지 실패하면 기존 로컬 자료만 보이거나 자료 없음으로 표시될 수 있다.

## 저장 및 운영 범위

- 읽기 전용 트랜잭션에서 승인된 두 출처의 제목·메타데이터를 읽는다. 회원가입 정보나 분석 결과를 업로드하는 기능이 아니다.
- 버전 수·멤버십·해시·보관 범위와 Parquet 왕복 검증을 통과한 새 색인만 교체한다. 원본 시각을 보존하고 기존 as_of 제한을 적용한다.
- 새 스냅샷이 없으면 다운로드·색인 생성을 건너뛴다. 실패 후에는 다음 주기에 재시도하며 상태 파일에 원격 오류 문자열·비밀값을 기록하지 않는다.
- 수집 PC는 n8n과 저장 브리지를 운영하고, 조회 PC는 DB에 접속 가능한 웹 서버만 실행하면 된다. 이 설정은 Windows 자동 시작이나 절전 방지를 등록하지 않는다.
