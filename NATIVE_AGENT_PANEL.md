# ComfyUI Native Agent Panel (single-agent integration)

## Architecture decision

**Use the official Comfy Agent Panel as the only in-app chat interface.**
The experimental custom-node Chat Automation panel, its LLM planner, API
routes, separate chat sessions, template registry, queue wrapper and related
tests have been deleted from this draft branch. Nothing from that experiment
was ever merged into `master`.

Existing first-party components are already integrated in this fork:

| Owner | Existing implementation |
| --- | --- |
| ComfyUI backend | `app/agent_host.py`: optional `comfyui.agent` v1 entry point |
| Startup/shutdown | `main.py --enable-agent`, `comfy/cli_args.py` |
| Same-origin API | Native host mounts `/api/agent` and `/api/workflows` |
| Readiness | `GET /features` includes `agent.state`; `agent_status` on `/ws` |
| Native frontend | Official Agent Panel, auth, consent, conversation and canvas sync |
| Execution | ComfyUI's own node graph, validation, queue, history and outputs |

The native Agent Panel uses `/api/agent/events` for conversation events and
CRDT graph updates; these are not ordinary messages on ComfyUI's `/ws`.
No replacement message bus, WebSocket proxy, chat panel or run loop should
be implemented in this repository merely to expose the official agent.

## Enable the actual official package

In the **same Python environment** where ComfyUI runs:

```bash
python -m pip install --pre -U "comfy-agent[go-nonfree]"
python main.py --enable-agent
```

Alternatively, the bundled agent version can be installed without the extra:

```bash
python -m pip install --pre -U comfy-agent
python main.py --enable-agent
```

Both commands target the official pre-release package. The latest verified
release during this review (13 November 2026) is **0.1.0rc8**, published
6 November 2026. For a reproducible UAT environment, pin
`comfy-agent==0.1.0rc8` and the matching `go-nonfree` extra rather than
silently pulling a future pre-release.

This fork already pins `comfyui-frontend-package==1.59.1` in
`requirements.txt`, matching the stable frontend release checked during
the review. **Do not** install a second UI extension or downgrade the
frontend merely to load the official Agent Panel. The agent is still
pre-release: verify its platform and dependency licenses before deployment.

Without the `go-nonfree` extra, some Comfy Agent actions depend on recent
`comfy-cli`. The upstream release notes specify `comfy-cli>=1.22.0` for
bypassing/muting a node and `>=1.24.0` for running workflows as the
current ComfyUI tab. Prefer the vendor-tested extra and verify those behaviors
on your own environment.

Open the regular ComfyUI page, click **Agent**, sign in if prompted, and
accept **Start using Comfy Agent** for the current account/workspace. Login
and consent are owned by the first-party panel; neither should be bypassed.

## Check the native host/route

Run from the repository root in another terminal:

```bash
python script_examples/check_native_agent.py --wait-seconds 90
```

For a stricter **authenticated** readiness check, supply the token securely
through `COMFY_AGENT_PROBE_TOKEN` (never as a command-line argument) and run:

```bash
python script_examples/check_native_agent.py --wait-seconds 90 --require-authenticated
```

Use `--auth-header X-API-KEY` when the connected local session authenticates
with an API key rather than `Authorization: Bearer`. The probe never prints
the token or stores it in a file. The authenticated check requires an actual
`GET /api/agent/identity` HTTP 200 response with `user_id`. It also refuses
to send credentials to a non-loopback endpoint unless the operator supplies
both an HTTPS URL and `--allow-remote`. Do not use this flag with an
untrusted server; it intentionally enables credential transmission.

The checker reads only the server's `GET /features` response, then performs
an unauthenticated `GET /api/agent/identity` to verify the proxy is mounted.
It reports `ready` if the flag is ready and the identity route returns
401/403 (authentication required), or HTTP 200 **with a valid JSON identity
containing a non-empty `user_id`**. A static HTML fallback returning HTTP
200 is explicitly rejected. This verifies route mounting, not the Agent's
ability to handle a real conversation.

Unauthorized is **not** proof that the signed-in user can send messages;
the next test must be in the UI.

Check results:

- `disabled`: restart with `--enable-agent`.
- `failed/package_missing`: install the package in the correct environment.
- `failed/outdated`: upgrade to a compatible v1 agent host.
- `starting`: inspect startup and child process logs.
- `ready`: finish normal first-party login and consent.

