# 근거 검색(RAG) — 계약·평가 기준·실험 기록 (P3-11)

| 항목 | 내용 |
|---|---|
| 작성 | PEANUTBUTTER1001, 2026-10-08 |
| 범위 | SRS v2.0 FR-N5 근거 검색의 **검색 단계(R)**. 답변 생성(G)은 D-7 결정 전이라 제품에 없다 |
| 결정 | [`구현_전_결정사항.md`](구현_전_결정사항.md) §5.3 `P3-11` 행 |
| 코드 | 도메인 `engine/src/regime_lab/rag/`, 유스케이스 `engine/src/regime_lab/retrieval.py`, 인프라 `engine/src/regime_lab/data/docs.py`, API `api/src/regime_api/routes/evidence.py`, 화면 `web/js/views/evidence.js`(사이드바 '근거 검색', `#/evidence`) |
| 상태 | 구현·합성 데이터 테스트 완료, 로컬 시연 데이터(§0)로 화면 검색 확인. **평가셋 수치는 아직 없다** (아래 표의 빈칸은 측정 전) |

## 0. 데이터 출처 — 현재 테스트 단계와 전환 예정

**현재(2026-10-08, 테스트 단계).** 이 PC에는 정식으로 수집한 외부 자료가 없어, 근거 검색은 n8n 엑셀(`주식 분석 n8n (1).xlsx`의 '수집 원자료' 시트, 한경·매경 등 RSS 뉴스)을 변환한 **로컬 시연 자료**를 읽는다.

| 항목 | 내용 |
|---|---|
| 로컬 설정 | `engine/config/paths.local.yaml`(Git 제외)에 `ext_store: ../rag-study/ext_store   # 2026-10-08 로컬 시연용: n8n 엑셀 뉴스 변환본 (저장소 밖)` |
| 변환본 위치 | 저장소 밖 `../rag-study/ext_store/docs/source=n8n_news/` (변환 스크립트 `../rag-study/xlsx_to_docs.py`) |
| 내용 | 4,832행 → 4,653건(같은 제목은 최초 수집이 가장 이른 행). LLM 판정 시트(시트1·종목별 종합)는 넣지 않음. `available_at` = 최초 수집 시각 |
| 범위 | 결정 §5.3 `P3-11` ①: 로컬 개발·평가·시연 픽스처일 뿐 정식 출처가 아니다. 저장소에 커밋하지 않는다. 제품 색인은 제목·회사명만(③) |

**전환 예정.** 크롤링·수집 자료도 지금의 주식 데이터처럼 **데이터 서버의 API·DB를 호출해 조회하는 방식**으로 바꾼다(주식 데이터는 Seonghwanaa 데이터 서버의 조회 전용 API를 쓴 실데이터 회귀 사례가 있다, `progress/PROGRESS.md` 10-07). 전환할 때 정할 것:

- 조회 계약: 지금의 docs 필드(§3·[`P3_수집_계약.md`](P3_수집_계약.md) §3)와 시점 규칙(§4)을 API 응답이 그대로 지키는지. 특히 `first_seen_at`·`available_at`·버전·삭제 표시
- 배치: 네트워크 호출은 인프라에만 둔다(AGENTS.md R2). 지금의 `data/docs.py`(파일 읽기)를 API 클라이언트 구현으로 바꾸거나 하나 더 두고, 유스케이스(`retrieval.py`)는 저장소 객체만 받으므로 바꾸지 않는 것이 목표. 새 위치·의존성·출처는 AGENTS.md §9에 따라 먼저 승인
- 색인 갱신: 지금은 서버 시작·`rag-index` 때만 색인을 만든다. 조회 방식에서는 증분 갱신·캐시 무효화 방법이 필요
- 권한: 출처 대장(P3-3)에 활성 출처로 기록된 자료만 조회·표시한다(FR-N1·NFR-15)

## 1. 해결하려는 문제

