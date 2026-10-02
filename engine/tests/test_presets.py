"""P1-9 찾은 조합 저장: 저장 형식·저장/불러오기/수정/삭제·값 스냅샷·탐색 기록에서 저장."""

import copy
import json

import pytest
from synth import make_market

import regime_lab.search as search
from regime_lab.config import Paths
from regime_lab.presets import PresetError, PresetStore, presets_dir
from regime_lab.runs import Strategy, execute
from regime_lab.search import SearchRequest, run_and_save_search, searches_dir

S = {"name": "draft", "patterns": ["rsi_rebound", "bb_lower_recover"], "combine": "or",
     "exit": {"stop_loss_pct": -5, "take_profit_pct": None, "max_hold_days": 10}, "markets": ["KOSDAQ"]}


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        self.t += 1
        return f"2026-10-02T09:00:{self.t:02d}"


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "input_file_hashes", lambda store: {"prices.parquet": "synthetic"})
    return Paths(store=tmp_path / "store", sql_dump=tmp_path / "d.sql", cache=tmp_path / "cache", runs=tmp_path / "runs")


@pytest.fixture
def store(paths, cfg):
    return PresetStore(presets_dir(paths), cfg, clock=Clock())


def test_create_snapshots_effective_values(store, cfg, paths):
    p = store.create("  RSI 반등 조합  ", S)
    assert p["name"] == "RSI 반등 조합" and p["revision"] == 1 and p["source"] == {"kind": "manual"}
    assert p["id"].startswith("p_") and p["strategy"]["name"] == p["id"]
    eff = p["strategy"]
    assert eff["exit"] == {**cfg["exit"], "stop_loss_pct": -5, "take_profit_pct": None, "max_hold_days": 10}
    assert eff["min_avg_value_krw"] == 500_000_000 and eff["cap_groups"] == ["large", "mid", "small"]
    assert eff["period"] == {"start": "2020-09-01", "end": "2026-09-18"}
    on_disk = json.loads((presets_dir(paths) / f"{p['id']}.json").read_text(encoding="utf-8"))
    assert on_disk == p and on_disk["format"] == 1
    Strategy.from_dict(eff).validate(cfg)  # 저장한 값은 그대로 다시 실행할 수 있다


def test_snapshot_ignores_later_default_changes(store, cfg, paths):
    p = store.create("고정", {"name": "x", "patterns": ["breakout_20d"]})
    cfg2 = copy.deepcopy(cfg)
    cfg2["exit"]["max_hold_days"] = 40
    assert PresetStore(presets_dir(paths), cfg2).get(p["id"])["strategy"]["exit"]["max_hold_days"] == 20


def test_list_get_update_delete(store):
    a = store.create("A", S)
    b = store.create("B", S)
    assert [x["id"] for x in store.list()] == [b["id"], a["id"]]  # 최근 수정 순
    a2 = store.update(a["id"], revision=1, name="A 새 이름")
    assert a2["revision"] == 2 and a2["name"] == "A 새 이름" and a2["strategy"] == a["strategy"]
    a3 = store.update(a["id"], revision=2, strategy={**S, "combine": "and"})
    assert a3["strategy"]["combine"] == "and" and a3["created_at"] == a["created_at"]
    assert store.list()[0]["id"] == a["id"]
    store.delete(b["id"])
    assert [x["id"] for x in store.list()] == [a["id"]]
    with pytest.raises(PresetError) as e:
        store.get(b["id"])
    assert e.value.code == "preset_not_found"


def test_name_rules(store):
    store.create("Momentum", S)
    for bad in ("", "   ", "x" * 61, "a\nb", 3):
        with pytest.raises(PresetError) as e:
            store.create(bad, S)
        assert e.value.code == "validation_failed" and "name" in e.value.detail["fields"]
    with pytest.raises(PresetError) as e:
        store.create(" momentum ", S)  # 대소문자·공백만 다른 이름도 중복
    assert e.value.code == "name_conflict"
    other = store.create("Other", S)
    with pytest.raises(PresetError) as e:
        store.update(other["id"], revision=1, name="MOMENTUM")
    assert e.value.code == "name_conflict"
    assert store.update(other["id"], revision=1, name="Other")["name"] == "Other"  # 자기 이름 유지는 허용


def test_invalid_strategy_rejected_with_field_paths(store):
    with pytest.raises(PresetError) as e:
        store.create("bad", {**S, "exit": {"max_hold_days": 999}})
    assert e.value.code == "validation_failed" and "strategy.exit.max_hold_days" in e.value.detail["fields"]
    with pytest.raises(PresetError) as e:
        store.create("bad2", {**S, "patterns": ["nope"]})
    assert "strategy.patterns" in e.value.detail["fields"]
    assert store.list() == []


def test_version_conflict(store):
    p = store.create("A", S)
    store.update(p["id"], revision=1, name="A1")
    with pytest.raises(PresetError) as e:
        store.update(p["id"], revision=1, name="A2")  # 다른 창에서 먼저 고친 뒤 옛 revision 으로 저장
    assert e.value.code == "version_conflict" and e.value.detail == {"revision": 2}


@pytest.mark.parametrize("pid", ["../x", "p_../../etc", "p_ZZZZZZZZZZZZ", "q_0123456789ab", ""])
def test_bad_ids_are_not_found(store, pid):
    with pytest.raises(PresetError) as e:
        store.get(pid)
    assert e.value.code == "preset_not_found"


def test_no_partial_files_left(store, paths):
    store.create("A", S)
    assert not list(presets_dir(paths).glob("*.tmp"))


# ---------------------------------------------------------------- 탐색 기록에서 저장
@pytest.fixture(scope="module")
def market(cfg):
    return make_market(cfg, n_tickers=40, seed=2)


def test_save_from_search_record(store, market, cfg, paths):
    req = SearchRequest.from_dict({"name": "src", "target_win_rate": 0.45, "min_trades": 30,
                                   "axes": {"patterns": ["breakout_20d", "rsi_rebound"]},
                                   "filters": {"markets": ["KOSPI"]}}, cfg)
    d = run_and_save_search(req, market, cfg, paths, search_id="s1")
    row = json.loads((d / "search.json").read_text(encoding="utf-8"))["rows"][0]
    p = store.create_from_search(searches_dir(paths), "s1", row["id"], "탐색에서 찾은 조합")
    assert p["source"] == {"kind": "search", "search_id": "s1", "candidate_id": row["id"], "status": row["status"]}
    st = p["strategy"]
    assert st["patterns"] == row["strategy"]["patterns"]
    assert st["exit"] == {**cfg["exit"], **row["strategy"]["exit"]}  # 기본값을 채운 스냅샷
    assert st["markets"] == ["KOSPI"]
    assert st["period"] == {"start": "2020-09-01", "end": "2026-09-18"}  # 탐색 구간이 아니라 전체 기간
    # 저장한 조합을 정방향으로 다시 돌릴 수 있고, 저장 → 다시 읽기 → 실행 값이 같다
    again = PresetStore(presets_dir(paths), cfg).get(p["id"])
    r = execute(Strategy.from_dict(again["strategy"]), market, cfg)
    assert r["strategies"][0].effective(cfg) == st

    for sid, cid, code in (("nope", row["id"], "search_not_found"), ("../s1", row["id"], "search_not_found"),
                           ("s1", "c999", "candidate_not_found")):
        with pytest.raises(PresetError) as e:
            store.create_from_search(searches_dir(paths), sid, cid, f"x-{sid}-{cid}")
        assert e.value.code == code
