/* Paper Pipeline - toolbar panel: current page + job list. */

const STAGE_TEXT = {
  queued: "排队中",
  metadata: "识别题录",
  fetch: "获取 PDF",
  translate: "翻译中",
  import: "入库 Zotero",
  done: "已完成",
  failed: "失败",
};

const ACTIVE = ["queued", "metadata", "fetch", "translate", "import"];

function send(type, payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type, ...(payload || {}) }, (resp) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, data: { error: chrome.runtime.lastError.message } });
      } else {
        resolve(resp || { ok: false, data: { error: "无响应" } });
      }
    });
  });
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function hintFromTab(tab) {
  if (!tab || !tab.url) return null;
  if (/^https?:\/\//.test(tab.url)) {
    try {
      const res = await chrome.tabs.sendMessage(tab.id, { type: "detect" });
      if (res && res.hint) return res.hint;
    } catch (e) {
      /* content script not present (e.g. PDF viewer) */
    }
  }
  if (/\.pdf($|\?)/i.test(tab.url)) {
    return {
      title: decodeURIComponent((tab.url.split("/").pop() || "").replace(/\.pdf.*$/i, "")),
      pdfUrl: tab.url,
      pageUrl: tab.url,
      fromPdfTab: true,
    };
  }
  return null;
}

function renderJob(job) {
  const box = el("div", "job");
  const row = el("div", "row");
  const title = el("div", "title", (job.record && job.record.title) ||
    job.hint.title || "(未命名)");
  title.style.flex = "1";
  row.append(title);
  const badge = el("span", "muted",
    STAGE_TEXT[job.state] || job.label || job.state);
  if (job.state === "done") badge.className = "ok";
  if (job.state === "failed") badge.className = "err";
  row.append(badge);
  box.append(row);

  const pct = (job.progress && job.progress.overall) || 0;
  if (ACTIVE.includes(job.state)) {
    const bar = el("div", "bar");
    const fill = el("i");
    fill.style.width = (job.state === "translate" ? pct : 4) + "%";
    bar.append(fill);
    box.append(bar);
    const stage = (job.progress && job.progress.stage) || "";
    if (stage) box.append(el("div", "muted", stage));
  }
  if (job.state === "failed" && job.error) {
    box.append(el("div", "muted err", job.error.slice(0, 150)));
  }
  if (job.state === "done") {
    box.append(el("div", "muted",
      "附件 " + ((job.zotero && job.zotero.attachments) || 0) + " 个" +
      (job.seconds ? " · " + job.seconds + "s" : "")));
  }
  return box;
}

async function refresh() {
  const cur = document.getElementById("current");
  const jobsBox = document.getElementById("jobs");
  cur.textContent = "";
  jobsBox.textContent = "";

  const health = await send("health");
  if (!health.ok) {
    const card = el("div", "card");
    card.append(el("div", "title", "本地服务未运行"));
    card.append(el("div", "muted",
      "请启动 PaperPipeline 服务（D:\\Software\\PaperPipeline\\run_service.pyw）。"));
    cur.append(card);
    return;
  }
  const zoteroOk = health.data && health.data.zotero;
  if (!zoteroOk) {
    const card = el("div", "card");
    card.append(el("div", "muted", "Zotero 未运行 —— 任务会排队等待。"));
    cur.append(card);
  }

  const tab = await activeTab();
  const hint = await hintFromTab(tab);
  if (hint) {
    const card = el("div", "card");
    card.append(el("div", "title", hint.title || "(未识别到标题)"));
    const bits = [];
    if (hint.year) bits.push(hint.year);
    if (hint.arxiv) bits.push("arXiv:" + hint.arxiv);
    if (hint.doi) bits.push("DOI " + hint.doi);
    if (hint.fromPdfTab) bits.push("PDF 直链");
    card.append(el("div", "muted", bits.join(" · ") || "将自动识别题录"));
    const row = el("div", "row");
    row.style.marginTop = "6px";
    const btn = el("button", "primary", "翻译并入库");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "提交中…";
      const resp = await send("createJob", { hint });
      if (!resp.ok) {
        btn.disabled = false;
        btn.textContent = "重试";
        card.append(el("div", "muted err",
          (resp.data && resp.data.error) || "提交失败"));
        return;
      }
      refresh();
    });
    row.append(btn);
    card.append(row);
    cur.append(card);
  } else {
    const card = el("div", "card");
    card.append(el("div", "muted", "当前页面未识别到论文。"));
    cur.append(card);
  }

  const list = await send("listJobs");
  const jobs = (list.data && list.data.jobs) || [];
  if (!jobs.length) {
    jobsBox.append(el("div", "muted", "还没有任务。"));
    return;
  }
  jobsBox.append(el("h1", null, "最近任务"));
  for (const job of jobs.slice(0, 8)) {
    jobsBox.append(renderJob(job));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refresh();
  const timer = setInterval(refresh, 2000);
  window.addEventListener("unload", () => clearInterval(timer));
});