- 과거 시점(as_of)의 브리핑·분석이 그때 알 수 없었던 문서를 근거로 쓰면 미래 정보 누설이다.
- 누설 경로는 두 가지다. ① 보이는 문서 ② BM25 의 IDF·평균 길이 같은 **말뭉치 통계**. 문서만 시점으로 걸러도 통계를 전체 문서로 내면 미래 문서가 늘 때 과거 순위가 바뀐다(n8n 엑셀 실측: 10-01 23:59 기준 "증권사 실적" 4·5위가 뒤바뀜).
- 그래서 역색인은 한 번만 만들고, 통계는 질의마다 as_of 에 보이는 문서로만 계산한다. `engine/tests/test_rag_rank.py` 가 절단 불변(미래 문서 50건 추가)과 대조군(전체 통계면 점수가 바뀜)을 함께 검사한다.

## 2. 검색 규칙

| 단계 | 규칙 |
|---|---|
| 문서 | `<ext_store>/docs/source=*/date=*/part-*.parquet` 확정본만. manifest `schema_version` 1 이 아니면 거부 |
| 시점 | `observed`(기본): `first_seen_at ≤ as_of` 그리고 `available_at ≤ as_of`. `historical_assumed`: `available_at ≤ as_of`. `doc_id` 별 최신 버전, `status == "deleted"` 는 늘 제외 ([`P3_수집_계약.md`](P3_수집_계약.md) §4) |
| 색인 필드 | 설정 `rag.index_fields` = 제목·회사명 (제품). 요약 등은 CLI `--fields` 개인 실험에서만 |
| 토큰화 | 한글 문자 2-gram, 영문·숫자 단어 그대로 (조사가 붙어도 겹침) |
| 순위 | BM25(k1·b 는 설정), 동점은 `doc_id` 순. MMR 은 `rag.mmr_lambda` 가 null 이면 끔 |
| 비슷한 제목 묶기 | `rag.group_threshold`(잠정 0.3)가 있으면 BM25 상위 `rag.group_candidate_k`(100)개를 순위 순서로 훑어, 앞 묶음 대표와 **질의 글자를 뺀** 제목 자카드가 문턱 이상이면 그 묶음에 넣는다. 결과는 묶음 대표, 나머지는 `similar`. 묶음은 그 시점에 보이는 후보로만 만든다(미리 전체 문서로 묶으면 미래 기사가 과거 묶음을 바꿈, `test_grouping_is_cut_invariant`). 글자 겹침이라 '같은 사건' 판정이 아니며 화면도 '제목이 비슷한 기사'로 쓴다. 묶기를 켜면 MMR 은 쓰지 않는다 |
| 근거 없음 | 보이는 문서 0건, 질의 토큰 불일치, 상위 점수 ≤ `rag.min_score` → `no_evidence` |
| 캐시 | 루트 `cache/rag/index_<키>.json`, 키 = `INDEX_VERSION` + `rag` 설정 + 문서 지문 |

`observed` 모드에서 소급 공시는 실제로 처음 본 날 이전 시점에 보이지 않는다. 과거 일봉 연구는 `historical_assumed` 로 따로 조회하고 결과를 분리해 보고한다.

## 3. API 계약 (P3-13 화면용)

`GET /api/evidence/search`

| 쿼리 | 필수 | 값 |
|---|---|---|
| `q` | 예 | 1~200자 |
| `as_of` | 예 | 시간대 포함 ISO 8601 (예 `2026-10-07T15:30:00+09:00`) |
| `mode` | 아니오 | `observed`(기본) · `historical_assumed` |
| `tickers` | 아니오 | 6자리 종목코드 쉼표 구분 |
| `k` | 아니오 | 1~20, 기본 5 |

응답 200: `{status, items, index, disclaimer}`

| 필드 | 뜻 |
|---|---|
| `status` | `ok` · `no_evidence` (근거 없음도 200) |
| `items[]` | `doc_id`, `version`, `source`, `title`, `corp_name`, `url`(원출처), `published_at`, `time_precision`(`date_only` 면 화면에 "날짜만"), `first_seen_at`, `available_at`, `score`, `matched_tokens`(검색 근거), `similar_count`, `similar[]`(`doc_id`·`title`·`source`·`url`·`published_at`·`time_precision`·`available_at`) |
| `index` | `index_version`, `docs_fingerprint`, `fields`, `total_docs`, `visible_docs`, `mode`, `as_of`, `grouping`(묶기 문턱, 끄면 null) |
| `disclaimer` | 비권유 고지 |