Automated probe contract tests:

```bash
python -m pip install pytest
python -m pytest -q tests-unit/native_agent_panel_check_test.py tests-unit/app_test/agent_host_test.py
```

The existing `tests-unit/app_test/agent_host_test.py` remains the reference
v1 host lifecycle/feature-flag test suite. It now also covers concurrent
status callbacks, late events during shutdown, a closed asyncio event loop,
and malformed provider status data. The probe is additional, not a second
agent runtime.

GitHub Actions checks that the published `comfy-agent==0.1.0rc8` wheel
still exposes `comfyui.agent` protocol `v1` and passes `pip check`. It also
starts the **actual fork** with the matching `go-nonfree` extra on CPU, probes
the same-origin identity endpoint, checks the installed frontend, verifies
that no duplicate Chat Automation extension is loaded, and negotiates the
native `/ws` feature flags across a fresh connection and reconnect. These
tests validate bootstrap and integration, **not** signed-in browser turns or
GPU generation.

## Recommended user journey

1. Start ComfyUI with `--enable-agent`.
2. Open the **official Agent Panel** (no Automation sidebar).
3. Ask Agent to find or reuse an installed/saved workflow or template.
4. Ask it to configure the *actual* canvas, inspect/repair nodes and validate.
5. Review the live graph and the model selection; confirm any paid-node usage
   with the product's official consent flow.
6. Run the graph through ComfyUI's existing execution system.
7. Refine the same workflow and inspect generated outputs in the native UI.

This avoids two different plans for the same canvas, duplicate execution
and two divergent chat histories. Workflows are the source of truth, not an
independent set of copied API graphs.

## Review findings and applied hardening

| Severity | Finding | Code-level outcome |
| --- | --- | --- |
| P0 | Concurrent package threads could race while publishing `agent_status` | The host serializes status reads and events with one lock, avoiding duplicate/out-of-order notifications |
| P0 | Package may emit callbacks after ComfyUI shutdown | `stop()` fences new events and status sender guards closed asyncio loops and shutdown races |
| P1 | An invalid status payload from a broken wheel could silently enter frontend flags | Invalid states are normalized to a failed/unknown feature state |
| P1 | An unrelated HTML fallback could respond 200 to the identity probe | The probe requires the documented `user_id` JSON shape for HTTP 200 |
| P1 | Pinned frontend and optional local wheel compatibility was not tested | CI checks the published wheel host protocol and tests a real CPU fork startup |
| P1 | Multiple `comfyui.agent` v1 entry points could be selected unpredictably | Fail closed with `ambiguous_host`, require operator to remove conflicting installs |
| P1 | Duplicate or late startup callbacks could spawn multiple Agent children | Only the first start may schedule work; executor scheduling errors publish `spawn_failed` |
| P1 | A 401/403 identity response is insufficient proof that an authenticated user is ready | Opt-in environment-token probe requires HTTP 200 and the documented `user_id` shape |
| P1 | Duplicate host entry point discovery could start the wrong agent distribution | Multiple `v1` providers are rejected; repeated `load()` reuses the same host |
| P1 | ComfyUI's executor could fail to schedule an optional Agent child | Emits `spawn_failed` rather than leaving the UI permanently on `starting` |
| P1 | Pre-release installation hints could select an incompatible placeholder package | Logs now use `python -m pip install --pre -U` in the active environment |

These changes are in `app/agent_host.py`, its existing test suite and the
small native probe. **No change is made to the official Agent Panel UI bundle,
private Agent endpoints, canvas CRDT logic, sign-in or user consent.**

## Model and cost observability (read-only, native-first)

**Source of truth:** The official Agent Panel is owned by the
`comfyui-frontend-package`, not a separate Chat Agent. The fork pins frontend
`1.59.1`. That released frontend can receive `agent_message_done.data.usage`
with token counts, but its stable contract does **not** promise a billable
`charge_micro_usd` or any model ID on every completion.

The upstream frontend development branch (checked 13 November 2026) has
advanced further: its generated `AgentTurnUsage` contract accepts optional
`charge_micro_usd`, in **millionths of USD**, only when a priced charge was
captured by the vendor. The official Panel's `agentEventTransport` already
consumes this field to show credits on assistant messages. It also declares
an `AgentModelReceipt` (model_id, selection source, profile revision) and
`AgentModelSelection` request schema. These are **not an approved stable
custom-extension API**, and a receipt is not yet guaranteed on the public
`agent_message_done` event. Do not duplicate the upstream credits UI,
write model switches directly to private endpoints, or guess the model from
`claude`/provider names.

