# regime_lab DB 구성 상세

> - 기준: 2026-10-14, 데이터 서버 PC 의 MariaDB 10.11.19 를 읽기 전용으로 직접 조회한 값
> - DB 이름: `regime_lab` · 표 16개 + 뷰 1개 · 전체 약 23GB · 엔진 InnoDB
> - 행 수는 `COUNT(*)` 정확값이다. `stock_minutes` 만 파티션 통계 추정값이다(표시함).
> - 열 표기: **PK** = 기본 키 구성 열, 자료형은 DB 원래 표기. "설명" 의 *(해석)* 은 열 이름·값으로 추정한 것.
> - 이 문서는 서버 PC 안에만 둔다(내부 운영 문서). 공개용 API 명세는 팀 레포 `docs/F1_데이터_서버_API.md`.

---

## 1. 전체 목록

| # | 표 | 묶음 | 내용 | 행 수 | 기간 | 용량 | 기본 키 | API 필수 조건 |
|---|---|---|---|---|---|---|---|---|
| 1 | `store_prices` | 일봉 | 수정주가 일봉 | 3,865,943 | 2020-09-01 ~ 2026-09-18 | 384MB | (ticker, date) | 종목 또는 기간 |
| 2 | `store_krx_daily` | 일봉 | KRX 원주가·소속부·시총 | 3,865,943 | 2020-09-01 ~ 2026-09-18 | 571MB | (ticker, date) | 종목 또는 기간 |
| 3 | `store_marketcap` | 일봉 | 시가총액·상장주식수·시장 | 3,865,943 | 2020-09-01 ~ 2026-09-18 | 304MB | (ticker, date) | 종목 또는 기간 |
| 4 | `store_index` | 일봉 | KOSPI·KOSDAQ 지수 | 2,966 | 2020-09-01 ~ 2026-09-18 | <1MB | (market, date) | 없음 |
| 5 | `store_sector` | 일봉 | 월말 업종 스냅샷 | 193,249 | 2020-09-29 ~ 2026-09-18 (74시점) | 9MB | (ticker, date) | 종목 또는 기간 |
| 6 | `store_master` | 일봉 | 종목 마스터 | 3,095 | – | <1MB | (ticker) | 없음 |
| 7 | `store_delisted` | 일봉 | 상장폐지 종목 | 333 | 2020-09-08 ~ 2026-09-16 | <1MB | (ticker) | 없음 |
| 8 | `v_store_daily` (뷰) | 일봉 | 일봉 통합 조회 | 3,865,943 | 2020-09-01 ~ 2026-09-18 | (뷰) | – | 종목 또는 기간 |
| 9 | `stock_minutes` | 1분봉 | 1분봉 | 약 228,471,963 (추정) | 2025-03-27 09:00 ~ 2026-09-17 20:02 | 14,788MB | (code, dt) | 종목과 기간 모두 |
| 10 | `kor_price` | 옛 자료 | 옛 일봉 (한글 열) | 11,350,052 | 1985-01-04 ~ 2026-09-17 | 907MB | (종목코드, 날짜) | 종목 또는 기간 |
| 11 | `kor_indicators` | 옛 자료 | 기술지표 일별 | 11,058,332 | 1985-10-23 ~ 2026-09-17 | 3,854MB | (기준일, 종목코드) | 기간 |
| 12 | `kor_def_info` | 옛 자료 | 종목 기본정보 일별 스냅샷 | 1,250,296 | 2024-11-11 ~ 2026-09-17 | 202MB | (표준코드, 단축코드, 기준일) | 종목 또는 기간 |
| 13 | `theme_finup_stock` | 테마 | 종목별 테마 (finup) | 831,938 | 2025-04-20 ~ 2026-08-22 | 164MB | 없음 | 종목 또는 기간 |
| 14 | `ext_document_manifests` | 기사 | 적재 스냅샷·출처별 매니페스트 | 6 | 적재 10-13 | <1MB | (snapshot_id, source) | `/v1/ext/snapshots` 전용 |
| 15 | `ext_document_versions` | 기사 | 문서 버전 (메타데이터 원문) | 6,346 | 첫 수집 10-08 ~ 10-13 (UTC) | 11MB | (doc_id, version) | `/v1/ext/snapshots` 전용 |
| 16 | `ext_document_snapshot_members` | 기사 | 스냅샷 ↔ 문서 버전 소속 | 18,720 | – | 9MB | (snapshot_id, source, doc_id, version) | `/v1/ext/snapshots` 전용 |
| 17 | `ext_document_coverage` | 기사 | 수집 공백 기록 | 0 | – | 0MB | (snapshot_id, source, entry_no) | `/v1/ext/snapshots` 전용 |

