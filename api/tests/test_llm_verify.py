"""test_llm_verify (FR-L2~L4, NFR-7): 입력에 없는 숫자·권유 표현 거부, 템플릿 폴백, 캐시."""

import copy
import json
import re
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regime_api.llm.prompt import build_facts
from regime_api.llm.provider import LLMError, get_provider
from regime_api.llm.template import render
from regime_api.llm.verify import verify
from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths
from regime_lab.pipeline import Prepared


class FakeProvider:
    name, model = "openai", "fake-model"

    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, 0

    def generate(self, system, user):
        self.calls += 1
        if self.error:
            raise LLMError(self.error)
        return self.text


@pytest.fixture()
def done(tmp_path):
    """저장 결과에서 보고하는 계약을 원본·API 키 없이 검증한다."""
    run_id = "synthetic_report"
    result = {
        "data_as_of": "2026-09-18", "fdr_family_size": 1, "min_cell_trades": 300,
        "strategies": [{
            "strategy": "bo",
            "strategy_input": {"patterns": ["breakout_20d"], "combine": "or", "markets": ["KOSDAQ"]},
            "summary": {"trades": 1200, "win_rate": .55, "mean_ret": .0234, "median_ret": .011,
                        "mean_excess": .0042, "payoff_ratio": 1.8, "sharpe": .512, "mdd": -.12,
                        "excluded_trades": 13, "skipped_entries": 19,
                        "period_start": "2021-07-21", "period_end": "2026-09-18"},
            "validation": {"p_value": .023, "fdr_pass": True, "split_judgement": "maintained",
                           "h1_trades": 610, "h2_trades": 590, "h1_mean_excess": .003,
                           "h2_mean_excess": .006, "random_percentile": .976,
                           "random_pass": True, "analysis_target": True},
            "split": {"split_date": "2024-02-29"},
            "cells_market": [{"regime": "bull", "market": "KOSDAQ", "cap_group": "mid",
                              "trades": 415, "mean_excess": -.0333, "sample_insufficient": False}],
        }],
    }
    paths = Paths(tmp_path / "absent_store", tmp_path / "absent.sql", tmp_path / "cache", tmp_path / "runs")
    prepared = Prepared(pd.DataFrame(), pd.DataFrame(), set(), pd.DataFrame(), {})
    with TestClient(create_app(Settings(paths=paths), prep=prepared, serve_web=False)) as client:
        d = paths.runs / run_id
        d.mkdir()
        (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
        yield client, run_id, result, build_facts(result, "bo")


def _use(client, provider):
    client.app.state.rl.reports._provider_factory = lambda s: (provider, None)


def good_text(f):
    m, v = f["metrics"], f["validation"]
    return (f"## 요약\n거래 {m['trades']:,}건의 평균 수익률은 {m['mean_ret_pct']}%, 승률은 {m['win_rate_pct']}%입니다.\n"
            f"## 검증 결과\np-value는 {v['p_value']}이며 3중 검증 결과 분석 대상 여부는 {v['analysis_target']}입니다.\n"
            f"## 한계\n셀 {f['cells']['insufficient_cells']}개는 {f['cells']['min_cell_trades']}건 미만입니다. "
            f"데이터 기준일은 {f['data_as_of']}이며 투자 권유가 아닙니다.")


def test_template_always_passes_verification(done):
    _, _, _, facts = done
    assert verify(render(facts), facts).ok


def test_verify_rejects_invented_number(done):
    _, _, _, facts = done
    assert verify(good_text(facts), facts).ok
    bad = good_text(facts) + " 연 환산 수익률은 37.8%로 추정됩니다."
    v = verify(bad, facts)
    assert not v.ok and "37.8" in v.unknown_numbers


@pytest.mark.parametrize("phrase", ["지금 매수하세요.", "이 종목을 추천 종목으로 봅니다.", "목표가는 산출하지 않았습니다.",
                                    "투자 비중을 늘리는 것이 좋습니다.", "유망한 전략입니다.", "This is a strong signal to buy."])
def test_verify_rejects_advice(done, phrase):
    _, _, _, facts = done
    v = verify(good_text(facts) + " " + phrase, facts)
    assert not v.ok and v.forbidden


def test_negated_disclaimer_is_allowed(done):
    _, _, _, facts = done
    assert verify(good_text(facts) + " 종목 추천이 아닙니다.", facts).ok


def test_verified_report_and_cache(done):
    client, run_id, _, facts = done
    fake = FakeProvider(good_text(facts))
    _use(client, fake)
    r1 = client.post(f"/api/runs/{run_id}/report").json()
    assert r1["status"] == "verified" and r1["text"] == good_text(facts) and not r1["cached"]
    r2 = client.post(f"/api/runs/{run_id}/report").json()
    assert r2["cached"] and fake.calls == 1


def test_rejected_output_is_not_shown(done):
    client, run_id, _, facts = done
    _use(client, FakeProvider(good_text(facts) + " 목표 수익률은 99.9%입니다. 매수하세요."))
    r = client.post(f"/api/runs/{run_id}/report").json()
    assert r["status"] == "fallback" and r["reason"] == "verification_failed"
    assert "99.9" not in r["text"] and r["text"] == render(facts) and r["warnings"]


def test_llm_error_falls_back(done):
    client, run_id, _, facts = done
    _use(client, FakeProvider(error="timeout"))
    r = client.post(f"/api/runs/{run_id}/report").json()
    assert r["status"] == "fallback" and r["reason"] == "llm_error" and r["text"] == render(facts)


def test_no_key_or_model_uses_template(done):
    client, run_id, _, _ = done
    r = client.post(f"/api/runs/{run_id}/report").json()
    assert r["status"] == "fallback" and r["reason"] in ("llm_model_not_configured", "llm_key_not_configured")


def test_provider_selection():
    class S:
        llm_provider, llm_model, llm_api_key, llm_timeout = "openai", None, None, 5

    assert get_provider(S)[1] == "llm_model_not_configured"
    S.llm_model = "m"
    assert get_provider(S)[1] == "llm_key_not_configured"
    S.llm_api_key = "k"
    p, reason = get_provider(S)
    assert reason is None and p.name == "openai" and p.model == "m"
    S.llm_provider = "gemini"
    assert get_provider(S) == (None, "gemini_not_implemented")


def test_openai_adapter_with_stub_client():
    from regime_api.llm.openai import OpenAIProvider

    class Msg:
        content = "  본문  "

    class Resp:
        choices = [type("C", (), {"message": Msg})]

    class Stub:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    assert kw["model"] == "m" and kw["messages"][0]["role"] == "system"
                    return Resp

    assert OpenAIProvider("k", "m", client=Stub).generate("s", "u") == "본문"

    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("401")

    with pytest.raises(LLMError):
        OpenAIProvider("k", "m", client=Boom).generate("s", "u")


def test_facts_carry_korean_labels(done):
    """report-v2: 본문에 코드값 대신 쓸 한국어 이름을 함께 넘긴다."""
    _, _, _, facts = done
    assert facts["strategy"]["patterns_ko"] == ["20일 고가 돌파"]
    v = facts["validation"]
    assert v["fdr_pass_ko"] in ("통과", "미통과") and v["random_pass_ko"] in ("통과", "미통과")
    assert v["analysis_target_ko"] in ("분석 대상", "분석 대상 아님")
    c = facts["cells"]
    assert c["sufficient_cells"] == len(c["sufficient"])
    assert c["sufficient_positive_cells"] + c["sufficient_negative_cells"] <= c["sufficient_cells"]
    for cell in c["sufficient"]:
        assert cell["regime_ko"] in ("상승장", "횡보장", "하락장", "국면 없음")
        assert cell["market_ko"] in ("코스피", "코스닥") and cell["cap_group_ko"] in ("대형주", "중형주", "소형주")


@pytest.mark.parametrize("with_cells", [True, False])
def test_template_explains_regime_cells(done, with_cells):
    import copy

    _, _, _, facts = done
    f = copy.deepcopy(facts)
    if with_cells:
        f["cells"]["sufficient"] = [{"regime": "bull", "market": "KOSDAQ", "cap_group": "mid", "regime_ko": "상승장",
                                     "market_ko": "코스닥", "cap_group_ko": "중형주", "trades": 415, "mean_excess_pct": -3.33}]
        f["cells"].update(sufficient_cells=1, sufficient_positive_cells=0, sufficient_negative_cells=1)
    else:
        f["cells"].update(sufficient=[], sufficient_cells=0, sufficient_positive_cells=0, sufficient_negative_cells=0)
    text = render(f)
    assert ("상승장·코스닥·중형주 -3.33%" in text) if with_cells else ("셀이 없어" in text)
    assert "`" not in text and "negative_both" not in text
    assert verify(text, f).ok


def test_report_v3_labels_and_saved_parameters(done):
    from regime_api.llm.prompt import PATTERN_KO

    _, _, result, _ = done
    r = copy.deepcopy(result)
    st = r["strategies"][0]["strategy_input"]
    st.update(patterns=list(PATTERN_KO), pattern_params={
        "rsi_rebound": {"threshold": 35, "window": 21}, "three_down_up": {"down_days": 4},
        "ma_cross_5_20": {"fast": 3}, "stochastic_rebound": {"oversold": 30.0},
    })
    facts = build_facts(r, "bo")
    assert len(facts["strategy"]["patterns_ko"]) == len(PATTERN_KO) == 14
    assert not set(facts["strategy"]["patterns_ko"]) & set(PATTERN_KO)
    assert "RSI 30" not in " ".join(facts["strategy"]["patterns_ko"])
    assert facts["strategy"]["pattern_params"] == st["pattern_params"]
    text = render(facts)
    assert "RSI 문턱 35" in text and "RSI 기간(일) 21" in text and "연속 하락 일수 4" in text
    assert "단기 이평(일) 3" in text and "과매도 문턱 30.0" in text
    assert "stochastic_rebound" not in text and "oversold" not in text and "window" not in text
    assert "2024-02-29" in text and "단위 없는 비율" in text
    assert "집계에서 제외" in text and "진입하지 못한 신호 19건" in text
    assert len(re.findall(r"[.!?](?:\s|$)", text)) <= 14
    assert verify(text, facts).ok


def test_report_v3_uses_saved_split_not_current_config(done):
    client, rid, result, _ = done
    r = copy.deepcopy(result)
    r["strategies"][0]["split"]["split_date"] = "2023-12-28"
    path = client.app.state.rl.paths.runs / rid / "result.json"
    path.write_text(json.dumps(r), encoding="utf-8")
    client.app.state.rl.cfg["analysis"]["split_date"] = "2025-01-31"
    rep = client.post(f"/api/runs/{rid}/report").json()
    assert rep["prompt_version"] == "report-v3"
    assert rep["facts"]["validation"]["split_date"] == "2023-12-28"
    assert "2025-01-31" not in rep["text"]


def test_old_result_does_not_invent_missing_parameters_or_split(done):
    _, _, result, _ = done
    r = copy.deepcopy(result)
    del r["strategies"][0]["split"]
    r["strategies"][0]["strategy_input"]["patterns"] = ["rsi_rebound", "three_down_up"]
    facts = build_facts(r, "bo")
    assert facts["strategy"]["pattern_params"] == {}
    assert facts["validation"]["split_date"] is None
    text = render(facts)
    assert "RSI 30" not in text and "3일 하락" not in text
    assert "기준일은 저장된 근거에서 확인할 수 없습니다" in text
    assert "2024-02-29" not in text and verify(text, facts).ok


def test_zero_trades_are_not_explained_as_zero_returns(done):
    _, _, result, _ = done
    r = copy.deepcopy(result)
    sr = r["strategies"][0]
    sr["summary"].update(trades=0, win_rate=None, mean_ret=None, median_ret=None, mean_excess=None,
                         payoff_ratio=None, sharpe=None, mdd=None, period_start=None, period_end=None)
    sr["validation"].update(analysis_target=None, fdr_pass=None, split_judgement=None,
                            p_value=None, h1_trades=0, h2_trades=0, h1_mean_excess=None,
                            h2_mean_excess=None, random_percentile=None)
    sr["cells_market"] = []
    facts = build_facts(r, "bo")
    text = render(facts)
    assert "승률은 산출 불가" in text and "승률은 0" not in text
    assert "산출 불가%" not in text and "산출 불가배" not in text
    assert "거래 기간은 산출 불가" in text and "None" not in text
    assert "분석 대상 여부를 산출하지 않았습니다" in text
    assert verify(text, facts).ok


def test_v3_does_not_reuse_v2_cache(done):
    from regime_api.llm.cache import cache_key

    client, rid, _, facts = done
    fake = FakeProvider(good_text(facts))
    _use(client, fake)
    key = cache_key(facts, "report-v2", fake.name, fake.model)
    client.app.state.rl.reports.cache.put(key, {"text": "이전 보고서", "status": "verified"})
    rep = client.post(f"/api/runs/{rid}/report").json()
    assert rep["text"] == good_text(facts) and not rep["cached"] and fake.calls == 1


def _i18n_ko(prefix: str) -> dict[str, str]:
    src = (Path(__file__).resolve().parents[2] / "web" / "js" / "i18n.js").read_text(encoding="utf-8")
    return dict(re.findall(rf"^\s*'{re.escape(prefix)}\.([\w.]+)': \['([^']*)'", src, re.M))


def test_report_labels_cover_every_pattern_param_and_exit():
    """패턴·조정 수치·청산이 늘면 보고서 이름표도 함께 늘어야 한다 (빠지면 본문에 영문 코드가 나온다).
    이름은 화면 문구(i18n 한국어)와 같게 유지한다."""
    from regime_api.llm.prompt import EXIT_KO, PARAM_KO, PATTERN_KO
    from regime_lab.config import load_config

    cfg = load_config()
    pat_i18n, pp_i18n = _i18n_ko("pat"), _i18n_ko("pp")
    assert set(PATTERN_KO) == set(cfg["patterns"])
    assert PATTERN_KO == {p: pat_i18n[p] for p in PATTERN_KO}
    for pat, limits in cfg["pattern_limits"].items():
        assert set(PARAM_KO.get(pat, {})) == set(limits), pat
        for k, label in PARAM_KO[pat].items():
            assert label == pp_i18n[f"{pat}.{k}"], f"{pat}.{k}"
    assert set(EXIT_KO) == set(cfg["exit_limits"])


def test_template_explains_enabled_exits_only(done):
    _, _, result, _ = done
    r = copy.deepcopy(result)
    r["strategies"][0]["strategy_input"]["exit"] = {
        "stop_loss_pct": -8, "take_profit_pct": None, "max_hold_days": 20,
        "trailing_stop_pct": -10, "breakeven_trigger_pct": 5, "ma_exit_window": 60}
    facts = build_facts(r, "bo")
    assert [x["rule_ko"] for x in facts["strategy"]["exits_ko"]] == [
        "손절", "트레일링 스톱(보유 중 최고 기준가 대비)", "본전 스톱(발동 수익률)", "이동평균 이탈 청산", "최대 보유"]
    text = render(facts)
    assert ("청산 조건은 손절 -8%, 트레일링 스톱(보유 중 최고 기준가 대비) -10%, 본전 스톱(발동 수익률) 5%, "
            "이동평균 이탈 청산 60일선, 최대 보유 20거래일입니다.") in text
    assert "익절" not in text and "trailing" not in text
    assert len(re.findall(r"[.!?](?:\s|$)", text)) <= 14
    assert verify(text, facts).ok

    del r["strategies"][0]["strategy_input"]["exit"]  # 청산이 저장되지 않은 구 결과
    old = build_facts(r, "bo")
    assert old["strategy"]["exits_ko"] == [] and "청산 조건" not in render(old)
