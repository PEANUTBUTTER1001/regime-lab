# 뉴스 메타데이터 DB 자동 전송 운영 도구

사용 중인 로컬 저장 프로그램을 재현하는 수동 실행 운영 도구다. 프로젝트 앱의 런타임이나 정식 수집기로 자동 등록하지 않으며, 기본 설정에서는 원격 전송이 꺼져 있다. 이번 운영 도구는 사용자의 명시적 병합 요청에 따라 포함한다. `docs/P3-P4_외부자료_브리핑_로드맵.md` §2·A0의 기존 n8n 독립 앱 이식본 처리나 프로젝트 런타임에 Sheets 의존성을 넣지 않는 결정은 변경하지 않는다. 정식 수집기 이관은 기존 ingest 구조에서 별도 작업으로 다룬다.

## 하는 일

기존 워크플로 → loopback `POST /v1/import` → 로컬 SQLite 이력/Parquet 확정 → raw 자료의 메타데이터를 서버 DB `ext_document_*` 네 테이블에 동기화한다.

- raw만 원격 전송. 제목·원기사 URL·ID·버전·게시 시각·실제 관측/이용 가능 시각·보관 범위·내용 지문을 보존한다.
- RSS 요약·본문·브리핑·종목별 판단은 원격 전송하지 않는다. briefing/stocks 요청은 기존 로컬 저장만 수행한다.
- 동일 `(doc_id, version)`의 내용이 다르면 배치 전체를 실패시킨다. 동일 자료 재전송은 신규 입력 0건이다. UPDATE/DELETE/CREATE 권한을 사용하지 않는다.
- 서버 입력과 전체 필드/해시/시각/manifest/멤버십 대조가 끝난 후 commit한다. 실패하면 rollback하고 HTTP 503을 반환한다. 로컬 원자료는 유지한다.
- coverage는 unknown이다. 운영 도구의 수집/전송 성공을 정식 출처 승인이나 전체 수집 증거로 해석하지 않는다.
- 현재는 raw 이력 전체를 대조하는 방식이다. 데이터가 커지면 배치 시간/메모리를 측정한 뒤 별도 증분 전송 설계가 필요하다.

## 준비

Python 3.12와 기존 `ingest`의 pyarrow/pytest를 사용한다. 새 Python 의존성은 없다. DB 전송은 Windows에 설치한 MariaDB 10.11 CLI로 검증했으며 다른 운영체제는 미검증이다.

1. 서버 담당자가 C3 v3 계약과 `schema.sql`을 대조해 표를 생성한다. 기존 표를 자동 변경/삭제하지 않는다. 수집 계정에는 네 표 SELECT·INSERT만 부여한다.
2. `config.example.json`을 Git에서 제외된 `config.local.json`으로 복사한다. 모든 경로는 사용자 PC의 절대 경로로 바꾸고, `store_root`는 저장소 밖에 둔다.
3. 기존 로컬 import 키 파일 경로와 spreadsheet_id를 설정한다. 키는 32자 이상이며 n8n의 Header Auth 자격증명 `X-Import-Key`와 같아야 한다. 키 값을 코드/워크플로 JSON/공개 문서에 넣지 않는다.
4. `collector.env.example`의 항목을 저장소 밖 개인 env 파일에 작성하고 `remote_db.env_file`에 그 경로를 지정한다. 예시는 실제 접속 정보가 아니다. DB 비밀번호는 CLI 인자가 아닌 자식 프로세스 환경에 전달된다.
5. `remote_db.client_executable`에 MariaDB 실행 파일 경로를 넣고, 준비가 끝나면 `enabled`를 true로 설정한다. 예시는 외부 전송 방지를 위해 false다.

저장소 루트에서 실행:

```powershell
uv run --project ingest python engine/scripts/news_db_bridge/server.py --config engine/scripts/news_db_bridge/config.local.json
```

8765가 사용 중이면 기존 프로그램/요청 상태를 확인하고 운영자 승인 후 교체한다. 이 저장소 사본을 실행해도 현재 운영 프로그램 파일을 자동 교체하거나 서버를 재시작하지 않는다.

## n8n 연결

기존 원자료 DB 저장 노드가 사용하는 설정을 유지한다.

- HTTP POST `http://127.0.0.1:8765/v1/import`
- Header Auth `X-Import-Key`, JSON body `{spreadsheet_id, kind: "raw", rows: [...]}`
- 각 원자료 행의 열 이름: `원기사 제목`, `출처`, `게시 시각`, `최초 수집 시각`, `RSS 요약`, `처리 상태`. 원격에는 요약을 보내지 않는다. 실제 이용 가능 시각은 로컬 버전 관측 시각 기준이다.
- 기존 노드의 최대 3회 재시도·120초 timeout 사용. 실패 후 다음 원자료 동기화에서도 전체 이력을 다시 대조한다. 별도 백그라운드 재시도나 예약을 추가하지 않는다.
- n8n이 Docker/다른 PC에서 실행되면 127.0.0.1은 다른 환경이다. 이 도구는 같은 Windows PC에서 구동한 n8n 연결만 검증했다. 외부 공개 바인딩을 자동으로 늘리지 않는다.