- 정렬 규칙(collation): `ext_document_*` 는 `utf8mb4_bin`(대소문자·바이트 구분), 나머지는 `utf8mb4_general_ci`.
- API: 1~13 은 `GET /v1/{표}` (한 번에 20만 행, 쿼리 30초), 14~17 은 `GET /v1/ext/snapshots/...` 로만 나간다.
- 쓰기: 수집 PC 계정만 가능 — 1~7·9 (시세 8개) 와 14~17 (기사 4개) 에 **추가(INSERT)만**. 10~13 은 조회만.

---

## 2. 일봉 (팀 백테스트의 원본)

공통: 거래일 1,483일, 종목 3,095개(상장 2,762 + 상장폐지 333). 팀원 PC 동기화(F1a)와 서버 팀 앱(F1b)이 이 표들을 Parquet 으로 받아 쓴다.

### 2.1 `store_prices` — 수정주가 일봉

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `date` | date | PK | 거래일 |
| 2 | `ticker` | varchar(20) | PK | 종목코드 (6자리) |
| 3 | `open` | double | | 시가 (수정주가) |
| 4 | `high` | double | | 고가 (수정주가) |
| 5 | `low` | double | | 저가 (수정주가) |
| 6 | `close` | bigint(20) | | 종가 (수정주가). 시·고·저와 자료형이 다르다(정수) |
| 7 | `volume` | bigint(20) | | 거래량 |
| 8 | `value` | bigint(20) | | 거래대금 (원) |

- 색인: PK (ticker, date), `idx_store_prices_date` (date)
- 수정주가 출처: 네이버 수정주가 (`store_master.price_source = naver_adj`)

### 2.2 `store_krx_daily` — KRX 원주가·소속부·시가총액

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `date` | date | PK | 거래일 |
| 2 | `ticker` | varchar(20) | PK | 종목코드 |
| 3 | `name` | varchar(48) | | 종목명 (그날 기준) |
| 4 | `market` | varchar(20) | | 시장 (KOSPI / KOSDAQ) |
| 5 | `dept` | varchar(45) | | 소속부. 값 예: 우량기업부, 중견기업부, 벤처기업부, 기술성장기업부, 관리종목(소속부없음), 투자주의환기종목(소속부없음), SPAC(소속부없음), 외국기업(소속부없음), 빈 값(KOSPI) |
| 6 | `open_raw` | bigint(20) | | 시가 (수정 전 원주가) |
| 7 | `high_raw` | bigint(20) | | 고가 (원주가) |
| 8 | `low_raw` | bigint(20) | | 저가 (원주가) |
| 9 | `close_raw` | bigint(20) | | 종가 (원주가) |
| 10 | `volume_raw` | bigint(20) | | 거래량 (원 기준) |
| 11 | `value` | bigint(20) | | 거래대금 (원) |
| 12 | `marketcap` | bigint(20) | | 시가총액 (원) |
| 13 | `shares` | bigint(20) | | 상장주식수 |

- 색인: PK (ticker, date), `idx_store_krx_daily_date` (date)
- 팀원 PC 에서는 날짜별 파일 `raw/krx_daily/YYYYMMDD.parquet` (1,483개) 로 바뀐다.

