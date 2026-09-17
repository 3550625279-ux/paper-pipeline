/* Paper Pipeline - detect a paper on the page and offer one-click translate+import. */

(() => {
  if (window.__paperPipelineLoaded) return;
  window.__paperPipelineLoaded = true;

  // ------------------------------------------------------------- detection

  function metaContent(name) {
    const el =
      document.querySelector('meta[name="' + name + '"]') ||
      document.querySelector('meta[property="' + name + '"]');
    return el && el.content ? el.content.trim() : "";
  }

  function metaAll(name) {
    return Array.from(document.querySelectorAll('meta[name="' + name + '"]'))
      .map((el) => (el.content || "").trim())
      .filter(Boolean);
  }

  function fromJsonLd() {
    const out = {};
    const nodes = document.querySelectorAll('script[type="application/ld+json"]');
    for (const node of nodes) {
      let data;
      try {
        data = JSON.parse(node.textContent);
      } catch (e) {
        continue;
      }
      const list = Array.isArray(data) ? data : (data["@graph"] || [data]);
      for (const entry of list) {
        const types = [].concat(entry["@type"] || []);
        if (!types.some((t) => /ScholarlyArticle|Article|Chapter/i.test(String(t)))) {
          continue;
        }
        if (!out.title && entry.headline) out.title = String(entry.headline).trim();
        if (!out.doi && entry.identifier) {
          const ids = [].concat(entry.identifier);
          for (const id of ids) {
            const value = typeof id === "string" ? id : (id && id.value) || "";
            if (/^10\.\d{4,}/.test(value)) {
              out.doi = value;
              break;
            }
          }
        }
        if (!out.authors && entry.author) {
          out.authors = [].concat(entry.author).map((a) =>
            typeof a === "string" ? a : (a && a.name) || ""
          ).filter(Boolean);
        }
        if (!out.year && entry.datePublished) {
          out.year = String(entry.datePublished).slice(0, 4);
        }
      }
    }
    return out;
  }

  function detectArxiv() {
    const direct = metaContent("citation_arxiv_id");
    if (direct) return direct.replace(/\.pdf$/i, "");
    const m =
      location.pathname.match(/\/abs\/([^/?#]+)/) ||
      location.pathname.match(/\/pdf\/([^/?#]+?)(?:\.pdf)?$/);
    if (m && /arxiv\.org$/i.test(location.hostname)) {
      return m[1].replace(/\.pdf$/i, "");
    }
    return "";
  }

  function detectPdfUrl() {
    const cited = metaContent("citation_pdf_url");
    if (cited) return cited;
    if (/\.pdf$/i.test(location.pathname)) return location.href;
    return "";
  }

  function detect() {
    const ld = fromJsonLd();
    const doi = metaContent("citation_doi") || ld.doi || "";
    const arxiv = detectArxiv();
    const title =
      metaContent("citation_title") || ld.title ||
      metaContent("og:title") || document.title || "";
    const authors = metaAll("citation_author").length
      ? metaAll("citation_author")
      : (ld.authors || []);
    const date =
      metaContent("citation_publication_date") ||
      metaContent("citation_date") ||
      metaContent("citation_online_date") || "";
    const year = (String(date).match(/(19|20)\d{2}/) || ld.year || [""])[0] || "";
    const venue =
      metaContent("citation_journal_title") ||
      metaContent("citation_conference_title") || "";
    const isPaper =
      Boolean(doi || arxiv) ||
      (Boolean(metaContent("citation_title")) && authors.length > 0);
    return {
      isPaper,
      hint: {
        title: title.slice(0, 400),
        doi,
        arxiv,
        authors: authors.slice(0, 25),
        year: String(year).slice(0, 4),
        venue,
        pdfUrl: detectPdfUrl(),
        pageUrl: location.href,
      },
    };
  }

  // ------------------------------------------------------------- messaging

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

  // -------------------------------------------------------------------- UI

  const HOST_ID = "paper-pipeline-host";
  let ui = null;

  const CSS = `
:host { all: initial; }
.wrap {
  position: fixed; right: 18px; bottom: 18px; z-index: 2147483647;
  font: 13px/1.5 system-ui, "Microsoft YaHei UI", sans-serif;
  color: #1b1b1b;
}
@media (prefers-color-scheme: dark) {
  .wrap { color: #e8e8e8; }
}
.pill {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 12px; border-radius: 999px; cursor: pointer;
  background: #2f6feb; color: #fff; border: 0;
  box-shadow: 0 2px 10px rgba(0,0,0,.28); font: inherit; font-weight: 600;
}
.pill:hover { background: #2560d4; }
.panel {
  width: 300px; border-radius: 10px; overflow: hidden;
  background: #fff; border: 1px solid rgba(0,0,0,.12);
  box-shadow: 0 8px 28px rgba(0,0,0,.30);
}
@media (prefers-color-scheme: dark) {
  .panel { background: #23262b; border-color: rgba(255,255,255,.14); }
}
.head {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 12px; background: #2f6feb; color: #fff; font-weight: 600;
}
.head .sp { flex: 1; }
.x {
  border: 0; background: transparent; color: #fff; cursor: pointer;
  font: inherit; font-size: 15px; line-height: 1; padding: 0 2px;
}
.body { padding: 11px 12px 12px; }
.t { font-weight: 600; margin-bottom: 3px; }
.m { opacity: .72; font-size: 12px; margin-bottom: 9px; }
.row { display: flex; align-items: center; gap: 8px; }
button.act {
  font: inherit; font-weight: 600; padding: 7px 14px; border-radius: 6px;
  border: 1px solid #2f6feb; background: #2f6feb; color: #fff; cursor: pointer;
}
button.act:disabled { opacity: .55; cursor: default; }
button.ghost {
  font: inherit; padding: 6px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid rgba(128,128,128,.55); background: transparent;
  color: inherit;
}
.bar { height: 5px; border-radius: 3px; background: rgba(128,128,128,.28);
       overflow: hidden; margin: 10px 0 5px; }
.bar > i { display: block; height: 100%; width: 0; background: #2f6feb;
           transition: width .25s ease; }
.stage { font-size: 12px; opacity: .78; min-height: 18px; }
.msg { margin-top: 9px; font-size: 12px; }
.err { color: #d4574a; }
.ok { color: #1e8e3e; }
.log { margin-top: 8px; font-size: 11px; opacity: .62; max-height: 72px;
       overflow: auto; white-space: pre-wrap; }
`;

  function build() {
    const host = document.createElement("div");
    host.id = HOST_ID;
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CSS;
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    root.append(style, wrap);
    document.documentElement.append(host);

    const pill = document.createElement("button");
    pill.className = "pill";
    pill.textContent = "翻译并入库";
    wrap.append(pill);

    const state = { host, wrap, pill, panel: null, jobId: null, timer: null };

    pill.addEventListener("click", () => {
      openPanel(state);
    });
    return state;
  }

  function openPanel(state) {
    if (state.panel) return;
    const detected = detect();
    const hint = detected.hint;

    const panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML = `
      <div class="head"><span>Paper Pipeline</span><span class="sp"></span>
        <button class="x" title="关闭">×</button></div>
      <div class="body">
        <div class="t"></div>
        <div class="m"></div>
        <div class="row">
          <button class="act">翻译并入库</button>
          <button class="ghost" hidden>打开 Zotero 分类</button>
        </div>
        <div class="bar" hidden><i></i></div>
        <div class="stage" hidden></div>
        <div class="msg" hidden></div>
        <div class="log" hidden></div>
      </div>`;
    state.wrap.textContent = "";
    state.wrap.append(panel);
    state.panel = panel;

    panel.querySelector(".t").textContent = hint.title || "(未识别到标题)";
    const bits = [];
    if (hint.authors && hint.authors.length) {
      bits.push(hint.authors.slice(0, 2).join(", ") +
        (hint.authors.length > 2 ? " 等" : ""));
    }
    if (hint.year) bits.push(hint.year);
    if (hint.venue) bits.push(hint.venue);
    if (hint.arxiv) bits.push("arXiv:" + hint.arxiv);
    if (hint.doi) bits.push("DOI " + hint.doi);
    panel.querySelector(".m").textContent = bits.join(" · ") || "将尝试自动识别题录";

    panel.querySelector(".x").addEventListener("click", () => {
      stopPolling(state);
      state.panel = null;
      state.wrap.textContent = "";
      state.wrap.append(state.pill);
    });

    const actBtn = panel.querySelector(".act");
    actBtn.addEventListener("click", async () => {
      actBtn.disabled = true;
      actBtn.textContent = "提交中…";
      const resp = await send("createJob", { hint });
      if (!resp.ok) {
        setMsg(state, "无法连接本地服务：" +
          ((resp.data && resp.data.error) || "未知错误") +
          "。请确认 PaperPipeline 服务已启动。", true);
        actBtn.disabled = false;
        actBtn.textContent = "重试";
        return;
      }
      state.jobId = resp.data.id;
      setStage(state, "已排队");
      panel.querySelector(".bar").hidden = false;
      startPolling(state);
    });

    setMsg(state, "", false);
  }

  function setMsg(state, text, isError) {
    const el = state.panel && state.panel.querySelector(".msg");
    if (!el) return;
    el.hidden = !text;
    el.textContent = text;
    el.className = "msg" + (isError ? " err" : "");
  }

  function setStage(state, text) {
    const el = state.panel && state.panel.querySelector(".stage");
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
  }

  function setProgress(state, pct) {
    const bar = state.panel && state.panel.querySelector(".bar > i");
    if (bar) bar.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }

  function setLog(state, lines) {
    const el = state.panel && state.panel.querySelector(".log");
    if (!el) return;
    const text = (lines || []).slice(-6).join("\n");
    el.hidden = !text;
    el.textContent = text;
  }

  function startPolling(state) {
    stopPolling(state);
    const tick = async () => {
      if (!state.jobId || !state.panel) return;
      const resp = await send("getJob", { id: state.jobId });
      if (!resp.ok || !resp.data) return;
      renderJob(state, resp.data);
    };
    tick();
    state.timer = setInterval(tick, 1500);
  }

  function stopPolling(state) {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  const STAGE_TEXT = {
    metadata: "识别题录…",
    fetch: "获取 PDF…",
    translate: "翻译中…",
    import: "入库 Zotero…",
    done: "已完成",
    failed: "失败",
  };

  function renderJob(state, job) {
    const progress = job.progress || {};
    if (job.state === "translate") {
      setProgress(state, progress.overall || 0);
      const stageName = progress.stage || "";
      const cnt = (progress.current != null && progress.total)
        ? ` ${progress.current}/${progress.total}` : "";
      setStage(state, `翻译中 ${(progress.overall || 0).toFixed(0)}%${cnt ? "　" + stageName + cnt : ""}`);
    } else if (job.state === "done") {
      setProgress(state, 100);
      setStage(state, "");
      const z = job.zotero || {};
      setMsg(state, `已入库：${z.attachments || 0} 个附件`, false);
      const msg = state.panel.querySelector(".msg");
      msg.className = "msg ok";
      const btn = state.panel.querySelector(".act");
      btn.textContent = "完成";
      btn.disabled = true;
      stopPolling(state);
    } else if (job.state === "failed") {
      setStage(state, "");
      setProgress(state, 0);
      setMsg(state, job.error || "失败", true);
      const btn = state.panel.querySelector(".act");
      btn.textContent = "重试";
      btn.disabled = false;
      stopPolling(state);
    } else {
      setStage(state, STAGE_TEXT[job.state] || job.label || job.state);
    }
    setLog(state, job.log);
  }

  // ------------------------------------------------------------------ init

  function init() {
    const detected = detect();
    if (!detected.isPaper) return;
    ui = build();
  }

  // the toolbar popup asks for the page's hint
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "detect") {
      const d = detect();
      sendResponse({ hint: d.hint, isPaper: d.isPaper });
    }
    return false;
  });

  if (document.readyState === "complete" || document.readyState === "interactive") {
    init();
  } else {
    window.addEventListener("DOMContentLoaded", init, { once: true });
  }
})();
