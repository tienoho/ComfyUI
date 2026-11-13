"""CPU-only contract tests for the administrator-controlled automation registry."""

import importlib.util
import json
from pathlib import Path

import pytest


ENGINE_PATH = Path(__file__).resolve().parents[1] / "custom_nodes" / "comfyui_agent_automation" / "engine.py"
SPEC = importlib.util.spec_from_file_location("comfy_automation_engine_tested", ENGINE_PATH)
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


@pytest.fixture
def registry(tmp_path):
    manifest = {
        "id": "image",
        "name": "Image",
        "description": "Text to image",
        "inputs": {
            "prompt": {"node_id": "6", "field": "text", "type": "string", "max_length": 200},
            "seed": {"node_id": "3", "field": "seed", "type": "integer", "min": 0, "max": 1000},
        },
    }
    graph = {
        "3": {"class_type": "Sampler", "inputs": {"seed": 8}},
        "4": {"class_type": "Loader", "inputs": {"ckpt_name": "__AUTO_CHECKPOINT__"}},
        "6": {"class_type": "Text", "inputs": {"text": "old"}},
    }
    (tmp_path / "image.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "image.api.json").write_text(json.dumps(graph), encoding="utf-8")
    return engine.Registry(tmp_path)


def test_discovery_exposes_only_declared_inputs(registry):
    items = registry.list()
    assert len(items) == 1
    assert items[0]["id"] == "image"
    assert set(items[0]["inputs"]) == {"prompt", "seed"}
    assert "class_type" not in str(items)


def test_build_graphs_isolated_and_seed_advances(registry):
    graphs = registry.build_graphs({
        "workflow_id": "image",
        "parameters": {"prompt": "Sunset", "seed": 14},
        "count": 3,
    }, checkpoints=["z.safetensors", "a.safetensors"])
    assert [x["3"]["inputs"]["seed"] for x in graphs] == [14, 15, 16]
    assert all(x["6"]["inputs"]["text"] == "Sunset" for x in graphs)
    assert all(x["4"]["inputs"]["ckpt_name"] == "a.safetensors" for x in graphs)
    graphs[0]["6"]["inputs"]["text"] = "changed"
    assert graphs[1]["6"]["inputs"]["text"] == "Sunset"


@pytest.mark.parametrize("plan", [
    {"workflow_id": "../image", "parameters": {"prompt": "a"}},
    {"workflow_id": "unknown", "parameters": {"prompt": "a"}},
    {"workflow_id": "image", "parameters": {"node_id": "evil"}},
    {"workflow_id": "image", "parameters": {"prompt": ""}},
    {"workflow_id": "image", "parameters": {"prompt": "A" * 201}},
    {"workflow_id": "image", "parameters": {"seed": True}},
    {"workflow_id": "image", "parameters": {"seed": 1001}},
    {"workflow_id": "image", "parameters": {}, "count": 9},
    {"workflow_id": "image", "parameters": {}, "count": False},
    {"workflow_id": "image", "parameters": {}, "count": 1, "graph": {}},
])
def test_rejects_untrusted_or_invalid_plans(registry, plan):
    with pytest.raises(engine.PlanError):
        registry.validate_plan(plan)


def test_missing_checkpoint_fails_before_queueing(registry):
    with pytest.raises(engine.PlanError, match="checkpoint"):
        registry.build_graphs({"workflow_id": "image", "parameters": {}, "count": 1}, checkpoints=[])


def test_does_not_overwrite_linked_node_inputs(registry):
    path = registry.directory / "image.api.json"
    graph = json.loads(path.read_text())
    graph["6"]["inputs"]["text"] = ["3", 0]
    path.write_text(json.dumps(graph))
    with pytest.raises(engine.PlanError, match="connections"):
        registry.build_graphs({"workflow_id": "image", "parameters": {"prompt": "no"}, "count": 1},
                              checkpoints=["x.safetensors"])


def test_does_not_read_graph_outside_template_directory(registry, tmp_path):
    outside = tmp_path.parent / "hidden.api.json"
    outside.write_text("{}")
    graph_path = registry.directory / "image.api.json"
    graph_path.unlink()
    graph_path.symlink_to(outside)
    with pytest.raises(engine.PlanError):
        registry.build_graphs({"workflow_id": "image", "parameters": {}, "count": 1},
                              checkpoints=["x.safetensors"])


def test_plan_parameters_cannot_change_graph_connections(registry):
    with pytest.raises(engine.PlanError):
        registry.validate_plan({
            "workflow_id": "image", "parameters": {"6": {"inputs": {"text": "bad"}}}, "count": 1,
        })



def test_template_revision_pins_both_manifest_and_graph(registry):
    original = registry.revision("image")
    assert len(original) == 64
    graph_path = registry.directory / "image.api.json"
    graph_path.write_text(graph_path.read_text() + " ", encoding="utf-8")
    assert registry.revision("image") != original
    after_graph = registry.revision("image")
    manifest_path = registry.directory / "image.json"
    manifest_path.write_text(manifest_path.read_text() + " ", encoding="utf-8")
    assert registry.revision("image") != after_graph


def test_template_revision_rejects_symlink(registry, tmp_path):
    graph_path = registry.directory / "image.api.json"
    other = tmp_path.parent / "other-graph.json"
    other.write_text(graph_path.read_text(), encoding="utf-8")
    graph_path.unlink()
    graph_path.symlink_to(other)
    with pytest.raises(engine.PlanError, match="symlink"):
        registry.revision("image")


def test_invalid_multiple_of_and_choice_types_rejected(registry):
    manifest_path = registry.directory / "image.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["inputs"]["seed"]["multiple_of"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(engine.PlanError, match="multiple_of"):
        registry.validate_plan({"workflow_id": "image", "parameters": {"seed": 2}})
    manifest["inputs"]["seed"]["multiple_of"] = 2
    manifest["inputs"]["seed"]["choices"] = [True, 4]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(engine.PlanError, match="allowed choice"):
        registry.validate_plan({"workflow_id": "image", "parameters": {"seed": 2}})
    assert registry.validate_plan({
        "workflow_id": "image", "parameters": {"seed": 4},
    })["parameters"]["seed"] == 4