### 2.3 `store_marketcap` — 시가총액·상장주식수

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `date` | date | PK | 거래일 |
| 2 | `ticker` | varchar(20) | PK | 종목코드 |
| 3 | `marketcap` | bigint(20) | | 시가총액 (원) |
| 4 | `shares` | bigint(20) | | 상장주식수 |
| 5 | `market` | varchar(20) | | 소속 시장 (KOSPI / KOSDAQ) |

- 색인: PK (ticker, date), `idx_store_marketcap_date` (date)

### 2.4 `store_index` — 지수 일봉

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `date` | date | PK | 거래일 |
| 2 | `market` | varchar(20) | PK | 지수 이름: `KOSPI`, `KOSDAQ` (팀원 PC 파일에서는 열 이름 `index`) |
| 3 | `open` | double | | 시가 |
| 4 | `high` | double | | 고가 |
| 5 | `low` | double | | 저가 |
| 6 | `close` | double | | 종가 |
| 7 | `volume` | bigint(20) | | 거래량 |

- 시장별 1,483행. 국면(상승·하락장) 판정에 쓴다.

### 2.5 `store_sector` — 월말 업종 스냅샷

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `date` | date | PK | 스냅샷 날짜 (월말, 74시점) |
| 2 | `ticker` | varchar(20) | PK | 종목코드 |
| 3 | `sector` | varchar(33) | | 업종 |

- 서버에는 시장 열이 없어 팀원 PC 에서는 `raw/sector/YYYYMMDD_ALL.parquet` (74개) 로 바뀐다.

### 2.6 `store_master` — 종목 마스터

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `ticker` | varchar(20) | PK | 종목코드 |
| 2 | `name` | varchar(48) | | 종목명 |
| 3 | `market` | varchar(20) | | 시장 (KOSPI / KOSDAQ) |
| 4 | `sector` | varchar(33) | | 업종 (최근) |
| 5 | `listing_date` | date | | 상장일 |
| 6 | `kind` | varchar(20) | | 종목 유형: 보통주, 우선주, 스팩, 리츠, 기타 |
| 7 | `price_source` | varchar(27) | | 가격 출처 (전부 `naver_adj` = 네이버 수정주가) |
| 8 | `is_delisted` | tinyint(1) | | 상장폐지 여부 (0 = 상장 2,762, 1 = 폐지 333). 팀원 PC 파일에서는 bool |

종목 구성:

| 시장 | 보통주 | 우선주 | 스팩 | 리츠 | 기타 | 합계 |
|---|---|---|---|---|---|---|
| KOSDAQ | 1,874 | 4 | 199 | 1 | 9 | 2,087 |
| KOSPI | 851 | 124 | 1 | 28 | 4 | 1,008 |

### 2.7 `store_delisted` — 상장폐지 종목

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `ticker` | varchar(20) | PK | 종목코드 |
| 2 | `name` | varchar(36) | | 종목명 |
| 3 | `delisting_date` | date | | 상장폐지일 = **마지막 거래일** |
| 4 | `market` | varchar(20) | | 시장 |

### 2.8 `v_store_daily` (뷰) — 일봉 통합 조회

수정주가 + 시가총액 + 원주가 + 종목 마스터를 한 줄로 합친 조회용 뷰. 행 수는 `store_prices` 와 같다. 열의 출처는 열 이름 기준 *(해석, 뷰 정의는 조회 전용 계정으로 볼 수 없음)*.

