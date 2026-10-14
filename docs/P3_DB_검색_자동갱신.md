# 공시·뉴스 HTTP 자동 갱신 (P3-11)

**실시간 뉴스 검색이 필요한 PC만 설정. 엔진·예측 작업만 하면 불필요.**

팀원 PC는 공통 데이터 서버의 조회 API에서 스냅샷을 받아 검증한 뒤 로컬 Parquet으로 검색한다. DB 계정, MariaDB 클라이언트, 수집 PC의 접속 파일은 필요하지 않다. 수집 PC의 DB 쓰기 경로와는 별개다.

## 팀원 PC에서 실행

Python 환경 관리 도구 `uv`와 Git을 설치한 뒤 PowerShell에서 실행한다. 병합 전 검증은 PR #81의 `fix/P3-11-project-news-refresh` 브랜치를 사용하고, 병합 후에는 최신 `develop`을 사용한다.

```powershell
git clone https://github.com/PEANUTBUTTER1001/regime-lab.git
cd regime-lab
git switch develop
git pull --ff-only
uv sync --project engine
uv sync --project api
$env:REGIME_DATA_API_URL = "<서버 담당에게 받은 주소>" # 가격 동기화와 같은 값
$env:REGIME_NEWS_REFRESH = "1"
# 서버가 키 인증을 켠 경우만 터미널에서 설정:
# $env:REGIME_DATA_API_TOKEN = "<서버 담당에게 받은 키>"
uv run --project api regime-api
```

브라우저에서 `http://127.0.0.1:8000`을 연다. 기본 데모 로그인으로 테스트할 수 있다. 실제 로그인 설정은 [X3 로그인 설계](X3_로그인_설계.md)를 따른다. 포트를 바꾸려면 실행 전에 `$env:REGIME_PORT = "8001"`을 설정한다.

- 접속값은 각 PC의 서버 실행 터미널 환경변수에만 설정한다. Git·PR·채널·로그에 주소나 키를 남기지 않는다.
- `REGIME_NEWS_REFRESH`를 켜지 않으면 기존 `ext_store`의 로컬 Parquet 읽기를 그대로 사용한다.
- 첫 다운로드는 백그라운드에서 실행한다. 웹은 먼저 열리며 공시·뉴스 검색은 검증 완료까지 `warming_up`을 반환한다. 가격 데이터가 없어도 뉴스 갱신은 별도로 동작한다.
- `REGIME_SAMPLE=1`은 기존 `sample30` 파일이 있는 PC만 사용한다. 뉴스만 확인할 때는 설정하지 않는다.
- 서버가 스냅샷 API를 제공해야 실제 다운로드가 된다. **2026-10-14 리뷰 반영 시점에는 최종 서버 응답 규격과 실서버 연동 확인이 남아 있다.** 아래 잠정 응답 규격은 서버 없이 계약 테스트로 검증했다.

## 갱신 주기와 저장 위치

`engine/config/default.yaml`의 `news_refresh`에 출처 두 개(`n8n_news`, `n8n_disclosure`), 갱신 주기 30초, HTTP 제한 시간 30초, 페이지 크기 1,000건, 응답당 16MiB, 스냅샷당 최대 200,000버전을 둔다. 한 번의 갱신이 끝난 뒤 다음 주기까지 기다리므로 겹쳐 실행하지 않는다.

저장 위치는 `engine/config/paths.example.yaml`의 `news_snapshots`이며 기본은 저장소 옆 `../regime-lab_news_snapshots`이다. `paths.local.yaml`이 이미 있으면 새 키가 없어도 같은 기본값을 쓴다. 바꾸려면 그 파일에 저장소 밖 경로를 추가한다.

```yaml
news_snapshots: ../regime-lab_news_snapshots
```

저장소 안으로 해석되는 경로는 거절한다. `snapshots/<snapshot_id>-<고유값>/docs/`와 `status.json`을 만든다. 기존 정상 사본을 덮어쓰거나 자동 삭제하지 않는다. 프로세스마다 고유한 폴더를 쓰며 최신 정상 사본으로 검색 인덱스를 교체한다. 재시작 시에는 서버에서 다시 검증해 내려받는다.

`status.json`의 `status: ok`, `last_success.versions`로 적용 상태를 확인한다. 실패하면 `status: stale`, 고정 오류 코드 `news_api_refresh_unavailable`만 기록하고 기존 검색 인덱스를 유지한다. 첫 갱신에 실패하면 검색은 준비 중이며 다음 주기에 다시 시도한다. 원격 오류 본문·주소·키는 상태 파일에 기록하지 않는다.

