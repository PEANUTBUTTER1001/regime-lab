"""수집 유스케이스 (plan/03-04 §3 단계 1~6): 창별 조회 → 원본 보관 → 정규화 → 중복·버전 → 정정 연결 → 기록.

- 창(window) 하나가 끝나야 그 창의 문서·커서·수집 범위(coverage)를 한 번에 확정한다(창 단위 트랜잭션).
  중간에 끊기면 그 창은 'gap' 으로 남고 다음 실행이 커서부터 이어 받는다.
- 같은 창을 다시 돌려도 문서 수·버전이 늘지 않는다(멱등). 내용이 바뀐 공시만 version + 1 로 추가한다.
- 시계·네트워크·파일은 주입받는다 (R2·R3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from regime_ingest.dedup import classify, link_amendments
from regime_ingest.normalize import SOURCE, normalize
from regime_ingest.ports import DartError, DocStore, Incomplete, ListClient, RateLimited, SourceLocked
from regime_ingest.timing import KST, days, month_windows


@dataclass
class RunStats:
    run_id: str
    mode: str
    window_from: str
    window_to: str
    started_at: str
    requests: int = 0
    received: int = 0
    new: int = 0
    duplicate: int = 0
    changed: int = 0
    errors: int = 0
    skipped_windows: int = 0
    status: str = "ok"
    message: str = ""
    error_samples: list[str] = field(default_factory=list)

    def record(self, finished_at: str) -> dict:
        return {**{k: v for k, v in self.__dict__.items() if k not in ("error_samples", "skipped_windows")},
                "source": SOURCE,
                "finished_at": finished_at}


def _fetch_window(client: ListClient, store: DocStore, cfg: dict, bgn: date, end: date, *, backfilled: bool,
                  now: Callable[[], datetime], stats: RunStats) -> tuple[dict[str, dict], int]:
    """한 창의 모든 corp_cls 를 받아 정규화한다. (문서, 잘못된 행 수). RateLimited·DartError 는 그대로 올린다."""
    docs: dict[str, dict] = {}
    bad = 0
    raw_day = now().astimezone(KST).date().isoformat()
    for cls in cfg["corp_cls"]:
        for page, body in client.pages(bgn, end, cls):
            items = body.get("list") or []
            store.save_raw(SOURCE, raw_day, stats.run_id, [{"window": [bgn.isoformat(), end.isoformat()],
                                                           "corp_cls": cls, "page": page, "response": body}])
            seen_at = now()
            for it in items:
                stats.received += 1
                try:
                    d = normalize(it, first_seen_at=seen_at, backfilled=backfilled, ingest_run_id=stats.run_id,
                                  viewer_url=cfg["viewer_url"], license_scope=cfg["license_scope"],
                                  window=(bgn, end), corp_cls=cls)
                except ValueError as e:
                    bad += 1
                    if len(stats.error_samples) < 5:
                        stats.error_samples.append(f"{bgn}~{end} {cls}: {e}")
                    continue
                docs.setdefault(d["doc_id"], d)  # 페이지가 밀려 같은 공시가 두 번 와도 처음 것만
    return docs, bad


def _commit_window(store: DocStore, cfg: dict, docs: dict[str, dict], bgn: date, end: date, *, complete: bool,
                   advance_cursor: bool, stats: RunStats, forward: bool, observed_at: str):
    """중복·버전 → 정정 후보 → 문서(tmp)·상태 → commit → 문서 확정. commit 전 실패면 tmp 를 지우고 되돌린다."""
    seen = store.seen(list(docs))
    keep = []
    for d in docs.values():
        kind, row = classify(d, seen)
        setattr(stats, kind, getattr(stats, kind) + 1)
        if row is not None:
            keep.append(row)
    staged: list = []
    try:
        hist = store.report_history({(d["corp_code"], d["report_base"]) for d in keep})
        store.add_report_history(link_amendments(keep, hist))
        staged = store.stage_docs(SOURCE, stats.run_id, keep)
        store.mark_seen(keep)
        # 잘못된 행이 있던 창은 완료로 치지 않는다. 순방향은 그날이 끝나기 전 일부만 본 것이라 'forward' 로 남겨
        # 소급이 그 날을 건너뛰지 않게 한다 (collected 는 소급으로 그 날 전체를 받은 경우만)
        state = "partial" if not complete else ("forward" if forward else "collected")
        for cls in cfg["corp_cls"]:
            store.set_coverage(SOURCE, [x.isoformat() for x in days(bgn, end)], cls, state, stats.run_id, observed_at)
        if advance_cursor:
            store.set_cursor(SOURCE, "backfill", end.isoformat())
        store.commit()
    except BaseException:
        store.rollback()
        store.discard(staged)
        raise
    store.publish(staged)


def _window_done(cov: dict, cfg: dict, bgn: date, end: date) -> bool:
    return all(cov.get((x.isoformat(), cls)) == "collected" for x in days(bgn, end) for cls in cfg["corp_cls"])


def collect(store: DocStore, client: ListClient, cfg: dict, start: date, end: date, *, mode: str,
            now: Callable[[], datetime], run_id: str, refetch: bool = False) -> RunStats:
    """mode='backfill': [start, end] 를 창으로 나눠 커서 다음 날부터 받는다 (backfilled=True).
    mode='forward': [start, end](보통 오늘 하루)를 바로 받는다. available_at = 처음 본 시각.

    소급은 **날짜별 수집 범위(coverage)** 로 건너뛸 창을 정한다: 모든 날 × 시장이 collected 인 창만 건너뛰고,
    partial·gap·기록 없음인 창은 다시 받는다(이미 받은 문서는 중복으로 걸러져 늘지 않는다). 그래서 요청 범위의
    앞쪽이 비어 있으면 뒤쪽을 먼저 받았더라도 채운다. refetch=True 면 collected 창도 다시 받는다(정정 재확인용).
    커서는 상태 표시용으로, 이번 실행에서 요청 시작일부터 빠짐없이 완료한 마지막 날이다."""
    if mode not in ("backfill", "forward"):
        raise ValueError(mode)
    stats = RunStats(run_id, mode, start.isoformat(), end.isoformat(), now().isoformat())
    req_day = now().astimezone(KST).date().isoformat()
    client.requests = store.requests_on(SOURCE, req_day)
    base_requests = client.requests
    try:
        lock = store.lock(SOURCE)
    except SourceLocked as e:  # 실제 잠금 충돌만 skipped. 경로·권한·manifest 오류는 그대로 실패 (계약 §5)
        stats.status, stats.message = "skipped", str(e)
        store.save_run(stats.record(now().isoformat()))
        store.commit()
        return stats
    try:
        windows = month_windows(start, end, int(cfg["window_months"])) if start <= end else []
        cov = store.coverage(SOURCE) if mode == "backfill" and not refetch else {}
        contiguous = mode == "backfill"
        for bgn, wend in windows:
            if cov and _window_done(cov, cfg, bgn, wend):
                stats.skipped_windows += 1
                if contiguous:
                    store.set_cursor(SOURCE, "backfill", wend.isoformat())
                continue
            try:
                docs, bad = _fetch_window(client, store, cfg, bgn, wend, backfilled=(mode == "backfill"), now=now,
                                          stats=stats)
            except DartError as e:  # RateLimited·Incomplete 포함
                store.rollback()
                for cls in cfg["corp_cls"]:  # 실패 창은 '공백' — 0건과 구분 (plan/03-04 §7.3)
                    store.set_coverage(SOURCE, [x.isoformat() for x in days(bgn, wend)], cls, "gap", run_id,
                                       now().isoformat())
                store.commit()
                stats.status = "partial" if isinstance(e, (RateLimited, Incomplete)) else "failed"
                stats.message = f"{bgn}~{wend}: {e}"
                break
            stats.errors += bad
            contiguous = contiguous and bad == 0
            _commit_window(store, cfg, docs, bgn, wend, complete=(bad == 0), advance_cursor=contiguous, stats=stats,
                           forward=(mode == "forward"), observed_at=now().isoformat())
        if stats.errors and stats.status == "ok":
            stats.status = "partial"
            stats.message = "잘못된 행이 있는 창은 partial 로 남김 (커서 미전진): " + "; ".join(stats.error_samples)
    finally:
        stats.requests = client.requests - base_requests
        store.set_requests(SOURCE, req_day, client.requests)
        store.save_run(stats.record(now().isoformat()))
        store.commit()
        store.unlock(lock)
    return stats
