# ComfyUI Chat Automation (optional custom extension)

This directory adds an authenticated Vietnamese chat panel and a safe
chat-to-ComfyUI workflow execution path. It is separate from the official
optional comfy-agent host (--enable-agent) already present in this fork.
Neither the model engine nor the built-in /prompt routes are modified.

## Features

- Chat panel appears at the bottom-right of the ComfyUI frontend.
- Use an OpenAI-compatible LLM to plan work from natural language, or select
  a registered workflow and use free-text prompting without an LLM.
- Plans reference only trusted local templates. No prompt can upload Python,
  install packages, modify nodes/connections, or execute a shell command.
- Confirmation is always required before queueing.
- Uses ComfyUI's existing prompt validation, execution queue, history, and
  job-specific cancellation.
- Supports a batch of 1 to 8 jobs with sequential seeds.
- Runs are in memory, so execution metadata does not survive restart.
  ComfyUI's native history remains the source of truth.

## Enable

This extension is bundled as a custom node. Install the fork normally, ensure
custom nodes are enabled, and restart ComfyUI. No additional Python dependency
is needed beyond ComfyUI's existing aiohttp and PyTorch stack.

Before starting ComfyUI, set a long, random token:

Linux / macOS:

    export COMFY_AUTOMATION_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    python main.py

Windows PowerShell:

    $env:COMFY_AUTOMATION_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
    python main.py

The token must have at least 24 characters. Enter it in the panel on every
page load. It is kept only in browser memory (not persisted in localStorage).

The existing official agent host can be enabled independently:

    python -m pip install --pre comfy-agent
    python main.py --enable-agent

The two features do not share chat sessions or API configuration. Do not
assume the official agent package is installed because this extension loads.

## Configure an LLM planner (optional)

Set all three variables before running ComfyUI:

    COMFY_AUTOMATION_LLM_URL=https://your-provider.example/v1/chat/completions
    COMFY_AUTOMATION_LLM_MODEL=your-supported-model
    COMFY_AUTOMATION_LLM_API_KEY=your-secret

For a local OpenAI-compatible endpoint such as Ollama, an HTTP loopback URL
is allowed, for example:

    COMFY_AUTOMATION_LLM_URL=http://127.0.0.1:11434/v1/chat/completions

The LLM sends only the message and the allowed workflow descriptions to the
configured provider. It never sends source images or complete workflow graphs.
Without an LLM, select a workflow in the UI: the message is inserted into its
declared prompt field. When exactly one workflow exists, selection is optional.

## Register and run workflows

Templates live in the trusted local templates/ directory. A pair of files
is required for each workflow:

- ID.json: admin-maintained manifest with name, description and editable
  scalar input declarations (node_id, field, type, optional min, max,
  max_length, choices).
- ID.api.json: ComfyUI API-format workflow graph exported via the UI.

The included text-to-image is a starter template derived from core node
types and uses the first checkpoint in ComfyUI's checkpoint directory. Review
the API graph and replace __AUTO_CHECKPOINT__ with a specific installed
checkpoint when production consistency matters. Models, precision, memory and
node compatibility still depend on the local installation.

You may add templates for upscale, image-to-image, video, audio and other tasks
by exporting working API graphs and declaring only the inputs that chat may
edit. Use unique lowercase template IDs (letters, digits, hyphen, underscore).
Never let end users modify the trusted templates directory.

## API contract

All routes require Authorization: Bearer YOUR_COMFY_AUTOMATION_TOKEN. If the
token is not configured, requests fail closed with HTTP 503.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /comfy-automation/v1/workflows | Approved workflow list |
| POST | /comfy-automation/v1/chat | Plan from message, optional workflow_id |
| POST | /comfy-automation/v1/runs | Confirm and queue a previously returned plan_id |
| GET | /comfy-automation/v1/runs/{run_id} | Per-job state and output metadata |
| POST | /comfy-automation/v1/runs/{run_id}/cancel | Cancel only this run |

Plans expire after 15 minutes and may be confirmed once. Chat requests cannot
supply a raw graph or URL. Up to 8 prompt graphs are validated before any are
enqueued. These endpoints assume single-process ComfyUI, matching its current
in-process queue and state ownership.

## Operational security and limits

- ComfyUI has historically been a local-first tool. Never expose the raw
  ComfyUI port on the public Internet; use an authenticated HTTPS reverse proxy
  and network access restrictions for remote use.
- The automation token is shared within one process, not a multi-tenant
  authorization system. Do not use this as a public hosted agent service.
- Custom nodes can execute code. Only install trusted packages; repository
  governance and node allowlisting still apply to this extension.
- Template administrators control which nodes are present in each graph; a
  registered graph may still have network or filesystem side effects via
  third-party nodes.
- Provider costs, content policy, media review and GPU quotas are external
  operational responsibilities. The batch limit of 8 is not a GPU budget.
- No automatic recurring scheduler, durable chat history, or multi-user RBAC
  is claimed in this initial integration.

## Verify

Run the fast, GPU-independent tests from the repository root:

    python -m pytest -q tests-unit/agent_automation_test.py

Then check manually on a machine with ComfyUI and an installed checkpoint:

1. Restart and open the UI; verify Chat Automation button appears.
2. Enter token, load workflows, and request an image in Vietnamese.
3. Inspect the proposed plan; deliberately decline once (nothing queued).
4. Confirm and verify ComfyUI queue/history and image in output/.
5. Test invalid token, missing checkpoint, malformed plan and cancellation.
6. If configured, test an LLM's multi-template selection and confirm the
   returned plan cannot alter undeclared node fields.

The code is an initial safe integration and requires GPU/runtime validation
before production sign-off.
