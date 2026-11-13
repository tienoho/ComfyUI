"""Offline usage summary for exported official Comfy Agent message_done events.

No Agent runtime, credential, private socket, or second UI is created. Missing
model or priced usage always remains unknown; charges are never estimated.
Usage: python script_examples/agent_usage_report.py events.jsonl --pretty
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re
import sys

MAX_LINE_BYTES = 1024 * 1024
MAX_EVENTS = 1_000_000
MAX_COUNTER = (1 << 63) - 1
TOKEN_KEYS = (
    "input_tokens", "output_tokens", "total_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class UsageReportError(ValueError):
    """Bad event format, conflicting terminal event, or unsafe aggregate."""


@dataclass(frozen=True)
class Completion:
    thread_id: str
    message_id: str
    usage: tuple[int | None, ...]
    charge_micro_usd: int | None
    model_id: str | None
    model_source: str | None


def _counter(value: object, name: str, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not minimum <= value <= MAX_COUNTER:
        raise UsageReportError(f"Invalid {name}: expected integer in [{minimum}, 2^63-1]")
    return value


def parse_completion(event: object) -> Completion | None:
    """Parse only official terminal frames, without inferring a model.

    model_receipt is an optional future extension, not a field promised on
    message_done by the currently pinned frontend. Absent means unknown.
    """
    if not isinstance(event, dict):
        raise UsageReportError("Event must be an object")
    frame = event.get("frame", event)
    if not isinstance(frame, dict):
        raise UsageReportError("Event frame must be an object")
    if frame.get("type") != "agent_message_done":
        return None
    data = frame.get("data")
    if not isinstance(data, dict):
        raise UsageReportError("agent_message_done data must be an object")
    ids = []
    for field in ("thread_id", "message_id"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise UsageReportError(f"Missing {field} on completed Agent turn")
        ids.append(value)
    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise UsageReportError("Agent usage must be an object or null")
    usage = usage or {}
    tokens = tuple(_counter(usage.get(k), k) for k in TOKEN_KEYS)
    charge = _counter(usage.get("charge_micro_usd"), "charge_micro_usd", minimum=1)
    model_id = None
    source = None
    receipt = data.get("model_receipt")
    if receipt is not None:
        if not isinstance(receipt, dict) or receipt.get("contract") != "v1":
            raise UsageReportError("Unexpected model receipt contract")
        model_id = receipt.get("model_id")
        source = receipt.get("source")
        if (not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id)
                or source not in ("request", "deployment_default")
                or not isinstance(receipt.get("profile_revision"), str)
                or not receipt["profile_revision"]):
            raise UsageReportError("Invalid v1 model receipt; model attribution refused")
    return Completion(*ids, tokens, charge, model_id, source)


def _usd(micro_usd: int) -> str:
    return str((Decimal(micro_usd) / Decimal(1_000_000)).quantize(Decimal("0.000001")))


def _totals(items: list[Completion]) -> dict:
    token_usage = {}
    for idx, key in enumerate(TOKEN_KEYS):
        known = [x.usage[idx] for x in items if x.usage[idx] is not None]
        token_usage[key] = {
            "known_total": sum(known),
            "reported_turns": len(known),
            "unknown_turns": len(items) - len(known),
        }
    charges = [x.charge_micro_usd for x in items if x.charge_micro_usd is not None]
    total = sum(charges)
    return {
        "turns": len(items),
        "token_usage": token_usage,
        "vendor_model_charge": {
            "known_micro_usd": total,
            "known_usd": _usd(total),
            "reported_turns": len(charges),
            "unknown_turns": len(items) - len(charges),
            "is_complete": len(charges) == len(items),
            "not_a_customer_invoice": True,
        },
    }


def summarize_lines(lines) -> dict:
    """Deduplicate terminal events and omit raw content, prompts and user IDs."""
    seen: dict[tuple[str, str], Completion] = {}
    read = unrelated = duplicates = 0
    for index, line in enumerate(lines, 1):
        if index > MAX_EVENTS:
            raise UsageReportError("Event count exceeds safe reporting limit")
        if isinstance(line, bytes):
            if len(line) > MAX_LINE_BYTES:
                raise UsageReportError(f"Line {index}: event exceeds 1 MiB")
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise UsageReportError(f"Line {index}: invalid UTF-8") from exc
        elif len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise UsageReportError(f"Line {index}: event exceeds 1 MiB")
        if not line.strip():
            continue
        read += 1
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise UsageReportError(f"Line {index}: invalid JSON") from exc
        try:
            item = parse_completion(event)
        except UsageReportError as exc:
            raise UsageReportError(f"Line {index}: {exc}") from exc
        if item is None:
            unrelated += 1
            continue
        key = (item.thread_id, item.message_id)
        prior = seen.get(key)
        if prior is not None:
            if prior != item:
                raise UsageReportError(f"Line {index}: conflicting terminal event for same turn")
            duplicates += 1
            continue
        seen[key] = item
    items = list(seen.values())
    grouped: dict[str, list[Completion]] = {}
    for item in items:
        grouped.setdefault(item.model_id or "unknown", []).append(item)
    return {
        "schema": "comfy-agent-usage-summary/v1",
        "source": "offline first-party agent_message_done export",
        "events_read": read,
        "unrelated_events": unrelated,
        "duplicate_terminal_events": duplicates,
        "unique_completed_turns": len(items),
        "model_attribution": {
            "receipt_backed_turns": sum(bool(x.model_id) for x in items),
            "unknown_turns": sum(not x.model_id for x in items),
            "note": "No model inferred from provider, text, deployment defaults or image-generation nodes",
        },
        "overall": _totals(items),
        "by_model": {key: _totals(grouped[key]) for key in sorted(grouped)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Local JSONL export; no live connection")
    parser.add_argument("--pretty", action="store_true", help="Indent JSON summary")
    parser.add_argument("--output", type=Path, help="Write aggregate JSON to a file")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            result = summarize_lines(stream)
        output = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None)
        if args.output:
            if args.output.resolve() == args.input.resolve():
                raise UsageReportError("Output must not overwrite the input event export")
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0
    except (UsageReportError, OSError) as exc:
        print(f"agent_usage_report: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
