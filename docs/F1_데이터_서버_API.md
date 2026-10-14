# F1 데이터 서버 API와 동기화

> WBS F1 (담당 Seonghwanaa) · 결정 F1a (2026-10-12) · 로드맵 S1 · 공동 지시서 3부 C1·C3
> 기준: 2026-10-13 데이터 서버 PC 에서 실제로 호출해 확인한 명세. **서버 주소·인증 키·계정은 이 문서와 저장소에 적지 않는다.** 아래 예시 값은 자리표시자다.

## 1. 구성

| 위치 | 역할 |
|---|---|
| 데이터 서버 PC | MariaDB `regime_lab`(표 12개 + 뷰 `v_store_daily`)와 조회 전용 API. 코드는 저장소 밖 |
| 수집 PC | 수집 결과를 서버 DB 에 추가 — 공동 지시서 C2 합의 뒤 |
| 팀원 PC | `engine/scripts/sync_data_server.py` 로 API 에서 받아 **기존 store 형식 Parquet** 로 저장. 엔진은 지금처럼 Parquet 만 읽는다(loader 변경 없음) |

## 2. 엔드포인트 (모두 GET, 쓰기 없음)

| 경로 | 설명 | 응답 |
|---|---|---|
| `/health` | 서버와 DB 상태 (`SELECT 1`) | `{"ok": true}` · DB 실패 503 `{"ok": false, "detail": …}` |
| `/tables` | 표 13개의 설명·종목 열·기간 열·필수 조건·열 목록 | JSON |
| `/v1/{table}` | 표 조회 | JSON 또는 CSV |
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

## 3. 접근 조건·인증

- 서버가 허용한 PC 에서만 접속된다. 접속이 거부(403)되면 서버 담당(Seonghwanaa)에게 요청한다.
- 서버가 키 인증을 쓰면 `X-API-Key` 헤더가 필요하다. 동기화 스크립트는 `REGIME_DATA_API_TOKEN` 이 있을 때만 이 헤더를 보낸다. 키가 필요한지는 서버 담당에게 확인한다.
- 서버 접근 제어·운영 상태(허용 범위, 인증, 네트워크 설정)는 공개 저장소에 적지 않고 서버 담당의 비공개 운영 기록에 둔다.
- 주소·키는 사람끼리 직접 전달한다. 채널·저장소·PR·로그에 쓰지 않는다.
- 응답 `Content-Type` 에 charset 이 없다(본문은 UTF-8). Windows PowerShell 5.1 `Invoke-WebRequest` 는 한글을 깨뜨려 보인다 — 동기화 스크립트는 UTF-8 로 읽는다.

## 4. 동기화 (`engine/scripts/sync_data_server.py`)

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
| 데이터 버전 식별값 | API 에 없음 — 동기화한 자료가 같은 것인지 판단 불가. 적재 기록을 API 로 읽는 경로 등은 별도 결정(서버 API 변경·재시작 동반) |
| 서버 운영·보안 | 서버 담당 비공개 운영 기록에서 관리 |
| 1분봉 파티션 | 2026-11 까지 있음. 12월 데이터 전에 추가 필요(서버 관리자) |
| 최신 데이터 | 일봉·지수 2026-09-18, 1분봉·지표 2026-09-17 (X4 수집·적재 뒤 갱신) |
