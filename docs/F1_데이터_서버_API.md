# F1 데이터 서버 API와 동기화

> WBS F1 (담당 Seonghwanaa) · 결정 F1a (2026-10-12) · 로드맵 S1 · 공동 지시서 3부 C1·C3
> 기준: 2026-10-13 데이터 서버 PC 에서 실제로 호출해 확인한 명세. **서버 주소·인증 키·계정은 이 문서와 저장소에 적지 않는다.** 아래 예시 값은 자리표시자다.

## 1. 구성

| 위치 | 역할 |
|---|---|
| 데이터 서버 PC | MariaDB `regime_lab`(표 12개 + 뷰 `v_store_daily`)와 조회 전용 API. 코드는 저장소 밖 |
| 수집 PC | 수집 결과를 서버 DB 에 추가 — 공동 지시서 C2 합의 뒤 |
| 팀원 PC | **원본을 받지 않는다(결정 F1d, 2026-10-14).** 개발은 합성 데이터로 하고, 서비스는 아래 앱 서버를 브라우저로 쓴다. (F1a 때는 `sync_data_server.py` 로 받아 Parquet 로 저장했다) |
| 앱 서버 (데이터 서버 PC) | 결정 F1b(2026-10-14): 팀 웹 앱을 이 PC 한 곳에서 서비스로 실행하고 팀원은 같은 망 브라우저로 접속 — 사용자 PC 에 데이터를 두지 않음 (§6) |

## 2. 엔드포인트 (모두 GET, 쓰기 없음)

| 경로 | 설명 | 응답 |
|---|---|---|
| `/health` | 서버와 DB 상태 (`SELECT 1`) | `{"ok": true}` · DB 실패 503 `{"ok": false, "detail": …}` |
| `/tables` | 표 13개의 설명·종목 열·기간 열·필수 조건·열 목록 | JSON |
| `/v1/{table}` | 표 조회 | JSON 또는 CSV |
| `/v1/ext/snapshots/…` | 기사 메타데이터 스냅샷 조회 3개 (아래 "기사 메타데이터 스냅샷") | JSON |
| `/guide` | 서버의 사용 가이드 원문 | text/plain |
| `/docs`, `/redoc`, `/openapi.json` | FastAPI 자동 문서 (`/docs`·`/redoc` 은 화면 스크립트를 인터넷 CDN 에서 받음) | HTML / JSON |

### `/v1/{table}` 파라미터

| 이름 | 형식 | 설명 |
|---|---|---|
| `ticker` | `005930` 또는 `005930,000660` | 종목코드, 최대 100개 |
| `start`, `end` | `2026-09-01` 또는 `2026-09-01T09:00` | 기간(양끝 포함). 날짜만 주면 그날 전체 |
| `columns` | `ticker,name` | 받을 열. 비우면 전체 |
| `format` | `json`(기본) 또는 `csv` | |

- 한도: 결과 **200,000행**, 쿼리 **30초**. 넘으면 잘라서 주지 않고 400(`결과가 200,000행을 넘습니다…`)·504 를 낸다. 페이지 처리는 없다 — 받는 쪽이 종목·기간으로 나눈다.
- 정렬: 종목 열, 기간 열 순.
- JSON 구조: `{"table": "<표>", "count": <행 수>, "columns": [...], "rows": [[...], ...]}` — 행은 배열이고 값 순서는 `columns` 와 같다. 날짜·시각은 문자열.

### 표와 필수 조건

