# regime-ingest — 외부 자료 수집 (P3-7 OpenDART 공시 목록)

과거 데이터 분석 도구의 근거 자료 수집기이며 투자 권유가 아닙니다. 설계: [`docs/plan/03-04-external-crawling-automation.md`](../docs/plan/03-04-external-crawling-automation.md)

- 범위: OpenDART 공시검색 **목록 메타데이터만**(`license_scope=metadata_only`). 원문 ZIP 은 받지 않고, 화면에는 보고서명·DART 링크만 보인다.
- 엔진·API 는 이 패키지를 import 하지 않고 저장된 Parquet(`<ext_store>/docs/`)만 읽는다.

## 설정

```bash
cp ingest/config/paths.example.yaml ingest/config/paths.local.yaml   # ext_store 위치 (Git 제외)
export DART_API_KEY=...   # 키는 환경변수로만. 파일·채팅·커밋에 쓰지 않는다
```

수치(창 크기·요청 간격·하루 상한·재시도)는 `ingest/config/default.yaml`.

## 명령

```bash
uv run --project ingest regime-ingest backfill --from 2021-01-01 --to 2026-10-06   # 5년 소급 (끊기면 다시 실행 → 커서부터)
uv run --project ingest regime-ingest run                                          # 순방향 (오늘 공시, OS 스케줄러로 주기 실행)
uv run --project ingest regime-ingest status                                       # 커서·수집 범위·최근 실행
uv run --project ingest python -m pytest ingest                                    # 테스트 (네트워크·키 없음)
```

## 저장 (`<ext_store>/`)

| 경로 | 내용 |
|---|---|
| `raw/opendart/<수집일>/<run_id>.jsonl.gz` | 받은 응답 그대로 (추가만) |
| `docs/source=opendart/date=<YYYY-MM>/part-*.parquet` | 정규화 문서 (plan §5 스키마). 내용이 바뀐 공시는 `version` + 1 새 행 |
| `state.sqlite` | `ingest_runs`·`coverage`(일 × 시장, collected/partial/gap)·`cursors`·`seen_keys`·`report_history`·`doc_files`·`request_days` |

- 커서 = "여기까지 빠짐없이 완료". 잘못된 행이 있던 창(`partial`)·실패 창(`gap`) 뒤로는 커서를 넘기지 않아 다음 실행이 다시 받는다
- 문서 파일은 `*.parquet.tmp` → SQLite commit → 이름 확정 순서. 중간에 죽으면 다음 실행 시작 때 정리된다

## 시각 규칙 (plan §4)

- 소급분(`backfilled=true`, 날짜만): `available_at` = 접수일 **다음 날 00:00 KST** → 다음 거래일 첫 봉부터 연결
- 순방향: `available_at` = 처음 본 시각(`first_seen_at`)
- 같은 접수번호의 **내용 변경 버전**(version ≥ 2)은 소급 모드여도 처음 본 시각부터 → 과거 as_of 결과 불변
- 종목코드는 6자리 대문자 영숫자만 연결(`resolved`), 형식이 틀리면 `invalid_code`(원값은 `stock_code_raw`)
- 정정 공시(`[기재정정]` 등)는 같은 회사·같은 기본 보고서명의 공시 중 **자기보다 앞선 접수번호**의 가장 늦은 것을 `amends_candidate_doc_id`(후보, `amends_basis=same_corp_base_title`)로 남긴다(수집 순서와 무관). 제목만으로 원공시 관계를 확정하지 않으며, 확정 규칙은 P3-6 계약·plan §11 1-4 실측 뒤
