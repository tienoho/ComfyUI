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

GitHub Actions also checks that the published `comfy-agent==0.1.0rc8` wheel
still exposes `comfyui.agent` protocol `v1`, with the expected host methods.
This is a **package contract smoke test**, not a browser/GPU end-to-end test.

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
| P1 | Pinned frontend and optional local wheel compatibility was not tested | CI now validates the published wheel host protocol separately from mock tests |

These changes are in `app/agent_host.py`, its existing test suite and the
small native probe. **No change is made to the official Agent Panel UI bundle,
private Agent endpoints, canvas CRDT logic, sign-in or user consent.**

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
  authenticated routes on the actual target OS/architecture.
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
