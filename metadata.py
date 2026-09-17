# -*- coding: utf-8 -*-
"""Resolve bibliographic metadata from a DOI, an arXiv id, or the web page."""
from __future__ import annotations

import gzip
import json
import re
import time
import urllib.parse
import urllib.request
import zlib

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _fetch(url: str, timeout: int = 35, tries: int = 4, json_out: bool = False):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Encoding": "gzip, deflate, identity",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in enc:
                raw = gzip.decompress(raw)
            elif "deflate" in enc:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            text = raw.decode("utf-8", "replace")
            return json.loads(text) if json_out else text
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.8 * (attempt + 1))
    raise last


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.strip()


def normalize_arxiv(value: str | None) -> str:
    if not value:
        return ""
    v = value.strip()
    v = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", v, flags=re.I)
    v = re.sub(r"\.pdf$", "", v, flags=re.I)
    v = re.sub(r"^arxiv:\s*", "", v, flags=re.I)
    return v.strip()


def _split_name(full: str) -> dict:
    full = re.sub(r"\s+", " ", (full or "").strip())
    if not full:
        return {"firstName": "", "lastName": "", "creatorType": "author"}
    parts = full.split(" ")
    if len(parts) == 1:
        return {"firstName": "", "lastName": parts[0], "creatorType": "author"}
    return {
        "firstName": " ".join(parts[:-1]),
        "lastName": parts[-1],
        "creatorType": "author",
    }


# --------------------------------------------------------------------- crossref

def _from_crossref_message(msg: dict) -> dict:
    title = (msg.get("title") or [""])[0]
    title = " ".join(re.sub(r"<[^>]+>", "", title).split())
    creators = []
    for a in msg.get("author") or []:
        creators.append({
            "firstName": a.get("given", "") or "",
            "lastName": a.get("family", "") or a.get("name", "") or "",
            "creatorType": "author",
        })
    year = ""
    for field in ("published-print", "published-online", "issued", "created"):
        parts = (msg.get(field) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0])
            break
    container = ""
    for key in ("container-title", "event", "institution"):
        val = msg.get(key)
        if isinstance(val, list) and val:
            container = val[0] or ""
            break
        if isinstance(val, dict) and val.get("name"):
            container = val["name"]
            break
    abstract = " ".join(
        re.sub(r"<[^>]+>", "", msg.get("abstract", "") or "").split()
    )
    return {
        "title": title,
        "creators": creators,
        "year": year,
        "venue": container,
        "volume": msg.get("volume", "") or "",
        "issue": msg.get("issue", "") or "",
        "pages": msg.get("page", "") or "",
        "doi": normalize_doi(msg.get("DOI", "")),
        "url": msg.get("URL", "") or "",
        "abstract": abstract[:1500],
        "crossrefType": msg.get("type", "") or "",
        "source": "crossref",
    }


def from_doi(doi: str) -> dict | None:
    doi = normalize_doi(doi)
    if not doi:
        return None
    try:
        data = _fetch(
            "https://api.crossref.org/works/" + urllib.parse.quote(doi),
            json_out=True,
        )
    except Exception:  # noqa: BLE001
        return None
    msg = (data or {}).get("message")
    return _from_crossref_message(msg) if msg else None


def search_crossref(title: str) -> dict | None:
    if not title or len(title) < 12:
        return None
    try:
        data = _fetch(
            "https://api.crossref.org/works?query.bibliographic="
            + urllib.parse.quote(title) + "&rows=5",
            json_out=True,
        )
    except Exception:  # noqa: BLE001
        return None
    want = set(re.findall(r"[a-z]{3,}", title.lower()))
    if not want:
        return None
    best, best_score = None, 0.0
    for item in (data or {}).get("message", {}).get("items", []):
        rec = _from_crossref_message(item)
        got = set(re.findall(r"[a-z]{3,}", rec["title"].lower()))
        if not got:
            continue
        score = len(got & want) / len(want)
        if score > best_score:
            best, best_score = rec, score
    return best if best_score >= 0.85 else None


