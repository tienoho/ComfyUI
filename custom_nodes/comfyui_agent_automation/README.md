# ComfyUI Chat Automation (optional custom extension)

This directory adds an authenticated Vietnamese chat panel and a safe
chat-to-ComfyUI workflow execution path. It is separate from the official
optional comfy-agent host (--enable-agent) already present in this fork.
Neither the model engine nor the built-in /prompt routes are modified.

## Features

- Automation registers as a **native ComfyUI sidebar tab** when supported by
  the frontend. Older frontends get the original floating launcher as fallback.
  It is separate from the official Comfy Agent panel: it does not claim to
  mutate or save the active canvas graph.
- Use an OpenAI-compatible LLM to plan work from natural language, or select
  a registered workflow and use free-text prompting without an LLM.
- Plans reference only trusted local templates. No prompt can upload Python,
  install packages, modify nodes/connections, or execute a shell command.
- Confirmation is always required before queueing.
- Uses ComfyUI's existing prompt validation, execution queue, history, and
  job-specific cancellation.
- Supports a batch of 1 to 8 jobs with sequential seeds.
- Supports bounded multi-turn conversation context: last 12 messages, sessions
  expire after 60 minutes of inactivity (maximum 64 active sessions).
- Users can inspect and edit declared parameters and batch size in the panel,
  then explicitly save a new plan revision before confirmation. Saving
  invalidates the older unconfirmed plan ID; no raw graph is submitted.
- Templates may declare a dynamic `checkpoints` source. The model selector
  shows actual installed checkpoints; if there is more than one, automatic
  alphabetical selection is **forbidden**. The user must choose a model.
- Plans are pinned to SHA-256 of the trusted manifest and API graph. An edit
  after planning blocks execution until the user creates a fresh plan.
- Confirmation is idempotent: retrying the same plan ID returns the existing
  run ID and does not enqueue duplicate jobs.
- Messages in one session are serialized; two simultaneous chat requests cannot
  overwrite the session history. Queue admission is checked again after async
  ComfyUI validation, since native clients may enqueue in the meantime.
- Queue admission limit: total running + pending ComfyUI jobs + new jobs
  must not exceed 32. There can be at most 128 pending plans and 256 runs
  tracked by this host.
- All official partner/API nodes marked as such by ComfyUI's node schema are
  refused by the automation adapter, regardless of the trusted template.
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
If only one of COMFY_AUTOMATION_LLM_MODEL or COMFY_AUTOMATION_LLM_URL is set,
planning returns a clear error instead of silently switching to local mode.

Without an LLM, select a workflow in the UI: the message is inserted into its
declared prompt field. When exactly one workflow exists, selection is optional.
Without an LLM, chat history remains available to the server but **natural
language revisions are not interpreted**: the next message becomes the next
prompt. With a configured LLM, the last plan and bounded chat history are
included to support follow-up instructions. No LLM may execute a workflow.

## Register and run workflows

Templates live in the trusted local templates/ directory. A pair of files
is required for each workflow:

- ID.json: admin-maintained manifest with name, description and editable
  scalar input declarations (node_id, field, type, optional min, max,
  max_length, choices).
- ID.api.json: ComfyUI API-format workflow graph exported via the UI.

The included text-to-image is a starter template derived from core node
types. It auto-selects an installed checkpoint only when exactly **one**
checkpoint exists. With multiple installed checkpoints the user must select
a specific checkpoint from the Chat Automation panel. Review
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
| GET | /comfy-automation/v1/openapi.json | OpenAPI 3.1 contract for external agent tool adapters |
| POST | /comfy-automation/v1/chat | Plan from message, optional workflow_id, session_id, checkpoint and count |
| POST | /comfy-automation/v1/plans/{plan_id}/revise | Edit typed scalar inputs and/or batch size; returns a fresh plan ID without executing |
| POST | /comfy-automation/v1/runs | Confirm and queue a previously returned plan_id |
| GET | /comfy-automation/v1/runs/{run_id} | Per-job state and output metadata |
| POST | /comfy-automation/v1/runs/{run_id}/cancel | Cancel only this run |

Plans expire after 15 minutes. After `/plans/{plan_id}/revise`, the old
unconfirmed plan ID is invalidated, and the replacement has a new 15-minute
approval window. Revision parameters are a full replacement set of approved
scalar values, not an incremental patch. They may execute only once, but repeated
confirmation calls are safe and return the existing run ID (HTTP 200
instead of 201). After template changes, confirmation returns HTTP 409 and
requires re-planning. Chat requests cannot
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
- Official ComfyUI partner/API nodes carrying the is_api_node flag are blocked
  here even if an administrator accidentally registers one. Third-party custom
  nodes may still call external services or incur charges without advertising
  that flag. Audit the exact nodes in every approved template.
- Provider costs, content policy, media review and GPU quotas are external
  operational responsibilities. The batch limit of 8 and queue admission of
  32 are not GPU time/VRAM budgets.