| # | 열 | 자료형 | 출처 표 | 설명 |
|---|---|---|---|---|
| 1 | `date` | date | store_prices | 거래일 |
| 2 | `ticker` | varchar(20) | store_prices | 종목코드 |
| 3 | `name` | varchar(48) | store_master | 종목명 |
| 4 | `open` | double | store_prices | 시가 (수정) |
| 5 | `high` | double | store_prices | 고가 (수정) |
| 6 | `low` | double | store_prices | 저가 (수정) |
| 7 | `close` | bigint(20) | store_prices | 종가 (수정) |
| 8 | `volume` | bigint(20) | store_prices | 거래량 |
| 9 | `value` | bigint(20) | store_prices | 거래대금 |
| 10 | `marketcap` | bigint(20) | store_marketcap | 시가총액 |
| 11 | `market` | varchar(20) | store_marketcap | 시장 |
| 12 | `open_raw` | bigint(20) | store_krx_daily | 시가 (원주가) |
| 13 | `close_raw` | bigint(20) | store_krx_daily | 종가 (원주가) |
| 14 | `volume_raw` | bigint(20) | store_krx_daily | 거래량 (원 기준) |
| 15 | `dept` | varchar(45) | store_krx_daily | 소속부 |
| 16 | `kind` | varchar(20) | store_master | 종목 유형 |
| 17 | `halted` | int(1) | (계산) | 거래정지 표시 (1 = 거래정지) |

---

## 3. 1분봉 — `stock_minutes`

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `code` | char(6) | PK | 종목코드 |
| 2 | `dt` | datetime | PK | 분 단위 시각 (KST) |
| 3 | `open_p` | int(11) | | 시가 |
| 4 | `high_p` | int(11) | | 고가 |
| 5 | `low_p` | int(11) | | 저가 |
| 6 | `close_p` | int(11) | | 종가 |
| 7 | `volume` | bigint(20) | | 거래량 |
| 8 | `value` | bigint(20) | | 거래대금 (원) |

| 항목 | 값 |
|---|---|
| 기간 | 2025-03-27 09:00 ~ 2026-09-17 20:02 (362거래일) |
| 규모 | 약 2.28억 행 (파티션 통계 추정), 14,788MB — DB 의 약 3분의 2 |
| 하루치 | 약 72만 행 · 약 2,529종목 · 08:00 ~ 20:02 (정규장 + 대체거래소 시간대) |
| 파티션 | 월별 22개 `p202502` ~ `p202611` (`p202502` 는 0행, `p202610`·`p202611` 은 10-13 에 추가, 아직 0행). MAXVALUE 파티션 없음 → 12월분은 11월 안에 추가 |
| 제품 사용 | 팀 결정 D-2b 로 제품에서 제외 (봉 예측은 일봉 예측으로 전환, P2-11). 팀원·서버 앱 동기화에 넣지 않음 |

---

## 4. 옛 자료 (참조 전용, 새로 수집하지 않음)

팀 규칙: `kor_price` 는 지표 **워밍업 보정**에만, `kor_indicators` 는 **테스트 참조값**에만 쓴다 (`AGENTS.md` §7).

### 4.1 `kor_price` — 옛 일봉

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `날짜` | date | PK | 거래일 |
| 2 | `시가` | bigint(20) | | 시가 |
| 3 | `고가` | bigint(20) | | 고가 |
| 4 | `저가` | bigint(20) | | 저가 |
| 5 | `종가` | bigint(20) | | 종가 |
| 6 | `거래량` | bigint(20) | | 거래량 |
| 7 | `거래대금` | bigint(20) | | 거래대금 |
| 8 | `종목코드` | varchar(20) | PK | 종목코드 |

- 11,350,052행 · 2,560종목 · 1985-01-04 ~ 2026-09-17 · 기본 키 (종목코드, 날짜)

### 4.2 `kor_indicators` — 기술지표 일별

