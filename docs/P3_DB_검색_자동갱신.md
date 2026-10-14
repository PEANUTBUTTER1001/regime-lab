# DB 자료 검색 자동 갱신

기존 `uv run --project api regime-api` 실행에 선택적으로 연결한다. `REGIME_NEWS_DB_CONFIG`가 없으면 기존 로컬 Parquet 동작을 유지한다. UI와 인증 모드는 기존 프로젝트 설정을 따른다.

## 로컬 설정

저장소 밖에 다음 JSON을 만든다. 경로는 각 PC의 실제 경로로 바꾼다. DB 접속 권한과 MariaDB CLI가 필요하다. 비밀번호나 API 키를 이 JSON이나 Git에 넣지 않는다. `env_file`은 기존 승인된 DB 접속 설정 파일 경로다.

```json
{
  "poll_seconds": 30,
  "sources": ["n8n_news", "n8n_disclosure"],
  "env_file": "C:/local-config/regime_server.env",
  "client_executable": "C:/Program Files/MariaDB 10.11/bin/mariadb.exe",
  "runtime_dir": "C:/local-data/regime-news"
}
```

프로젝트 루트에서 다음과 같이 실행한다. 이미 실행 중인 서버에는 적용되지 않으므로 운영자 확인 후 재시작한다.

```powershell
$env:REGIME_NEWS_DB_CONFIG = "C:/local-config/news-db.json"
$env:REGIME_PORT = "8001"
uv run --project api regime-api
```

## 동작과 확인

- 시작 시 DB를 읽고, 이후 설정된 간격마다 완성된 최신 스냅샷을 확인한다. DB에는 쓰지 않는다.
- 승인된 두 출처의 제목·메타데이터만 읽는다. 회원가입 정보나 분석 결과를 이 기능으로 업로드하지 않는다.
- 버전 수·멤버십·해시·보관 범위와 Parquet 왕복 검증을 통과한 새 검색 색인만 교체한다. 자료는 저장소 밖 고유 폴더에 남는다.
- 새 스냅샷이 없으면 다운로드와 색인 생성을 건너뛴다. 연결·검증 실패 시 마지막 정상 검색 자료를 유지하고 다음 주기에 재시도한다.
- `runtime_dir/status.json`에서 `status`, `checked_at`, `last_success`를 확인한다. 원격 오류 문자열과 비밀값은 기록하지 않는다.
- 갱신 이후 검색 요청부터 새 자료를 사용한다. 이미 열린 결과는 검색을 다시 실행해야 한다. 과거 기준 시각을 선택한 경우 그 시각 이후 자료는 표시하지 않는다.
- PC·수집기·DB·웹 서버가 실행되어야 전체 수집/조회 흐름이 동작한다. 이 설정 자체가 Windows 자동 시작을 등록하지는 않는다.
