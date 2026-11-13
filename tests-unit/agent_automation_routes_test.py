"""CPU-only API smoke test with ComfyUI engine/queue boundaries mocked.

Exercises the actual aiohttp routes, token gate, plan confirmation, queuing,
history status and cancellation without loading GPU models or ComfyUI itself.
"""

import asyncio
import importlib.util
import shutil
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

    nodes_module = ModuleType("nodes")
    nodes_module.NODE_CLASS_MAPPINGS = {}

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
        "nodes": nodes_module,
        name: module,
    }):
        spec.loader.exec_module(module)
    module._test_nodes = nodes_module
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
            assert res.status == 200  # idempotent retry, no duplicate job
            assert (await res.json())["run_id"] == data["run_id"]
            assert len(server.prompt_queue.items) == 1

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



def test_multiturn_session_batch_and_completion(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    extension, server, _ = load_extension()

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer " + TOKEN}
        try:
            base = "/comfy-automation/v1"
            first = await client.post(base + "/chat", headers=headers,
                                      json={"message": "Biển Quảng Ninh"})
            first_data = await first.json()
            session_id = first_data["session_id"]
            second = await client.post(base + "/chat", headers=headers, json={
                "session_id": session_id, "message": "Bầu trời xanh", "count": 3,
            })
            second_data = await second.json()
            assert second_data["session_id"] == session_id
            assert second_data["plan"]["count"] == 3
            assert len(extension._sessions[session_id]["messages"]) == 4

            queued = await client.post(base + "/runs", headers=headers,
                                       json={"plan_id": second_data["plan_id"]})
            assert queued.status == 201
            run = await queued.json()
            assert len(run["prompt_ids"]) == 3
            assert len(server.prompt_queue.items) == 3
            assert [item[2]["3"]["inputs"]["seed"] for item in server.prompt_queue.items] == [42, 43, 44]

            server.prompt_queue.items.clear()

            def history(prompt_id=None):
                return {prompt_id: {
                    "status": {"status_str": "success"},
                    "outputs": {"9": {"images": [{
                        "filename": "generated.png", "subfolder": "", "type": "output",
                    }]}},
                }}

            server.prompt_queue.get_history = history
            status = await client.get(base + "/runs/" + run["run_id"], headers=headers)
            jobs = (await status.json())["jobs"]
            assert all(item["status"] == "completed" for item in jobs)
            assert jobs[0]["outputs"]["9"]["images"][0]["filename"] == "generated.png"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_changed_template_rejected_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    extension, server, _ = load_extension()
    for name in ("text-to-image.json", "text-to-image.api.json"):
        shutil.copyfile(extension._registry.directory / name, tmp_path / name)
    extension._registry = extension.Registry(tmp_path)

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer " + TOKEN}
        try:
            base = "/comfy-automation/v1"
            res = await client.post(base + "/chat", headers=headers,
                                    json={"message": "Một ngọn núi"})
            plan_id = (await res.json())["plan_id"]
            graph = tmp_path / "text-to-image.api.json"
            graph.write_text(graph.read_text() + "\n", encoding="utf-8")
            res = await client.post(base + "/runs", headers=headers, json={"plan_id": plan_id})
            assert res.status == 409
            assert not server.prompt_queue.items
        finally:
            await client.close()

    asyncio.run(scenario())


def test_confirm_is_idempotent_under_concurrency(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    _, server, _ = load_extension()

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer " + TOKEN}
        try:
            base = "/comfy-automation/v1"
            plan = await client.post(base + "/chat", headers=headers,
                                     json={"message": "Cầu cảng"})
            plan_id = (await plan.json())["plan_id"]
            first, second = await asyncio.gather(
                client.post(base + "/runs", headers=headers, json={"plan_id": plan_id}),
                client.post(base + "/runs", headers=headers, json={"plan_id": plan_id}),
            )
            assert sorted([first.status, second.status]) == [200, 201]
            ids = [(await item.json())["run_id"] for item in (first, second)]
            assert ids[0] == ids[1]
            assert len(server.prompt_queue.items) == 1
        finally:
            await client.close()

    asyncio.run(scenario())


def test_queue_saturation_prevents_submission(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    extension, server, _ = load_extension()

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer " + TOKEN}
        try:
            base = "/comfy-automation/v1"
            plan = await client.post(base + "/chat", headers=headers,
                                     json={"message": "Phong cảnh"})
            plan_id = (await plan.json())["plan_id"]
            server.prompt_queue.items = [(0, str(i), {}, {}, [], {}) for i in range(extension._MAX_QUEUE)]
            res = await client.post(base + "/runs", headers=headers, json={"plan_id": plan_id})
            assert res.status == 429
            assert len(server.prompt_queue.items) == extension._MAX_QUEUE
        finally:
            await client.close()

    asyncio.run(scenario())


def test_partial_dispatch_failure_is_observable_and_compensated(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    _, server, _ = load_extension()
    original_put = server.prompt_queue.put

    def fail_after_accept(item):
        original_put(item)
        raise RuntimeError("Queue store unavailable")

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer " + TOKEN}
        try:
            base = "/comfy-automation/v1"
            plan = await client.post(base + "/chat", headers=headers,
                                     json={"message": "Bãi biển"})
            plan_id = (await plan.json())["plan_id"]
            server.prompt_queue.put = fail_after_accept
            res = await client.post(base + "/runs", headers=headers, json={"plan_id": plan_id})
            assert res.status == 503
            result = await res.json()
            assert result["run_id"]
            assert not server.prompt_queue.items
            state = await client.get(base + "/runs/" + result["run_id"], headers=headers)
            assert (await state.json())["jobs"][0]["status"] == "cancelled"
            repeat = await client.post(base + "/runs", headers=headers, json={"plan_id": plan_id})
            assert repeat.status == 200
            assert (await repeat.json())["run_id"] == result["run_id"]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_invalid_count_and_oversized_payload(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    _, server, _ = load_extension()

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer " + TOKEN}
        try:
            base = "/comfy-automation/v1"
            bad = await client.post(base + "/chat", headers=headers,
                                    json={"message": "Ảnh", "count": True})
            assert bad.status == 400
            too_big = await client.post(base + "/chat", headers=headers,
                                        data="x" * 18000)
            assert too_big.status == 413
        finally:
            await client.close()

    asyncio.run(scenario())



def test_partner_api_nodes_are_blocked_before_queue(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    monkeypatch.delenv("COMFY_AUTOMATION_LLM_MODEL", raising=False)
    extension, server, _ = load_extension()

    class PaidNode:
        @classmethod
        def GET_SCHEMA(cls):
            return SimpleNamespace(is_api_node=True)

    extension._test_nodes.NODE_CLASS_MAPPINGS["SaveImage"] = PaidNode

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer " + TOKEN}
        try:
            base = "/comfy-automation/v1"
            plan = await client.post(base + "/chat", headers=headers,
                                     json={"message": "Tạo ảnh"})
            plan_id = (await plan.json())["plan_id"]
            res = await client.post(base + "/runs", headers=headers, json={"plan_id": plan_id})
            assert res.status == 403
            assert not server.prompt_queue.items
        finally:
            await client.close()

    asyncio.run(scenario())


def test_machine_readable_openapi_is_authenticated(monkeypatch):
    monkeypatch.setenv("COMFY_AUTOMATION_TOKEN", TOKEN)
    extension, server, _ = load_extension()

    async def scenario():
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            base = "/comfy-automation/v1/openapi.json"
            denied = await client.get(base)
            assert denied.status == 401
            allowed = await client.get(base, headers={"Authorization": "Bearer " + TOKEN})
            assert allowed.status == 200
            schema = await allowed.json()
            assert schema["openapi"] == "3.1.0"
            assert "confirmWorkflowRun" == schema["paths"]["/comfy-automation/v1/runs"]["post"]["operationId"]
        finally:
            await client.close()

    asyncio.run(scenario())
