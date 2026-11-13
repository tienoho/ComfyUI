"""Native ComfyUI extension: authenticated chat-to-workflow automation.

The existing comfy-agent host remains untouched. This extension does not
install models, accept executable graphs from chat, or expose a shell.
"""

from __future__ import annotations

import hmac
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
_pending: dict[str, tuple[float, dict]] = {}
_runs: dict[str, dict] = {}
_logger = logging.getLogger(__name__)
_routes = PromptServer.instance.routes


def _authenticate(request):
    expected = os.environ.get("COMFY_AUTOMATION_TOKEN", "")
    if len(expected) < 24:
        raise web.HTTPServiceUnavailable(text="Set COMFY_AUTOMATION_TOKEN (24+ characters)")
    provided = request.headers.get("Authorization", "")
    if not hmac.compare_digest(provided, "Bearer " + expected):
        raise web.HTTPUnauthorized(text="Invalid automation token")


def _cleanup():
    now = time.time()
    for key, (expiry, _) in list(_pending.items()):
        if expiry < now:
            del _pending[key]
    for key, item in list(_runs.items()):
        if item["created_at"] < now - 86400:
            del _runs[key]


async def _read_json(request):
    if request.content_length is not None and request.content_length > 16384:
        raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=request.content_length)
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError) as error:
        raise web.HTTPBadRequest(text="Invalid JSON request") from error
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Expected JSON object")
    return data


def _error(error: PlanError):
    return web.json_response({"error": str(error)}, status=400)


@_routes.get("/tcg-automation/v1/workflows")
async def automation_workflows(request):
    _authenticate(request)
    return web.json_response({"workflows": _registry.list(),
                              "llm_configured": bool(os.getenv("COMFY_AUTOMATION_LLM_MODEL") and
                                                     os.getenv("COMFY_AUTOMATION_LLM_URL"))})


@_routes.post("/tcg-automation/v1/chat")
async def automation_chat(request):
    _authenticate(request)
    data = await _read_json(request)
    if set(data) - {"message", "workflow_id"}:
        raise web.HTTPBadRequest(text="Unsupported request fields")
    try:
        plan = await create_plan(_registry, data.get("message"), data.get("workflow_id"))
    except PlanError as error:
        return _error(error)
    _cleanup()
    plan_id = str(uuid.uuid4())
    _pending[plan_id] = (time.time() + 900, plan)
    return web.json_response({"plan_id": plan_id, "plan": plan,
                              "message": "Review the plan and confirm to queue execution."})


@_routes.post("/tcg-automation/v1/runs")
async def automation_run(request):
    _authenticate(request)
    data = await _read_json(request)
    if set(data) != {"plan_id"} or not isinstance(data.get("plan_id"), str):
        raise web.HTTPBadRequest(text="Only plan_id is accepted")
    _cleanup()
    # One-time plans prevent accidental repeated executions.
    pending = _pending.pop(data["plan_id"], None)
    if pending is None or pending[0] < time.time():
        raise web.HTTPBadRequest(text="Plan expired or already used")
    plan = pending[1]
    try:
        graphs = _registry.build_graphs(plan, folder_paths.get_filename_list("checkpoints"))
    except PlanError as error:
        return _error(error)

    server = PromptServer.instance
    prepared = []
    for graph in graphs:
        prompt_id = str(uuid.uuid4())
        server.node_replace_manager.apply_replacements(graph)
        valid = await execution.validate_prompt(prompt_id, graph, None)
        if not valid[0]:
            return web.json_response({"error": "ComfyUI rejected the workflow",
                                      "details": valid[1], "node_errors": valid[3]}, status=400)
        prepared.append((prompt_id, graph, valid[2]))

    # Validation of every item happens before any queue write.
    for prompt_id, graph, outputs in prepared:
        number = server.number
        server.number += 1
        extra_data = {"create_time": int(time.time() * 1000),
                      "comfy_usage_source": "comfy-automation"}
        server.prompt_queue.put((number, prompt_id, graph, extra_data, outputs, {}))

    run_id = str(uuid.uuid4())
    ids = [item[0] for item in prepared]
    _runs[run_id] = {"created_at": time.time(), "workflow_id": plan["workflow_id"],
                     "prompt_ids": ids, "cancelled_ids": []}
    _logger.info("Automation queued run_id=%s workflow_id=%s jobs=%d",
                 run_id, plan["workflow_id"], len(ids))
    return web.json_response({"run_id": run_id, "prompt_ids": ids}, status=201)


def _lookup_run(run_id):
    try:
        uuid.UUID(run_id)
    except (ValueError, AttributeError):
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
    for prompt_id in run["prompt_ids"]:
        history = server.prompt_queue.get_history(prompt_id=prompt_id)
        if prompt_id in run["cancelled_ids"]:
            status = "cancel_requested"
        elif prompt_id in running_ids:
            status = "running"
        elif prompt_id in queued_ids:
            status = "queued"
        elif prompt_id in history:
            record = history[prompt_id]
            status = record.get("status", {}).get("status_str", "completed")
        else:
            status = "unknown"
        output = history.get(prompt_id, {}).get("outputs", {})
        jobs.append({"prompt_id": prompt_id, "status": status, "outputs": output})
    return jobs


@_routes.get("/tcg-automation/v1/runs/{run_id}")
async def automation_run_status(request):
    _authenticate(request)
    _cleanup()
    run = _lookup_run(request.match_info["run_id"])
    return web.json_response({"run_id": request.match_info["run_id"],
                              "workflow_id": run["workflow_id"], "jobs": _run_status(run)})


@_routes.post("/tcg-automation/v1/runs/{run_id}/cancel")
async def automation_cancel(request):
    _authenticate(request)
    run = _lookup_run(request.match_info["run_id"])
    queue = PromptServer.instance.prompt_queue
    cancelled = []
    for prompt_id in run["prompt_ids"]:
        if queue.interrupt_if_running(prompt_id) or queue.delete_queue_item(
                lambda item, target=prompt_id: item[1] == target):
            cancelled.append(prompt_id)
    run["cancelled_ids"].extend(x for x in cancelled if x not in run["cancelled_ids"])
    return web.json_response({"cancelled_prompt_ids": cancelled})
