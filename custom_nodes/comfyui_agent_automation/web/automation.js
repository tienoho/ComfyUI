import { app } from "../../scripts/app.js";

app.registerExtension({
  name: "comfy.automation.chat",
  setup() {
    if (document.getElementById("comfy-automation-launcher")) return;

    const style = document.createElement("style");
    style.textContent = [
      "#comfy-automation-launcher{position:fixed;bottom:22px;right:22px;z-index:10050;background:#3269c9;color:#fff;border:0;border-radius:99px;padding:12px 17px;cursor:pointer;font-weight:700;box-shadow:0 6px 20px #0005}",
      "#comfy-automation-panel{position:fixed;bottom:76px;right:22px;z-index:10050;width:min(420px,calc(100vw - 28px));height:min(640px,calc(100vh - 100px));display:none;flex-direction:column;background:#191b21;color:#eee;border:1px solid #51545e;border-radius:14px;box-shadow:0 14px 45px #0008;font:14px system-ui,sans-serif}",
      "#comfy-automation-panel button{cursor:pointer;border-radius:7px;padding:7px 11px;background:#3269c9;color:white;border:0}",
      "#comfy-automation-panel.comfy-automation-sidebar{position:relative;inset:auto;width:100%;height:100%;max-width:none;max-height:none;border:0;border-radius:0;box-shadow:none;display:flex;min-height:0}",
      "#comfy-automation-panel.comfy-automation-sidebar #comfy-automation-header button{display:none}",
      "#comfy-automation-panel .comfy-automation-field{display:flex;flex-direction:column;gap:4px;margin:8px 0}",
      "#comfy-automation-panel .comfy-automation-plan{background:#252a35;border:1px solid #445171;padding:10px;border-radius:10px}",
      "#comfy-automation-panel .comfy-automation-plan label{font-size:12px;color:#c4cede}",
      "#comfy-automation-panel .comfy-automation-plan input,#comfy-automation-panel .comfy-automation-plan select,#comfy-automation-panel .comfy-automation-plan textarea{width:100%;box-sizing:border-box}",
      "#comfy-automation-panel button.comfy-automation-secondary{background:#3d424b}",

      "#comfy-automation-panel button:disabled{opacity:.5;cursor:not-allowed}",
      "#comfy-automation-panel input,#comfy-automation-panel select,#comfy-automation-panel textarea{color:#eee;background:#292d36;border:1px solid #51545e;border-radius:6px;padding:7px;min-width:0}",
      "#comfy-automation-panel textarea{resize:vertical;min-height:58px;width:100%;box-sizing:border-box}",
      "#comfy-automation-messages{overflow:auto;flex:1;padding:10px;display:flex;flex-direction:column;gap:8px}",
      ".comfy-automation-message{background:#2a2d35;padding:9px;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}",
      ".comfy-automation-user{background:#22375c}",
      "#comfy-automation-header,#comfy-automation-controls,#comfy-automation-compose{padding:10px;display:flex;gap:8px;border-bottom:1px solid #3c414a}",
      "#comfy-automation-controls{flex-direction:column}",
      "#comfy-automation-compose{flex-direction:column;border-bottom:0;border-top:1px solid #3c414a}",
      ".comfy-automation-row{display:flex;gap:8px;align-items:center}",
      ".comfy-automation-row>*{flex:1}",
    ].join("\n");
    document.head.appendChild(style);

    const el = (tag, props = {}, parent) => {
      const node = document.createElement(tag);
      Object.entries(props).forEach(([key, value]) => {
        if (key === "text") node.textContent = value;
        else node[key] = value;
      });
      if (parent) parent.appendChild(node);
      return node;
    };

    const launcher = el("button", { id: "comfy-automation-launcher", text: "⚡ Automation" }, document.body);
    const panel = el("section", { id: "comfy-automation-panel" }, document.body);
    panel.setAttribute("aria-label", "ComfyUI workflow automation");

    const header = el("div", { id: "comfy-automation-header" }, panel);
    const heading = el("strong", { text: "Automation · Workflow có kiểm soát" }, header);
    heading.style.flex = "1";
    const close = el("button", { text: "×", title: "Đóng" }, header);

    const controls = el("div", { id: "comfy-automation-controls" }, panel);
    const token = el("input", { type: "password", placeholder: "COMFY_AUTOMATION_TOKEN", autocomplete: "off" }, controls);
    token.setAttribute("aria-label", "Automation token");
    const workflow = el("select", {}, controls);
    workflow.setAttribute("aria-label", "Workflow");
    el("option", { value: "", text: "Tự động chọn workflow" }, workflow);

    const countRow = el("div", { className: "comfy-automation-row" }, controls);
    el("label", { text: "Số tác vụ (1–8; để trống cho AI chọn)" }, countRow);
    const count = el("input", { type: "number", min: "1", max: "8", step: "1", value: "", placeholder: "Mặc định / AI chọn" }, countRow);
    count.setAttribute("aria-label", "Batch count");

    const checkpoint = el("select", {}, controls);
    checkpoint.setAttribute("aria-label", "Checkpoint đang cài đặt");
    el("option", { value: "", text: "Tự chọn khi có một checkpoint" }, checkpoint);
    const canvasLabel = el("label", { className: "comfy-automation-row" }, controls);
    const includeCanvas = el("input", { type: "checkbox", checked: false }, canvasLabel);
    includeCanvas.style.flex = "0 0 auto";
    el("span", { text: "Dùng loại node trên canvas làm ngữ cảnh (không gửi ảnh hoặc prompt)" }, canvasLabel);
    const nativeStatus = el("div", { text: "Comfy Agent: đang kiểm tra trạng thái..." }, controls);

    const messages = el("div", { id: "comfy-automation-messages" }, panel);
    messages.setAttribute("role", "log");
    messages.setAttribute("aria-live", "polite");
    const compose = el("div", { id: "comfy-automation-compose" }, panel);
    const prompt = el("textarea", { placeholder: "Ví dụ: Tạo ảnh hoàng hôn tại vịnh Hạ Long..." }, compose);
    const row = el("div", { className: "comfy-automation-row" }, compose);
    const refresh = el("button", { text: "Tải workflow" }, row);
    const send = el("button", { text: "Lập kế hoạch" }, row);
    let busy = false;
    let sessionId = null;
    let currentConfirm = null;
    let llmConfigured = false;
    let workflowCatalog = [];
    let installedCheckpoints = [];
    const pollTimers = new Map();

    const message = (text, isUser = false) => {
      const bubble = el("div", { className: "comfy-automation-message" + (isUser ? " comfy-automation-user" : "") }, messages);
      bubble.textContent = text;
      messages.scrollTop = messages.scrollHeight;
      return bubble;
    };
    const setBusy = (state) => {
      busy = state;
      send.disabled = state;
      refresh.disabled = state;
    };

    const api = async (path, method = "GET", data = undefined) => {
      const headers = { Authorization: "Bearer " + token.value };
      if (data !== undefined) headers["Content-Type"] = "application/json";
      const res = await fetch("/comfy-automation/v1" + path, {
        method, headers, credentials: "same-origin",
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      });
      const responseText = await res.text();
      let body;
      try { body = JSON.parse(responseText); } catch { body = { error: responseText }; }
      if (!res.ok) {
        const error = new Error(body.error || "HTTP " + res.status);
        error.detail = body;
        error.status = res.status;
        throw error;
      }
      return body;
    };

    async function loadWorkflows() {
      setBusy(true);
      try {
        const data = await api("/workflows");
        llmConfigured = data.llm_configured;
        workflowCatalog = data.workflows;
        installedCheckpoints = data.checkpoints || [];
        workflow.replaceChildren();
        el("option", { value: "", text: "Tự động chọn workflow" }, workflow);
        for (const item of data.workflows) el("option", { value: item.id, text: item.name }, workflow);
        checkpoint.replaceChildren();
        el("option", { value: "", text: installedCheckpoints.length > 1 ?
          "Chọn checkpoint (bắt buộc khi có nhiều model)" : "Tự chọn khi có một checkpoint" }, checkpoint);
        for (const modelName of installedCheckpoints) {
          el("option", { value: modelName, text: modelName }, checkpoint);
        }
        try {
          const nativeFeatures = await fetch("/features", { credentials: "same-origin" });
          if (nativeFeatures.ok) {
            const flags = await nativeFeatures.json();
            nativeStatus.textContent = flags?.agent?.state === "ready" ?
              "Comfy Agent chính thức: sẵn sàng (chỉnh sửa canvas ở panel chính thức)." :
              "Comfy Agent chính thức: chưa sẵn sàng. Automation vẫn hoạt động độc lập.";
          } else {
            nativeStatus.textContent = "Không đọc được trạng thái Comfy Agent.";
          }
        } catch {
          nativeStatus.textContent = "Không đọc được trạng thái Comfy Agent.";
        }
        message("Có " + data.workflows.length + " workflow được duyệt và " +
          installedCheckpoints.length + " checkpoint đã cài." +
          (data.llm_configuration_error ? " Cấu hình LLM chưa đủ URL và model." :
          (data.llm_configured ? " AI planner đã sẵn sàng." :
          " Chế độ không LLM: tin nhắn sẽ được dùng như prompt.")));
      } catch (error) {
        message("Không tải được workflow: " + error.message);
      } finally { setBusy(false); }
    }

    async function followRun(runId) {
      if (pollTimers.has(runId)) return;
      const output = message("Đã xếp hàng. Run ID: " + runId);
      const resultLinks = el("div", { className: "comfy-automation-message" }, messages);
      const rendered = new Set();
      const cancel = el("button", { text: "Hủy run" }, messages);
      let failures = 0;
      let finished = false;
      let poll = null;

      const update = async () => {
        try {
          const data = await api("/runs/" + encodeURIComponent(runId));
          failures = 0;
          const lines = data.jobs.map((job, index) => {
            for (const nodeOutput of Object.values(job.outputs || {})) {
              if (!nodeOutput || typeof nodeOutput !== "object") continue;
              for (const kind of ["images", "gifs", "videos", "audio"]) {
                for (const asset of nodeOutput[kind] || []) {
                  if (!asset || typeof asset.filename !== "string" || asset.type !== "output") continue;
                  const key = [job.prompt_id, asset.filename, asset.subfolder || ""].join(":");
                  if (rendered.has(key)) continue;
                  rendered.add(key);
                  const query = new URLSearchParams({
                    filename: asset.filename, subfolder: asset.subfolder || "", type: "output",
                  });
                  const anchor = el("a", { href: "/view?" + query, text: asset.filename }, resultLinks);
                  anchor.target = "_blank";
                  anchor.rel = "noopener noreferrer";
                  if (kind === "images") {
                    const preview = el("img", {
                      src: "/view?" + query, alt: asset.filename, loading: "lazy",
                    }, anchor);
                    preview.style.cssText = "display:block;max-width:100%;max-height:240px;object-fit:contain;margin:5px 0 10px;border-radius:6px";
                  }
                  resultLinks.appendChild(document.createTextNode("  "));
                }
              }
            }
            return (index + 1) + ". " + job.status + " (" + job.prompt_id + ")";
          });
          output.textContent = "Run " + runId + "\n" + lines.join("\n") +
            (data.dispatch_error ? "\nCó lỗi khi đưa tác vụ vào hàng đợi." : "");
          finished = data.jobs.every((job) =>
            ["completed", "failed", "cancelled", "not_submitted"].includes(job.status));
          if (finished) cancel.disabled = true;
        } catch (error) {
          failures += 1;
          output.textContent = "Không lấy được trạng thái: " + error.message +
            (failures < 4 ? " (đang thử lại)" : " (ngừng cập nhật tự động)");
          if (failures >= 4) finished = true;
        }
        if (finished && poll !== null) {
          clearInterval(poll);
          pollTimers.delete(runId);
        }
      };
      cancel.addEventListener("click", async () => {
        try {
          const response = await api("/runs/" + encodeURIComponent(runId) + "/cancel", "POST", {});
          message(response.cancelled_prompt_ids.length ?
            "Đã yêu cầu hủy " + response.cancelled_prompt_ids.length + " tác vụ." :
            "Không còn tác vụ đang chạy hoặc chờ để hủy.");
          cancel.disabled = true;
          await update();
        } catch (error) { message("Lỗi hủy: " + error.message); }
      });
      pollTimers.set(runId, true);
      await update();
      if (!finished) {
        poll = setInterval(update, 3000);
        pollTimers.set(runId, poll);
      } else {
        pollTimers.delete(runId);
      }
    }

    function showPlan(proposal) {
      let active = proposal;
      let dirty = false;
      const definition = workflowCatalog.find((item) => item.id === active.plan.workflow_id);
      const wrapper = el("div", { className: "comfy-automation-plan" }, messages);
      el("strong", { text: "Kế hoạch cần xác nhận" }, wrapper);
      el("div", { text: "Workflow: " +
        (definition?.name || active.plan.workflow_id) }, wrapper);
      el("div", { text: "Chỉ chạy workflow mẫu đã duyệt, không tự sửa canvas đang mở." }, wrapper);

      const editor = el("div", {}, wrapper);
      const inputs = [];
      let save;
      let confirm;
      const markDirty = () => {
        dirty = true;
        if (save) save.disabled = false;
        if (confirm) confirm.disabled = true;
      };
      const addField = (key, spec) => {
        const container = el("div", { className: "comfy-automation-field" }, editor);
        el("label", { text: key + (spec.description ? " — " + spec.description : "") }, container);
        const previous = active.plan.parameters[key];
        let control;
        if (spec.source === "checkpoints") {
          control = el("select", {}, container);
          el("option", { value: "", text: "Chọn checkpoint..." }, control);
          for (const value of installedCheckpoints) {
            el("option", { value, text: value }, control);
          }
          control.value = previous ?? "";
        } else if (spec.type === "boolean") {
          control = el("input", { type: "checkbox", checked: previous ?? false }, container);
        } else if (spec.type === "string" && ["prompt", "negative_prompt"].includes(key)) {
          control = el("textarea", { value: previous ?? "", rows: 3 }, container);
        } else if (spec.type === "integer" || spec.type === "number") {
          control = el("input", {
            type: "number",
            value: previous === undefined ? "" : String(previous),
            ...(spec.min === undefined ? {} : { min: String(spec.min) }),
            ...(spec.max === undefined ? {} : { max: String(spec.max) }),
            step: String(spec.multiple_of || (spec.type === "integer" ? 1 : "any")),
            placeholder: "Giữ mặc định trong workflow",
          }, container);
        } else {
          control = el("input", { type: "text", value: previous ?? "", placeholder: "Giữ mặc định" }, container);
        }
        control.addEventListener("input", markDirty);
        inputs.push({ key, spec, control, hadValue: previous !== undefined });
      };

      for (const [key, spec] of Object.entries(definition?.inputs || {})) addField(key, spec);
      const countField = el("div", { className: "comfy-automation-field" }, editor);
      el("label", { text: "Số tác vụ" }, countField);
      const batch = el("input", {
        type: "number", min: "1", max: "8", step: "1",
        value: String(active.plan.count),
      }, countField);
      batch.addEventListener("input", markDirty);
      const actions = el("div", { className: "comfy-automation-row" }, wrapper);
      save = el("button", { text: "Lưu chỉnh sửa", className: "comfy-automation-secondary", disabled: true }, actions);
      confirm = el("button", { text: "Xác nhận chạy" }, actions);
      currentConfirm = confirm;

      const needModel = () =>
        installedCheckpoints.length > 1 && definition?.inputs?.checkpoint &&
        !active.plan.parameters.checkpoint;
      if (needModel()) {
        confirm.disabled = true;
        el("div", { text: "Có nhiều checkpoint; cần chọn model rồi lưu trước khi chạy." }, wrapper);
      }

      save.addEventListener("click", async () => {
        if (!dirty) return;
        const parameters = {};
        for (const { key, spec, control, hadValue } of inputs) {
          if (spec.type === "boolean") {
            if (hadValue || control.checked) parameters[key] = control.checked;
          } else if (spec.type === "integer" || spec.type === "number") {
            if (control.value.trim()) {
              const value = Number(control.value);
              if (!Number.isFinite(value) ||
                  (spec.type === "integer" && !Number.isInteger(value))) {
                message("Tham số " + key + " không phải số hợp lệ.");
                return;
              }
              parameters[key] = value;
            }
          } else if (control.value.trim()) {
            parameters[key] = control.value;
          }
        }
        const countValue = Number(batch.value);
        if (!Number.isInteger(countValue) || countValue < 1 || countValue > 8) {
          message("Số tác vụ phải từ 1 đến 8.");
          return;
        }
        save.disabled = true;
        try {
          active = await api("/plans/" + encodeURIComponent(active.plan_id) + "/revise", "POST", {
            parameters, count: countValue,
          });
          dirty = false;
          confirm.disabled = needModel();
          message("Đã lưu phiên bản kế hoạch mới; bản cũ không còn được xác nhận.");
        } catch (error) {
          message("Không lưu được chỉnh sửa: " + error.message);
          if (error.status !== 409) save.disabled = false;
        }
      });

      confirm.addEventListener("click", async () => {
        if (dirty) {
          message("Cần lưu các thay đổi trước khi chạy.");
          return;
        }
        confirm.disabled = true;
        try {
          const run = await api("/runs", "POST", { plan_id: active.plan_id });
          message(run.dispatch_error ? "Lần dispatch trước gặp lỗi; kiểm tra Run ID." :
            (run.replayed ? "Dùng lại tác vụ đã xác nhận; không xếp trùng." :
              "Đã gửi tác vụ vào hàng đợi ComfyUI."));
          save.disabled = true;
          await followRun(run.run_id);
        } catch (error) {
          message("Không chạy được: " + error.message);
          if (error.detail?.run_id) {
            message("Kiểm tra trạng thái tác vụ sau lỗi dispatch.");
            await followRun(error.detail.run_id);
          } else if (![400, 409].includes(error.status)) {
            confirm.disabled = false;
          }
        }
      });
      messages.scrollTop = messages.scrollHeight;
    }

    send.addEventListener("click", async () => {
      if (busy || !prompt.value.trim()) return;
      const hasBatch = count.value.trim() !== "";
      const batch = hasBatch ? Number(count.value) : null;
      if (hasBatch && (!Number.isInteger(batch) || batch < 1 || batch > 8)) {
        message("Số tác vụ phải là số nguyên từ 1 đến 8.");
        return;
      }
      const userText = prompt.value.trim();
      message(userText, true);
      const indicator = message("Đang lập kế hoạch từ yêu cầu...");
      const previousConfirm = currentConfirm;
      const wasConfirmEnabled = previousConfirm && !previousConfirm.disabled;
      if (previousConfirm) previousConfirm.disabled = true;
      setBusy(true);
      try {
        let canvasContext = null;
        if (includeCanvas.checked) {
          if (!app.isGraphReady || typeof app.graphToPrompt !== "function") {
            throw new Error("Chưa có canvas hợp lệ để lấy ngữ cảnh.");
          }
          // Extract only class names: never send widget values, image data,
          // authentication tokens or the graph itself to the planning model.
          const snapshot = await app.graphToPrompt();
          const nodes = Object.values(snapshot.output || {});
          if (nodes.length > 1024) throw new Error("Canvas quá nhiều node.");
          canvasContext = {
            node_count: nodes.length,
            node_types: nodes.map((node) => node.class_type)
              .filter((name) => typeof name === "string" && name.length > 0 && name.length <= 128)
              .slice(0, 64),
          };
        }
        const data = await api("/chat", "POST", {
          ...(canvasContext ? { canvas_context: canvasContext } : {}),
          message: userText,
          ...(hasBatch ? { count: batch } : {}),
          ...(checkpoint.value ? { checkpoint: checkpoint.value } : {}),
          ...(sessionId ? { session_id: sessionId } : {}),
          ...(workflow.value ? { workflow_id: workflow.value } : {}),
        });
        sessionId = data.session_id;
        if (currentConfirm) currentConfirm.disabled = true;
        indicator.textContent = "Kế hoạch đã sẵn sàng. Vui lòng kiểm tra thông số:";
        showPlan(data);
        message(llmConfigured ?
          "Gửi tiếp tin nhắn để AI đề xuất thay đổi; hoặc sửa trực tiếp trên biểu mẫu kế hoạch." :
          "Chế độ không LLM: sửa trực tiếp trên biểu mẫu hoặc gửi prompt mới.");
        prompt.value = "";
      } catch (error) {
        indicator.textContent = "Không lập được kế hoạch: " + error.message;
        if (wasConfirmEnabled && previousConfirm) previousConfirm.disabled = false;
        if (error.status === 404 && sessionId) {
          sessionId = null;
          message("Phiên chat đã hết hạn; gửi lại để tạo phiên mới.");
        }
      } finally { setBusy(false); }
    });

    refresh.addEventListener("click", loadWorkflows);
    launcher.addEventListener("click", () => { panel.style.display = "flex"; token.focus(); });
    close.addEventListener("click", () => { panel.style.display = "none"; });
    prompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        send.click();
      }
    });
    // Prefer ComfyUI's native sidebar contract over a fixed overlay. The
    // floating launcher remains only as a fallback for older frontend builds.
    if (typeof app.extensionManager?.registerSidebarTab === "function") {
      try {
        app.extensionManager.registerSidebarTab({
          id: "comfyui.automation",
          title: "Automation",
          tooltip: "Chạy workflow đã duyệt; không thay thế Comfy Agent",
          icon: "pi pi-bolt",
          type: "custom",
          render(container) {
            panel.classList.add("comfy-automation-sidebar");
            container.appendChild(panel);
            panel.style.display = "flex";
            launcher.hidden = true;
          },
          destroy() {
            for (const timer of pollTimers.values()) {
              if (timer !== true) clearInterval(timer);
            }
            pollTimers.clear();
            panel.remove();
            launcher.hidden = false;
          },
        });
        launcher.hidden = true;
      } catch (error) {
        message("Không đăng ký được sidebar, sử dụng bảng chat nổi: " + error.message);
      }
    }

    message("Nhập token, tải workflow và trò chuyện nhiều lượt để tinh chỉnh kế hoạch. " +
      "Mỗi lượt chạy phải được xác nhận. Phiên hội thoại chỉ lưu trong bộ nhớ server.");
  },
});