# ---------------------------------------------------------------------- openalex

def from_openalex_doi(doi: str) -> dict | None:
    doi = normalize_doi(doi)
    if not doi:
        return None
    try:
        work = _fetch(
            "https://api.openalex.org/works/doi:" + urllib.parse.quote(doi),
            json_out=True,
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(work, dict) or not work.get("title"):
        return None
    return _from_openalex_work(work)


def _from_openalex_work(work: dict) -> dict:
    creators = []
    for a in work.get("authorships") or []:
        name = ((a.get("author") or {}).get("display_name") or "").strip()
        if name:
            creators.append(_split_name(name))
    src = ((work.get("primary_location") or {}).get("source") or {})
    biblio = work.get("biblio") or {}
    pages = ""
    if biblio.get("first_page"):
        pages = str(biblio["first_page"])
        if biblio.get("last_page"):
            pages += "-" + str(biblio["last_page"])
    return {
        "title": (work.get("title") or "").strip(),
        "creators": creators,
        "year": str(work.get("publication_year") or ""),
        "venue": src.get("display_name") or "",
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "pages": pages,
        "doi": normalize_doi(work.get("doi")),
        "url": "",
        "abstract": "",
        "crossrefType": work.get("type") or "",
        "source": "openalex",
    }


# ------------------------------------------------------------------- arxiv page

def from_arxiv(arxiv_id: str) -> dict | None:
    aid = normalize_arxiv(arxiv_id)
    if not aid:
        return None
    try:
        html = _fetch(f"https://arxiv.org/abs/{aid}")
    except Exception:  # noqa: BLE001
        return None
    return _parse_citation_meta(html, default_url=f"https://arxiv.org/abs/{aid}")


def _meta_content(html: str, *names: str) -> list[str]:
    out = []
    for name in names:
        for m in re.finditer(
            r'<meta[^>]+name=["\']' + re.escape(name)
            + r'["\'][^>]+content=["\']([^"\']*)["\']',
            html, re.I,
        ):
            out.append(_unescape(m.group(1)).strip())
    return [v for v in out if v]


def _unescape(text: str) -> str:
    import html as _html
    return _html.unescape(text)


def _parse_citation_meta(html: str, default_url: str = "") -> dict | None:
    """Parse Highwire/citation_* meta tags (used by arXiv, ACL, IEEE, Springer...)."""
    titles = _meta_content(html, "citation_title", "dc.title", "og:title")
    if not titles:
        return None
    title = " ".join(titles[0].split())
    if len(title) < 8:
        return None

    authors = _meta_content(html, "citation_author", "dc.creator")
    creators = [_split_name(a) for a in authors]
    creators = [c for c in creators if c["lastName"]]

    date = (_meta_content(
        html, "citation_publication_date", "citation_date", "dc.date",
        "citation_online_date",
    ) or [""])[0]
    year = ""
    m = re.search(r"(19|20)\d{2}", date)
    if m:
        year = m.group(0)

    venue = (_meta_content(
        html, "citation_journal_title", "citation_conference_title",
        "citation_inbook_title", "dc.source",
    ) or [""])[0]

    doi = normalize_doi((_meta_content(html, "citation_doi", "dc.identifier") or [""])[0])
    pdf_url = (_meta_content(html, "citation_pdf_url") or [""])[0]
    pages = (_meta_content(html, "citation_firstpage") or [""])[0]
    last = (_meta_content(html, "citation_lastpage") or [""])[0]
    if pages and last:
        pages = f"{pages}-{last}"
    volume = (_meta_content(html, "citation_volume") or [""])[0]
    issue = (_meta_content(html, "citation_issue") or [""])[0]

    return {
        "title": title,
        "creators": creators,
        "year": year,
        "venue": venue,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "doi": doi,
        "url": (_meta_content(html, "citation_public_url", "og:url")
                or [default_url])[0],
        "abstract": " ".join(
            (_meta_content(html, "citation_abstract", "dc.description",
                           "og:description") or [""])[0].split()
        )[:1500],
        "pdfUrl": pdf_url,
        "crossrefType": "",
        "source": "page",
    }


# ---------------------------------------------------------------------- resolve

def resolve(hint: dict) -> dict:
    """Resolve the best record we can from the hints the extension sent.

    hint may contain: doi, arxiv, title, authors, year, pdfUrl, pageUrl, html
    """
    doi = normalize_doi(hint.get("doi"))
    arxiv = normalize_arxiv(hint.get("arxiv"))

    if doi:
        rec = from_doi(doi)
        if rec:
            return _finish(rec, hint)
        rec = from_openalex_doi(doi)
        if rec:
            return _finish(rec, hint)

    if arxiv:
        rec = from_openalex_doi(f"10.48550/arXiv.{arxiv}")
        if rec:
            rec["arxiv"] = arxiv
            if not rec.get("url"):
                rec["url"] = f"https://arxiv.org/abs/{arxiv}"
            return _finish(rec, hint)
        rec = from_arxiv(arxiv)
        if rec:
            rec["arxiv"] = arxiv
            return _finish(rec, hint)

    # last resort: match by title
    title = hint.get("title") or ""
    rec = search_crossref(title)
    if rec:
        return _finish(rec, hint)
    return _finish({}, hint)


def _finish(rec: dict, hint: dict) -> dict:
    """Fill gaps from the extension's own page-scraped hints."""
    merged = dict(rec)
    if not merged.get("title"):
        merged["title"] = hint.get("title") or ""
    if not merged.get("creators"):
        authors = hint.get("authors") or []
        if isinstance(authors, str):
            authors = [a for a in re.split(r";|\band\b", authors) if a.strip()]
        merged["creators"] = [_split_name(a) for a in authors if a]
    if not merged.get("year"):
        m = re.search(r"(19|20)\d{2}", str(hint.get("year") or ""))
        merged["year"] = m.group(0) if m else ""
    if not merged.get("venue"):
        merged["venue"] = hint.get("venue") or ""
    if not merged.get("doi"):
        merged["doi"] = normalize_doi(hint.get("doi"))
    if not merged.get("url"):
        merged["url"] = hint.get("pageUrl") or ""
    if hint.get("pdfUrl") and not merged.get("pdfUrl"):
        merged["pdfUrl"] = hint["pdfUrl"]
    if hint.get("abstract") and not merged.get("abstract"):
        merged["abstract"] = str(hint["abstract"])[:1500]
    if hint.get("arxiv"):
        merged["arxiv"] = normalize_arxiv(hint["arxiv"])
    merged.setdefault("abstract", "")
    merged.setdefault("volume", "")
    merged.setdefault("issue", "")
    merged.setdefault("pages", "")
    return merged


def looks_like_paper(rec: dict) -> bool:
    title = rec.get("title") or ""
    if len(title) < 12:
        return False
    if rec.get("doi") or rec.get("arxiv"):
        return True
    # needs at least a year or authors to be plausible
    return bool(rec.get("creators")) and bool(rec.get("year"))


def title_from_pdf(path) -> str:
    """Read the largest text on page 1 as a rough title (fallback only)."""
    try:
        import fitz  # PyMuPDF
    except Exception:  # noqa: BLE001
        return ""
    try:
        with fitz.open(str(path)) as doc:
            if not doc.page_count:
                return ""
            page = doc[0]
            blocks = page.get_text("dict").get("blocks", [])
    except Exception:  # noqa: BLE001
        return ""
    best, best_size = "", 0.0
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                size = float(span.get("size") or 0)
                if len(text) > 18 and size > best_size:
                    best, best_size = text, size
    return " ".join(best.split())
