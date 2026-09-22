"""test_disclaimer (FR-U6·U7, FR-G6): 모든 결과 응답에 고지와 거래 수, 브리핑은 data_unavailable."""

from conftest import run_to_end


def test_result_responses_carry_disclaimer_and_counts(client):
    run_id, _ = run_to_end(client)
    res = client.get(f"/api/runs/{run_id}/result").json()
    assert "not investment advice" in res["disclaimer"]
    for s in res["strategies"]:
        assert isinstance(s["summary"]["trades"], int)
        for c in s["cells_market"] + s["cells_stock"]:
            assert "trades" in c and "sample_insufficient" in c
        for t in s["tickers"]:
            assert "trades" in t and "sample_insufficient" in t
        assert "analysis_target" in s["validation"]
    tk = res["strategies"][0]["tickers"][0]["ticker"]
    d = client.get(f"/api/runs/{run_id}/stocks/{tk}").json()
    assert "not investment advice" in d["disclaimer"] and "trades" in d["sample"]
    rep = client.post(f"/api/runs/{run_id}/report").json()
    assert "not investment advice" in rep["disclaimer"]
    assert "not investment advice" in client.get("/api/meta").json()["disclaimer"]


def test_briefing_is_data_unavailable(client):
    b = client.get("/api/briefing").json()
    assert b["status"] == "data_unavailable" and b["reason"] and "not investment advice" in b["disclaimer"]
