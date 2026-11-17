# Google Colab GPU

Open [`ComfyUI_Colab_GPU_Native_Agent.ipynb`](./ComfyUI_Colab_GPU_Native_Agent.ipynb) in Google Colab, select a GPU runtime and run cells in order.

The notebook clones `tienoho/ComfyUI`, installs the repository dependencies and the reviewed optional `comfy-agent[go-nonfree]==0.1.0rc8` package, optionally copies user-owned models from Drive, starts `main.py --enable-agent` on loopback, exposes the UI using the session-scoped Colab port proxy and runs the native Agent readiness probe. No second Agent UI is created.

**Limitations:** GPU availability, disk space, model license, pre-release wheel compatibility, browser WebSocket behavior, native sign-in and Agent consent are not guaranteed on Colab. Validate with real runtime UAT. No public tunnel, unauthenticated exposure or private Agent protocol patches are provided. The notebook has not been executed on a live Colab GPU as part of this PR.