### Offline aggregate report from authorized exports

A stdlib-only analyzer reads **existing** official `agent_message_done`
JSONL events or frontend development recordings with a `frame` wrapper:

```bash
python script_examples/agent_usage_report.py /path/to/authorized-agent-events.jsonl --pretty
python script_examples/agent_usage_report.py /path/to/authorized-agent-events.jsonl --output ./agent-usage-summary.json
```

This does not connect to `/api/agent/events`, read private Agent storage,
capture browser traffic, scrape the Panel, or require a new authentication
token. You must already have a legally authorized JSONL export; **the stable
Panel does not currently offer a built-in export workflow from this script**.
Do not export real customer prompts, tokens or identities into public CI logs.
The report excludes all raw prompt text, chat content, user identity and
thread/message IDs.

It reports exact token counts where present and a separately measured
`vendor_model_charge.known_micro_usd` and `known_usd` only when the vendor
actually returns a positive `charge_micro_usd`. Each metric has
`reported_turns` and `unknown_turns`; **an absent charge means unknown,
not $0 or free usage**. The total of known values is *not* necessarily the
complete amount, the amount charged to a Comfy workspace, or the invoice
total; use the vendor's first-party billing/usage logs for financial
reconciliation. Duplicate terminal events after reconnect are deduplicated
by the (thread_id, message_id) pair. Conflicting duplicates fail closed.

The summary groups by model only when a **valid, explicit v1 model receipt**
is present in the exported completion data. The current stable event schema
does not promise such a receipt, so the normal result is
`by_model.unknown`. The analyzer never invents a default model, maps
image-generation checkpoints to chat LLMs, or estimates token cost from
third-party pricing tables. No automatic live model/charge capture is claimed.

### When upstream selection support is stable

1. Confirm the shipped `comfyui-frontend-package`, `comfy-agent` wheel and
   authorized public model-selection/receipt contract match.
2. Reuse the official Panel's own model selector and per-turn credits display,
   not an injected DOM badge or an independent sidebar.
3. Verify every accepted turn receives the requested/effective model receipt,
   with source and profile revision. A missing receipt must be shown as unknown.
4. Verify debiting/credits against official billing records; distinguish
   model execution charge from purchase price or partner-node execution cost.
5. Add browser tests for unavailable model, workspace switch, reconnect,
   charge missing, and paid execution confirmation before enabling changes.

Until then, keep this as a read-only, offline diagnostic and do not alter
the vendor's model router or private Agent message protocol.

## What this does *not* implement

The `comfyui.agent` **v1** host contract exposes only:

- `register(app)`
- `start(comfy_url, comfy_path, logger, on_change)`
- `status()`
- `stop()`

There is **no verified public method** on that contract for registering
custom tools or arbitrary MCP servers inside the official Agent Panel.
Consequently this branch does not modify proprietary agent internals,
inject into its event stream, impersonate its login or claim that the
previous custom automation tools are attached to the panel.

For **external** coding assistants, Comfy Org maintains `comfy-mcp`. It is
a separate supported MCP process that drives comfy-cli and can submit,
monitor and inspect local jobs. It does **not** automatically become a tool
inside the official in-app Agent Panel. Scheduled unattended execution,
multi-tenant approvals, accounting and durable orchestration remain separate
operational requirements unless explicitly supported by a future agent API.

## Production acceptance checklist

- Confirm local `comfy-agent` wheel, `agent.state=ready` and same-origin
  authenticated routes on the actual target OS/architecture. Run the
  `--require-authenticated` probe on loopback with a test account if available.
- Complete first-party account/workspace consent and verify a real chat turn.
- Verify live canvas edits, graph validation, model loading and GPU generation.
- Verify results/outputs and state after refresh, network reconnect and
  agent restart.
- Do not expose the raw ComfyUI HTTP port publicly. Protect `/prompt`,
  `/queue`, `/upload` and other powerful core routes with ingress auth and
  network restrictions.
- Verify paid partner nodes cannot spend credits unexpectedly.
- Do not call a CPU-only startup or unit test an end-to-end GPU/Agent UAT.