응답의 기존 로컬 결과 필드는 유지하고 raw 요청에 `remote_db`를 추가한다. `remote_db.status=ok`와 snapshot_id/문서 수/버전 수/new_versions/reused_versions/confirmed_at을 확인한다. 원자료가 없으면 `no_raw_data`이며 DB 동기화 성공이 아니다. 오류는 `503 remote_sync_unavailable`, `retry_safe=true`, `local_result`를 반환한다. 기존 동기화 결과 노드가 응답 전체를 results에 담으면 추가 필드도 함께 확인할 수 있다.

인증된 `GET /v1/status`에 최근 원격 상태가 포함된다. `store_root/remote_sync/status.json`에는 비밀값 없는 상태만 남고 변환한 불변 스냅샷은 `remote_sync/snapshots` 아래에 쌓인다. 삭제/보존 정책은 운영자가 별도로 정한다. 로그/원자료/상태 파일은 커밋하지 않는다.

## 파일과 검증

- `server.py`: 인증·입력 제한과 HTTP 연결, raw 저장 뒤 동기화 호출.
- `store.py`, `normalize.py`: 기존 로컬 버전 저장/정규화와 장애 시 Parquet 복구.
- `search_snapshot.py`: raw 이력에서 메타데이터 전용 docs 사본 생성.
- `remote_transfer.py`: C3 DB 트랜잭션·중복/충돌·해당 스냅샷 전수 대조, 수동 CLI는 기본 rollback이고 `--commit`일 때만 확정.
- `remote_sync.py`: 원자료 후속 전송, advisory lock, 상태 파일과 실패 재시도. 운영 도구 내부에서만 사용.
- `schema.sql`: 서버 담당자의 수동 검토/생성용. 시작 시 실행하지 않음.

```powershell
# basetemp 상위 폴더를 먼저 만들고 매 실행 새 이름을 사용한다.
New-Item -ItemType Directory -Force cache/bridge-tests
uv run --project ingest python -m pytest engine/scripts/news_db_bridge -q --basetemp cache/bridge-tests/run-1 -o cache_dir=cache/bridge-tests/pytest-cache
```

합성 테스트는 인증 거부, 로컬 실패 시 미전송, 원격 실패 503/로컬 보존/재시도, 변경 버전 보존, 메타데이터 전송 범위, briefing/stocks 미전송, 상태 파일의 Windows 잠금 재시도 등을 검사한다. DB/Sheets 접속값 없이 실행된다.

개인 운영 환경(2026-10-13)에서는 원본 5,966문서·6,068버전 전수 대조, Parquet 왕복 후 32개 검색 조건 일치, 실제 HTTP 경로 2회 호출의 신규 입력 0건을 확인했다. 실제 자료·접속값·개인 경로는 이 브랜치에 없다. 다른 PC 재현과 새로운 기사 수집부터의 전체 워크플로 실행은 별도 확인 대상이다.

앱의 기사 조회 API·자동 색인 갱신·삭제 전달 계약은 이 도구에 포함되지 않는다.

### 저장소 사본 검증 (2026-10-13)

- `uv run --project engine python -m pytest engine`: 479 passed, 47 skipped, 1 failed. 실패는 변경하지 않은 `test_search_runs.py::test_same_input_same_record`의 Windows 폴더 교체 PermissionError였고, 새 basetemp의 해당 테스트 재실행은 1 passed였다. 최초 전체 실행이 완전히 통과한 것으로 기록하지 않는다.
- `uv run --project api python -m pytest api`: 114 passed, 39 skipped. anyio 별칭 폐기 예고 경고 1개.
- `uv run --project ingest python -m pytest ingest`: 139 passed.
- 운영 도구 테스트만 실행: 32 passed. 위 engine 전체 수집에도 포함된다.
- 커밋 대상에 실제 DB 비밀번호/import 키·개인 경로가 없음을 검사하고 `git diff --cached --check`를 통과했다.

### PR #78 리뷰 반영 (2026-10-14)

기존 키 확인은 전송할 `(doc_id, version)`만 조회한다. `source`·해시 충돌을 먼저 거절하고, 본문·파생 열 검증은 해당 스냅샷 멤버에만 수행한다. 다른 출처의 summary_link 자료는 읽거나 metadata_only 정책으로 해석하지 않는다. Windows 전용 프로세스 플래그가 없는 OS는 플래그 0을 쓴다(MariaDB 실행 파일은 수집 PC에 별도 필요).

운영 도구 테스트 38개 통과. 가짜 SQL 클라이언트로 실제 transfer 함수의 INSERT/검증/COMMIT/ROLLBACK을 실행해 dry-run 무기록, 같은 키 내용 충돌, 불변 멤버십 변경 거절, 신규 버전 추가·기존 버전 재사용, 범위 밖 출처 공존을 확인했다. API 114 통과/39 원본 없음 skip, ingest 139 통과. 실서버 DB에는 이 테스트로 접속하지 않았다.
