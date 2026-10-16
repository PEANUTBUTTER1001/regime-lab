"""새 예측/영향력 계약은 저장 결과로 확인. 원본·외부 키·실서버 불필요."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from regime_lab.data.influence_store import InfluenceStore
from test_forecast_contract import RUN, client, market  # noqa: F401 — 기존 형식2 fixture도 유지
from test_influence_contract import WEIGHTS, _events, _meta


def _upgrade(client):
    state = client.app.state.rl
    store = state.forecasts
    meta, pred, scores = store.read_run(RUN)
    meta.update(format_version=5, fusion_kind="weighted", as_of_mode="observed", comparison_runs={"price": RUN},
                influence_versions={"model": "inf-test", "snapshots": {}}, feature_policy={"windows": [1, 5, 20]})
    pred["bar_ordinal"] = pred.groupby("code")["as_of"].rank(method="dense").astype(int) - 1
    pred["docs_missing"], pred["influence_missing"], pred["doc_count"] = False, False, 1
    ev = pd.DataFrame([{"doc_id": "opendart:synthetic", "version": 1, "code": "005930", "source": "opendart",
                        "group": "earnings", "linked_as_of": pd.Timestamp("2026-09-01 10:00"), "linked_ordinal": 0,
                        "until": pd.Timestamp.max, "available_at": "2026-09-01T08:00:00+09:00"}])
    rid = "fusion-synthetic"
    meta["run_id"] = rid
    store.write_run(rid, meta, pred, scores, evidence=ev)
    state.rag = SimpleNamespace(docs=[{"doc_id": "opendart:synthetic", "version": 1, "title": "합성 실적 공시",
                                       "available_at": "2026-09-01T08:00:00+09:00", "first_seen_at": "2026-09-01T08:00:00+09:00",
                                       "license_scope": "metadata_only", "url": "https://dart.example/synthetic"}])
    return rid


def test_fused_run_metadata_and_paginated_references(client):
    run = _upgrade(client)
    r = client.get(f"/api/forecasts/runs/{run}/stocks/005930")
    assert r.status_code == 200 and r.json()["fusion_kind"] == "weighted"
    assert r.json()["documents"]["count"] == 1
    res = client.get(f"/api/forecasts/runs/{run}/evidence", params={"code": "005930", "page_size": 1})
    assert res.status_code == 200 and res.json()["total"] == 1 and res.json()["disclaimer"]
    assert res.json()["items"][0]["version"] == 1
    assert client.get(f"/api/forecasts/runs/{run}/evidence", params={"code": "005930", "page": 0}).status_code == 422
    assert client.get(f"/api/forecasts/runs/{run}/evidence", params={"code": "005930", "page_size": 201}).status_code == 422
    client.app.state.rl.rag.docs[0]["status"] = "deleted"
    assert client.get(f"/api/forecasts/runs/{run}/evidence", params={"code": "005930"}).json()["items"][0]["title"] is None
    client.app.state.rl.rag.docs[0]["status"] = "active"
    client.app.state.rl.rag.manifests = {"opendart": {"active": False}}
    assert client.get(f"/api/forecasts/runs/{run}/evidence", params={"code": "005930"}).json()["items"][0]["status"] == "restricted"


def test_old_price_run_has_no_fabricated_evidence(client):
    _upgrade(client)
    response = client.get(f"/api/forecasts/runs/{RUN}/evidence", params={"code": "005930"})
    assert response.status_code == 200 and response.json()["status"] == "no_evidence"


def test_missing_document_features_do_not_claim_documents_were_used(client):
    run = _upgrade(client)
    store = client.app.state.rl.forecasts
    meta, pred, scores = store.read_run(run)
    pred["docs_missing"] = True
    rid = "missing-doc-input"
    store.write_run(rid, {**meta, "run_id": rid}, pred, scores, evidence=store.read_evidence(run, "005930"))
    res = client.get(f"/api/forecasts/runs/{rid}/evidence", params={"code": "005930"})
    assert res.status_code == 200 and res.json()["status"] == "docs_missing" and res.json()["total"] == 0


def test_influence_v2_horizon_and_knowledge_cutoff(client):
    state = client.app.state.rl
    meta = _meta("inf-v2", format_version=2, knowledge_cutoff="2026-03-02T00:00:00+09:00",
                 metrics={"1": {"rmse": .02, "baseline_rmse": .03}, "2": {"rmse": .04, "baseline_rmse": .05}})
    w = pd.concat([WEIGHTS.assign(h=h, window=5, feature="f_report_type_other_5") for h in (1, 2)], ignore_index=True)
    state.influence.write_model("inf-v2", meta, w, _events([]))
    for resource in ("model", "weights"):
        early = client.get(f"/api/influence/{resource}", params={"as_of": "2026-02-01T10:00:00+09:00"})
        assert early.json()["status"] == "no_model"
    res = client.get("/api/influence/weights", params={"as_of": "2026-04-01T10:00:00+09:00", "h": 2})
    assert res.status_code == 200 and all(r["h"] == 2 for r in res.json()["rows"])
    assert res.json()["baseline"]["model"] == .04
    assert client.get("/api/influence/weights", params={"h": 0}).status_code == 422


def test_legacy_single_horizon_weights_are_not_relabelled_as_h5(client):
    client.app.state.rl.influence.write_model("legacy-one-day", _meta("legacy-one-day"), WEIGHTS, _events([]))
    res = client.get("/api/influence/weights", params={"as_of": "2026-04-01T10:00:00+09:00", "h": 5})
    assert res.status_code == 200 and res.json()["status"] == "no_model"
