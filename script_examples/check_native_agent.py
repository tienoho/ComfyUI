"""Check the official Comfy Agent host and API without running a second agent.

Usage: python script_examples/check_native_agent.py --wait-seconds 90
A 401/403 from /api/agent/identity is expected before signing in.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirects)


class ProbeError(Exception):
    """The native Agent host is unhealthy or its public route is incompatible."""


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("--url must be an HTTP(S) origin without credentials or a path")
    return value.rstrip("/")


def _get_status(base: str, path: str, *, timeout: float = 3.0) -> tuple[int, bytes]:
    try:
        with _OPENER.open(Request(base + path, headers={"Accept": "application/json"}), timeout=timeout) as response:
            return response.status, response.read(65537)
    except HTTPError as error:
        error.close()
        return error.code, b""


def interpret_feature(data: dict) -> tuple[str, str | None]:
    """Only the host's v1 feature flag is authoritative for readiness."""
    if not isinstance(data, dict) or "agent" not in data:
        return "disabled", "Start ComfyUI with --enable-agent"
    flag = data["agent"]
    if not isinstance(flag, dict):
        raise ProbeError("Unexpected 'agent' feature format")
    state = flag.get("state")
    if state == "ready":
        return "ready", None
    if state == "starting":
        return "starting", None
    if state == "failed":
        reason = flag.get("reason")
        if reason == "package_missing":
            return "failed", 'Install: python -m pip install --pre "comfy-agent[go-nonfree]"'
        if reason == "outdated":
            return "failed", 'Update: python -m pip install --pre -U "comfy-agent[go-nonfree]"'
        return "failed", f"Comfy Agent host failed ({reason or 'unknown'}); inspect ComfyUI logs"
    raise ProbeError(f"Unexpected 'agent' state: {state!r}")


def probe_once(base: str, *, get_status=_get_status) -> dict:
    status, body = get_status(base, "/features")
    if status != 200:
        raise ProbeError(f"ComfyUI /features returned HTTP {status}")
    if len(body) > 65536:
        raise ProbeError("/features response exceeds size limit")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ProbeError("ComfyUI /features returned invalid JSON") from error
    state, hint = interpret_feature(data)
    if state != "ready":
        return {"state": state, "hint": hint}
    route_status, identity_body = get_status(base, "/api/agent/identity")
    if route_status == 200:
        try:
            identity = json.loads(identity_body)
        except (ValueError, UnicodeDecodeError) as error:
            raise ProbeError("Agent identity endpoint returned non-JSON content") from error
        if (not isinstance(identity, dict)
                or not isinstance(identity.get("user_id"), str)
                or not identity["user_id"].strip()):
            raise ProbeError("Agent identity endpoint returned invalid user_id")
    elif route_status not in (401, 403):
        raise ProbeError(f"Native /api/agent/identity route returned HTTP {route_status}")
    return {
        "state": "ready", "route_status": route_status,
        "hint": "Open the built-in Agent panel and complete sign-in/consent",
    }


def main(argv: list[str] | None = None, *, get_status=_get_status) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8188", help="ComfyUI HTTP(S) origin")
    parser.add_argument("--wait-seconds", type=float, default=0,
                        help="Wait up to N seconds for ready (default: do not wait)")
    args = parser.parse_args(argv)
    try:
        base = _base_url(args.url)
        if not 0 <= args.wait_seconds <= 300:
            raise ValueError("--wait-seconds must be between 0 and 300")
        deadline = time.monotonic() + args.wait_seconds
        while True:
            try:
                result = probe_once(base, get_status=get_status)
            except (URLError, TimeoutError, OSError) as error:
                result = {"state": "unreachable", "hint": str(error)}
            if result["state"] == "ready":
                print(json.dumps(result, ensure_ascii=False))
                return 0
            if result["state"] in ("disabled", "failed") or time.monotonic() >= deadline:
                print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
                return 1
            time.sleep(min(1.0, max(0, deadline - time.monotonic())))
    except (ValueError, ProbeError) as error:
        print(json.dumps({"state": "error", "hint": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