## 무결성 검증

- 스냅샷 ID, 승인 출처, metadata_only·coverage unknown 계약을 검사한다.
- 출처별 매니페스트 버전 수 = 출처별 멤버 수 = 실제 받은 버전 수를 대조한다. 매니페스트만 먼저 적재된 스냅샷은 거절하고 다음 주기에 재시도한다.
- 각 문서의 해시·파생 시각·출처·키·중복과 페이지 순서를 확인한다. 같은 스냅샷 ID가 유지되면 재다운로드하지 않는다.
- Arrow 스키마로 Parquet을 저장한 뒤 다시 읽어 모든 값과 스키마를 비교한다. 검색 인덱스의 버전 수도 일치해야 메모리의 기존 인덱스를 교체한다.
- 기존 `as_of`의 이용 가능 시각 기준과 제목·회사명 검색 범위를 유지한다. 게시 시각을 이용 가능 시각으로 바꾸지 않는다.

## 잠정 HTTP 응답 규격 (PR #81 리뷰 기반)

서버 최종 규격은 [F1 데이터 서버 API](F1_데이터_서버_API.md)에 확정될 예정이다. 아래 JSON 외곽 필드 이름을 가짜 HTTP 응답에 명시해 검증했다. 최종 규격과 다르면 읽기 어댑터와 계약 테스트를 함께 맞춰야 한다.

모두 GET이며 `REGIME_DATA_API_TOKEN`이 있으면 `X-API-Key` 헤더로만 보낸다. 리디렉션은 거절한다.

1. `/v1/ext/snapshots/latest?sources=n8n_news,n8n_disclosure`

```json
{"snapshot_id": "<64자리 소문자 SHA-256>"}
```

완성 후보가 없으면 404. 모든 출처의 매니페스트가 있어도 멤버 적재 중일 수 있어 추가 검증한다.

2. `/v1/ext/snapshots/{snapshot_id}`

```json
{
  "snapshot_id": "<동일 ID>",
  "manifests": [
    {"source": "n8n_news", "manifest_json": "<원문 JSON 문자열>", "arrow_schema_json": "<원문 JSON 문자열>", "document_versions": 1, "coverage_state": "unknown", "coverage_rows": 0},
    {"source": "n8n_disclosure", "manifest_json": "<원문 JSON 문자열>", "arrow_schema_json": "<원문 JSON 문자열>", "document_versions": 1, "coverage_state": "unknown", "coverage_rows": 0}
  ],
  "member_counts": {"n8n_news": 1, "n8n_disclosure": 1},
  "coverage_rows": 0
}
```

3. `/v1/ext/snapshots/{snapshot_id}/documents?source=…&limit=1000` (다음 쪽은 `after_doc_id`, `after_version` 추가)

```json
{
  "snapshot_id": "<동일 ID>",
  "source": "n8n_news",
  "rows": [{"doc_id": "n8n_news:example", "version": 1, "source": "n8n_news", "status": "active", "license_scope": "metadata_only", "first_seen_at_utc": "2026-10-14 00:00:00.000000", "available_at_utc": "2026-10-14 00:00:00.000000", "record_sha256": "<해시>", "document_json": "<원문 JSON 문자열>"}],
  "next_cursor": null
}
```

행은 `(doc_id, version)` 오름차순. 더 있으면 `next_cursor`는 마지막 행의 `{"doc_id": "…", "version": 1}`이고 마지막 쪽만 null이다. JSON 열 세 개는 객체로 재인코딩하지 않고 원문 문자열, 버전·개수는 JSON 정수, 파생 시각은 UTC 문자열이다.

## 검증과 남은 실서버 확인

CI는 가짜 HTTP로 정상 페이지 처리, 해시·개수·멤버십·출처·왕복 불일치, 미완성 적재, 오류 후 이전 자료 보존과 재시도, 응답 제한·리디렉션 거절, 웹 시작 비차단을 검사한다.

서버 규격 확정 후 DB 설정이 없는 PC에서 다음을 확인한다: 웹 접속 → 상태 ok/버전 수 일치 → 현재 검색 → 새 스냅샷 반영 → 과거 as_of 절단 → 서버 장애 중 이전 결과 유지와 복구 → 가격 store 없는 PC에서 뉴스 검색. 서버 주소와 토큰은 서버 담당에게 비공개로 받아 환경변수로 설정한다.