| 표 | 종목 열 | 기간 열 | 필수 조건 | store 파일 |
|---|---|---|---|---|
| `store_master` | ticker | — | 없음 | `master.parquet` |
| `store_delisted` | ticker | — | 없음 | `delisted.parquet` |
| `store_index` | market | date | 없음 | `index.parquet` (열 이름 `index`) |
| `store_prices` | ticker | date | ticker 또는 start+end | `prices.parquet` |
| `store_marketcap` | ticker | date | ticker 또는 start+end | `marketcap.parquet` |
| `store_krx_daily` | ticker | date | ticker 또는 start+end | `raw/krx_daily/YYYYMMDD.parquet` |
| `store_sector` | ticker | date | ticker 또는 start+end | `raw/sector/YYYYMMDD_<시장>.parquet` |
| `stock_minutes` | code | dt | ticker 와 start+end 모두 | `../minutes/stock_minutes_YYYYMM.parquet` |
| `v_store_daily` (뷰) | ticker | date | ticker 또는 start+end | — (엔진 `load_daily` 와 같은 조인) |
| `kor_price`, `kor_indicators`, `kor_def_info`, `theme_finup_stock` | — | — | 표마다 다름 | 엔진 store 아님 (덤프 원본·참조용) |

### 응답 예시 (값은 자리표시자)

`/v1/store_master?ticker=000001&columns=ticker,name`
```json
{"table": "store_master", "count": 1, "columns": ["ticker", "name"], "rows": [["000001", "예시종목"]]}
```

`/v1/store_prices?ticker=000001&start=2026-09-17&end=2026-09-18&format=csv`
```csv
date,ticker,open,high,low,close,volume,value
2026-09-17,000001,1000.0,1010.0,990.0,1005,12345,12406725
2026-09-18,000001,1005.0,1020.0,1000.0,1015,23456,23807840
```

### 오류

본문은 `{"detail": ...}`. 400 필수 조건 누락·없는 열·형식 오류·종목 101개 이상·20만 행 초과, 403 허용 목록 밖 IP, 401 키 없음(인증을 켰을 때만), 422 없는 표·`format` 값 오류, 503 DB 오류, 504 쿼리 30초 초과.

### 기사 메타데이터 스냅샷 (`/v1/ext/snapshots/…`, P3-11·공동 지시서 C3)