11,058,332행 · 1985-10-23 ~ 2026-09-17 · 마지막 날 2,466종목 · 기본 키 (기준일, 종목코드). **기본 키가 날짜부터라 종목만으로 조회하면 표 전체를 읽는다 → API 는 기간 필수.**

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `기준일` | date | PK | 기준 날짜 |
| 2 | `종목코드` | varchar(20) | PK | 종목코드 |
| 3 | `pre_close` | double | | 전일 종가 |
| 4 | `RSI_14` | double | | RSI (14일) |
| 5 | `MACD` | double | | MACD |
| 6 | `Signal_Line` | double | | MACD 시그널선 |
| 7 | `MACD_Histogram` | double | | MACD 히스토그램 (MACD − 시그널) |
| 8 | `BB_Upper_19` | double | | 볼린저밴드 상단 (19일) |
| 9 | `BB_Lower_19` | double | | 볼린저밴드 하단 (19일) |
| 10 | `BB_Middle_19` | double | | 볼린저밴드 중심 (19일) |
| 11 | `BB_Upper_20` | double | | 볼린저밴드 상단 (20일) |
| 12 | `BB_Lower_20` | double | | 볼린저밴드 하단 (20일) |
| 13 | `BB_Middle_20` | double | | 볼린저밴드 중심 (20일) |
| 14 | `BB_Upper_19_ddof` | double | | 볼린저밴드 상단 (19일, 표본표준편차 *(해석: ddof=1)*) |
| 15 | `BB_Lower_19_ddof` | double | | 볼린저밴드 하단 (19일, 표본표준편차) |
| 16 | `BB_Middle_19_ddof` | double | | 볼린저밴드 중심 (19일, 표본표준편차) |
| 17 | `BB_Upper_20_ddof` | double | | 볼린저밴드 상단 (20일, 표본표준편차) |
| 18 | `BB_Lower_20_ddof` | double | | 볼린저밴드 하단 (20일, 표본표준편차) |
| 19 | `BB_Middle_20_ddof` | double | | 볼린저밴드 중심 (20일, 표본표준편차) |
| 20 | `tenkan_sen` | double | | 일목균형표 전환선 |
| 21 | `kijun_sen` | double | | 일목균형표 기준선 |
| 22 | `senkou_span_a` | double | | 일목균형표 선행스팬 A |
| 23 | `senkou_span_b` | double | | 일목균형표 선행스팬 B |
| 24 | `MA_5` | double | | 이동평균 (5일) |
| 25 | `MA_20` | double | | 이동평균 (20일) |
| 26 | `MA_60` | double | | 이동평균 (60일) |
| 27 | `MA_120` | double | | 이동평균 (120일) |
| 28 | `MA_240` | double | | 이동평균 (240일) |
| 29 | `zone` | text | | 5자리 숫자 코드 (예: `43102`, `01234`) *(해석: 이동평균 5개의 배열 순서)* |
| 30 | `MA_19` | double | | 이동평균 (19일) |
| 31 | `MA_Volume_19` | double | | 거래량 이동평균 (19일) |
| 32 | `MA_59` | double | | 이동평균 (59일) |
| 33 | `MA_Volume_59` | double | | 거래량 이동평균 (59일) |
| 34 | `VWAP_20` | double | | 거래량가중평균가 (20일) |
| 35 | `VWAP_60` | double | | 거래량가중평균가 (60일) |
| 36 | `VWAP_120` | double | | 거래량가중평균가 (120일) |
| 37 | `VWAP_240` | double | | 거래량가중평균가 (240일) |
| 38 | `VWAP_19` | double | | 거래량가중평균가 (19일) |
| 39 | `VWAP_59` | double | | 거래량가중평균가 (59일) |
| 40 | `pre_volume` | bigint(20) | | 전일 거래량 |
| 41 | `plus_di_14` | double | | +DI (14일, 방향성 지표) |
| 42 | `minus_di_14` | double | | −DI (14일) |
| 43 | `adx_14` | double | | ADX (14일, 추세 강도) |

### 4.3 `kor_def_info` — 종목 기본정보 일별 스냅샷

