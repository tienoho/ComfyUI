"""Local-first chat automation adapter for ComfyUI's existing execution engine.

This extension deliberately does not replace the official comfy-agent host,
expose arbitrary graph mutation, install models, or execute shell commands.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from pathlib import Path

from aiohttp import web

import execution
import folder_paths
from server import PromptServer

from .engine import PlanError, Registry
from .planner import create_plan


WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}

_registry = Registry(Path(__file__).parent / "templates")
_pending: dict[str, dict] = {}
_confirmed: dict[str, str] = {}
_runs: dict[str, dict] = {}
_sessions: dict[str, dict] = {}
_confirm_lock = asyncio.Lock()
_planner_slots = asyncio.Semaphore(2)
_logger = logging.getLogger(__name__)
_routes = PromptServer.instance.routes

_PLAN_TTL = 900
_SESSION_TTL = 3600
_RUN_TTL = 86400
_MAX_PLANS = 128
_MAX_SESSIONS = 64
_MAX_QUEUE = 32
_MAX_RUNS = 256


def _authenticate(request):
    expected = os.environ.get("COMFY_AUTOMATION_TOKEN", "")
    if len(expected) < 24:
        raise web.HTTPServiceUnavailable(text="Set COMFY_AUTOMATION_TOKEN (24+ characters)")
    provided = request.headers.get("Authorization", "")
    if not hmac.compare_digest(provided, "Bearer " + expected):
        raise web.HTTPUnauthorized(text="Invalid automation token")


def _cleanup():
    now = time.monotonic()
    for plan_id, item in list(_pending.items()):
        if item["expires_at"] <= now:
            del _pending[plan_id]
    for session_id, item in list(_sessions.items()):
        if item["updated_at"] + _SESSION_TTL <= now:
            del _sessions[session_id]
    # Never forget a live run merely because a long-running generation crossed
    # the retention window. Results are kept 24 hours after the last observation
    # of terminal state; ComfyUI history retains execution records independently.
    for run_id, item in list(_runs.items()):
        if item["terminal_at"] is not None and item["terminal_at"] + _RUN_TTL <= now:
            del _runs[run_id]
    for plan_id, run_id in list(_confirmed.items()):
        if run_id not in _runs:
            del _confirmed[plan_id]


async def _read_json(request):
    if request.content_length is not None and request.content_length > 16384:
        raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=request.content_length)
    try:
        # Guard chunked requests as well as Content-Length bodies.
        raw = await request.content.read(16385)
        if len(raw) > 16384:
            raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=len(raw))
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise web.HTTPBadRequest(text="Invalid JSON request") from error
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Expected JSON object")
    return data


def _error(error: PlanError):
    return web.json_response({"error": str(error)}, status=400)


def _get_session(session_id):
    if not isinstance(session_id, str):
        raise web.HTTPBadRequest(text="Invalid session ID")
    session = _sessions.get(session_id)
    if session is None:
        raise web.HTTPNotFound(text="Session expired or not found")
    return session


def _create_session():
    if len(_sessions) >= _MAX_SESSIONS:
        raise web.HTTPTooManyRequests(text="Too many active chat sessions")
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "messages": [],
        "previous_plan": None,
        "updated_at": time.monotonic(),
    }
    return session_id, _sessions[session_id]


@_routes.get("/comfy-automation/v1/workflows")
async def automation_workflows(request):
    _authenticate(request)
    return web.json_response({
        "workflows": _registry.list(),
        "llm_configured": bool(os.getenv("COMFY_AUTOMATION_LLM_MODEL") and
                               os.getenv("COMFY_AUTOMATION_LLM_URL")),
    })


@_routes.post("/comfy-automation/v1/chat")
async def automation_chat(request):
    _authenticate(request)
    data = await _read_json(request)
    if set(data) - {"message", "workflow_id", "session_id", "count"}:
        raise web.HTTPBadRequest(text="Unsupported request fields")
    requested_count = data.get("count")
    if requested_count is not None and (type(requested_count) is not int or not 1 <= requested_count <= 8):
        raise web.HTTPBadRequest(text="Count must be an integer between 1 and 8")
    _cleanup()
    # A session is optional for backward compatibility with earlier API clients.
    supplied = data.get("session_id")
    if supplied is None:
        session_id, session = _create_session()
    else:
        session_id, session = supplied, _get_session(supplied)
    if len(_pending) >= _MAX_PLANS:
        raise web.HTTPTooManyRequests(text="Too many unconfirmed plans")

    try:
        # Limit concurrent provider calls and total provider-side spend per host.
        async with _planner_slots:
            plan = await create_plan(
                _registry, data.get("message"), data.get("workflow_id"),
                history=list(session["messages"]),
                previous_plan=session["previous_plan"],
            )
        if requested_count is not None:
            plan["count"] = requested_count
            plan = _registry.validate_plan(plan)
        revision = _registry.revision(plan["workflow_id"])
    except PlanError as error:
        return _error(error)

    plan_id = str(uuid.uuid4())
    _pending[plan_id] = {
        "expires_at": time.monotonic() + _PLAN_TTL,
        "plan": plan,
        "revision": revision,
        "session_id": session_id,
    }
    session["messages"].extend([
        {"role": "user", "content": data["message"]},
        {"role": "assistant", "content": _plan_summary(plan)},
    ])
    session["messages"] = session["messages"][-12:]
    session["previous_plan"] = plan
    session["updated_at"] = time.monotonic()
    return web.json_response({
        "session_id": session_id,
        "plan_id": plan_id,
        "plan": plan,
        "template_revision": revision,
        "expires_in_seconds": _PLAN_TTL,
        "message": "Review the proposed plan; execution requires explicit confirmation.",
    })


def _plan_summary(plan):
    return json.dumps(plan, ensure_ascii=False, separators=(",", ":"))


@_routes.post("/comfy-automation/v1/runs")
async def automation_run(request):
    _authenticate(request)
    data = await _read_json(request)
    if set(data) != {"plan_id"} or not isinstance(data.get("plan_id"), str):
        raise web.HTTPBadRequest(text="Only plan_id is accepted")
    plan_id = data["plan_id"]

    async with _confirm_lock:
        _cleanup()
        previous = _confirmed.get(plan_id)
        if previous:
            run = _runs[previous]
            return web.json_response({
                "run_id": previous, "prompt_ids": run["prompt_ids"], "replayed": True,
            }, status=200)

        proposed = _pending.get(plan_id)
        if proposed is None:
            raise web.HTTPBadRequest(text="Plan expired or not found")
        plan = proposed["plan"]

        try:
            if _registry.revision(plan["workflow_id"]) != proposed["revision"]:
                return web.json_response({"error": "Workflow changed after approval; re-plan required"}, status=409)
            graphs = _registry.build_graphs(plan, folder_paths.get_filename_list("checkpoints"))
        except PlanError as error:
            return _error(error)

        server = PromptServer.instance
        running, queued = server.prompt_queue.get_current_queue_volatile()
        if len(running) + len(queued) + len(graphs) > _MAX_QUEUE:
            raise web.HTTPTooManyRequests(text="ComfyUI queue capacity reached")
        if len(_runs) >= _MAX_RUNS:
            raise web.HTTPTooManyRequests(text="Automation run tracking is full")

        prepared = []
        for graph in graphs:
            prompt_id = str(uuid.uuid4())
            server.node_replace_manager.apply_replacements(graph)
            valid = await execution.validate_prompt(prompt_id, graph, None)
            if not valid[0]:
                return web.json_response({
                    "error": "ComfyUI rejected the workflow",
                    "details": valid[1], "node_errors": valid[3],
                }, status=400)
            prepared.append((prompt_id, graph, valid[2]))

        # Detect edits to a trusted template while validation was suspended.
        try:
            if _registry.revision(plan["workflow_id"]) != proposed["revision"]:
                return web.json_response({"error": "Workflow changed during validation; re-plan required"}, status=409)
        except PlanError as error:
            return _error(error)

        run_id = str(uuid.uuid4())
        now = time.monotonic()
        run = {
            "created_at": now,
            "updated_at": now,
            "terminal_at": None,
            "workflow_id": plan["workflow_id"],
            "template_revision": proposed["revision"],
            "prompt_ids": [item[0] for item in prepared],
            "submitted_ids": [],
            "cancelled_ids": [],
            "dispatch_error": False,
        }

        try:
            for prompt_id, graph, outputs in prepared:
                number = server.number
                server.number += 1
                extra_data = {
                    "create_time": int(time.time() * 1000),
                    "comfy_usage_source": "comfy-automation",
                }
                server.prompt_queue.put((number, prompt_id, graph, extra_data, outputs, {}))
                run["submitted_ids"].append(prompt_id)
        except Exception:
            # A queue implementation may accept an item then raise. Attempt to
            # remove *every* prepared ID, never touch unrelated ComfyUI jobs.
            _logger.exception("Automation dispatch failed run_id=%s", run_id)
            run["dispatch_error"] = True
            for prompt_id in run["prompt_ids"]:
                try:
                    removed = server.prompt_queue.delete_queue_item(
                        lambda item, target=prompt_id: item[1] == target)
                    interrupted = False if removed else server.prompt_queue.interrupt_if_running(prompt_id)
                    if removed or interrupted:
                        run["cancelled_ids"].append(prompt_id)
                except Exception:
                    _logger.exception("Could not compensate prompt_id=%s", prompt_id)
            _runs[run_id] = run
            _confirmed[plan_id] = run_id
            _pending.pop(plan_id)
            return web.json_response({
                "error": "Partial dispatch failure; inspect run status before retrying",
                "run_id": run_id, "prompt_ids": run["prompt_ids"],
            }, status=503)

        _runs[run_id] = run
        _confirmed[plan_id] = run_id
        _pending.pop(plan_id)
        _logger.info("Automation queued run_id=%s workflow_id=%s jobs=%d",
                     run_id, plan["workflow_id"], len(run["prompt_ids"]))
        return web.json_response({
            "run_id": run_id, "prompt_ids": run["prompt_ids"], "replayed": False,
        }, status=201)


def _lookup_run(run_id):
    try:
        canonical = str(uuid.UUID(run_id))
    except (ValueError, AttributeError, TypeError):
        raise web.HTTPNotFound() from None
    if canonical != run_id:
        raise web.HTTPNotFound()
    run = _runs.get(run_id)
    if run is None:
        raise web.HTTPNotFound(text="Run not found in this ComfyUI session")
    return run


def _run_status(run):
    server = PromptServer.instance
    running, queued = server.prompt_queue.get_current_queue_volatile()
    running_ids = {item[1] for item in running}
    queued_ids = {item[1] for item in queued}
    jobs = []
    terminal = True
    for prompt_id in run["prompt_ids"]:
        history = server.prompt_queue.get_history(prompt_id=prompt_id)
        record = history.get(prompt_id, {})
        status_record = record.get("status", {})
        raw_status = status_record.get("status_str") if isinstance(status_record, dict) else None
        if prompt_id in run["submitted_ids"] or prompt_id in running_ids or prompt_id in queued_ids:
            if raw_status in ("success", "completed"):
                status = "completed"
            elif raw_status in ("error", "failed"):
                status = "failed"
            elif raw_status in ("interrupted", "cancelled", "canceled"):
                status = "cancelled"
            elif prompt_id in running_ids:
                status = "cancel_requested" if prompt_id in run["cancelled_ids"] else "running"
            elif prompt_id in queued_ids:
                status = "queued"
            elif prompt_id in run["cancelled_ids"]:
                status = "cancelled"
            else:
                status = "unknown"
        else:
            status = "cancelled" if prompt_id in run["cancelled_ids"] else "not_submitted"
        if status in ("unknown", "queued", "running", "cancel_requested"):
            terminal = False
        jobs.append({
            "prompt_id": prompt_id, "status": status,
            "outputs": record.get("outputs", {}),
        })
    if terminal and run["terminal_at"] is None:
        run["terminal_at"] = time.monotonic()
    if not terminal:
        run["terminal_at"] = None
    run["updated_at"] = time.monotonic()
    return jobs


@_routes.get("/comfy-automation/v1/runs/{run_id}")
async def automation_run_status(request):
    _authenticate(request)
    _cleanup()
    run = _lookup_run(request.match_info["run_id"])
    return web.json_response({
        "run_id": request.match_info["run_id"],
        "workflow_id": run["workflow_id"],
        "dispatch_error": run["dispatch_error"],
        "jobs": _run_status(run),
    })


@_routes.post("/comfy-automation/v1/runs/{run_id}/cancel")
async def automation_cancel(request):
    _authenticate(request)
    run = _lookup_run(request.match_info["run_id"])
    queue = PromptServer.instance.prompt_queue
    cancelled = []
    for prompt_id in run["prompt_ids"]:
        if prompt_id not in run["submitted_ids"] or prompt_id in run["cancelled_ids"]:
            continue
        # Running and pending cancellation both target this exact job ID.
        if queue.interrupt_if_running(prompt_id) or queue.delete_queue_item(
                lambda item, target=prompt_id: item[1] == target):
            cancelled.append(prompt_id)
    run["cancelled_ids"].extend(cancelled)
    run["updated_at"] = time.monotonic()
    return web.json_response({"cancelled_prompt_ids": cancelled})