수집 PC 가 서버 DB 의 기사 표 4개(`ext_document_*`, C3 v3)에 넣은 스냅샷을 팀원 PC 가 받아 검색용 로컬 Parquet 으로 만들 때 쓴다(받는 쪽 PR #81, `docs/P3_DB_검색_자동갱신.md`). 기사 표는 `/v1/{table}` 로는 열지 않고 이 경로로만 연다. `license_scope` 가 `metadata_only` 인 행만 내보낸다(제목·회사명 등 메타데이터, 본문·요약 없음).

> **결정 F1d(2026-10-14):** 원본(시세·기사)은 데이터 서버 PC 에서만 보관한다. 위 "팀원 PC 가 받아" 부분은 이 결정 뒤 서버 PC 안(팀 앱 서버)에서 받는 것으로 읽는다. 받는 쪽은 PR #81(hchee99)로 정했고 서버 PC 의 앱에서만 켠다(결정 F1c 개정, §6.5).

| 경로 | 응답 | 오류 |
|---|---|---|
| `/v1/ext/snapshots/latest?sources=n8n_news,n8n_disclosure` | `{"snapshot_id": "<64자리 소문자 hex>"}` | 404 완성된 스냅샷 없음, 400 `sources` 형식 오류, 422 `sources` 없음 |
| `/v1/ext/snapshots/{snapshot_id}/documents?source=<출처>&limit=1000[&after_doc_id=…&after_version=…]` | 문서 버전 한 쪽 (아래 예시) | 400 `limit` 이 1~1000 밖·`after_*` 하나만 있음, 404 스냅샷·출처 없음 |

- **"완성된 최신" 기준:** 요청한 모든 출처의 manifest 가 있고, 출처마다 manifest 의 `document_versions` 가 그 스냅샷의 멤버 수와 같은 스냅샷 가운데 `imported_at_utc`(출처별 값 중 가장 늦은 것)가 가장 늦은 것. 같으면 `snapshot_id` 가 큰 것. 적재 중(멤버 수가 모자란) 스냅샷은 고르지 않는다.
- **데이터 버전 식별:** `snapshot_id` 가 기사 자료의 버전이다. 같은 ID 면 같은 자료이므로 받는 쪽은 다시 받지 않는다. (주가 표에는 아직 이런 값이 없다 — §5)
- **페이지:** 행은 `(doc_id, version)` 오름차순. 더 있으면 `next_cursor` 가 그 쪽 마지막 행의 `{"doc_id", "version"}` 이고, 다음 요청에 `after_doc_id`·`after_version` 으로 넘긴다. 마지막 쪽만 `null`. 같은 스냅샷 안에서는 멤버가 바뀌지 않아 쪽이 어긋나지 않는다.
- **값 형식:** `manifest_json`·`arrow_schema_json`·`document_json` 은 DB 에 저장된 **원문 문자열 그대로**다(객체로 다시 인코딩하지 않음 — 받는 쪽이 `record_sha256` 으로 검증한다). 버전·개수는 정수, `first_seen_at_utc`·`available_at_utc` 는 UTC `YYYY-MM-DD HH:MM:SS.ffffff` 문자열.
- 한도·인증·오류 형식은 위 표 조회와 같다(쿼리 30초, `{"detail"}`, §3 접근 조건).

`/v1/ext/snapshots/{snapshot_id}` (값은 자리표시자)
```json
{
  "snapshot_id": "<64자리 소문자 hex>",
  "manifests": [
    {"source": "n8n_disclosure", "manifest_json": "<원문 JSON 문자열>", "arrow_schema_json": "<원문 JSON 문자열>", "document_versions": 1, "coverage_state": "unknown", "coverage_rows": 0},
    {"source": "n8n_news", "manifest_json": "<원문 JSON 문자열>", "arrow_schema_json": "<원문 JSON 문자열>", "document_versions": 1, "coverage_state": "unknown", "coverage_rows": 0}
  ],
  "member_counts": {"n8n_disclosure": 1, "n8n_news": 1},
  "coverage_rows": 0
}
```

`/v1/ext/snapshots/{snapshot_id}/documents?source=n8n_news&limit=1000`
```json
{
  "snapshot_id": "<64자리 소문자 hex>",
  "source": "n8n_news",
  "rows": [{"doc_id": "n8n_news:example", "version": 1, "source": "n8n_news", "status": "active", "license_scope": "metadata_only",
            "first_seen_at_utc": "2026-10-14 00:00:00.000000", "available_at_utc": "2026-10-14 00:00:00.000000",
            "record_sha256": "<64자리 hex>", "document_json": "<원문 JSON 문자열>"}],
  "next_cursor": {"doc_id": "n8n_news:example", "version": 1}
}
```

## 3. 접근 조건·인증

- 서버가 허용한 PC 에서만 접속된다. 접속이 거부(403)되면 서버 담당(Seonghwanaa)에게 요청한다.
- 서버가 키 인증을 쓰면 `X-API-Key` 헤더가 필요하다. 동기화 스크립트는 `REGIME_DATA_API_TOKEN` 이 있을 때만 이 헤더를 보낸다. 키가 필요한지는 서버 담당에게 확인한다.
- 서버 접근 제어·운영 상태(허용 범위, 인증, 네트워크 설정)는 공개 저장소에 적지 않고 서버 담당의 비공개 운영 기록에 둔다.
- 주소·키는 사람끼리 직접 전달한다. 채널·저장소·PR·로그에 쓰지 않는다.
- 응답 `Content-Type` 에 charset 이 없다(본문은 UTF-8). Windows PowerShell 5.1 `Invoke-WebRequest` 는 한글을 깨뜨려 보인다 — 동기화 스크립트는 UTF-8 로 읽는다.

## 4. 동기화 (`engine/scripts/sync_data_server.py`)

> **결정 F1d(2026-10-14):** 이 동기화는 데이터 서버 PC 안에서만(`127.0.0.1`) 쓴다. 팀원 PC 로 원본을 받지 않는다. 받은 폴더에는 표별 행 수·내용 해시를 적은 지문 파일을 남긴다(구현 X11·X14).

```powershell
$env:REGIME_DATA_API_URL = "http://<서버주소>:<포트>"   # 터미널에서 직접. 파일·채팅에 남기지 않음
uv run --project engine python engine/scripts/sync_data_server.py --out ..\regime-data-sync
```

- 결과: `<out>/store/` (prices·marketcap·master·index·delisted·sample30, raw/krx_daily, raw/sector) — `--minutes` 를 주면 `<out>/minutes/` 도.
- **원본을 덮어쓰지 않는다.** `<out>` 이 있으면 멈추고, 옆 `.tmp_<out>` 폴더에 받아 검증을 통과해야 이름을 바꾼다. 실패하면 임시 폴더만 지운다.
- **검증:** 응답 `count` = 받은 행 수, 열·자료형이 store 형식과 같음, 키 중복 0, 기존 store(`paths.local.yaml` 의 store, 또는 `--compare`)와 겹치는 기간·종목의 값이 모두 같음. 하나라도 다르면 저장하지 않는다. 기존 store 가 없는 PC 는 `--no-compare --sample-tickers <30종목>`.
- 서버 형식과 store 형식 차이(2026-10-13 승인): `store_index.market` → `index`, krx_daily 는 날짜별 파일, sector 는 서버에 시장 열이 없어 날짜별 `YYYYMMDD_ALL.parquet`(loader 는 date·ticker·sector 만 읽어 결과 같음), 날짜 문자열 → timestamp, `is_delisted` 0/1 → bool. sample30 은 기존 store 의 30종목을 따른다.
- 20만 행 한도: 일봉은 거래일 60일씩, 1분봉은 20종목·한 달씩 받고, 한도에 걸리면 그 범위만 반으로 나눠 다시 받는다.
- 1분봉(`--minutes`)은 수천 번 요청·수 GB 라 봉 예측에만 필요할 때 받는다. 시작 달은 기존 1분봉 매니페스트에서, 없으면 `--minutes-start YYYY-MM`.
- 받은 뒤 `engine/config/paths.local.yaml` 의 `store` 를 `<out>/store` 로 바꾸고 `uv run --project engine regime-lab prepare`. 파일 수정 시각이 달라 준비 캐시는 한 번 새로 만들어진다(`input_file_hashes`).

### 받은 뒤 확인 (F1.2)

```bash
uv run --project engine python -c "from regime_lab.data.loader import input_file_hashes as f, load_master as m; from pathlib import Path; p=Path('../regime-data-sync/store'); print(f(p)); print(m(p)[['ticker','name']].head())"
```

- `input_file_hashes`: Parquet 5개는 크기·수정 시각, `raw/krx_daily`·`raw/sector` 는 파일 수와 해시. 수정 시각이 PC 마다 달라 준비 캐시 키는 PC 마다 다르다 — 같은 자료인지는 아래 행 수와 동기화가 끝에 출력하는 표별 행 수로 본다.
- 2026-09-18 기준 기대값(원본 PC): prices·marketcap·krx_daily 3,865,943행, index 2,966행, master 3,095종목(`ticker`·`name` 열 있음), delisted 333, sector 193,249행, `raw/krx_daily` 1,483개 파일.
- 기존 원본이 있는 PC 는 동기화가 겹치는 기간을 이미 전부 대조했다(다르면 저장 안 함).

## 5. 남은 결정·과제

| 항목 | 상태 |
|---|---|
| 데이터 버전 식별값 | 기사 스냅샷은 `snapshot_id` 로 해결(§2 기사 메타데이터 스냅샷). 주가 표는 결정 F1d ②(2026-10-14): 데이터 서버 PC 안 동기화가 받은 폴더에 지문 파일(표별 행 수·내용 해시)을 남긴다. 서버 API 는 바꾸지 않는다 |
| 서버 운영·보안 | 서버 담당 비공개 운영 기록에서 관리 |
| 1분봉 파티션 | 결정 D-2b(2026-10-14)로 앱은 1분봉을 쓰지 않음 — 파티션 추가 불필요(표는 유지, 물리 삭제 안 함) |
| 최신 데이터 | 일봉·지수 2026-09-18, 1분봉·지표 2026-09-17 (X4 수집·적재 뒤 갱신). 새 시세 출처는 결정 X4a(금융위원회_주식시세정보), 지수·마스터·폐지·업종은 새로 수집하지 않음 |

## 6. 서버 단일 실행 운영 (결정 F1b, 2026-10-14)

> 결정: `docs/구현_전_결정사항.md` §5.3 `F1b` · 이 절은 작업 분배와 구현안이다. **서버 주소·팀원 IP 는 이 문서와 저장소에 적지 않는다.**

### 6.1 구성

```
[수집 PC] ─ DB 추가 쓰기 ─▶ [데이터 서버 PC]
                             ├─ MariaDB + 조회 API (기존)
                             ├─ 새벽 갱신: API(127.0.0.1) → 새 store 폴더 → paths 전환 → prepare → 앱 재시작
                             └─ 팀 웹 앱 regime-api (같은 망, IP 허용 목록, 로그인 없음)
                                    ▲ 브라우저만 (팀원 PC 에 데이터 저장 없음)
```

- 엔진은 지금처럼 Parquet 을 읽는다(F1a 유지). Parquet 은 같은 PC 안에서 DB → 새 폴더 변환으로 만든다.
- 분석 작업은 한 번에 1개(E4)이고, 동시 요청은 대기열로 줄 세운다(F1b ④, `구현_계획.md` §6.6).
- 봉 예측·영향력·브리핑 같은 무거운 계산은 사용자 요청 때 돌리지 않고 미리 만든 결과를 조회한다.
- `data.as_of_date` 는 발표까지 2026-09-18 고정(F1b ⑤). 바꾸면 준비 데이터 지문이 바뀌어 이전 실행의 종목 상세가 `stale_run` 이 된다.

### 6.2 어디에 무엇을 두나

| 구역 | 두는 것 | 두지 않는 것 |
|---|---|---|
| 팀 저장소 (공개) | 앱 코드, 설정 이름·기본값, 문서, 테스트 | 서버 주소, 팀원 IP, 키 |
| 데이터 서버 폴더 (서버 PC 의 로컬 git) | 앱 시작 파일, 새벽 갱신 묶음, 방화벽 스크립트 — 데이터 API 운영 파일과 같은 방식 | 팀 앱 코드 사본 수정 |
| 서버 PC 설정 (파일 밖) | 저장소 체크아웃·`uv`, `engine/config/paths.local.yaml`(Git 제외), 환경변수(`REGIME_AUTH_MODE=demo`, `REGIME_HOST`, `REGIME_ALLOWED_NETS`), 작업 스케줄러, 방화벽 | — |

### 6.3 작업 분배

| WBS | 작업 | 담당 | 구역 | 카드·구현안 |
|---|---|---|---|---|
| X11 | 앱 서버 설치·첫 변환·자동 시작 (서버 앱에 `REGIME_NEWS_REFRESH=1` 설정 포함) | `Seonghwanaa` | 서버 폴더·서버 설정 |
| (P3-11) PR #81 | 기사 스냅샷 받는 쪽 — #84 규격 대조·실서버 확인 뒤 병합 | `hchee99` (리뷰 `PEANUTBUTTER1001`) | 팀 저장소 | [P2-P4 로드맵](P2-P4_예측화면_통합모델_로드맵.md) S8 |
| X12 | 같은 망 접속 + IP 허용 목록 | `PEANUTBUTTER1001` (2026-10-15 사용자 지시로 이관) | 팀 저장소 (값은 서버 설정) | [P3-P4 로드맵](P3-P4_외부자료_브리핑_로드맵.md) B22 |
| X13 | 작업 대기열 | `PEANUTBUTTER1001` | 팀 저장소 | [구현_계획](구현_계획.md) §6.6 |
| X14 | 새벽 자동 갱신 | `Seonghwanaa` (X4 연계 `hchee99`) | 서버 폴더 (동기화 스크립트 보완이 필요하면 그 부분만 팀 저장소) | P2-P4 로드맵 S9 |
| P2-11 | 일봉 예측 전환 (D-2b) | `Seonghwanaa` | 팀 저장소, 실행은 서버 | P2-P4 로드맵 S7 |
| — | `AGENTS.md` §1·§4 갱신 | `PEANUTBUTTER1001` | 팀 저장소 | 구현 뒤 `/agents-md` |

### 6.4 순서와 완료 기준

- X12 설정: `REGIME_ALLOWED_NETS`는 팀원별 IPv4 `/32`를 쉼표로 구분. 코드의 CIDR 지원과 `/24` 전체 운영 허용은 구분. IPv4 RFC1918만 지원, loopback 자동 허용, Supabase에서는 변수 무시. 값은 서버 PC에만 보관. 공식 `regime-api` 데모 진입점으로 직접 접속하고 로컬 프록시·터널 제외. 실제 주소 고정·방화벽 범위·광범위 Python 허용 규칙을 확인하고, 기존 서버 재시작은 별도 승인 후 수행.
- 비허용 PC 수용 확인: 앱까지 도달하는 요청은 403 `network_not_allowed`, 방화벽이 먼저 막으면 연결 실패가 정상. LLM 설정은 허용 PC도 `local_only` 403. 데이터 API·DB 포트의 기존 정책 변경은 이번 X12 구현 범위 밖.

1. X11 — 서버 PC 에서 loopback 으로 실행·결과·근거 검색 화면 정상, 재부팅 뒤 자동 시작
2. X12·X13 (병행) — 같은 망 팀원 PC 브라우저에서 실행·결과 확인, 허용 목록 밖 PC 는 403, 두 사람이 동시에 실행하면 둘째가 대기 뒤 완료
3. X14 — 새벽 작업 1회 성공 로그, 갱신 중에도 이전 데이터로 조회 가능, 실패 시 이전 폴더 유지
4. P2-11 — 서버에서 일봉 예측 실행 1회, 봉 예측 화면에서 확인
5. `AGENTS.md` 갱신

- 전체 확인은 M4(전체 회귀)·P4-9(통합 회귀)에 포함한다.
- 서버 RAM 이 앱과 MariaDB 를 함께 돌리기에 부족하면 F1b ②(실행 PC)를 다시 결정한다.

### 6.5 기사 데이터 (결정 F1c)

- 경로: 데이터 서버 API 기사 스냅샷(§2 "기사 메타데이터 스냅샷") → 앱의 백그라운드 갱신(PR #81) → 검증 뒤 검색 색인 교체. 같은 `snapshot_id` 면 다시 받지 않는다.
- **받는 쪽 (결정 F1c 개정):** PR #81(hchee99)의 앱 백그라운드 갱신을 쓴다. 서버 PC 의 앱만 `REGIME_DATA_API_URL=http://127.0.0.1:8100`·`REGIME_NEWS_REFRESH=1` 로 켜고, 팀원 PC 는 켜지 않는다(F1d). 저장 위치는 #81 의 `news_snapshots` 경로 설정. WBS X11.4(동기화 스크립트 확장)는 취소.
- 형식: #81 이 매니페스트 버전 수·멤버 수·해시·Parquet 왕복을 검증한다. #84 확정 규격과의 대조와 실서버 연결 확인이 #81 병합 조건이다.
- 서비스 범위: 서버 PC 앱에는 출처 대장(`docs/P3_출처_대장.md`, P3-3)에서 활성인 출처만 반영한다. 지금 스냅샷(`n8n_news`·`n8n_disclosure`)은 매니페스트상 `local_fixture: true`·`coverage: unknown` 이라 출처 대장 활성화 전에는 개발·평가용이다.
- `coverage: unknown` 이면 영향 분석 등에서 수집 공백(`coverage_gap`)으로 보일 수 있다.