1,250,296행 · 2024-11-11 ~ 2026-09-17 · 기본 키 (표준코드, 단축코드, 기준일)

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `표준코드` | varchar(12) | PK | 표준코드 (ISIN 12자리) |
| 2 | `단축코드` | varchar(6) | PK | 단축코드 (6자리 종목코드) — API 의 종목 열 |
| 3 | `한글_종목명` | varchar(100) | | 한글 종목명 |
| 4 | `한글_종목약명` | varchar(100) | | 한글 종목 약명 |
| 5 | `영문_종목명` | varchar(100) | | 영문 종목명 |
| 6 | `상장일` | date | | 상장일 |
| 7 | `시장구분` | varchar(20) | | KOSPI / KOSDAQ |
| 8 | `증권구분` | varchar(20) | | 주권, 부동산투자회사, 외국주권, 주식예탁증권 등 |
| 9 | `소속부` | varchar(20) | | 소속부 |
| 10 | `주식종류` | varchar(10) | | 보통주, 구형우선주, 신형우선주, 종류주권 등 |
| 11 | `액면가` | int(11) | | 액면가 |
| 12 | `상장주식수` | bigint(20) | | 상장주식수 |
| 13 | `유동주식수` | bigint(20) | | 유동주식수 |
| 14 | `기준일` | date | PK | 스냅샷 날짜 |

---

## 5. 테마 — `theme_finup_stock`

831,938행 · 2,576종목 · 테마 598개 · 2025-04-20 ~ 2026-08-22 · **기본 키·색인 없음** (모든 열이 text). **사용 허가 미확인(T-3)** — 허가가 안 나면 API 에서 뺀다.

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `date` | date | | 기준 날짜 |
| 2 | `stock_code` | text | | 종목코드 — API 의 종목 열 |
| 3 | `stock_name` | text | | 종목명 |
| 4 | `ftheme_idx` | text | | 테마 번호 (finup) |
| 5 | `ftheme_name` | text | | 테마 이름 |
| 6 | `ftheme_desc` | text | | 테마 설명 |

---

## 6. 기사 메타데이터 — `ext_document_*` (C3 v3, 2026-10-13 생성)

공통: 정렬 규칙 `utf8mb4_bin`. JSON 열은 MariaDB 에서 `longtext` + `CHECK (json_valid(...))` 로 저장된다. 출처 2개 — 공시 `n8n_disclosure` 812버전(게시 10-01 ~ 10-14), 뉴스 `n8n_news` 5,534버전(게시 09-30 ~ 10-13). 전부 `status = active`, `license_scope = metadata_only`(본문 없음). 매니페스트상 `local_fixture`·`coverage unknown` 이라 출처 대장 활성화 전까지 개발·평가용.

### 6.1 `ext_document_manifests` — 스냅샷·출처별 매니페스트

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `snapshot_id` | char(64) ascii_bin | PK | 스냅샷 ID (SHA-256 소문자 16진수) |
| 2 | `source` | varchar(64) | PK | 출처 (`n8n_news`, `n8n_disclosure`) |
| 3 | `schema_version` | int(10) unsigned | | 스키마 버전 (지금 1) |
| 4 | `normalization_policy_version` | varchar(191) | | 정규화 정책 버전 (NULL 가능) |
| 5 | `time_policy_version` | varchar(191) | | 시각 정책 버전 (NULL 가능) |
| 6 | `manifest_json` | longtext (JSON) | | 원래 매니페스트. 키: coverage, input_sha256, license_scope, local_fixture, schema_version, source, time_policy |
| 7 | `mapping_policy_json` | longtext (JSON) | | 변환 정책 출처 (원래 매니페스트는 바꾸지 않음). 키: mapping_version, scope, status_source, timestamps |
| 8 | `arrow_schema_json` | longtext (JSON) | | 손실 없는 Parquet 내보내기용 열 순서·자료형. 키: fields, ipc_base64 |
| 9 | `coverage_state` | varchar(16) | | `recorded` 또는 `unknown` (수집 성공 여부가 아님). CHECK 제약 |
| 10 | `document_versions` | bigint(20) unsigned | | 이 출처의 문서 버전 수 |
| 11 | `coverage_rows` | bigint(20) unsigned | | 수집 공백 기록 행 수 |
| 12 | `imported_at_utc` | datetime(6) | | DB 적재 시각 (UTC). **first_seen_at 으로 쓰지 않음** |

