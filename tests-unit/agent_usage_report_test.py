"""Offline accounting tests for official Agent message_done event exports."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

PATH = Path(__file__).resolve().parents[1] / "script_examples" / "agent_usage_report.py"
SPEC = importlib.util.spec_from_file_location("agent_usage_report", PATH)
assert SPEC is not None and SPEC.loader is not None
report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = report
SPEC.loader.exec_module(report)


def event(thread="t", message="m", usage=None, **attrs):
    return {"type": "agent_message_done", "data": {
        "thread_id": thread, "message_id": message, "usage": usage, **attrs,
    }}


def jsonl(*frames):
    return [json.dumps(frame) + "\n" for frame in frames]


def test_stable_frontend_null_usage_is_unknown_not_free():
    result = report.summarize_lines(jsonl(event()))
    assert result["unique_completed_turns"] == 1
    cost = result["overall"]["vendor_model_charge"]
    assert cost["unknown_turns"] == 1 and not cost["is_complete"]
    assert cost["known_usd"] == "0.000000"
    assert result["model_attribution"]["unknown_turns"] == 1


def test_wrapper_reconnect_and_other_frames_are_safe():
    done = event(usage={"input_tokens": 14, "output_tokens": 4, "charge_micro_usd": 6038})
    lines = jsonl({"frame": done, "capturedAt": 123}, done, {
        "type": "agent_message_delta", "data": {"delta": "PRIVATE PROMPT"},
    })
    result = report.summarize_lines(lines)
    assert result["duplicate_terminal_events"] == 1
    assert result["unrelated_events"] == 1
    assert result["unique_completed_turns"] == 1
    assert result["overall"]["vendor_model_charge"]["known_usd"] == "0.006038"
    assert "PRIVATE PROMPT" not in json.dumps(result)


def test_missing_charge_does_not_contaminate_known_charge():
    result = report.summarize_lines(jsonl(
        event(message="m1", usage={"input_tokens": 10}),
        event(message="m2", usage={"input_tokens": 20, "charge_micro_usd": 3}),
    ))
    charge = result["overall"]["vendor_model_charge"]
    assert (charge["known_micro_usd"], charge["unknown_turns"], charge["is_complete"]) == (3, 1, False)
    assert result["overall"]["token_usage"]["input_tokens"]["known_total"] == 30
    assert result["overall"]["token_usage"]["output_tokens"]["unknown_turns"] == 2


def test_model_receipt_requires_provenance_and_contract():
    receipt = {"contract": "v1", "model_id": "claude-opus-5",
               "source": "request", "profile_revision": "rev1"}
    result = report.summarize_lines(jsonl(event(model_receipt=receipt)))
    assert result["model_attribution"]["receipt_backed_turns"] == 1
    assert result["by_model"]["claude-opus-5"]["turns"] == 1
    with pytest.raises(report.UsageReportError, match="Invalid v1 model receipt"):
        report.summarize_lines(jsonl(event(
            model_receipt={**receipt, "model_id": "not a valid model id"})))


def test_text_not_mistaken_for_model_provenance():
    result = report.summarize_lines(jsonl(event(text="I used claude-opus-5")))
    assert result["by_model"]["unknown"]["turns"] == 1


@pytest.mark.parametrize("bad", [0, -1, 1.2, True, "3", 2**64])
def test_invalid_charge_fails_closed(bad):
    with pytest.raises(report.UsageReportError, match="charge_micro_usd"):
        report.summarize_lines(jsonl(event(usage={"charge_micro_usd": bad})))


@pytest.mark.parametrize("bad", [-1, 0.5, True, "5"])
def test_invalid_token_counter_fails_closed(bad):
    with pytest.raises(report.UsageReportError, match="input_tokens"):
        report.summarize_lines(jsonl(event(usage={"input_tokens": bad})))


def test_conflicting_duplicate_is_rejected():
    with pytest.raises(report.UsageReportError, match="conflicting terminal"):
        report.summarize_lines(jsonl(
            event(usage={"charge_micro_usd": 3}),
            event(usage={"charge_micro_usd": 4}),
        ))


def test_empty_stream_reports_no_fake_charges():
    result = report.summarize_lines([])
    assert result["unique_completed_turns"] == 0
    assert result["overall"]["vendor_model_charge"]["known_micro_usd"] == 0


def test_multithread_same_message_is_not_collapsed():
    assert report.summarize_lines(jsonl(event("t1", "m"), event("t2", "m")))[
        "unique_completed_turns"] == 2


def test_errors_do_not_leak_raw_prompt_or_token():
    with pytest.raises(report.UsageReportError) as raised:
        report.summarize_lines(jsonl({"type": "agent_message_done", "data": {
            "message_id": "sensitive-prompt-data", "usage": {"charge_micro_usd": 100},
        }}))
    assert "sensitive-prompt-data" not in str(raised.value)


def test_line_length_limit():
    with pytest.raises(report.UsageReportError, match="exceeds 1 MiB"):
        report.summarize_lines(["x" * (report.MAX_LINE_BYTES + 1)])


def test_file_cli_and_prevent_overwrite(tmp_path, capsys):
    source = tmp_path / "events.jsonl"
    output = tmp_path / "summary.json"
    source.write_text("".join(jsonl(event(usage={"charge_micro_usd": 123}))), encoding="utf-8")
    assert report.main([str(source), "--output", str(output), "--pretty"]) == 0
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["overall"]["vendor_model_charge"]["known_usd"] == "0.000123"
    assert report.main([str(source), "--output", str(source)]) == 2
    assert "must not overwrite" in capsys.readouterr().err
