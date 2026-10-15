"""X12 policy contracts, synthetic paths and clients; no data, keys or external services."""
import ipaddress
import os
import socket
import threading
import time
from dataclasses import replace

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from regime_api import main
from regime_api.settings import Settings
from regime_lab.config import Paths
from test_auth_contract import A, auth_app, csrf, signin
from test_job_queue import RUN, SEARCH, runner, wait_search

ALLOWED = "192.168.40.21"
OUTSIDE = "192.168.40.22"
DENIED = {"code": "network_not_allowed", "message": "Access from this network is not allowed.",
          "detail": None, "retryable": False}


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "store", tmp_path / "dump", tmp_path / "cache", tmp_path / "runs")


@pytest.fixture
def env_settings(paths, monkeypatch):
    for key in list(os.environ):
        if key.startswith("REGIME_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr("regime_api.settings.load_paths", lambda: paths)
    return Settings.from_env


@pytest.fixture
def lan_app(paths, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>synthetic X12</html>", encoding="utf-8")
    (web / "asset.js").write_text("// synthetic", encoding="utf-8")
    settings = Settings(paths=paths, host="0.0.0.0", web_dir=web,
                        allowed_nets=[ipaddress.ip_network(ALLOWED)])
    app = main.create_app(settings, prep=object(), rag=object())
    calls = []
    @app.get("/probe")
    def probe(request: Request):
        calls.append(request.client.host)
        return {"client": request.client.host}
    # Put the probe before the root StaticFiles mount.
    app.router.routes.insert(0, app.router.routes.pop())
    app.state.probe_calls = calls
    return app


@pytest.mark.parametrize("raw, expected", [
    ("", []), (" , ", []),
    (" 10.0.0.1 , 172.16.0.0/12, ,192.168.0.0/16 ",
     ["10.0.0.1/32", "172.16.0.0/12", "192.168.0.0/16"]),
    ("10.0.0.0/8,172.31.255.255/32,192.168.255.255",
     ["10.0.0.0/8", "172.31.255.255/32", "192.168.255.255/32"]),
])
def test_demo_env_normalizes_only_valid_private_networks(env_settings, monkeypatch, raw, expected):
    monkeypatch.setenv("REGIME_ALLOWED_NETS", raw)
    settings = env_settings()
    settings.validate_auth()
    assert list(map(str, settings.allowed_nets)) == expected
    assert all(str(net) not in repr(settings) for net in settings.allowed_nets)


@pytest.mark.parametrize("bad", [
    "not-a-net", "192.168.1.5/24", "10.0.0.1/33", "10.0.0.0/7", "172.0.0.0/8",
    "172.15.255.255", "172.32.0.0/16", "8.8.8.8", "0.0.0.0/0", "0.0.0.0",
    "127.0.0.1", "169.254.0.0/16", "192.0.2.0/24", "198.18.0.0/15", "224.0.0.0/4",
    "fc00::/7", "::/0", "::1", "::ffff:192.168.40.21/128",
])
def test_bad_env_entry_rejects_entire_list_without_echo(env_settings, monkeypatch, bad):
    monkeypatch.setenv("REGIME_ALLOWED_NETS", "10.0.0.1," + bad)
    with pytest.raises(ValueError) as exc:
        env_settings().validate_auth()
    assert "REGIME_ALLOWED_NETS" in str(exc.value)
    assert bad not in str(exc.value)
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


@pytest.mark.parametrize("nets", [
    [ipaddress.ip_network("8.8.8.0/24")], [ipaddress.ip_network("fd00::/8")], [object()],
    ["192.168.1.5/24"], ["10.0.0.1", "bad"],
])
def test_direct_settings_cannot_bypass_validation(paths, nets):
    with pytest.raises(ValueError):
        main.create_app(Settings(paths=paths, host="0.0.0.0", allowed_nets=nets), serve_web=False)
    assert not paths.runs.exists()


@pytest.mark.parametrize("host", ["0.0.0.0", ALLOWED])
@pytest.mark.parametrize("raw", ["", " , "])
def test_non_loopback_requires_nonempty_list_before_appstate(env_settings, monkeypatch, host, raw):
    monkeypatch.setenv("REGIME_HOST", host)
    monkeypatch.setenv("REGIME_ALLOWED_NETS", raw)
    settings = env_settings()
    with pytest.raises(ValueError, match="REGIME_ALLOWED_NETS"):
        main.create_app(settings, serve_web=False)
    assert not settings.paths.runs.exists()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_unconfigured_loopback_demo_preserves_guest_config(paths, host):
    app = main.create_app(Settings(paths=paths, host=host), prep=object(), rag=object(), serve_web=False)
    with TestClient(app) as client:
        assert client.get("/api/auth/config").json()["guest_allowed"] is True
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("host, allowed", [
    (ALLOWED, True), (OUTSIDE, False), ("10.1.2.3", False),
    ("127.0.0.1", True), ("127.0.0.2", True), ("::1", True),
    ("::ffff:" + ALLOWED, True), ("::ffff:" + OUTSIDE, False), ("::ffff:127.0.0.1", True),
    ("fd00::21", False), ("2001:db8::21", False), ("testclient", False), ("localhost", False),
])
def test_client_address_boundary(lan_app, host, allowed):
    with TestClient(lan_app, client=(host, 50000)) as client:
        response = client.get("/probe")
    assert response.status_code == (200 if allowed else 403)
    assert lan_app.state.probe_calls == ([host] if allowed else [])
    if not allowed:
        assert response.json() == DENIED
        assert host not in response.text


def test_missing_client_is_denied(lan_app):
    with TestClient(lan_app, client=None) as client:
        assert client.get("/probe").json() == DENIED
    assert not lan_app.state.probe_calls


@pytest.mark.parametrize("path", ["/api/health", "/api/auth/config", "/", "/index.html", "/asset.js",
                                 "/docs", "/openapi.json", "/missing"])
@pytest.mark.parametrize("method", ["GET", "POST", "OPTIONS"])
def test_filter_covers_every_route_method_and_cors_preflight(lan_app, path, method):
    # A CORS preflight must not short-circuit the outer LAN filter.
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware import Middleware
    lan_app.user_middleware.append(Middleware(CORSMiddleware, allow_origins=["https://example.test"],
                                             allow_methods=["*"]))
    with TestClient(lan_app, client=(OUTSIDE, 50000)) as client:
        response = client.request(method, path, headers={"Origin": "https://example.test",
                                                       "Access-Control-Request-Method": "GET"})
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == DENIED


def test_allowed_static_and_api_keep_normal_responses(lan_app):
    with TestClient(lan_app, client=(ALLOWED, 50000)) as client:
        assert client.get("/").text == "<html>synthetic X12</html>"
        assert client.get("/asset.js").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert client.get("/missing").status_code == 404


@pytest.mark.parametrize("headers", [
    {"X-Forwarded-For": ALLOWED}, {"X-Forwarded-For": "127.0.0.1"},
    {"Forwarded": "for=127.0.0.1"}, {"X-Real-IP": ALLOWED},
])
def test_forwarded_headers_never_grant_access(lan_app, headers):
    with TestClient(lan_app, client=(OUTSIDE, 50000)) as client:
        assert client.get("/probe", headers=headers).json() == DENIED
    assert not lan_app.state.probe_calls


def test_remote_llm_read_and_local_only_mutations(lan_app):
    settings = lan_app.state.rl.settings
    settings.llm_api_key = "sk-synthetic-only-1234"
    original = (settings.llm_model, settings.llm_api_key)
    with TestClient(lan_app, client=(ALLOWED, 50000)) as remote:
        assert remote.get("/api/llm/config").status_code == 200
        for method in ("PUT", "DELETE"):
            response = remote.request(method, "/api/llm/config", headers={"X-Forwarded-For": "127.0.0.1"},
                                      json={"model": "test", "api_key": "sk-other-synthetic"})
            assert response.status_code == 403 and response.json()["code"] == "local_only"
    assert (settings.llm_model, settings.llm_api_key) == original
    for host in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        with TestClient(lan_app, client=(host, 50000)) as local:
            assert local.put("/api/llm/config", json={"model": "test", "api_key": "sk-synthetic-local-1234"}).status_code == 200
            assert local.delete("/api/llm/config").status_code == 200


@pytest.mark.parametrize("raw", [ALLOWED, "not-a-net", "0.0.0.0/0", "fd00::/8"])
@pytest.mark.parametrize("infer_mode", [False, True])
def test_supabase_env_ignores_lan_value_before_parsing(env_settings, monkeypatch, paths, raw, infer_mode):
    if not infer_mode:
        monkeypatch.setenv("REGIME_AUTH_MODE", "supabase")
    monkeypatch.setenv("REGIME_SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("REGIME_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
    monkeypatch.setenv("REGIME_AUTH_STATE_DIR", str(paths.runs.parent / "auth"))
    monkeypatch.setenv("REGIME_ALLOWED_NETS", raw)
    settings = env_settings()
    settings.validate_auth()
    assert settings.auth_mode == "supabase" and settings.allowed_nets == []


@pytest.mark.parametrize("nets", [[], [ipaddress.ip_network("10.0.0.1")], ["not-a-net"]])
def test_supabase_direct_field_does_not_install_filter_or_bypass_auth(auth_app, nets):
    settings = replace(auth_app.state.rl.settings, allowed_nets=nets)
    app = main.create_app(settings, prep=object(), rag=object(), serve_web=False,
                          auth_provider=auth_app.state.test_provider)
    app.state.rl.auth.site_url = "http://testserver"
    with TestClient(app, client=("203.0.113.50", 50000)) as client:
        assert client.get("/api/auth/config").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        headers = signin(client)
        assert client.get("/api/auth/me").json()["user"]["id"] == A
        assert client.post("/api/auth/logout", headers=headers).status_code == 204
        guest = client.post("/api/auth/guest", headers=csrf(client))
        assert guest.status_code == 200


def test_lan_clients_share_fifo_and_keep_per_ip_limit(paths, runner):
    settings = Settings(paths=paths, host="0.0.0.0", allowed_nets=[ipaddress.ip_network("192.168.40.0/24")])
    app = main.create_app(settings, prep=object(), rag=object(), serve_web=False)
    jobs = app.state.rl.jobs
    with TestClient(app, client=(ALLOWED, 50000)) as first, TestClient(app, client=(OUTSIDE, 50000)) as second:
        rid = first.post("/api/runs", json=RUN).json()["run_id"]
        assert runner.started.wait(2)
        same = first.post("/api/searches", json=SEARCH)
        assert same.status_code == 409 and same.json()["code"] == "user_busy"
        response = second.post("/api/searches", json=SEARCH)
        assert response.status_code == 202 and response.json()["queue_position"] == 1
        sid = response.json()["search_id"]
        runner.gate.set()
        assert jobs.wait(rid)["status"] == "completed"
        assert wait_search(jobs, sid)["status"] == "completed"
        assert runner.active[1] == 1 and runner.calls == ["first", "second"]


@pytest.mark.parametrize("mode, options", [("demo", {"proxy_headers": False}), ("supabase", {})])
def test_run_preserves_supabase_options_and_disables_demo_proxy_headers(paths, monkeypatch, mode, options):
    import uvicorn
    settings = Settings(paths=paths, auth_mode=mode)
    sentinel = object()
    captured = []
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(main, "create_app", lambda value: sentinel)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.append((app, kw)))
    main.run()
    assert captured == [(sentinel, {"host": settings.host, "port": settings.port, **options})]


def test_actual_uvicorn_ignores_forwarded_headers_even_with_trust_all(paths, monkeypatch):
    import uvicorn
    settings = Settings(paths=paths, host="0.0.0.0", allowed_nets=[ipaddress.ip_network(ALLOWED)])
    app = main.create_app(settings, prep=object(), rag=object(), serve_web=False)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(main, "create_app", lambda value: app)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    checks = []

    def serve(actual_app, **options):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(actual_app, **{**options, "host": "127.0.0.1", "port": port},
                                                    log_config=None, log_level="error", lifespan="off"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert server.started
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3) as client:
                    for headers in ({}, {"X-Forwarded-For": "203.0.113.50"},
                                    {"Forwarded": "for=203.0.113.50", "X-Real-IP": "203.0.113.50"}):
                        # Real socket peer is loopback. If Uvicorn rewrites it from XFF, this becomes 403.
                        response = client.get("/api/health", headers=headers)
                        assert response.status_code == 200, response.text
                        checks.append(response.status_code)
                    blocked_llm = client.put("/api/llm/config", json={"model": "test"})
                    assert blocked_llm.status_code == 422  # local request reached ordinary key validation
            finally:
                server.should_exit = True
                thread.join(5)
                assert not thread.is_alive()
    monkeypatch.setattr(uvicorn, "run", serve)
    main.run()
    assert checks == [200, 200, 200]
