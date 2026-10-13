"""P3-13.3 영향 분석 조회 (SRS FR-N6·FR-N7, 결정 P3-12a): 모델 고르기·가중치 표시·사건 연결 봉·결과 없음 4종·절단 불변.

P3-12 학습 결과는 아직 없으므로 계약 형식(docs/P3_영향분석_화면_요구.md §8)의 합성 결과 폴더로 돈다.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from regime_lab import influence_runs as IR
from regime_lab.config import load_config
from regime_lab.data.influence_store import InfluenceStore

KST = timezone(timedelta(hours=9))
CODE = "005930"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def meta(version, train_end, test_end="2025-12-31", **kw):
    m = {"format_version": 1, "model_version": version, "created_at": "2026-10-12T20:00:00+09:00",
         "target": "next_1d_excess", "as_of_mode": "observed",
         "periods": {"train": {"start": "2021-01-04", "end": train_end},
                     "valid": {"start": "2024-03-04", "end": "2024-06-28"}, "test": {"start": "2024-07-01", "end": test_end}},
         "baseline": {"name": "price_only", "metric": "hit_rate", "value": 0.51, "model_value": 0.53, "higher_is_better": True},
         "min_eval_samples": 30, "inputs": {"docs": "fp-docs", "prices": "fp-px"}}
    m.update(kw)
    return m


WEIGHTS = pd.DataFrame([
    # group_type, group, weight, direction, n_events, n_eval, eval_metric, ci_low, ci_high, sample_ok
    ("report_type", "자기주식취득결정", 0.20, "+", 400, 80, 0.55, 0.05, 0.35, True),
    ("report_type", "유상증자결정", -0.30, "-", 300, 60, 0.54, -0.50, -0.10, True),
    ("report_type", "단일판매공급계약", 0.40, "+", 200, 40, 0.52, -0.10, 0.90, True),    # 구간이 0 포함 → 불확실
    ("report_type", "임상시험계획승인", 0.90, "+", 20, 5, None, None, None, False),        # 표본 부족 → 숨김
    ("source_type", "disclosure", 0.05, "0", 900, 185, 0.53, 0.01, 0.09, True),           # 방향 0 → 불확실
], columns=["group_type", "group", "weight", "direction", "n_events", "n_eval", "eval_metric", "ci_low", "ci_high",
            "sample_ok"])


def ev(doc_id, available_at, version=1, period="test", group="자기주식취득결정", contribution=0.01):
    return {"doc_id": doc_id, "version": version, "ticker": CODE, "available_at": available_at,
            "linked_bar_start": None, "group": group, "contribution": contribution, "period": period}


def doc(doc_id, seen, available=None, version=1, title="자기주식취득결정"):
    return {"doc_id": doc_id, "version": version, "first_seen_at": seen, "available_at": available or seen,
            "title": title, "url": f"https://dart.example/{doc_id}", "source_type": "disclosure",
            "license_scope": "metadata_only", "time_precision": "minute", "status": "active"}


def frame():
    """2024-02-26 ~ 2024-03-29 영업일 일봉. 2024-03-01(금, 삼일절)은 휴장."""
    days = [d for d in pd.bdate_range("2024-02-26", "2024-03-29") if d != pd.Timestamp("2024-03-01")]
    return pd.DataFrame({"ticker": CODE, "date": days, "close": [1000.0 + i for i in range(len(days))], "is_warmup": False})


class Cov:
    """DocsStore.coverage() 대역 — 수집 범위 이력 행."""

    def __init__(self, rows):
        self.rows = rows

    def coverage(self):
        return list(self.rows)


def cov_rows(days, state="collected", observed="2024-04-01T00:00:00+09:00"):
    return [{"source": "opendart", "seq": i, "day": d, "corp_cls": c, "state": state, "run_id": "r", "observed_at": observed}
            for i, (d, c) in enumerate((d, c) for d in days for c in ("Y", "K"))]


def store_with(tmp_path, models):
    s = InfluenceStore(tmp_path / "runs")
    for m, events in models:
        s.write_model(m["model_version"], m, WEIGHTS, pd.DataFrame(events))
    return s


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=KST)


EVENTS = [
    ev("opendart:A", "2024-02-26T08:59:00+09:00"),   # 09:00 전 → 같은 날 봉
    ev("opendart:B", "2024-02-27T09:00:00+09:00"),   # 정확히 09:00 → 그 날 09:00 봉이 아니라 다음 날
    ev("opendart:C", "2024-02-28T15:40:00+09:00"),   # 장 마감 뒤 → 다음 거래일
    ev("opendart:D", "2024-02-29T16:00:00+09:00"),   # 다음 날 3/1 휴장 → 3/4(월)
    ev("opendart:E", "2024-03-05T23:30:00+00:00"),   # UTC → KST 3/6 08:30 → 3/6
    ev("opendart:F", "2024-03-07T10:00:00+09:00", period="valid", group="유상증자결정", contribution=None),
]
DOCS = [doc("opendart:A", "2024-02-26T08:59:00+09:00"), doc("opendart:B", "2024-02-27T09:00:00+09:00"),
        doc("opendart:C", "2024-02-28T15:40:00+09:00"), doc("opendart:D", "2024-02-29T16:00:00+09:00"),
        doc("opendart:E", "2024-03-05T23:30:00+00:00"),
        # F: 소급분 — 쓸 수 있게 된 시각은 과거지만 처음 본 것은 3/20 → observed 에서는 3/20 전엔 안 보임
        doc("opendart:F", "2024-03-20T12:00:00+09:00", available="2024-03-07T10:00:00+09:00")]


def test_no_results_folder_is_no_model(tmp_path, cfg):
    s = InfluenceStore(tmp_path / "runs")
    t = "2024-03-15T15:30:00+09:00"
    assert IR.model_view(s, t)["status"] == "no_model"
    assert IR.weights_view(s, t)["status"] == "no_model"
    assert IR.events_view(s, DOCS, Cov([]), frame(), cfg, CODE, None, None, t)["status"] == "no_model"


def test_model_picked_only_after_train_end_day(tmp_path, cfg):
    s = store_with(tmp_path, [(meta("m-old", "2024-02-29"), EVENTS), (meta("m-new", "2024-03-15"), EVENTS)])
    assert IR.model_view(s, "2024-02-29T20:00:00+09:00")["status"] == "no_model"   # 학습 끝 날 당일은 아직
    assert IR.model_view(s, "2024-03-04T00:00:00+09:00")["model"]["model_version"] == "m-old"
    assert IR.model_view(s, "2024-03-15T23:59:00+09:00")["model"]["model_version"] == "m-old"
    assert IR.model_view(s, "2024-03-16T09:00:00+09:00")["model"]["model_version"] == "m-new"
    # T 뒤에 학습된 모델이 있어도 모델이 아예 없을 때와 같은 응답 (뒤 모델의 존재를 알리지 않음)
    t = "2024-02-20T10:00:00+09:00"
    assert IR.model_view(s, t) == IR.model_view(InfluenceStore(tmp_path / "none"), t)


def test_weights_display_rules(tmp_path, cfg):
    s = store_with(tmp_path, [(meta("m1", "2024-02-29"), EVENTS)])
    res = IR.weights_view(s, "2024-03-15T10:00:00+09:00")
    shown = {r["group"]: r["direction_shown"] for r in res["rows"]}
    assert shown == {"자기주식취득결정": "+", "유상증자결정": "-", "단일판매공급계약": "uncertain",
                     "임상시험계획승인": "hidden", "disclosure": "uncertain"}
    assert [r["group"] for r in res["rows"]][-1] == "임상시험계획승인"  # 표본 부족은 큰 가중치라도 맨 뒤
    assert [r["group"] for r in res["rows"]][:2] == ["단일판매공급계약", "유상증자결정"]  # 그다음 |가중치| 큰 순
    assert res["baseline"] == {"name": "price_only", "metric": "hit_rate", "baseline": 0.51, "model": 0.53, "improved": True}
    assert res["model"]["eval_beyond_as_of"] is True  # 평가 구간(~2025-12-31)이 기준 시각 뒤까지
    r = next(x for x in res["rows"] if x["group"] == "임상시험계획승인")
    assert r["eval_metric"] is None and r["ci_low"] is None and isinstance(r["n_eval"], int)
    worse = store_with(tmp_path / "b", [(meta("m2", "2024-02-29", baseline={
        "name": "price_only", "metric": "logloss", "value": 0.60, "model_value": 0.62, "higher_is_better": False}), EVENTS)])
    assert IR.weights_view(worse, "2024-03-15T10:00:00+09:00")["baseline"]["improved"] is False


def test_event_linking_time_boundaries(tmp_path, cfg):
    s = store_with(tmp_path, [(meta("m1", "2024-02-23"), EVENTS)])
    res = IR.events_view(s, DOCS, Cov(cov_rows(["2024-02-26"])), frame(), cfg, CODE, None, None,
                         "2024-03-29T16:00:00+09:00", mode="historical_assumed")
    link = {e["doc_id"]: e["linked_bar_start"] for e in res["events"]}
    assert link == {"opendart:A": "2024-02-26T09:00:00+09:00", "opendart:B": "2024-02-28T09:00:00+09:00",
                    "opendart:C": "2024-02-29T09:00:00+09:00", "opendart:D": "2024-03-04T09:00:00+09:00",
                    "opendart:E": "2024-03-06T09:00:00+09:00", "opendart:F": "2024-03-08T09:00:00+09:00"}
    e = next(x for x in res["events"] if x["doc_id"] == "opendart:E")
    assert e["available_at"] == "2024-03-06T08:30:00+09:00" and e["close"] == 1000.0 + 6  # 3/6 은 7번째 거래일 (2/26 부터, 3/1 휴장)
    assert e["title"] == "자기주식취득결정" and e["license_scope"] == "metadata_only"


def test_observed_hides_backfilled_version_until_first_seen(tmp_path, cfg):
    s = store_with(tmp_path, [(meta("m1", "2024-02-23"), EVENTS)])
    args = (s, DOCS, Cov(cov_rows(["2024-03-11"])), frame(), cfg, CODE, None, None)
    obs = {e["doc_id"] for e in IR.events_view(*args, "2024-03-15T16:00:00+09:00")["events"]}
    hist = {e["doc_id"] for e in IR.events_view(*args, "2024-03-15T16:00:00+09:00", mode="historical_assumed")["events"]}
    assert "opendart:F" not in obs and hist - obs == {"opendart:F"}
    assert IR.events_view(*args, "2024-03-15T16:00:00+09:00")["mode"] == "observed"  # 모델 as_of_mode 기본


def test_prices_and_calendar_stop_at_confirmed_close(tmp_path, cfg):
    s = store_with(tmp_path, [(meta("m1", "2024-02-23"), EVENTS)])
    morning = IR.events_view(s, DOCS, Cov([]), frame(), cfg, CODE, None, None, "2024-02-27T10:00:00+09:00")
    assert morning["last_bar"] == "2024-02-26" and morning["bars"][-1]["date"] == "2024-02-26"
    # B(2/27 09:00 관측)는 2/28 봉에 연결될 사건 — 그 봉은 아직 없으므로 연결 봉 없음
    assert {e["doc_id"]: e["linked_bar_start"] for e in morning["events"]} == {"opendart:A": "2024-02-26T09:00:00+09:00",
                                                                               "opendart:B": None}
    close = IR.events_view(s, DOCS, Cov([]), frame(), cfg, CODE, None, None, "2024-02-27T15:30:00+09:00")
    assert close["last_bar"] == "2024-02-27"


def _truncate(t: datetime, mode, docs, rows, fr):
    """T 뒤 정보만 지운다. observed 는 T 뒤에 처음 본 자료·T 뒤 관측 기록까지 지우고, historical_assumed 는
    소급 가정에 따라 T 이전 날짜에 대한 늦은 기록은 남기고 T 뒤 날짜만 지운다 (retrieval.coverage 규칙)."""
    tk = t.astimezone(KST).replace(tzinfo=None)
    if mode == "observed":
        keep_docs = [d for d in docs if datetime.fromisoformat(d["first_seen_at"]) <= t]
        keep_rows = [r for r in rows if datetime.fromisoformat(r["observed_at"]) <= t]
    else:
        keep_docs = [d for d in docs if datetime.fromisoformat(d["available_at"]) <= t]
        keep_rows = [r for r in rows if r["day"] <= tk.date().isoformat()]
    keep_fr = fr[fr["date"] + pd.Timedelta(hours=15, minutes=30) <= tk]
    return keep_docs, keep_rows, keep_fr


@pytest.mark.parametrize("t", ["2024-03-07T09:30:00+09:00", "2024-03-12T15:30:00+09:00", "2024-03-21T08:00:00+09:00"])
def test_truncation_invariance(tmp_path, cfg, t):
    """T 뒤의 자료·가격·수집 기록·모델 버전을 지워도 T 시점 세 응답이 같다."""
    tt = datetime.fromisoformat(t)
    rows = cov_rows(["2024-03-04", "2024-03-05"]) + cov_rows(["2024-03-06"], state="gap", observed="2024-03-25T00:00:00+09:00")
    full = store_with(tmp_path / "full", [(meta("m-old", "2024-02-23"), EVENTS), (meta("m-late", "2024-03-25"), EVENTS)])
    cut = store_with(tmp_path / "cut", [(meta("m-old", "2024-02-23"), EVENTS)])
    for mode in ("observed", "historical_assumed"):
        docs, rows2, fr2 = _truncate(tt, mode, DOCS, rows, frame())
        a = IR.events_view(full, DOCS, Cov(rows), frame(), cfg, CODE, "2024-02-01", None, t, mode=mode)
        b = IR.events_view(cut, docs, Cov(rows2), fr2, cfg, CODE, "2024-02-01", None, t, mode=mode)
        assert a == b and a["status"] == "ok"
    assert IR.weights_view(full, t) == IR.weights_view(cut, t)
    assert IR.model_view(full, t) == IR.model_view(cut, t)


def test_empty_results_are_distinguished(tmp_path, cfg):
    s = store_with(tmp_path, [(meta("m1", "2024-02-23"), EVENTS)])
    t = "2024-03-29T16:00:00+09:00"
    seen = "2024-03-28T00:00:00+09:00"
    gap = Cov(cov_rows(["2024-03-25"], state="gap", observed=seen))
    ok = Cov(cov_rows(["2024-03-25", "2024-03-26"], observed=seen))
    assert IR.events_view(s, DOCS, ok, frame(), cfg, CODE, "2024-03-25", "2024-03-29", t)["status"] == "no_events"
    res = IR.events_view(s, DOCS, gap, frame(), cfg, CODE, "2024-03-25", "2024-03-29", t)
    assert res["status"] == "coverage_gap" and res["coverage_gaps"]["opendart"]["gap_days"] == ["2024-03-25"]
    assert res["bars"] and res["events"] == []  # 수집 공백이어도 가격은 보인다
    assert IR.events_view(s, DOCS, gap, frame(), cfg, CODE, "2024-02-26", "2024-03-29", t)["status"] == "ok"
    bad = InfluenceStore(tmp_path / "bad" / "runs")
    bad.write_model("m9", meta("m9", "2024-02-23", format_version=9), WEIGHTS, pd.DataFrame(EVENTS))
    with pytest.raises(IR.InfluenceError) as e:
        IR.model_view(bad, t)
    assert e.value.code == "data_unavailable"


def test_validation_and_unknown_ticker(tmp_path, cfg):
    s = store_with(tmp_path, [(meta("m1", "2024-02-23"), EVENTS)])
    for kw, field in [({"as_of": "2024-03-15T10:00:00"}, "as_of"), ({"ticker": "5930"}, "ticker"),
                      ({"start": "2024/03/01"}, "from"), ({"start": "2024-03-10", "end": "2024-03-01"}, "to"),
                      ({"mode": "x"}, "mode")]:
        args = {"ticker": CODE, "start": None, "end": None, "as_of": "2024-03-15T10:00:00+09:00", "mode": None, **kw}
        with pytest.raises(IR.InfluenceError) as e:
            IR.events_view(s, DOCS, Cov([]), frame(), cfg, args["ticker"], args["start"], args["end"], args["as_of"],
                           mode=args["mode"])
        assert e.value.code == "validation_failed" and field in e.value.detail["fields"]
    with pytest.raises(IR.InfluenceError) as e:
        IR.events_view(s, DOCS, Cov([]), frame(), cfg, "000660", None, None, "2024-03-15T10:00:00+09:00")
    assert e.value.code == "ticker_not_found"


def test_store_skips_unfinished_and_refuses_overwrite(tmp_path):
    s = store_with(tmp_path, [(meta("m1", "2024-02-23"), EVENTS)])
    (s.root / ".tmp_m2").mkdir()
    (s.root / "m3").mkdir()  # 파일이 다 없는 폴더
    assert [m["model_version"] for m in s.list_models()] == ["m1"]
    with pytest.raises(FileExistsError):
        s.write_model("m1", meta("m1", "2024-02-23"), WEIGHTS, pd.DataFrame(EVENTS))
    assert list(s.read_events("m1", CODE)["doc_id"]) == [e["doc_id"] for e in EVENTS]
