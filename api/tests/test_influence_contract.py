"""P3-13.3 영향 분석 API 계약 (결정 P3-12a). P3-12 결과 없이 계약 형식의 합성 결과 폴더로 돈다."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab import retrieval
from regime_lab.config import Paths, load_config
from regime_lab.data.loader import DocsStore, InfluenceStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine" / "tests"))
from rag_synth import make_doc, write_store  # noqa: E402
from synth import make_market  # noqa: E402
from test_auth_contract import auth_app, signin  # noqa: E402,F401 — 로그인 켠 앱 (X3)


KST = timezone(timedelta(hours=9))
CODE = "000002"
ERROR_KEYS = {"code", "message", "detail", "retryable"}
AS_OF = "2026-03-31T16:00:00+09:00"


@pytest.fixture(scope="module")
def market():
    return make_market(load_config(), n_tickers=4, seed=5, end="2026-04-30")


def _meta(version="inf-v1", train_end="2025-12-30", **kw):
    m = {"format_version": 1, "model_version": version, "created_at": "2026-10-12T20:00:00+09:00",
         "target": "next_1d_excess", "as_of_mode": "historical_assumed",
         "periods": {"train": {"start": "2021-01-04", "end": train_end},
                     "valid": {"start": "2026-01-02", "end": "2026-02-27"}, "test": {"start": "2026-03-02", "end": "2026-04-30"}},
         "baseline": {"name": "price_only", "metric": "hit_rate", "value": 0.51, "model_value": 0.52, "higher_is_better": True},
         "min_eval_samples": 30, "inputs": {"docs": "fp", "prices": "fp"}}
    m.update(kw)
    return m


WEIGHTS = pd.DataFrame([("report_type", "자기주식취득결정", 0.2, "+", 400, 80, 0.55, 0.05, 0.35, True),
                        ("report_type", "임상시험계획승인", 0.9, "+", 20, 5, None, None, None, False)],
                       columns=["group_type", "group", "weight", "direction", "n_events", "n_eval", "eval_metric",
                                "ci_low", "ci_high", "sample_ok"])


def _docs():
    seen = datetime(2026, 3, 10, 8, 30, tzinfo=KST)
    return [make_doc(1, "합성회사", "자기주식취득결정", CODE, seen, status="active", time_precision="minute"),
            make_doc(2, "합성회사", "자기주식취득결정", CODE, seen + timedelta(days=30), status="active",
                     time_precision="minute")]  # 4/9 — AS_OF 뒤


def _events(docs):
    return pd.DataFrame([{"doc_id": d["doc_id"], "version": 1, "ticker": CODE, "available_at": d["available_at"],
                          "linked_bar_start": None, "group": "자기주식취득결정", "contribution": 0.01, "period": "test"}
                         for d in docs])


def _coverage(ext: Path, days, state="collected"):
    rows = [{"seq": i, "source": "opendart", "day": d, "corp_cls": c, "state": state, "run_id": "r",
             "observed_at": "2026-03-01T00:00:00+09:00"} for i, (d, c) in enumerate((d, c) for d in days for c in "YK")]
    (ext / "coverage" / "source=opendart").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(ext / "coverage" / "source=opendart" / "coverage_log.parquet", index=False)


def _app(tmp_path, market, with_model=True, cov_state="collected", meta=None):
    ext = tmp_path / "ext"
    docs = [{**d, "status": "active"} for d in _docs()]
    write_store(ext, docs)
    _coverage(ext, ["2026-03-09", "2026-03-10"], cov_state)
    paths = Paths(tmp_path / "store", tmp_path / "dump.sql", tmp_path / "cache", tmp_path / "runs", ext)
    if with_model:
        InfluenceStore(paths.runs).write_model((meta or _meta())["model_version"], meta or _meta(), WEIGHTS, _events(docs))
    ei = retrieval.open_index(DocsStore(ext, paths.cache), load_config(), use_cache=False)
    return create_app(Settings(paths=paths), prep=market, rag=ei, serve_web=False)


@pytest.fixture()
def client(tmp_path, market):
    with TestClient(_app(tmp_path, market)) as c:
        yield c


def test_no_results_is_no_model_everywhere(tmp_path, market):
    with TestClient(_app(tmp_path, market, with_model=False)) as c:
        for path, extra in (("/api/influence/model", {}), ("/api/influence/weights", {}),
                            ("/api/influence/events", {"ticker": CODE})):
            r = c.get(path, params={"as_of": AS_OF, **extra})
            assert r.status_code == 200 and r.json()["status"] == "no_model", path
            assert r.json()["model"] is None and r.json()["notice"] and r.json()["disclaimer"]


def test_model_and_weights(client):
    m = client.get("/api/influence/model", params={"as_of": AS_OF}).json()
    assert m["status"] == "ok" and m["model"]["model_version"] == "inf-v1" and m["model"]["eval_beyond_as_of"] is True
    assert {"target", "as_of_mode", "periods", "baseline", "min_eval_samples"} <= m["model"].keys()
    w = client.get("/api/influence/weights", params={"as_of": AS_OF}).json()
    assert [(r["group"], r["direction_shown"], r["sample_ok"]) for r in w["rows"]] == [
        ("자기주식취득결정", "+", True), ("임상시험계획승인", "hidden", False)]
    assert w["baseline"]["improved"] is True and w["rows"][1]["eval_metric"] is None
    # 학습 끝(2025-12-30) 당일·이전 시각에는 모델이 없다
    assert client.get("/api/influence/model", params={"as_of": "2025-12-30T23:00:00+09:00"}).json()["status"] == "no_model"


def test_events_only_visible_at_as_of_with_prices_cut(client):
    r = client.get("/api/influence/events", params={"ticker": CODE, "from": "2026-03-02", "as_of": AS_OF})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok" and body["mode"] == "historical_assumed"
    assert [e["doc_id"] for e in body["events"]] == ["opendart:20261007000001"]  # 4/9 자료는 아직 없음
    e = body["events"][0]
    assert e["linked_bar_start"] == "2026-03-10T09:00:00+09:00" and e["available_at"] == "2026-03-10T08:30:00+09:00"
    assert e["title"] == "자기주식취득결정" and e["period"] == "test" and e["close"] is not None
    assert body["bars"][0]["date"] == "2026-03-02" and body["bars"][-1]["date"] == body["last_bar"] == "2026-03-31"


def test_no_events_vs_coverage_gap(tmp_path, market):
    q = {"ticker": CODE, "from": "2026-03-09", "to": "2026-03-09", "as_of": AS_OF}
    with TestClient(_app(tmp_path / "a", market)) as c:
        assert c.get("/api/influence/events", params=q).json()["status"] == "no_events"
    with TestClient(_app(tmp_path / "b", market, cov_state="gap")) as c:
        body = c.get("/api/influence/events", params=q).json()
        assert body["status"] == "coverage_gap" and body["coverage_gaps"]["opendart"]["gap_days"] == ["2026-03-09"]


def test_errors(client, tmp_path, market):
    for params, field in [({"as_of": "2026-03-31T16:00:00"}, "as_of"), ({"as_of": AS_OF, "ticker": "abc"}, "ticker"),
                          ({"as_of": AS_OF, "ticker": CODE, "from": "2026-04-01", "to": "2026-03-01"}, "to"),
                          ({"as_of": AS_OF, "ticker": CODE, "mode": "x"}, "mode")]:
        r = client.get("/api/influence/events", params=params)
        assert r.status_code == 422 and set(r.json()) == ERROR_KEYS and field in r.json()["detail"]["fields"]
    r = client.get("/api/influence/model", params={"as_of": ""})
    assert r.status_code == 422 and r.json()["code"] == "validation_failed"
    r = client.get("/api/influence/events", params={"as_of": AS_OF, "ticker": "999999"})
    assert r.status_code == 404 and r.json()["code"] == "ticker_not_found"
    with TestClient(_app(tmp_path / "bad", market, meta=_meta(format_version=9))) as c:
        r = c.get("/api/influence/weights", params={"as_of": AS_OF})
        assert r.status_code == 503 and r.json()["code"] == "data_unavailable" and set(r.json()) == ERROR_KEYS


def test_login_required(auth_app):
    with TestClient(auth_app) as anon:
        r = anon.get("/api/influence/model", params={"as_of": AS_OF})
        assert r.status_code == 401 and r.json()["code"] == "authentication_required"
    with TestClient(auth_app) as a:
        signin(a, "a")
        assert a.get("/api/influence/model", params={"as_of": AS_OF}).json()["status"] == "no_model"
