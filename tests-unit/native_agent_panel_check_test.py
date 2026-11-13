"""CPU-only, stdlib-compatible tests for the native agent readiness probe."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


CHECK = Path(__file__).resolve().parents[1] / "script_examples" / "check_native_agent.py"
SPEC = importlib.util.spec_from_file_location("native_agent_probe", CHECK)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def fake(status=200, feature=None, identity=401):
    data = json.dumps(feature if feature is not None else {}).encode()

    def respond(base, path):
        assert base == "http://127.0.0.1:8188"
        return (status, data) if path == "/features" else (
            identity, b'{"user_id":"demo-user"}' if identity == 200 else b""
        )

    return respond


@pytest.mark.parametrize("flag, expected", [
    ({}, "disabled"),
    ({"agent": {"state": "starting"}}, "starting"),
    ({"agent": {"state": "failed", "reason": "package_missing"}}, "failed"),
    ({"agent": {"state": "failed", "reason": "outdated"}}, "failed"),
    ({"agent": {"state": "ready"}}, "ready"),
])
def test_feature_flag_states(flag, expected):
    assert probe.interpret_feature(flag)[0] == expected


@pytest.mark.parametrize("http_status", [200, 401, 403])
def test_ready_route_with_or_without_login(http_status):
    result = probe.probe_once("http://127.0.0.1:8188", get_status=fake(
        feature={"agent": {"state": "ready"}}, identity=http_status))
    assert result["state"] == "ready"
    assert result["route_status"] == http_status


def test_missing_proxy_is_not_reported_as_ready():
    with pytest.raises(probe.ProbeError, match="HTTP 404"):
        probe.probe_once("http://127.0.0.1:8188", get_status=fake(
            feature={"agent": {"state": "ready"}}, identity=404))


def test_unexpected_flag_and_bad_payload():
    with pytest.raises(probe.ProbeError, match="format"):
        probe.interpret_feature({"agent": True})
    with pytest.raises(probe.ProbeError, match="invalid JSON"):
        probe.probe_once("http://127.0.0.1:8188", get_status=lambda *_: (200, b"not JSON"))


def test_offline_and_ready_exit_codes(capsys):
    assert probe.main(["--wait-seconds", "0"], get_status=fake()) == 1
    assert probe.main(["--wait-seconds", "0"], get_status=fake(
        feature={"agent": {"state": "ready"}}, identity=401)) == 0
    assert '"state": "ready"' in capsys.readouterr().out


def test_url_validation():
    with pytest.raises(ValueError):
        probe._base_url("https://user:pass@example.com")
    with pytest.raises(ValueError):
        probe._base_url("http://localhost:8188/prompt")
    assert probe._base_url("http://localhost:8188/") == "http://localhost:8188"


@pytest.mark.parametrize("identity_body", [b"<html>ComfyUI</html>", b"{}", b'{"user_id":""}', b"not json"])
def test_false_ready_from_static_fallback_is_rejected(identity_body):
    def respond(base, path):
        if path == "/features":
            return 200, b'{"agent":{"state":"ready"}}'
        return 200, identity_body

    with pytest.raises(probe.ProbeError, match="identity endpoint"):
        probe.probe_once("http://127.0.0.1:8188", get_status=respond)



def test_authenticated_probe_rejects_unauthenticated_identity():
    with pytest.raises(probe.ProbeError, match="did not authenticate"):
        probe.probe_once("http://127.0.0.1:8188", get_status=fake(
            feature={"agent": {"state": "ready"}}, identity=401),
            require_authenticated=True)
    result = probe.probe_once("http://127.0.0.1:8188", get_status=fake(
        feature={"agent": {"state": "ready"}}, identity=200),
        require_authenticated=True)
    assert result["identity_authenticated"] is True


def test_authenticated_probe_requires_environment_token(monkeypatch, capsys):
    monkeypatch.delenv("COMFY_AGENT_PROBE_TOKEN", raising=False)
    assert probe.main(["--require-authenticated"], get_status=fake()) == 2
    assert "COMFY_AGENT_PROBE_TOKEN" in capsys.readouterr().err


def test_authenticated_remote_probe_requires_explicit_https(monkeypatch, capsys):
    monkeypatch.setenv("COMFY_AGENT_PROBE_TOKEN", "not-a-real-token")
    assert probe.main([
        "--url", "https://external.example", "--require-authenticated",
    ], get_status=fake()) == 2
    assert "--allow-remote" in capsys.readouterr().err
    assert probe.main([
        "--url", "http://external.example", "--require-authenticated",
        "--allow-remote",
    ], get_status=fake()) == 2
    assert "HTTPS" in capsys.readouterr().err


@pytest.mark.parametrize("header_name, expected", [
    ("Authorization", "Bearer fake-test-token"),
    ("X-API-KEY", "fake-test-token"),
])
def test_auth_header_is_not_printed_and_is_derived_from_environment(
        monkeypatch, capsys, header_name, expected):
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def read(self, _max_size):
            return b'{"agent":{"state":"ready"}}'

    seen_headers = []

    class Opener:
        def open(self, request, timeout):
            seen_headers.append(request.headers)
            return Response()

    monkeypatch.setattr(probe, "_OPENER", Opener())
    status, body = probe._get_status(
        "https://local.example", "/features", credential="fake-test-token",
        credential_header=header_name)
    assert status == 200 and body
    key = "Authorization" if header_name == "Authorization" else "X-api-key"
    assert seen_headers[0][key] == expected
    assert "fake-test-token" not in capsys.readouterr().out



@pytest.mark.parametrize("auth_header,wire_header", [
    ("Authorization", "Authorization"),
    ("X-API-KEY", "X-api-key"),
])
def test_cli_authenticated_probe_uses_env_token_without_exposing_it(
        monkeypatch, capsys, auth_header, wire_header):
    class Response:
        status = 200
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def read(self, _size):
            return self.body

    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            if request.full_url.endswith("/features"):
                return Response(b'{"agent":{"state":"ready"}}')
            assert request.full_url.endswith("/api/agent/identity")
            return Response(b'{"user_id":"verified-user"}')

    monkeypatch.setenv("COMFY_AGENT_PROBE_TOKEN", "secret-probe-token-123")
    monkeypatch.setattr(probe, "_OPENER", Opener())
    assert probe.main([
        "--require-authenticated", "--auth-header", auth_header,
    ]) == 0
    assert len(seen) == 2
    expected = "Bearer secret-probe-token-123" if auth_header == "Authorization" else "secret-probe-token-123"
    assert all(request.headers[wire_header] == expected for request in seen)
    output = capsys.readouterr()
    assert '"identity_authenticated": true' in output.out
    assert "secret-probe-token-123" not in output.out + output.err
