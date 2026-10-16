"""실제 자료/학습/저장/예측 세 모델의 통합·재현·근거·형식 호환 검사."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from regime_lab.data.forecast_store import ForecastStore
from regime_lab.data.influence_store import InfluenceStore
from regime_lab.forecast_runs import evidence_view, run_fusion, stock_forecast
from regime_lab.fusion import fingerprint
from regime_lab.influence_runs import run_training, weights_view
from test_forecast_runs import MemoryStore, _krx
from test_fusion_documents import fixture_inputs


class DocsFixture:
    def __init__(self, docs, coverage):
        self.docs, self.rows = docs, coverage

    def manifests(self):
        return {"opendart": {"schema_version": 1, "license_scope": "metadata_only"}}

    def files(self):
        return []

    def read(self, files):
        return self.docs

    def coverage(self):
        return self.rows

    def fingerprint(self, files):
        return fingerprint([self.docs, self.rows])


class PricesFixture(MemoryStore):
    def __init__(self, tmp, raw):
        super().__init__(krx=raw)
        self.disk = ForecastStore(tmp, None, tmp)

    def write_run(self, *args, **kwargs):
        return self.disk.write_run(*args, **kwargs)


@pytest.fixture(scope="module")
def workflow(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fusion")
    c, _, docs, cov = fixture_inputs()
    c["forecast"]["model"].update(max_iter=5, min_samples_leaf=3)
    c["forecast"]["horizon"]["1d"] = [5, 20]
    c["forecast"]["fusion_compare"].update(min_samples=10, min_days=2, bootstrap=10)
    prices = PricesFixture(tmp, _krx(codes=("005930", "000660"), days=90, start="2026-01-01"))
    ds, ins = DocsFixture(docs, cov), InfluenceStore(tmp)
    now = datetime(2026, 10, 16, 17)
    version, model = run_training(prices, ds, ins, c, now)
    run, meta = run_fusion(prices, ds, ins, c, now, with_influence=True)
    return c, prices, ds, ins, version, model, run, meta, tmp


def test_three_models_same_samples_and_family(workflow):
    c, prices, ds, ins, version, model, run, meta, tmp = workflow
    assert meta["format_version"] == 5 and meta["fusion_kind"] == "weighted"
    assert meta["influence_versions"]["model"] == version
    assert set(meta["comparison_runs"]) == {"price", "docs"}
    assert len(prices.disk.list_runs()) == 3
    lengths = []
    for rid in [run, *meta["comparison_runs"].values()]:
        mm, pred, score = prices.disk.read_run(rid)
        lengths.append(len(pred))
        assert mm["evaluation_mode"] == "retrospective"
    assert len(set(lengths)) == 1
    for h, score in prices.disk.read_scores(run).items():
        assert set(score["comparisons"]) == {"docs_price", "weighted_price", "weighted_docs"}
        assert len({r["n"] for r in score["comparisons"].values()}) == 1
        assert score["weighted_verdict"] in ("improved", "not_improved", "insufficient")


def test_evidence_exact_versions_pagination_and_revocation(workflow):
    c, prices, ds, ins, version, model, run, meta, tmp = workflow
    stock = stock_forecast(prices.disk, c, run, "005930")
    assert stock["status"] == "ok" and stock["documents"]["count"] == 5
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    ev = evidence_view(prices.disk, c, run, "005930", None, ds.docs, now, page_size=1)
    assert ev["status"] == "ok" and ev["total"] == 5 and len(ev["items"]) == 1
    first = ev["items"][0]
    revoked = [dict(d, status="deleted") if d["doc_id"] == first["doc_id"] else d for d in ds.docs]
    hidden = evidence_view(prices.disk, c, run, "005930", None, revoked, now, page_size=1)
    assert hidden["items"][0]["title"] is None and hidden["items"][0]["status"] == "restricted"
    # 예측 자체는 근거 표시 정책 변경으로 바뀌지 않는다.
    assert stock_forecast(prices.disk, c, run, "005930") == stock
    removed = [d for d in ds.docs if d["doc_id"] != first["doc_id"]]
    assert evidence_view(prices.disk, c, run, "005930", None, removed, now, page_size=1)["items"][0]["status"] == "missing"
    expired = [dict(d, expires_at="2026-06-01T00:00:00+09:00") for d in ds.docs]
    assert evidence_view(prices.disk, c, run, "005930", None, expired, now)["items"][0]["status"] == "restricted"
    disabled = {"opendart": {"active": False}}
    assert evidence_view(prices.disk, c, run, "005930", None, ds.docs, now, manifests=disabled)["items"][0]["title"] is None


def test_influence_v2_horizon_and_cutoff_and_reuse(workflow):
    c, prices, ds, ins, version, model, run, meta, tmp = workflow
    assert weights_view(ins, "2026-02-20T15:31:00+09:00")["status"] == "no_model"
    rows = weights_view(ins, "2026-03-02T15:31:00+09:00", 2)["rows"]
    assert rows and all(r["h"] == 2 for r in rows)
    c2 = {**c, "fusion": {**c["fusion"], "classification_version": 2}}
    with pytest.raises(ValueError, match="同|동일"):
        run_fusion(prices, ds, ins, c2, datetime(2026, 10, 16, 18), with_influence=True)
    changed = DocsFixture(ds.docs, [*ds.rows, {"source": "opendart", "day": "2026-12-01", "corp_cls": "Y",
                                             "state": "gap", "seq": 99999, "observed_at": "2026-12-01T00:00:00+09:00"}])
    with pytest.raises(ValueError, match="동일"):
        run_fusion(prices, changed, ins, c, datetime(2026, 10, 16, 18), with_influence=True)


def test_changing_input_during_read_does_not_publish_training(workflow):
    from regime_lab.influence_runs import training_inputs

    c, prices, ds, ins, version, model, run, meta, tmp = workflow

    class RacingDocs(DocsFixture):
        calls = 0

        def fingerprint(self, files):
            self.calls += 1
            return str(self.calls)

    with pytest.raises(ValueError, match="도중 변경"):
        training_inputs(prices, RacingDocs(ds.docs, ds.rows), c)


def test_no_training_artifact_is_no_silent_fallback(workflow):
    c, prices, ds, ins, version, model, run, meta, tmp = workflow
    with pytest.raises(ValueError, match="영향력 모델 없음"):
        run_fusion(prices, ds, InfluenceStore(tmp / "empty"), c, datetime(2026, 10, 16, 19), with_influence=True)


def test_cli_rejects_weight_option_without_docs_and_executes_full_flow(workflow, monkeypatch, capsys):
    from types import SimpleNamespace
    from regime_lab import cli

    c, prices, ds, ins, version, model, run, meta, tmp = workflow
    monkeypatch.setattr(cli, "load_config", lambda: c)
    monkeypatch.setattr(cli, "load_paths", lambda: SimpleNamespace(store=tmp, runs=tmp, cache=tmp, ext_store=tmp))
    monkeypatch.setattr(cli, "ForecastStore", lambda *a: prices)
    monkeypatch.setattr(cli, "DocsStore", lambda *a: ds)
    monkeypatch.setattr(cli, "InfluenceStore", lambda *a: ins)
    before = len(prices.disk.list_runs())
    assert cli.main(["forecast-run", "--timeframe", "1d", "--with-influence"]) == 2
    assert len(prices.disk.list_runs()) == before
    assert "--with-docs" in capsys.readouterr().err
    assert cli.main(["influence-run", "--as-of-mode", "observed"]) == 0
    assert "model_version" in capsys.readouterr().out
    assert cli.main(["forecast-run", "--timeframe", "1d", "--with-docs", "--with-influence"]) == 0
    assert len(prices.disk.list_runs()) == before + 3
