"""P1-6 역방향 탐색 실행·진행·취소·탐색 기록 (설계 §4.2·§4.3)."""

import json
import re

import pandas as pd
import pytest
from synth import make_market

import regime_lab.search as search
from regime_lab.config import Paths
from regime_lab.context import SEARCH_STAGES, RecordingContext
from regime_lab.search import SearchRequest, run_and_save_search, run_search


@pytest.fixture(scope="module")
def market(cfg):
    return make_market(cfg, n_tickers=40, seed=2)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "input_file_hashes", lambda store: {"prices.parquet": "synthetic"})
    return Paths(store=tmp_path / "store", sql_dump=tmp_path / "dump.sql", cache=tmp_path / "cache",
                 runs=tmp_path / "runs")


@pytest.fixture
def req(cfg):
    return SearchRequest.from_dict({"name": "p16", "target_win_rate": 0.45, "min_trades": 30,
                                    "axes": {"patterns": ["breakout_20d", "rsi_rebound"],
                                             "max_hold_days": [5, 20]}}, cfg)


class CancelAfter(RecordingContext):
    """explore 단계에서 n 개 후보를 처리한 뒤 취소를 요청한다."""

    def __init__(self, n):
        super().__init__(stage_names=SEARCH_STAGES)
        self.n = n

    def progress(self, done, total):
        super().progress(done, total)
        if self.current == "explore" and done >= self.n:
            self.cancel()


def _read(d):
    return (json.loads((d / "search.json").read_text(encoding="utf-8")),
            json.loads((d / "meta.json").read_text(encoding="utf-8")),
            pd.read_parquet(d / "candidates.parquet"))


def test_completed_search_record(market, req, cfg, paths):
    ctx = RecordingContext(stage_names=SEARCH_STAGES)
    d = run_and_save_search(req, market, cfg, paths, ctx=ctx)
    assert d.parent == paths.runs / "searches"
    assert re.fullmatch(r"\d{8}T\d{6}_search_p16_[0-9a-f]{8}", d.name)
    assert not list(d.parent.glob(".tmp_*"))

    sj, meta, cand = _read(d)
    assert [x["stage"] for x in meta["stage_log"]] == SEARCH_STAGES
    snap = ctx.snapshot()
    assert snap["stages"] == SEARCH_STAGES and "met_so_far" in snap

    assert sj["status"] == meta["status"] == "completed"
    assert sj["fdr_family_size"] == sj["counts"]["candidates"] == sj["counts"]["processed"] == len(cand) == 8
    assert sj["request"] == req.to_dict() and sj["method"] == "exhaustive"
    assert sj["split"]["explore"]["end"] == "2024-02-29"
    assert sj["note"] and sj["disclaimer"].startswith("Historical analysis")
    assert [r["order"] for r in sj["rows"]] == list(range(1, 9))
    for r in sj["rows"]:
        assert r["strategy"]["name"] == r["id"]
        assert (r["evaluate"] is None) == (r["status"] in ("not_met", "insufficient_trades"))
    assert sj["counts"]["both"] + sj["counts"]["explore_only"] >= 1
    assert meta["data_version"] and meta["config"]["search"]["max_candidates"] == 200


def test_cancel_during_explore_keeps_partial_record(market, req, cfg, paths):
    d = run_and_save_search(req, market, cfg, paths, ctx=CancelAfter(3))
    sj, meta, cand = _read(d)
    assert sj["status"] == meta["status"] == "cancelled"
    assert sj["counts"]["processed"] == len(cand) == len(sj["rows"]) == 3
    assert sj["fdr_family_size"] == sj["counts"]["candidates"] == 8  # 취소해도 m 은 시도하려던 전체 후보 수
    assert all(r["evaluate"] is None for r in sj["rows"])
    assert "save" not in [x["stage"] for x in meta["stage_log"]]


def test_cancel_before_first_candidate(market, req, cfg, paths):
    d = run_and_save_search(req, market, cfg, paths, ctx=CancelAfter(0))
    sj, meta, cand = _read(d)
    assert sj["status"] == "cancelled" and sj["counts"]["processed"] == len(cand) == 0 and sj["rows"] == []
    assert sj["fdr_family_size"] == 8 and sj["closest"] is None


def test_cancel_before_evaluate_marks_not_evaluated(market, req, cfg):
    out = run_search(req, market, cfg, RecordingContext(cancel_on_stage="evaluate", stage_names=SEARCH_STAGES))
    t = out["table"]
    assert out["status"] == "cancelled" and len(t) == 8
    met = t["explore_met"]
    assert met.any() and (t.loc[met, "status"] == "not_evaluated").all()
    assert t["evaluate_trades"].isna().all()


def test_same_input_same_record(market, req, cfg, paths):
    a = run_and_save_search(req, market, cfg, paths, search_id="a")
    b = run_and_save_search(req, market, cfg, paths, search_id="b")
    sa, _, ca = _read(a)
    sb, _, cb = _read(b)
    pd.testing.assert_frame_equal(ca, cb)
    assert {**sa, "search_id": None} == {**sb, "search_id": None}


def test_failure_leaves_no_record(market, req, cfg, paths, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(search, "run_search", boom)
    with pytest.raises(RuntimeError):
        run_and_save_search(req, market, cfg, paths, search_id="x")
    root = paths.runs / "searches"
    assert not (root / "x").exists() and not list(root.glob(".tmp_*"))


def test_existing_search_id_is_rejected(market, req, cfg, paths):
    run_and_save_search(req, market, cfg, paths, search_id="dup")
    with pytest.raises(FileExistsError):
        run_and_save_search(req, market, cfg, paths, search_id="dup")


def test_record_includes_warmup_file_fingerprint(market, req, cfg, paths):
    """NFR-10: 워밍업 파일(cache/warmup/*.parquet, X5 과거 지수 포함)의 유무·변경이 기록과 data_version 에 남는다."""
    import os

    d0 = run_and_save_search(req, market, cfg, paths, search_id="w0")
    m0 = json.loads((d0 / "meta.json").read_text(encoding="utf-8"))
    assert m0["input_files"]["cache/warmup"] == "n=0"

    w = paths.cache / "warmup"
    w.mkdir(parents=True)
    (w / "index_warmup.parquet").write_bytes(b"x" * 10)
    d1 = run_and_save_search(req, market, cfg, paths, search_id="w1")
    m1 = json.loads((d1 / "meta.json").read_text(encoding="utf-8"))
    assert "cache/warmup/index_warmup.parquet" in m1["input_files"] and "cache/warmup" not in m1["input_files"]
    assert m1["data_version"] != m0["data_version"]

    (w / "index_warmup.parquet").write_bytes(b"x" * 11)
    os.utime(w / "index_warmup.parquet", (1, 1))
    d2 = run_and_save_search(req, market, cfg, paths, search_id="w2")
    m2 = json.loads((d2 / "meta.json").read_text(encoding="utf-8"))
    assert m2["data_version"] != m1["data_version"]
