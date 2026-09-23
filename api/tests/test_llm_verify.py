"""test_llm_verify (FR-L2~L4, NFR-7): 입력에 없는 숫자·권유 표현 거부, 템플릿 폴백, 캐시."""

import pytest
from conftest import run_to_end

from regime_api.llm.prompt import build_facts
from regime_api.llm.provider import LLMError, get_provider
from regime_api.llm.template import render
from regime_api.llm.verify import verify


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
def done(client):
    run_id, _ = run_to_end(client)
    result = client.get(f"/api/runs/{run_id}/result").json()
    return client, run_id, result, build_facts(result, "bo")


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
