# -*- coding: utf-8 -*-
"""PaperPipeline local service.

Long-running, windowless HTTP service on 127.0.0.1 that turns a paper the
browser found into a translated, Zotero-filed item.
"""
from __future__ import annotations

import json
import logging
import queue
import secrets
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
import metadata
import pdfsource
import zotero_api

VERSION = "0.1.0"

LOG_FILE = config.LOG_DIR / "service.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("paperpipeline")

STATE_LOCK = threading.RLock()
JOB_QUEUE: "queue.Queue[str]" = queue.Queue()


# ---------------------------------------------------------------------- state

def default_state() -> dict:
    return {
        "token": secrets.token_hex(16),
        "port": config.port(),
        "tempCollection": None,
        "jobs": {},
        "order": [],
    }


def load_state() -> dict:
    state = config.load_state()
    fresh = default_state()
    if not state:
        return fresh
    for key, value in fresh.items():
        state.setdefault(key, value)
    return state


def save_state(state: dict) -> None:
    config.save_state(state)


# ----------------------------------------------------------------------- jobs

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_job(hint: dict) -> dict:
    return {
        "id": datetime.now().strftime("%Y%m%d-%H%M%S") + "-"
              + secrets.token_hex(3),
        "state": "queued",
        "label": "排队中",
        "progress": {"overall": 0.0},
        "hint": hint,
        "record": None,
        "files": {},
        "zotero": None,
        "error": None,
        "log": [],
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }


def touch(state: dict, job: dict) -> None:
    job["updatedAt"] = now_iso()
    with STATE_LOCK:
        state["jobs"][job["id"]] = job
        if job["id"] not in state["order"]:
            state["order"].append(job["id"])
        state["order"] = state["order"][-200:]
        save_state(state)


def set_state(state: dict, job: dict, state_name: str, label: str) -> None:
    job["state"] = state_name
    job["label"] = label
    touch(state, job)


# ------------------------------------------------------------------- pipeline

def resolve_temp_collection(state: dict) -> int | None:
    """Numeric collection id of the TEMP collection (cached, verified)."""
    cached = state.get("tempCollection") or {}
    name = config.temp_collection_name()
    col = zotero_api.find_collection(name)
    if col:
        key = col.get("key")
        cid = cached.get("id")
        if cached.get("key") != key or not cid:
            # The local API only exposes string keys; the connector needs the
            # numeric id. Recover it from the database when it is not cached.
            cid = zotero_api.collection_id_from_db(
                config.zotero_data_dir(), name
            )
            state["tempCollection"] = {"key": key, "id": cid, "name": name}
            save_state(state)
        return cid
    return None


