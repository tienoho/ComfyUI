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
        return (status, data) if path == "/features" else (identity, b"")

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
