/* Paper Pipeline - service worker: the only place that talks to the local service. */

// config.js is generated at install time and is not in version control, so a
// fresh clone will not have it. Degrade with a clear message instead of
// letting the service worker fail to start.
let CONFIG = null;
try {
  importScripts("config.js");
  CONFIG = typeof PP_CONFIG === "object" ? PP_CONFIG : null;
} catch (e) {
  CONFIG = null;
}

const NOT_SET_UP = {
  error:
    "插件还没配置。请先运行项目里的 install.cmd，它会生成 extension/config.js，然后在 chrome://extensions 里重新加载本扩展。",
};

function baseUrl() {
  return "http://127.0.0.1:" + ((CONFIG && CONFIG.port) || 8787);
}

async function call(path, options = {}) {
  if (!CONFIG) {
    return { ok: false, status: 0, data: NOT_SET_UP };
  }
  const res = await fetch(baseUrl() + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-PaperPipeline-Token": CONFIG.token || "",
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    data = { error: "bad json: " + text.slice(0, 120) };
  }
  return { ok: res.ok, status: res.status, data };
}

async function health() {
  if (!CONFIG) {
    return { ok: false, status: 0, data: NOT_SET_UP };
  }
  try {
    return await call("/api/health");
  } catch (e) {
    return { ok: false, status: 0, data: { error: String(e) } };
  }
}

async function createJob(hint) {
  return call("/api/jobs", { method: "POST", body: JSON.stringify(hint) });
}

async function getJob(id) {
  return call("/api/jobs/" + encodeURIComponent(id));
}

async function listJobs() {
  return call("/api/jobs");
}

const HANDLERS = {
  health,
  createJob: (msg) => createJob(msg.hint),
  getJob: (msg) => getJob(msg.id),
  listJobs,
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const fn = HANDLERS[msg && msg.type];
  if (!fn) {
    sendResponse({ ok: false, status: 0, data: { error: "unknown " + msg?.type } });
    return true;
  }
  Promise.resolve(fn(msg))
    .then(sendResponse)
    .catch((err) => sendResponse({
      ok: false, status: 0, data: { error: String(err) } ,
    }));
  return true; // keep the channel open for the async reply
});

/* Badge feedback so the toolbar shows something even with the panel closed. */
async function refreshBadge() {
  if (!CONFIG) {
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#b23c3c" });
    return;
  }
  const h = await health();
  if (!h.ok) {
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#b23c3c" });
    return;
  }
  const jobs = await listJobs();
  const running = (jobs.data?.jobs || []).filter(
    (j) => ["queued", "metadata", "fetch", "translate", "import"].includes(j.state)
  ).length;
  if (running) {
    chrome.action.setBadgeText({ text: String(running) });
    chrome.action.setBadgeBackgroundColor({ color: "#2f6feb" });
  } else {
    chrome.action.setBadgeText({ text: "" });
  }
}

chrome.runtime.onInstalled.addListener(refreshBadge);
chrome.runtime.onStartup.addListener(refreshBadge);
setInterval(refreshBadge, 5000);