- No automatic recurring scheduler, durable chat history, or multi-user RBAC
  is claimed in this initial integration.

## Verify

Run the fast, GPU-independent tests from the repository root:

    python -m pytest -q tests-unit/agent_automation_test.py tests-unit/agent_automation_routes_test.py

Then check manually on a machine with ComfyUI and an installed checkpoint:

1. Restart and open the UI; verify Chat Automation button appears.
2. Enter token, load workflows, and request an image in Vietnamese.
3. Inspect the proposed plan; deliberately decline once (nothing queued).
4. Confirm and verify ComfyUI queue/history and image in output/.
5. Test invalid token, missing checkpoint, malformed plan and cancellation.
6. If configured, test an LLM's multi-template selection and confirm the
   returned plan cannot alter undeclared node fields.

## Agent interoperability: reuse the official ecosystem

The optional official comfy-agent host and its `/api/agent` routes have
their own lifecycle and API. This extension **does not** automatically register
itself as a tool of that closed-package host. Do not claim integration until
its concrete tool registration interface has been tested against the installed
wheel.

Comfy-Org also maintains **comfy-mcp** for Claude Code, Cursor and other local
AI agents. For node/model discovery, UI-independent workflow execution and
external MCP clients, install and use the maintained first-party tool rather
than creating a second MCP implementation. The extension is complementary:
it adds human-approved, centrally allowlisted, bounded runs inside ComfyUI.

External agents requiring the stricter workflow-approval path can consume the
machine-readable local OpenAPI 3.1 document:

    GET /comfy-automation/v1/openapi.json

Supply the same Bearer token. The schema describes discovery, planning,
status, cancellation and approval routes. **Execution requires explicit
user confirmation; do not let an unattended LLM autonomously call the
confirmWorkflowRun operation.**

## Execution states and failure handling

- `queued` and `running` come from ComfyUI's existing queue; `completed`
  and `failed` come from its recorded job history.
- `cancel_requested` is not final. A job may still complete before interruption.
  `cancelled` means a cancellation was accepted and no final success is present.
- A partial queue dispatch failure returns HTTP 503 with a `run_id`, attempts
  best-effort targeted cancellation, and marks `dispatch_error`. The caller
  must inspect that run before deciding to issue a new plan.
- `unknown` means the engine has no visible queue/history record; it is not
  counted as successful. Manually cleared ComfyUI history can cause this.
- Run state and session context live in RAM; they are not durable after
  restart. Completed runs are retained for up to 24 hours after a terminal
  state is observed. Old unobserved runs are reaped only when their job IDs
  have left the active ComfyUI queue. Native ComfyUI history and output files
  remain independent.
- A single shared Bearer token is **not** a multi-user identity or authorization
  system. Use only within a trusted local environment.
- The token protects only this extension's /comfy-automation routes; it does
  NOT add authentication or restrictions to ComfyUI's existing raw /prompt,
  /queue, /history, /upload or other endpoints. Exposing the raw ComfyUI port
  would let callers bypass this workflow registry. Restrict the network,
  authenticate at a reverse proxy and/or use workspace governance controls.

## Verification limitations

The focused tests use a mock execution queue and do not prove successful GPU
inference. The inherited upstream test-launch workflow checks out Comfy-Org's
upstream repository rather than this fork. CI from that workflow is not
evidence of this extension's startup. Real server load, browser flows, model
compatibility, multi-user tenancy and GPU generation require separate testing.

The code is a hardened local-first integration but still requires real
GPU/runtime validation before production sign-off.

## Interaction acceptance criteria

1. The frontend registers the Automation tab in ComfyUI's native sidebar
   when `registerSidebarTab` is available; fallback launcher is used only
   on unsupported frontend builds.
2. Chat proposals are independent of the active graph. Users must not be
   led to believe a chat edits canvas nodes; first-party Comfy Agent is the
   preferred tool for approved graph editing.
3. The panel loads registered templates, installed checkpoints and input
   constraints directly from the host, not a hard-coded list of models.
4. The user can inspect and modify plan parameters without another LLM call.
   Changes stay local to the form until `Lưu chỉnh sửa` is explicitly used.
   Confirmation of unsaved edits is disabled.
5. Multiple checkpoints never silently select an arbitrary file. Uninstalled
   or unsupported selections are rejected by the host before queueing.
6. Concurrent requests in the same chat session maintain a consistent order
   and cannot replace each other's context.
7. Submission rechecks native queue capacity after node validation and
   never removes unrelated jobs during error compensation.
8. Repeated confirmation with the same plan ID never creates duplicate jobs.
9. Only the workflows and typed scalar parameters declared in the trusted
   manifest may be controlled. No shell, arbitrary graph edits or paid
   ComfyUI API nodes are exposed through Automation.
10. Real GPU output and visual UI/browser acceptance remain environment
    dependent and are not claimed as passing based on CPU-only tests.
