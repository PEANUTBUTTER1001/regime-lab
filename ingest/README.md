# regime-ingest — 외부 자료 수집 (P3-7 OpenDART 공시 목록)

과거 데이터 분석 도구의 근거 자료 수집기이며 투자 권유가 아닙니다. 설계: [`docs/plan/03-04-external-crawling-automation.md`](../docs/plan/03-04-external-crawling-automation.md) · 계약: `docs/P3_수집_계약.md` (hchee99, PR #23)

- 범위: OpenDART 공시검색 **목록 메타데이터만**(`license_scope=metadata_only`). 원문 ZIP 은 받지 않고, 화면에는 보고서명·DART 링크만 보인다.
- 엔진·API 는 이 패키지를 import 하지 않고 저장된 Parquet(`<ext_store>/docs/`)만 읽는다.

## 설정

```bash
cp ingest/config/paths.example.yaml ingest/config/paths.local.yaml   # ext_store 위치 (Git 제외, 저장소 밖만 허용)
export DART_API_KEY=...   # 키는 환경변수로만. 파일·채팅·커밋에 쓰지 않는다
```

수치(창 크기·요청 간격·하루 상한·재시도)는 `ingest/config/default.yaml`.

## 명령

```bash
uv run --project ingest regime-ingest backfill --from 2021-01-01 --to 2026-10-06   # 5년 소급 (끊기면 다시 실행 → 안 받은 창만, --refetch 는 전부 다시)
uv run --project ingest regime-ingest run                                          # 순방향 (오늘 공시, OS 스케줄러로 주기 실행)
uv run --project ingest regime-ingest status                                       # 커서·수집 범위·최근 실행
uv run --project ingest python -m pytest ingest                                    # 테스트 (네트워크·키 없음)
```

## 순방향 스케줄 (P3-7.5, plan §6.1·§7.1)

OS 스케줄러로 `run` 을 주기 실행한다(API 서버와 독립). 같은 출처가 이미 돌고 있으면 새 실행은 `skipped` 로 기록하고 끝난다(잠금).
주기는 거래일 07:00~20:00 KST 5분, 그 밖은 1시간 (요청 수 추정: 하루 공시 약 400건 → 1회 약 8요청 × 약 170회 ≈ 1,400요청/일, 상한 10,000 안).
**등록은 수집 PC 담당자가 직접** 한다 — 아래는 예시이며 저장소 스크립트가 자동 등록하지 않는다. 키는 OS 의 사용자 환경변수로 둔다.

macOS·Linux (`crontab -e`, 시스템 시간대가 KST 라고 가정):

```cron
*/5 7-19 * * 1-5  cd /path/to/regime-lab && uv run --project ingest regime-ingest run >> ~/regime-ingest.log 2>&1
0 0-6,20-23 * * *  cd /path/to/regime-lab && uv run --project ingest regime-ingest run >> ~/regime-ingest.log 2>&1
```

Windows (작업 스케줄러, PowerShell — `DART_API_KEY` 는 사용자 환경변수로 미리 설정):

```powershell
$act = New-ScheduledTaskAction -Execute "uv" -Argument "run --project ingest regime-ingest run" -WorkingDirectory "C:\path\to\regime-lab"
$day = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 07:00
$day.Repetition = (New-ScheduledTaskTrigger -Once -At 07:00 -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 13)).Repetition
Register-ScheduledTask -TaskName "regime-ingest-opendart" -Action $act -Trigger $day
```

- 휴장일에도 돌지만 공시가 없으면 `013`(데이터 없음) 한 번씩만 요청한다. 휴장일 목록 출처는 D-5
- 상태 확인: `regime-ingest status` — 최근 실행의 `status`(ok/partial/failed/skipped)·요청 수, 날짜별 collected/partial/gap 수
- 순방향 실행은 오늘 날짜를 `forward`(일부만 봄)로 남긴다. 소급(`backfill`)은 `collected` 만 건너뛰므로 나중에 그 날 전체를 다시 받아 빠진 공시를 채운다. 순방향으로 이미 본 공시는 중복으로 걸러져 `available_at`(처음 본 시각)이 그대로다

## 저장 (`<ext_store>/`)

| 경로 | 내용 |
|---|---|
| `raw/opendart/<수집일>/<run_id>.jsonl.gz` | 받은 응답 그대로 (추가만) |
| `docs/source=opendart/date=<YYYY-MM>/part-*.parquet` | 정규화 문서 (plan §5 스키마). 내용이 바뀐 공시는 `version` + 1 새 행 |
| `state.sqlite` | `ingest_runs`·`coverage`(일 × 시장, collected/forward/partial/gap, 관측 시각) + 덮어쓰지 않는 `coverage_log`·`cursors`·`seen_keys`·`report_history`·`doc_files`·`request_days` |

- `coverage/source=<출처>/coverage_log.parquet`: 수집 범위 이력(추가만)을 커밋마다 내보낸 파일. 열 `seq·source·day·corp_cls·state·run_id·observed_at`. 소비자는 (day, corp_cls)마다 `observed_at ≤ T`인 마지막 행으로 T 시점 수집 상태(collected·forward·partial·gap, 행 없음 = 시도 안 함)를 다시 만든다. 공백(`gap`)은 "자료 0건"과 다르다 (FR-N4)
- 커서 = "여기까지 빠짐없이 완료". 잘못된 행이 있던 창(`partial`)·실패 창(`gap`) 뒤로는 커서를 넘기지 않아 다음 실행이 다시 받는다
- 문서 파일은 `*.parquet.tmp` → SQLite commit → 이름 확정 순서. 중간에 죽으면 다음 실행 시작 때 정리된다

## 소비 쪽: 과거 시점 조회 (계약 §4)

`regime_ingest.dedup.select_as_of(docs, t, mode)` — doc_id 마다 t 에 허용되는 최신 버전.

| mode | 조건 | 용도 |
|---|---|---|
| `observed` (기본) | `first_seen_at ≤ t` 이고 `available_at ≤ t` | 수집기가 실제로 가지고 있던 것 — 브리핑·분봉 |
| `historical_assumed` | `available_at ≤ t` | 소급 정책을 받아들인 일봉 연구. 당시 보유 증거가 아니므로 결과를 따로 보고 |

엔진·API 는 이 패키지를 import 하지 않으므로 문서-봉 결합(P4-2, hchee99)이 같은 규칙을 Parquet 위에서 쓰거나 공용 위치를 G2 에서 정한다. 봉 경계·확정 시각·거래일 달력은 P2-4 `bars.py`(Seonghwanaa)가 제공한다.

## 시각 규칙 (plan §4)

- 소급분(`backfilled=true`, 날짜만): `available_at` = 접수일 **다음 날 00:00 KST** → 다음 거래일 첫 봉부터 연결
- 순방향: `available_at` = 처음 본 시각(`first_seen_at`)
- 같은 접수번호의 **내용 변경 버전**(version ≥ 2)은 `max(처음 본 시각, 직전 버전 available_at)` 부터, `backfilled=false` → 과거 as_of 결과 불변
- 행 검증: 접수번호 숫자 14자리·회사코드 8자리·접수일이 조회 창 안·시장이 조회한 시장. 000 응답도 list·페이지 필드와 끝 페이지 건수·고유 접수번호·total_count 를 대조 → 어긋나면 그 창은 `gap`
- 서버 메시지는 기록하지 않고 상태 코드만 남긴다. 리디렉션은 따라가지 않는다(키가 든 URL 보호). HTTP 429 = 요청 제한
- 실제 잠금 충돌만 `skipped`. 저장 경로·권한·manifest(`docs/source=opendart/_manifest.json` 의 schema·policy 버전) 오류는 실패
- 종목코드는 6자리 대문자 영숫자만 연결(`resolved`), 형식이 틀리면 `invalid_code`(원값은 `stock_code_raw`)
- 정정 공시(`[기재정정]` 등)는 같은 회사·같은 기본 보고서명의 공시 중 **자기보다 앞선 접수번호**의 가장 늦은 것을 `amends_candidate_doc_id`(후보, `amends_basis=same_corp_base_title`)로 남긴다(수집 순서와 무관). 제목만으로 원공시 관계를 확정하지 않으며, 확정 규칙은 P3-6 계약·plan §11 1-4 실측 뒤
