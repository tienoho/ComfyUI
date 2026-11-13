"""Validated workflow templates and execution plans for the optional chat extension.

Only local, administrator-supplied API workflow files are executable. Model
responses may choose a template and edit declared scalar inputs; they cannot
add nodes, alter connections, install packages, or change filesystem paths.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path


class PlanError(ValueError):
    pass


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MAX_BATCH = 8


class Registry:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()

    def revision(self, workflow_id: str) -> str:
        """Pin the exact trusted manifest and API graph that were approved."""
        self._manifest(workflow_id)
        manifest_path = self.directory / (workflow_id + ".json")
        graph_path = self.directory / (workflow_id + ".api.json")
        if graph_path.is_symlink() or not graph_path.is_file():
            raise PlanError("Workflow API graph is missing or is a symlink")
        try:
            if manifest_path.stat().st_size > 65536 or graph_path.stat().st_size > 2097152:
                raise PlanError("Workflow template exceeds size limits")
            digest = hashlib.sha256()
            for path in (manifest_path, graph_path):
                digest.update(path.read_bytes())
                digest.update(b"\x00")
            return digest.hexdigest()
        except OSError as error:
            raise PlanError("Cannot read workflow template") from error

    def _manifest(self, workflow_id: str) -> dict:
        if not isinstance(workflow_id, str) or not _ID.fullmatch(workflow_id):
            raise PlanError("Invalid workflow ID")
        manifest_path = self.directory / (workflow_id + ".json")
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PlanError("Workflow is not registered")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise PlanError("Cannot load workflow manifest") from error
        if not isinstance(manifest, dict) or manifest.get("id") != workflow_id:
            raise PlanError("Invalid workflow manifest")
        if not isinstance(manifest.get("name"), str) or not isinstance(manifest.get("description"), str):
            raise PlanError("Workflow name and description are required")
        inputs = manifest.get("inputs")
        if not isinstance(inputs, dict) or not inputs:
            raise PlanError("Workflow requires declared editable inputs")
        for key, spec in inputs.items():
            if not isinstance(key, str) or not _ID.fullmatch(key) or not isinstance(spec, dict):
                raise PlanError("Invalid workflow input declaration")
            if not isinstance(spec.get("node_id"), str) or not isinstance(spec.get("field"), str):
                raise PlanError("Workflow input must target a node and field")
            if spec.get("type") not in ("string", "integer", "number", "boolean"):
                raise PlanError("Unsupported editable input type")
            if "multiple_of" in spec and (type(spec["multiple_of"]) is not int or spec["multiple_of"] <= 0):
                raise PlanError("Invalid input multiple_of")
            if "choices" in spec and (not isinstance(spec["choices"], list) or not spec["choices"]):
                raise PlanError("Invalid input choices")
        return manifest

    def list(self) -> list[dict]:
        items = []
        if not self.directory.is_dir():
            return items
        for path in sorted(self.directory.glob("*.json")):
            try:
                manifest = self._manifest(path.stem)
            except PlanError:
                continue
            items.append({
                "id": manifest["id"],
                "name": manifest["name"],
                "description": manifest["description"],
                "inputs": {k: {"type": v["type"], "description": v.get("description", "")}
                           for k, v in manifest["inputs"].items()},
            })
        return items

    def validate_plan(self, value: dict) -> dict:
        if not isinstance(value, dict) or set(value) - {"workflow_id", "parameters", "count"}:
            raise PlanError("Plan has unsupported fields")
        workflow_id = value.get("workflow_id")
        manifest = self._manifest(workflow_id)
        parameters = value.get("parameters")
        if not isinstance(parameters, dict):
            raise PlanError("Plan parameters must be an object")
        if set(parameters) - set(manifest["inputs"]):
            raise PlanError("Plan attempts to modify an undeclared input")
        count = value.get("count", 1)
        if type(count) is not int or not 1 <= count <= _MAX_BATCH:
            raise PlanError("Batch count must be between 1 and 8")
        normalized = {}
        for key, item in parameters.items():
            spec = manifest["inputs"][key]
            kind = spec["type"]
            if kind == "string":
                if not isinstance(item, str) or not item.strip():
                    raise PlanError(key + " must be non-empty text")
                if len(item) > int(spec.get("max_length", 4096)):
                    raise PlanError(key + " is too long")
            elif kind == "integer":
                if type(item) is not int:
                    raise PlanError(key + " must be an integer")
            elif kind == "number":
                if type(item) not in (int, float) or not math.isfinite(item):
                    raise PlanError(key + " must be a finite number")
            elif type(item) is not bool:
                raise PlanError(key + " must be boolean")
            if kind in ("integer", "number"):
                if "min" in spec and item < spec["min"]:
                    raise PlanError(key + " is below the minimum")
                if "max" in spec and item > spec["max"]:
                    raise PlanError(key + " is above the maximum")
            if "multiple_of" in spec and type(item) is int and item % spec["multiple_of"] != 0:
                raise PlanError(key + " must be a multiple of " + str(spec["multiple_of"]))
            if "choices" in spec and not any(type(item) is type(choice) and item == choice for choice in spec["choices"]):
                raise PlanError(key + " is not an allowed choice")
            normalized[key] = item
        return {"workflow_id": workflow_id, "parameters": normalized, "count": count}

    def build_graphs(self, plan: dict, checkpoints: list[str] | None = None) -> list[dict]:
        plan = self.validate_plan(plan)
        manifest = self._manifest(plan["workflow_id"])
        graph_path = self.directory / (plan["workflow_id"] + ".api.json")
        if graph_path.is_symlink():
            raise PlanError("Symlinked workflows are not permitted")
        if not graph_path.is_file():
            raise PlanError("Export this workflow in ComfyUI API format before running it")
        try:
            original = json.loads(graph_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise PlanError("Cannot read the API workflow") from error
        if not isinstance(original, dict) or not original or len(original) > 1024:
            raise PlanError("API workflow must contain 1-1024 nodes")
        for key, spec in manifest["inputs"].items():
            node = original.get(spec["node_id"])
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                raise PlanError("Declared input refers to a missing node")
            if spec["field"] not in node["inputs"]:
                raise PlanError("Declared input refers to a missing node field")
            if isinstance(node["inputs"][spec["field"]], list):
                raise PlanError("Editable inputs cannot overwrite node connections")

        graphs = []
        for index in range(plan["count"]):
            graph = copy.deepcopy(original)
            for node in graph.values():
                if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
                    for field, value in node["inputs"].items():
                        if field == "ckpt_name" and value == "__AUTO_CHECKPOINT__":
                            if not checkpoints:
                                raise PlanError("No checkpoint installed; configure the workflow model")
                            node["inputs"][field] = sorted(checkpoints)[0]
            for key, value in plan["parameters"].items():
                spec = manifest["inputs"][key]
                graph[spec["node_id"]]["inputs"][spec["field"]] = value
            # Vary the seed for batch images while preserving an explicit seed
            # when a single item is requested.
            if plan["count"] > 1 and "seed" in manifest["inputs"]:
                spec = manifest["inputs"]["seed"]
                node_input = graph[spec["node_id"]]["inputs"]
                current = node_input[spec["field"]]
                if type(current) is int:
                    maximum = spec.get("max", 2 ** 64 - 1)
                    minimum = spec.get("min", 0)
                    node_input[spec["field"]] = minimum + (current - minimum + index) % (maximum - minimum + 1)
            graphs.append(graph)
        return graphs
