# -*- coding: utf-8 -*-
"""Import a paper (record + attachment files) into the local Zotero library."""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:23119"
LOCAL_USER = "/api/users/0"
ZOTERO_PORT = 23119


# ------------------------------------------------------------------- low level

def _request(path: str, payload=None, headers=None, raw: bytes | None = None,
             timeout: int = 180, method: str | None = None, retries: int = 3):
    body = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None
    )
    hdrs = {"X-Zotero-Connector-API-Version": "3", **(headers or {})}
    if method is None:
        method = "POST" if body is not None else "GET"
    last = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(
                BASE + path, data=body, headers=hdrs, method=method,
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < max(1, retries):
                time.sleep(1.5 * (attempt + 1))
    return "ERR", repr(last)


def _api_get(path: str, timeout: int = 40):
    try:
        with urllib.request.urlopen(BASE + LOCAL_USER + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def port_open(timeout: float = 0.8) -> bool:
    """Is anything accepting connections on Zotero's connector port?

    A raw socket check is used because the full HTTP ping retries with backoff,
    which is far too slow to sit behind a health endpoint that a browser polls
    every couple of seconds.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex(("127.0.0.1", ZOTERO_PORT)) == 0
    except Exception:  # noqa: BLE001
        return False


def is_running() -> bool:
    """Fast check that Zotero's connector is actually answering."""
    if not port_open():
        return False
    status, _ = _request("/connector/ping", timeout=4, retries=1)
    return status == 200


_STATUS_CACHE: dict = {"at": 0.0, "value": None}
_STATUS_TTL = 3.0


def is_running_cached() -> bool:
    """Same check, but shared between rapid polls (the UI polls every 2-5s)."""
    now = time.time()
    if _STATUS_CACHE["value"] is not None and now - _STATUS_CACHE["at"] < _STATUS_TTL:
        return bool(_STATUS_CACHE["value"])
    value = is_running()
    _STATUS_CACHE["at"] = now
    _STATUS_CACHE["value"] = value
    return value


def collections() -> list[dict]:
    return _api_get("/collections") or []


def find_collection(name: str) -> dict | None:
    """Find a collection by name via the read-only local API."""
    for c in collections():
        if (c.get("data") or {}).get("name") == name:
            return c
    return None


def collection_id_from_db(data_dir, name: str) -> int | None:
    """Look up a collection's numeric id in zotero.sqlite.

    The connector API wants the numeric id, but the read-only local API only
    exposes string keys. Reading the database is the reliable way to bridge
    them; we work on a copy so a running Zotero is never disturbed.
    """
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path as _Path

    if not data_dir:
        return None
    db = _Path(data_dir) / "zotero.sqlite"
    if not db.is_file():
        return None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        shutil.copy2(db, tmp)
        con = sqlite3.connect(tmp)
        row = con.execute(
            "SELECT collectionID FROM collections WHERE collectionName = ? "
            "ORDER BY collectionID LIMIT 1",
            (name,),
        ).fetchone()
        con.close()
        return int(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------ item build

def item_type_for(rec: dict) -> str:
    ctype = (rec.get("crossrefType") or "").lower()
    if rec.get("arxiv") and not rec.get("venue"):
        return "preprint"
    if ctype in ("journal-article", "article"):
        return "journalArticle"
    if ctype in ("proceedings-article", "conference-paper"):
        return "conferencePaper"
    if ctype in ("posted-content", "preprint"):
        return "preprint"
    if ctype == "book-chapter":
        return "bookSection"
    venue = rec.get("venue") or ""
    if re_search(r"journal|transaction|letters|review|science|nature", venue):
        return "journalArticle"
    if rec.get("pages") or re_search(r"proceedings|conference|workshop|symposium", venue):
        return "conferencePaper"
    return "preprint" if rec.get("arxiv") else "journalArticle"


def re_search(pattern: str, text: str) -> bool:
    import re
    return bool(re.search(pattern, text or "", re.I))


def build_item(rec: dict, item_key: str, tags: list[str]) -> dict:
    kind = item_type_for(rec)
    item: dict = {
        "id": item_key,
        "itemType": kind,
        "title": rec.get("title") or "(untitled)",
        "creators": rec.get("creators") or [],
        "date": str(rec.get("year") or ""),
        "language": "en",
        "abstractNote": (rec.get("abstract") or "")[:1500],
        "tags": [{"tag": t} for t in tags],
        "attachments": [],
    }
    doi = rec.get("doi") or ""
    if doi:
        item["DOI"] = doi
    url = rec.get("url") or (f"https://doi.org/{doi}" if doi else "")
    if rec.get("arxiv") and not url:
        url = f"https://arxiv.org/abs/{rec['arxiv']}"
    if url:
        item["url"] = url

    if kind == "journalArticle":
        item["publicationTitle"] = rec.get("venue", "")
        item["volume"] = rec.get("volume", "")
        item["issue"] = rec.get("issue", "")
        item["pages"] = rec.get("pages", "")
    elif kind == "conferencePaper":
        item["proceedingsTitle"] = rec.get("venue", "")
        item["conferenceName"] = rec.get("venue", "")
        item["pages"] = rec.get("pages", "")
        if doi.startswith("10.18653"):
            item["publisher"] = "Association for Computational Linguistics"
    elif kind == "preprint":
        item["repository"] = "arXiv" if rec.get("arxiv") else (rec.get("venue") or "")
        if rec.get("arxiv"):
            item["archiveID"] = f"arXiv:{rec['arxiv']}"
    return item


# --------------------------------------------------------------------- import

def save_attachment(session_id: str, parent_key: str, title: str,
                    path: Path) -> tuple:
    data = path.read_bytes()
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    metadata = {
        "sessionID": session_id,
        "parentItemID": parent_key,
        "title": title,
        "url": path.as_uri(),
    }
    return _request(
        "/connector/saveAttachment?"
        + urllib.parse.urlencode({"sessionID": session_id}),
        headers={
            "Content-Type": ctype,
            # header values must be latin-1 safe
            "X-Metadata": json.dumps(metadata, ensure_ascii=True),
        },
        raw=data,
    )


def import_paper(rec: dict, files: list[tuple[str, Path]],
                 collection_id: int | None, tags: list[str]) -> dict:
    """Create the item, place it in the collection, attach files.

    Returns {ok, itemKey, attachments, error}
    """
    result = {"ok": False, "itemKey": None, "attachments": 0, "error": None}
    if not is_running():
        result["error"] = "Zotero 未运行或连接器不可用"
        return result

    session = "paperpipeline-" + uuid.uuid4().hex
    item_key = uuid.uuid4().hex[:8].upper()
    item = build_item(rec, item_key, tags)

    status, body = _request(
        "/connector/saveItems",
        {
            "sessionID": session,
            "uri": rec.get("url") or "https://example.invalid/paperpipeline",
            "single": False,
            "items": [item],
        },
        headers={"Content-Type": "application/json"},
    )
    if status != 201:
        result["error"] = f"saveItems 失败 ({status}) {body[:160]}"
        return result
    result["itemKey"] = item_key

    if collection_id:
        st, bd = _request(
            "/connector/updateSession",
            {
                "sessionID": session,
                "target": f"C{collection_id}",
                "tags": tags,
            },
            headers={"Content-Type": "application/json"},
        )
        if st != 200:
            result["error"] = f"归入分类失败 ({st}) {bd[:120]}"

    ok = 0
    for title, path in files:
        if not Path(path).exists():
            continue
        st, bd = save_attachment(session, item_key, title, Path(path))
        if st == 201:
            ok += 1
        elif result["error"] is None:
            result["error"] = f"附件 {title} 失败 ({st}) {str(bd)[:100]}"
    result["attachments"] = ok
    result["ok"] = ok > 0
    return result
