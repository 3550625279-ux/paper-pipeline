# -*- coding: utf-8 -*-
"""Locate or download the source PDF for a paper."""
from __future__ import annotations

import mimetypes
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def is_pdf_file(path: Path) -> bool:
    try:
        if path.stat().st_size < 8000:
            return False
        with path.open("rb") as fh:
            return fh.read(5) == b"%PDF-"
    except Exception:  # noqa: BLE001
        return False


def download(url: str, dest: Path, timeout: int = 120) -> bool:
    """Download a PDF if the URL really serves a PDF."""
    if not url:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/pdf,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            head = resp.read(5)
            if head != b"%PDF-":
                return False
            with dest.open("wb") as out:
                out.write(head)
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    out.write(chunk)
        return is_pdf_file(dest)
    except Exception:  # noqa: BLE001
        return False


def find_downloaded(title: str, session_hint: str = "") -> Path | None:
    """Look for an already-downloaded PDF whose filename matches the title."""
    want = _norm(title)
    if len(want) < 12:
        return None
    want_head = want[:40]
    if session_hint:
        p = Path(session_hint)
        if p.is_file() and is_pdf_file(p):
            return p
    candidates: list[tuple[float, Path]] = []
    for folder in config.download_dirs():
        if not folder.exists():
            continue
        try:
            entries = list(folder.glob("*.pdf"))
        except Exception:  # noqa: BLE001
            continue
        for pdf in entries:
            stem = _norm(pdf.stem)
            if not stem:
                continue
            if stem.startswith(want_head) or want_head.startswith(stem[:40]):
                try:
                    candidates.append((pdf.stat().st_mtime, pdf))
                except Exception:  # noqa: BLE001
                    pass
    if not candidates:
        return None
    candidates.sort(reverse=True)
    for _, pdf in candidates:
        if is_pdf_file(pdf):
            return pdf
    return None


def acquire(rec: dict, work_dir: Path, hint: dict) -> Path | None:
    """Return a local path to the paper's PDF, downloading if needed.

    Order: an explicit local path from the extension, an already-downloaded
    copy, then the publisher/arXiv PDF URL.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    explicit = hint.get("localPath")
    if explicit:
        p = Path(explicit)
        if p.is_file() and is_pdf_file(p):
            return p

    found = find_downloaded(rec.get("title") or "", hint.get("downloadPath") or "")
    if found:
        return found

    dest = work_dir / "original.pdf"
    urls: list[str] = []
    if rec.get("pdfUrl"):
        urls.append(rec["pdfUrl"])
    aid = rec.get("arxiv")
    if aid:
        urls.append(f"https://arxiv.org/pdf/{aid}")
    doi = rec.get("doi")
    if doi and not aid:
        urls.append(f"https://doi.org/{doi}")
    for url in urls:
        if download(url, dest):
            return dest
    return None