def run_job(state: dict, job: dict) -> None:
    hint = job.get("hint") or {}
    work = config.WORK_DIR / job["id"]
    work.mkdir(parents=True, exist_ok=True)

    # ---- 1. metadata
    set_state(state, job, "metadata", "识别题录")
    rec = metadata.resolve(hint)
    job["record"] = rec
    if not rec.get("title"):
        # fall back to the PDF's own front page
        pass
    job["log"].append(f"题录来源：{rec.get('source', 'unknown')}")
    touch(state, job)

    # ---- 2. PDF
    set_state(state, job, "fetch", "获取 PDF")
    pdf = pdfsource.acquire(rec, work, hint)
    if not pdf:
        # last resort: if we have a local path from the extension, use it
        raise RuntimeError("找不到或无法下载这篇论文的 PDF（可能是付费墙）")
    job["log"].append(f"PDF：{pdf}")
    if not rec.get("title"):
        guess = metadata.title_from_pdf(pdf)
        if guess:
            rec["title"] = guess
            job["record"] = rec
    touch(state, job)

    # ---- 3. translate
    set_state(state, job, "translate", "翻译中")
    cfg = config.translator_settings()
    if not (cfg.get("openai_model") and cfg.get("openai_base_url")
            and cfg.get("openai_api_key")):
        raise RuntimeError(
            "翻译模型未配置：请在 config.json 的 translator 段填入 "
            "openai_base_url / openai_api_key / openai_model"
        )
    job_file = work / "job.json"
    job_file.write_text(json.dumps({
        "translatorConfig": cfg,
        "workDir": str(work),
        "sourcePdf": str(pdf),
        "vendorDir": str(config.VENDOR_DIR),
    }, ensure_ascii=False), encoding="utf-8")

    py = config.translator_python()
    if py is None:
        raise RuntimeError(
            "未配置翻译环境：请在 config.json 的 paths.translatorPython "
            "填入装有 pdf2zh-next 的 python.exe 路径"
        )
    if not py.exists():
        raise RuntimeError(f"翻译环境不存在：{py}")

    worker = Path(__file__).resolve().parent / "worker_translate.py"
    # The worker's stderr carries a lot of babeldoc logging; send it to a file.
    # Leaving it as an unread pipe would fill the buffer and deadlock the worker.
    err_path = work / "worker.err.log"
    err_file = open(err_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(py), "-u", str(worker), str(job_file)],
        stdout=subprocess.PIPE, stderr=err_file,
        text=True, encoding="utf-8", errors="replace",
        cwd=str(work), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    outcome: dict = {}
    for line in proc.stdout:
        line = (line or "").strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        etype = ev.get("type")
        if etype == "progress":
            job["progress"] = {
                "phase": ev.get("phase"),
                "stage": ev.get("stage"),
                "current": ev.get("current"),
                "total": ev.get("total"),
                "overall": ev.get("overall"),
            }
            job["label"] = f"翻译中 {ev.get('overall', 0):.0f}%"
            touch(state, job)
        elif etype == "phase":
            job["label"] = ev.get("message") or job["label"]
            touch(state, job)
        elif etype in ("log", "warn"):
            job["log"].append(ev.get("message") or "")
            touch(state, job)
        elif etype == "done":
            outcome = ev
        elif etype == "error":
            job["log"].append("worker: " + str(ev.get("message")))
            if ev.get("trace"):
                job["log"].append(str(ev["trace"])[-600:])
            touch(state, job)
    proc.wait()
    try:
        err_file.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        err_tail = err_path.read_text(encoding="utf-8", errors="replace")[-800:]
    except Exception:  # noqa: BLE001
        err_tail = ""
    if proc.returncode != 0 and err_tail.strip():
        job["log"].append("stderr: " + err_tail.strip()[-400:])

    if not outcome:
        raise RuntimeError("翻译未产出结果")

    job["files"] = outcome.get("files") or {}
    job["pages"] = outcome.get("pages")
    job["untranslated"] = outcome.get("untranslated")
    job["seconds"] = outcome.get("seconds")
    if job.get("untranslated"):
        job["log"].append(f"仍有 {job['untranslated']} 处疑似漏翻")
    touch(state, job)

    # ---- 4. import into Zotero
    set_state(state, job, "import", "入库 Zotero")
    if not zotero_api.is_running():
        job["log"].append("Zotero 未运行，等待中……")
        touch(state, job)
        for _ in range(60):  # wait up to ~3 minutes
            time.sleep(3)
            if zotero_api.is_running():
                break
    coll_id = resolve_temp_collection(state)
    if coll_id is None:
        job["log"].append(
            f"未找到「{config.temp_collection_name()}」分类，将只入库不进分类"
        )

    files_to_attach: list[tuple[str, Path]] = []
    original = outcome.get("original")
    if original and Path(original).exists():
        files_to_attach.append(("原文 PDF", Path(original)))
    labels = {
        "dual": "双语对照 PDF",
        "mono": "中文译本 PDF",
        "glossary": "术语表 CSV",
    }
    for key, label in labels.items():
        p = job["files"].get(key)
        if p and Path(p).exists():
            files_to_attach.append((label, Path(p)))

    if not files_to_attach:
        raise RuntimeError("没有可入库的文件")

    tags = config.default_tags()
    if not rec.get("doi") and not rec.get("arxiv"):
        tags.append("待补元数据")

    result = zotero_api.import_paper(rec, files_to_attach, coll_id, tags)
    job["zotero"] = result
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "入库失败")

    # keep the original PDF next to the translation for reuse
    set_state(state, job, "done", "已完成")
    job["log"].append(f"入库 {result.get('attachments')} 个附件")
    touch(state, job)


