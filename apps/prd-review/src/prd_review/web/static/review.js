const dialog = document.querySelector("#review-progress");
const statusLine = document.querySelector("#job-status");
const closeButton = document.querySelector("#job-close");
const title = document.querySelector("#job-title");
const stages = [...document.querySelectorAll(".job-steps li")];

function applyProgress(event) {
  if (event.type === "error") throw new Error(event.message);
  if (event.type === "done") {
    window.location.assign(event.url);
    return true;
  }
  const active = stages.findIndex((stage) => stage.dataset.step === event.step);
  if (active < 0) return false;
  if (event.type === "stage") {
    stages.forEach((stage, index) => {
      stage.dataset.state = index < active ? "complete" : index === active ? "active" : "";
    });
    stages[active].querySelector("small").textContent = event.detail;
    statusLine.textContent = event.detail;
  } else if (event.type === "progress") {
    stages[active].querySelector("small").textContent = `已收到 ${event.characters.toLocaleString()} 个输出字符`;
  }
  return false;
}

if (dialog && window.fetch && window.ReadableStream && dialog.showModal) {
  let running = false;
  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", (event) => {
    if (running) event.preventDefault();
  });
  document.querySelectorAll("[data-review]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (running) return;
      const data = new FormData(form);
      const button = event.submitter || form.querySelector("button");
      button.disabled = true;
      running = true;
      closeButton.hidden = true;
      title.textContent = "正在评审";
      statusLine.textContent = "正在提交…";
      statusLine.classList.remove("job-error");
      stages.forEach((stage) => {
        stage.dataset.state = "";
        stage.querySelector("small").textContent = "";
      });
      dialog.showModal();
      let complete = false;
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: data,
          headers: { "Accept": "application/x-ndjson" },
        });
        if (!response.ok || !response.body ||
            !response.headers.get("content-type")?.startsWith("application/x-ndjson")) {
          throw new Error("提交失败，请刷新页面后重试。");
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          pending += decoder.decode(value, { stream: true });
          const lines = pending.split("\n");
          pending = lines.pop();
          for (const line of lines) {
            if (line.trim() && applyProgress(JSON.parse(line))) {
              complete = true;
              return;
            }
          }
        }
        throw new Error("评审连接提前结束，请重试。");
      } catch (error) {
        title.textContent = "评审暂未完成";
        statusLine.textContent = error.message;
        statusLine.classList.add("job-error");
        closeButton.hidden = false;
      } finally {
        if (!complete) {
          running = false;
          button.disabled = false;
        }
      }
    });
  });
}

function revealModule(hash = window.location.hash) {
  const details = document.getElementById(hash.slice(1));
  if (details?.matches("details.module")) details.open = true;
}
document.querySelectorAll('.module-nav a[href^="#module-"]').forEach((link) => {
  link.addEventListener("click", () => revealModule(link.hash));
});
window.addEventListener("hashchange", () => revealModule());
revealModule();