## References

- First-party Agent Panel: https://github.com/Comfy-Org/ComfyUI_frontend/tree/main/src/workbench/extensions/agent
- Official pre-release wheel: https://pypi.org/project/comfy-agent/
- Agent Panel documentation: https://support.comfy.org/articles/4013072355-comfy-agent
- External Comfy MCP: https://github.com/Comfy-Org/comfy-mcp


## Windows PowerShell verification

```powershell
python -m pip install --pre -U "comfy-agent[go-nonfree]"
python -m pip check
python main.py --enable-agent
```

In a second PowerShell terminal:

```powershell
python script_examples/check_native_agent.py --wait-seconds 90
```

For an authenticated test, set the token using a secure local secret injection
mechanism instead of entering it into PowerShell history, then run:

```powershell
python script_examples/check_native_agent.py --require-authenticated --wait-seconds 90
```

After completing the smoke tests, manually verify sign-in, first-use consent,
chat follow-ups, model selection, canvas edits, result previews, WebSocket
reconnections and a ComfyUI restart. These flows are implemented in the
first-party frontend, not within this repository's small Agent host wrapper.

## Deployment gate and known limits

- The `comfy-agent` runtime is still a vendor **pre-release**. Run the
  actual wheel on every target OS you intend to support.
- A CI green check for host readiness or the HTTP identity route does not
  prove a real account can execute a graph, nor that GPU generation works.
- `comfyui-frontend-package==1.59.1` is the stable frontend package pinned
  in this fork. Frontend Agent Panel changes must land in
  `Comfy-Org/ComfyUI_frontend` or in an authorized fork; this backend PR does
  not patch the distributed frontend bundle.
- The official wheel's internal tool registration and runtime logic are not
  part of the public host v1 interface. Do not monkeypatch undocumented
  internal endpoints, storage or WebSocket frames.
- Do not expose `/prompt`, `/queue`, `/upload`, `/api/agent` or other
  ComfyUI routes on an unauthenticated public interface. Keep ComfyUI bound
  to loopback or behind a correctly configured authentication proxy.
- No production readiness claim is made until actual GPU/browser UAT,
  account-level permissions, model licensing and recovery tests pass.


## Agent Panel UAT scenarios on a real workstation

The following require an actual account, the official frontend and an installed
compatible model. Record the OS, ComfyUI commit, `comfy-agent` wheel version,
frontend version, GPU model/VRAM and a redacted copy of the logs.

| Case | Operation | Passing evidence |
| --- | --- | --- |
| A01 | Start without `--enable-agent` | Agent unavailable; native probe reports `disabled` |
| A02 | Enable Agent with package missing/incompatible | Clear `package_missing` or `outdated` status; ComfyUI itself still serves |
| A03 | Launch installed `comfy-agent` | `/features` is `ready`, first-party Agent button activates |
| A04 | Attempt to send before login/consent | Official sign-in/consent gate blocks the send; no silent tool execution |
| A05 | Sign in and accept first-party consent | One conversation in native Panel; correct account/workspace identity |
| A06 | Request an image workflow | Agent selects a compatible installed model and creates or edits the live canvas |
| A07 | Change prompt/steps/dimensions | Visible graph parameters match the user's instruction; no unrequested node deletion |
| A08 | Execute approved graph with an actual model | Native queue runs once; output appears in the correct ComfyUI tab |
| A09 | Introduce a missing model/node | Clear recovery guidance; no silent use of a different model |
| A10 | Cancel a running workflow | Only intended run is affected; status and resources recover |
| A11 | Interrupt/reconnect network while Panel is open | No duplicate chat turn or out-of-order canvas edits after reconnect |
| A12 | Reload browser and reselect saved workflow | Previous workflow/chat target stays correct; no writes to an unrelated tab |
| A13 | Restart ComfyUI with Agent active | Host stops, restarts and reconnects; old sockets are discarded |
| A14 | Change signed-in account/workspace | Identity and consent belong to the new account; no cross-account chat leakage |
| A15 | Open workflow with paid partner/API nodes | Official billing/consent gate controls paid execution |
| A16 | Verify UI at narrow viewport | Native Agent dock remains usable; no separate floating Automation overlay |

Do not mark this matrix complete based solely on GitHub Actions: an authenticated
Agent session and a real GPU workload are prerequisites for the relevant cases.
