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

    const launcher = el("button", { id: "comfy-automation-launcher", text: "⚡ Chat Automation" }, document.body);
    const panel = el("section", { id: "comfy-automation-panel" }, document.body);
    panel.setAttribute("aria-label", "ComfyUI workflow automation");

    const header = el("div", { id: "comfy-automation-header" }, panel);
    const heading = el("strong", { text: "Chat Agent · Workflow" }, header);
    heading.style.flex = "1";
    const close = el("button", { text: "×", title: "Đóng" }, header);

    const controls = el("div", { id: "comfy-automation-controls" }, panel);
    const token = el("input", { type: "password", placeholder: "COMFY_AUTOMATION_TOKEN", autocomplete: "off" }, controls);
    token.setAttribute("aria-label", "Automation token");
    const workflow = el("select", {}, controls);
    workflow.setAttribute("aria-label", "Workflow");
    el("option", { value: "", text: "Tự động chọn workflow" }, workflow);

    const messages = el("div", { id: "comfy-automation-messages" }, panel);
    messages.setAttribute("role", "log");
    messages.setAttribute("aria-live", "polite");
    const compose = el("div", { id: "comfy-automation-compose" }, panel);
    const prompt = el("textarea", { placeholder: "Ví dụ: Tạo ảnh hoàng hôn tại vịnh Hạ Long..." }, compose);
    const row = el("div", { className: "comfy-automation-row" }, compose);
    const refresh = el("button", { text: "Tải workflow" }, row);
    const send = el("button", { text: "Lập kế hoạch" }, row);
    let busy = false;
    let pollTimer = null;

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
      if (!res.ok) throw new Error(body.error || "HTTP " + res.status);
      return body;
    };

    async function loadWorkflows() {
      setBusy(true);
      try {
        const data = await api("/workflows");
        workflow.replaceChildren();
        el("option", { value: "", text: "Tự động chọn workflow" }, workflow);
        for (const item of data.workflows) el("option", { value: item.id, text: item.name }, workflow);
        message("Đã tải " + data.workflows.length + " workflow. " +
          (data.llm_configured ? "LLM planner đã cấu hình." : "Chế độ không LLM: chọn workflow hoặc dùng mẫu duy nhất."));
      } catch (error) {
        message("Không tải được workflow: " + error.message);
      } finally { setBusy(false); }
    }

    async function followRun(runId) {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
      const output = message("Đã xếp hàng. Run ID: " + runId);
      let stopped = false;
      const update = async () => {
        try {
          const data = await api("/runs/" + encodeURIComponent(runId));
          const lines = data.jobs.map((job, index) => (index + 1) + ". " + job.status + " (" + job.prompt_id + ")");
          output.textContent = "Run " + runId + "\n" + lines.join("\n") +
            "\nKết quả xem trong ComfyUI History / Output.";
          if (data.jobs.every((job) => !["queued", "running", "unknown"].includes(job.status))) {
            stopped = true;
          }
        } catch (error) {
          output.textContent = "Không lấy được trạng thái: " + error.message;
          stopped = true;
        }
        if (stopped && pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      };
      await update();
      if (!stopped) pollTimer = setInterval(update, 3000);
      const cancel = el("button", { text: "Hủy run" }, messages);
      cancel.addEventListener("click", async () => {
        try {
          await api("/runs/" + encodeURIComponent(runId) + "/cancel", "POST", {});
          message("Đã gửi yêu cầu hủy.");
          cancel.disabled = true;
        } catch (error) { message("Lỗi hủy: " + error.message); }
      });
    }

    send.addEventListener("click", async () => {
      if (busy || !prompt.value.trim()) return;
      const userText = prompt.value.trim();
      message(userText, true);
      setBusy(true);
      try {
        const data = await api("/chat", "POST", {
          message: userText,
          ...(workflow.value ? { workflow_id: workflow.value } : {}),
        });
        message("Kế hoạch đề xuất:\n" + JSON.stringify(data.plan, null, 2));
        const confirm = el("button", { text: "✓ Xác nhận chạy" }, messages);
        confirm.addEventListener("click", async () => {
          confirm.disabled = true;
          try {
            const run = await api("/runs", "POST", { plan_id: data.plan_id });
            await followRun(run.run_id);
          } catch (error) { message("Không chạy được: " + error.message); }
        }, { once: true });
        prompt.value = "";
      } catch (error) {
        message("Không lập được kế hoạch: " + error.message);
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
    message("Nhập token, tải danh sách workflow, rồi gửi yêu cầu. Mọi lần chạy đều cần xác nhận.");
  },
});
