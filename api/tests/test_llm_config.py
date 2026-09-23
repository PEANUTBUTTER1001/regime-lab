"""test_llm_config (결정 E10 잠정): 설정 화면의 모델명·키 적용·조회·지우기.

키 원문은 응답·보고서 캐시 파일에 남지 않고, 서버를 새로 만들면(재시작) 사라진다.
"""

import pytest
from conftest import run_to_end
from fastapi.testclient import TestClient

from regime_api.llm.prompt import build_facts
from regime_api.llm.provider import get_provider

KEY = "sk-test-secret-0000-abcd1234"
LOCAL = ("127.0.0.1", 50000)


class RecordingProvider:
    """get_provider 가 받은 settings 값을 기록하고, 검증을 통과하는 문장을 돌려주는 모의 공급사."""

    name = "openai"

    def __init__(self, model, text):
        self.model, self.text, self.calls = model, text, 0

    def generate(self, system, user):
        self.calls += 1
        return self.text


def local(client) -> TestClient:
    return TestClient(client.app, client=LOCAL)


def good_text(f):
    m = f["metrics"]
    return f"## 요약\n거래 {m['trades']:,}건의 평균 수익률은 {m['mean_ret_pct']}%입니다. 투자 권유가 아닙니다."


def test_initial_state_none(client):
    r = client.get("/api/llm/config")
    assert r.status_code == 200
    assert r.json() == {"provider": "openai", "model": None, "key_set": False, "key_hint": None, "source": "none"}


def test_env_values_are_reported_as_env(make_client):
    with make_client(llm_model="env-model", llm_api_key="sk-env-9999") as c:
        body = c.get("/api/llm/config").json()
    assert body["source"] == "env" and body["model"] == "env-model" and body["key_hint"] == "…9999"


def test_put_applies_and_never_echoes_key(client):
    lc = local(client)
    r = lc.put("/api/llm/config", json={"model": " gpt-test ", "api_key": f"  {KEY}  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"provider": "openai", "model": "gpt-test", "key_set": True, "key_hint": "…1234", "source": "ui"}
    assert KEY not in r.text and KEY not in lc.get("/api/llm/config").text
    st = client.app.state.rl.settings
    assert (st.llm_model, st.llm_api_key) == ("gpt-test", KEY)
    provider, reason = get_provider(st)
    assert reason is None and provider.model == "gpt-test"


def test_put_without_key_keeps_existing_key(client):
    lc = local(client)
    lc.put("/api/llm/config", json={"model": "m1", "api_key": KEY})
    r = lc.put("/api/llm/config", json={"model": "m2", "api_key": ""})
    assert r.status_code == 200 and r.json()["model"] == "m2"
    assert client.app.state.rl.settings.llm_api_key == KEY


def test_put_without_any_key_is_rejected(client):
    r = local(client).put("/api/llm/config", json={"model": "m1"})
    assert r.status_code == 422 and "api_key" in r.json()["detail"]["fields"]
    assert client.app.state.rl.settings.llm_model is None


@pytest.mark.parametrize("body, field", [
    ({"model": "   ", "api_key": KEY}, "model"),
    ({"model": "m\n1", "api_key": KEY}, "model"),
    ({"model": "m" * 101, "api_key": KEY}, "model"),
    ({"model": "m1", "api_key": "sk\tbad"}, "api_key"),
    ({"model": "m1", "api_key": "k" * 301}, "api_key"),
])
def test_put_validation(client, body, field):
    r = local(client).put("/api/llm/config", json=body)
    assert r.status_code == 422 and field in r.json()["detail"]["fields"]
    assert "sk\tbad" not in r.text


def test_remote_requests_are_forbidden(client):
    remote = TestClient(client.app, client=("192.168.0.10", 50000))
    assert remote.put("/api/llm/config", json={"model": "m1", "api_key": KEY}).status_code == 403
    assert remote.delete("/api/llm/config").status_code == 403
    assert remote.get("/api/llm/config").status_code == 200
    assert client.app.state.rl.settings.llm_api_key is None


def test_delete_restores_env_values(make_client):
    with make_client(llm_model="env-model", llm_api_key="sk-env-9999") as c:
        lc = local(c)
        lc.put("/api/llm/config", json={"model": "ui-model", "api_key": KEY})
        r = lc.delete("/api/llm/config")
        assert r.json()["model"] == "env-model" and r.json()["key_hint"] == "…9999" and r.json()["source"] == "env"


def test_delete_without_env_returns_to_template(client):
    lc = local(client)
    lc.put("/api/llm/config", json={"model": "m1", "api_key": KEY})
    assert lc.delete("/api/llm/config").json()["source"] == "none"
    run_id, _ = run_to_end(client)
    rep = client.post(f"/api/runs/{run_id}/report").json()
    assert rep["status"] == "fallback" and rep["reason"] == "llm_model_not_configured"
    assert "⚙" in rep["reason_message"]


def test_report_uses_ui_values_and_cache_has_no_key(client, tmp_path):
    run_id, _ = run_to_end(client)
    result = client.get(f"/api/runs/{run_id}/result").json()
    text = good_text(build_facts(result, "bo"))
    seen = {}

    def factory(settings):
        seen["model"], seen["key"] = settings.llm_model, settings.llm_api_key
        p, reason = get_provider(settings)
        return (RecordingProvider(settings.llm_model, text), None) if p else (None, reason)

    client.app.state.rl.reports._provider_factory = factory
    local(client).put("/api/llm/config", json={"model": "gpt-ui", "api_key": KEY})
    rep = client.post(f"/api/runs/{run_id}/report").json()
    assert seen == {"model": "gpt-ui", "key": KEY}
    assert rep["status"] == "verified" and rep["model"] == "gpt-ui" and KEY not in str(rep)
    files = list((tmp_path / "cache" / "llm_reports").glob("*.json"))
    assert files and all(KEY not in f.read_text(encoding="utf-8") for f in files)
    assert client.post(f"/api/runs/{run_id}/report").json()["cached"] is True


def test_restart_forgets_ui_key(make_client):
    with make_client() as c:
        local(c).put("/api/llm/config", json={"model": "m1", "api_key": KEY})
    with make_client() as c2:  # 새 서버 = 재시작
        assert c2.get("/api/llm/config").json()["source"] == "none"


def test_provider_error_message_hides_key_fragments():
    from regime_api.llm.openai import OpenAIProvider
    from regime_api.llm.provider import LLMError

    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError(f"Incorrect API key provided: sk-test-************1234. raw={KEY}")

    with pytest.raises(LLMError) as ei:
        OpenAIProvider(KEY, "m1", client=Boom()).generate("s", "u")
    msg = str(ei.value)
    assert KEY not in msg and "sk-test-****" not in msg and "1234" not in msg and "[redacted]" in msg
