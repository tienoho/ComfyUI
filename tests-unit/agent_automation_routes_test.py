"""CPU-only API smoke test with ComfyUI engine/queue boundaries mocked.

Exercises the actual aiohttp routes, token gate, plan confirmation, queuing,
history status and cancellation without loading GPU models or ComfyUI itself.
"""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


EXTENSION = Path(__file__).resolve().parents[1] / "custom_nodes" / "comfyui_agent_automation"
TOKEN = "safe-integration-test-token-32-characters"


class FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    def get_current_queue_volatile(self):
        return [], list(self.items)

    def get_history(self, prompt_id=None):
        return {}

    def interrupt_if_running(self, prompt_id):
        return False

    def delete_queue_item(self, predicate):
        previous = len(self.items)
        self.items = [item for item in self.items if not predicate(item)]
        return len(self.items) != previous


def load_extension():
    server_module = ModuleType("server")
    server = SimpleNamespace(
        routes=web.RouteTableDef(),
        number=0,
        prompt_queue=FakeQueue(),
        node_replace_manager=SimpleNamespace(apply_replacements=lambda graph: None),
    )
    server_module.PromptServer = type("PromptServer", (), {"instance": server})

    execution_module = ModuleType("execution")

    async def validate(prompt_id, graph, targets):
        return True, None, ["9"], {}

    execution_module.validate_prompt = validate

    folder_paths_module = ModuleType("folder_paths")
    folder_paths_module.get_filename_list = lambda kind: ["installed.safetensors"]

    name = "comfy_automation_routes_test_package"
    spec = importlib.util.spec_from_file_location(
        name, EXTENSION / "__init__.py", submodule_search_locations=[str(EXTENSION)]
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "server": server_module,
        "execution": execution_module,
        "folder_paths": folder_paths_module,
        name: module,
    }):
        spec.loader.exec_module(module)
    return module, server, folder_paths_module


def test_token_plan_queue_status_and_cancel(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_URL", raising=False)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    extension, server, folder_paths = load_extension()

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            base = "/comfy-automation/v1"
            res = await client.get(base + "/workflows")
            assert res.status == 401

            headers = {"Authorization": "Bearer " + TOKEN}
            res = await client.get(base + "/workflows", headers=headers)
            assert res.status == 200
            listing = await res.json()
            assert any(row["id"] == "text-to-image" for row in listing["workflows"])

            res = await client.post(
                base + "/chat", headers=headers,
                json={"message": "Vẽ hoàng hôn trên biển"},
            )
            assert res.status == 200
            plan_id = (await res.json())["plan_id"]
            assert len(server.prompt_queue.items) == 0  # confirmation is mandatory

            res = await client.post(base + "/runs", headers=headers, json={"plan_id": plan_id})
            assert res.status == 201
            data = await res.json()
            assert len(data["prompt_ids"]) == 1
            assert len(server.prompt_queue.items) == 1
            queued_graph = server.prompt_queue.items[0][2]
            assert queued_graph["6"]["inputs"]["text"] == "Vẽ hoàng hôn trên biển"
            assert queued_graph["4"]["inputs"]["ckpt_name"] == "installed.safetensors"

            res = await client.post(base + "/runs", headers=headers, json={"plan_id": plan_id})
            assert res.status == 400  # one-time plan token

            res = await client.get(base + "/runs/" + data["run_id"], headers=headers)
            assert (await res.json())["jobs"][0]["status"] == "queued"

            res = await client.post(base + "/runs/" + data["run_id"] + "/cancel",
                                    headers=headers, json={})
            assert res.status == 200
            assert (await res.json())["cancelled_prompt_ids"] == data["prompt_ids"]
            assert not server.prompt_queue.items

            folder_paths.get_filename_list = lambda kind: []
            res = await client.post(base + "/chat", headers=headers,
                                    json={"message": "Another image"})
            new_plan = (await res.json())["plan_id"]
            res = await client.post(base + "/runs", headers=headers, json={"plan_id": new_plan})
            assert res.status == 400
            assert "checkpoint" in (await res.json())["error"].lower()
        finally:
            await client.close()

    asyncio.run(scenario())


def test_missing_server_token_fails_closed(monkeypatch):
    monkeypatch.delenv("COMFY_AUTOMATION_TOKEN", raising=False)
    _, server, _ = load_extension()

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.get("/comfy-automation/v1/workflows")
            assert response.status == 503
        finally:
            await client.close()

    asyncio.run(scenario())