오류(공통 형식 `{code, message, detail, retryable}`): 422 `validation_failed`(`detail.fields`), 503 `warming_up`(재시도 가능), 503 `data_unavailable`(ext_store 없음·문서 없음·스키마 불일치, 빈 결과로 대신하지 않음).

## 4. 평가 기준

평가셋과 실험 데이터는 **저장소 밖**에 둔다(외부 자료 원문 포함, AGENTS.md §7). 형식만 여기 적는다.

```text
[{id, query, type, as_of, mode?, tickers?, split: "dev"|"test",
  relevant: [{url | doc_id, grade?}], clusters?: {url|doc_id: 사건ID}}]
```

- 규모·분할: 40~60문항, 7개 유형(종목명·약칭·다른 표현·같은 사건·시점·답 없음·전제 오류). 튜닝은 `dev` 만, `test` 는 마지막에 한 번 `--split test` 로 채점한다.
- 정답 후보는 여러 방식의 상위 결과와 직접 찾은 결과를 합친 뒤 표시한다(BM25 쪽으로 기울지 않게). 10문항은 팀원 1명이 따로 표시해 일치율을 기록한다.
- 묶기를 켜면 묶음 하나가 순위 하나다. 정답 기사가 대표가 아니라 `similar` 안에 있어도 그 순위에서 찾은 것으로 센다.
- 지표: Recall@5, MRR, nDCG@5, 고유 사건 수@5, 근거 없음 정확도(답 없는 질문의 근거 없음 비율 / 답 있는 질문의 잘못된 근거 없음 비율), 질의 p50·p95 지연.

```bash
uv run --project engine regime-lab rag-eval --gold <저장소 밖 평가셋.json> --split dev --docs <문서 폴더>
```

## 5. 실험 기록 (측정한 값만)

| ID | 구성 | 데이터 | Recall@5 | MRR | 고유 사건@5 | 비고 |
|---|---|---|---|---|---|---|
| E0 | 띄어쓰기 BM25 | 뉴스 엑셀 | | | | 기준선 |
| E1 | 2-gram BM25 (제품 기본) | 뉴스 엑셀 | | | | |
| E2 | E1 + 종목 별칭 | 뉴스 엑셀 | | | | |
| E3 | 로컬 임베딩 (저장소 밖) | 뉴스 엑셀 | | | | 제품 미도입 결정 |
| E4 | E1/E2 + E3, RRF | 뉴스 엑셀 | | | | |
| E5 | 최선 + MMR | 뉴스 엑셀 | | | | |
| E6 | 전체 통계 vs as_of 통계 | 뉴스 엑셀 | 상위 5건이 바뀐 시점 비율: | | | 누설 크기 |
| E7 | 비슷한 제목 묶기 (질의 시점) | 뉴스 엑셀 | | | | 아래 관찰 |
| P1 | 제품 구성 | 공시 메타데이터 | | | | 공시 평가셋 |

관찰(평가셋 수치 아님, 2026-10-08 n8n 엑셀·"삼성전자 자사주 매입"·지금 기준):

- 묶기·MMR 없이 상위 20건이 모두 한 사건("자사주 계획 물량 매입 완료")의 다른 언론사 기사였다.
- MMR(제목 자카드, λ 0.5·0.3)은 순서만 바꾸고 상위 10건이 여전히 같은 사건이었다 — 후보 20건이 모두 같은 사건이라 고를 다른 사건이 없음. 그래서 MMR 대신 묶기를 택했다.
- 묶기 문턱: 질의 글자를 포함하면 결과가 질의 글자를 공유해 27건이 한 묶음이 됐다(문턱 0.3). 질의 글자를 빼면 0.2 는 '대한제강 주가 상승'과 '대한전선 주가 하락' 같은 틀 제목을 잘못 묶어, 다른 근거를 가리지 않도록 보수적으로 0.3 을 잠정값으로 뒀다.
- 0.3 결과: 상위 10개 묶음 안에 비슷한 기사 13건이 접혔다(7·4·2건). 같은 사건이지만 표현이 달라 따로 남은 기사도 있다(예 '100% 돌파', '목표량 다 사들였다'). 문턱은 평가셋의 사건 표시(`clusters`)와 고유 사건 수@5 로 조정한다.

채택·제외한 기술과 이유는 측정 뒤 이 절에 적는다.
