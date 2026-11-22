"""Static regression coverage for the fork's Colab GPU notebook."""
from __future__ import annotations

import json
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "ComfyUI_Colab_GPU_Native_Agent.ipynb"


def test_colab_notebook_json_and_python_cells_compile():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), f"{NOTEBOOK.name}:cell-{index}", "exec")


def test_colab_starts_official_agent_with_local_proxy_only():
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    code = "\\n".join("".join(c["source"]) for c in cells if c["cell_type"] == "code")
    assert "REPO_REF = 'master'" in code
    assert "remote_head != local_head" in code or "local_head != remote_head" in code
    assert "'--enable-agent'" in code
    assert "'--listen','127.0.0.1'" in code
    assert "serve_kernel_port_as_window(8188)" in code
    assert "check_native_agent.py" in code
    assert "Port 8188 already in use" in code
    assert "ngrok.connect(" not in code