def worker_loop(state: dict) -> None:
    while True:
        job_id = JOB_QUEUE.get()
        try:
            with STATE_LOCK:
                job = state["jobs"].get(job_id)
            if not job:
                continue
            run_job(state, job)
        except Exception as exc:  # noqa: BLE001
            log.error("job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
            with STATE_LOCK:
                job = state["jobs"].get(job_id)
                if job:
                    job["state"] = "failed"
                    job["label"] = "失败"
                    job["error"] = str(exc)
                    job["log"].append("错误：" + str(exc))
                    touch(state, job)
        finally:
            JOB_QUEUE.task_done()


def resume_queued(state: dict) -> None:
    """Requeue anything that was interrupted by a restart."""
    changed = False
    for job_id, job in (state.get("jobs") or {}).items():
        if job.get("state") in ("queued", "metadata", "fetch", "translate", "import"):
            job["state"] = "failed"
            job["label"] = "中断"
            job["error"] = "服务重启导致任务中断"
            changed = True
    if changed:
        save_state(state)


# ------------------------------------------------------------------- HTTP api

class Handler(BaseHTTPRequestHandler):
    server_version = "PaperPipeline/" + VERSION
    state: dict = {}

    def log_message(self, fmt, *args):  # noqa: A003
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- helpers
    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-PaperPipeline-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        token = (self.state or {}).get("token")
        if not token:
            return True
        given = self.headers.get("X-PaperPipeline-Token")
        return bool(given) and secrets.compare_digest(given, token)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:  # noqa: BLE001
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return {}

    # -- routes
    def do_OPTIONS(self):  # noqa: N802
        self._send(204, {})

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path == "/api/health":
            self._send(200, {
                "ok": True,
                "version": VERSION,
                "zotero": zotero_api.is_running(),
                "tempCollection": (self.state.get("tempCollection") or {}).get("name"),
            })
            return
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return
        if path == "/api/jobs":
            ids = list(reversed(self.state.get("order") or []))[:30]
            jobs = [self.state["jobs"][i] for i in ids if i in self.state["jobs"]]
            self._send(200, {"jobs": jobs})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = (self.state.get("jobs") or {}).get(job_id)
            self._send(200 if job else 404, job or {"error": "not found"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return
        if path == "/api/jobs":
            hint = self._body()
            if not (hint.get("title") or hint.get("doi") or hint.get("arxiv")):
                self._send(400, {"error": "缺少 title/doi/arxiv"})
                return
            job = new_job(hint)
            with STATE_LOCK:
                self.state["jobs"][job["id"]] = job
                self.state["order"].append(job["id"])
                save_state(self.state)
            JOB_QUEUE.put(job["id"])
            log.info("queued job %s: %s", job["id"], hint.get("title"))
            self._send(202, job)
            return
        if path == "/api/rerun-temp":
            # re-resolve the TEMP collection id (used after first setup)
            self._send(200, {"tempCollection": self.state.get("tempCollection")})
            return
        self._send(404, {"error": "not found"})


def main() -> int:
    state = load_state()
    if not state.get("token"):
        state["token"] = secrets.token_hex(16)
    resume_queued(state)
    save_state(state)

    Handler.state = state
    threading.Thread(target=worker_loop, args=(state,), daemon=True).start()

    port = int(state.get("port") or config.port())
    # On Windows, SO_REUSEADDR lets a second process silently share the port,
    # which makes "is the service already running?" impossible to answer.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        log.error("无法监听端口 %d：%s", port, exc)
        log.error("端口可能被占用。改 config.json 的 service.port，"
                  "或先运行 pipeline.cmd stop。")
        return 1
    log.info("PaperPipeline %s listening on 127.0.0.1:%d", VERSION, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