### 6.2 `ext_document_versions` — 문서 버전

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `doc_id` | varchar(191) | PK | 문서 ID (`<출처>:...` 접두사) |
| 2 | `version` | bigint(20) unsigned | PK | 버전 (1 이상, CHECK 제약). 지금 1~2 |
| 3 | `source` | varchar(64) | | 출처 |
| 4 | `status` | varchar(32) | | 상태 (지금 전부 `active`) |
| 5 | `license_scope` | varchar(32) | | 이용 범위 (지금 전부 `metadata_only`) |
| 6 | `first_seen_at_utc` | datetime(6) | | 처음 수집한 시각 (UTC) |
| 7 | `available_at_utc` | datetime(6) | | 이용 가능 시각 (UTC). 과거 시점 검색(as_of)의 기준 |
| 8 | `record_sha256` | char(64) ascii_bin | | 레코드 해시 (받는 쪽 검증용) |
| 9 | `document_json` | longtext (JSON) | | 원래 메타데이터 그대로. 키: available_at, backfilled, body_ref, content_hash, corp_name, doc_id, first_seen_at, ingest_run_id, license_scope, published_at, published_at_basis, rcept_no, source, source_type, status, summary, ticker_status, tickers, time_precision, title, url, version |

- 색인: PK (doc_id, version), UNIQUE `uq_doc_source` (doc_id, version, source), `ix_doc_observed` (source, first_seen_at_utc, available_at_utc)

### 6.3 `ext_document_snapshot_members` — 스냅샷 소속

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `snapshot_id` | char(64) ascii_bin | PK | 스냅샷 ID → `ext_document_manifests` 외래 키 |
| 2 | `source` | varchar(64) | PK | 출처 → 같은 외래 키 |
| 3 | `doc_id` | varchar(191) | PK | 문서 ID → `ext_document_versions` 외래 키 |
| 4 | `version` | bigint(20) unsigned | PK | 버전 → 같은 외래 키 |

- 색인: PK (4열 전체), `doc_id` (doc_id, version, source). 외래 키 2개 (→ manifests, → versions)
- 스냅샷 3개가 문서를 겹쳐 담아 18,720행 (최신 스냅샷: 공시 812 + 뉴스 5,534)

### 6.4 `ext_document_coverage` — 수집 공백 기록

| # | 열 | 자료형 | 키 | 설명 |
|---|---|---|---|---|
| 1 | `snapshot_id` | char(64) ascii_bin | PK | 스냅샷 ID → `ext_document_manifests` 외래 키 |
| 2 | `source` | varchar(64) | PK | 출처 → 같은 외래 키 |
| 3 | `entry_no` | bigint(20) unsigned | PK | 입력 순서 (1 이상, 반복 관측 보존) |
| 4 | `record_sha256` | char(64) ascii_bin | | 레코드 해시 |
| 5 | `coverage_json` | longtext (JSON) | | 원래 seq·day·corp_cls·state·run_id·observed_at (시간대 포함) |

- 지금 0행 (`coverage_state = unknown`)

---

## 7. 접근·권한·운영 요약

| 누가 | 방법 | 권한 |
|---|---|---|
| 팀원 PC | 데이터 API 8100: `/v1/{표}` (1~13), `/v1/ext/snapshots/...` (14~17) | 읽기만. 20만 행·30초·종목 100개 제한, 허용 목록 PC 만 |
| 수집 PC | DB 직접 (3306), 전용 계정 | 시세 8개 + 기사 4개 표 **추가만** |
| 서버 팀 앱 (8000) | DB 가 아니라 동기화한 Parquet (`regime-server\data\sync-20261014`) | – |
| API 조회 계정 | 서버 PC 안에서만 | `regime_lab` 조회만 |

- **현재 최신 날짜:** 일봉·지수 2026-09-18, 1분봉·`kor_*` 2026-09-17 (수집 PC 적재는 C2 합의 뒤). 기사는 10-13 까지 들어옴.
- **알려진 문제:** MariaDB 기본 설치 권한으로 모든 계정이 `test`, `test_*` DB 에 전체 권한을 가진다 (`test`, `test_train` DB 있음). 정리 보류 (10-14 결정). `regime_lab` 에는 영향 없음.
