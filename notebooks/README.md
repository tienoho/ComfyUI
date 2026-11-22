# Google Colab GPU

Open [`ComfyUI_Colab_GPU_Native_Agent.ipynb`](./ComfyUI_Colab_GPU_Native_Agent.ipynb) in Google Colab, select a GPU runtime and run cells in order.

The notebook clones `tienoho/ComfyUI`, installs the repository dependencies and the reviewed optional `comfy-agent[go-nonfree]==0.1.0rc8` package, optionally copies user-owned models from Drive, starts `main.py --enable-agent` on loopback, exposes the UI using the session-scoped Colab port proxy and runs the native Agent readiness probe. No second Agent UI is created.

**Limitations:** GPU availability, disk space, model license, pre-release wheel compatibility, browser WebSocket behavior, native sign-in and Agent consent are not guaranteed on Colab. Validate with real runtime UAT. No public tunnel, unauthenticated exposure or private Agent protocol patches are provided. The notebook has not been executed on a live Colab GPU as part of this PR.

## Review hardening (17 November 2026)

The notebook clones `master` after the upstream integration, verifies an existing checkout's Git origin and branch, and refuses to start when port 8188 is already occupied. The initial HTTP `/features` check is **only** a server-startup check; the later native probe checks Agent readiness independently. An Agent probe failure is reported, not bypassed. The optional pre-release package is not guaranteed to install or authenticate on every Colab image.

For reproducible production-like UAT, pin `REPO_REF` to a reviewed immutable commit in a private copy of this notebook. On repeated runs, an existing checkout is checked against the remote; it is never silently overwritten.
