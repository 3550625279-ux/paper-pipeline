"""
PaperPipeline - References / Bibliography 边界检测器

使用 PyMuPDF 结构化文本分析（非 OCR），多因子评分。
返回 1-based 页码，或 None（表示未检测到，应翻译全文）。

用于让翻译只覆盖正文，跳过参考文献区，既省钱又让译文更好读。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


# ── heading 变体 ──────────────────────────────────────────────

_HEADING_VARIANTS: list[str] = [
    "references",
    "reference",
    "bibliography",
    "works cited",
    "literature cited",
    "references and notes",
]

_APPENDIX_VARIANTS: list[str] = [
    "appendix",
    "appendices",
    "supplementary material",
    "supplementary materials",
    "supplemental material",
    "supplemental materials",
]

# 带编号前缀的 pattern，如 "6 References"、"7. References"、"VI References"
_ROMAN_RE = r"(?:[IVXLC]+)"
_NUM_PREFIX_RE = re.compile(
    rf"^(?:\d+\.?\s+|{_ROMAN_RE}\.?\s+)?(.+)$", re.IGNORECASE
)

# bibliography-like evidence
_YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}[a-z]?\b")
_BIB_KEYWORDS = [
    "et al.", "doi", "doi.org", "arxiv", "proceedings",
    "journal", "conference", "vol.", "pp.", "trans.",
    "ieee", "acm", "springer", "publisher",
]
_NUMBERED_REF_RE = re.compile(r"^\s*\[\d+\]")
_DOT_NUMBERED_REF_RE = re.compile(r"^\s*\d+\.\s")

# TOC evidence
_TOC_HEADINGS = ["contents", "table of contents"]
_TOC_DOTLINE_RE = re.compile(r"\.{4,}\s*\d+\s*$")


# ── helpers ───────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Strip, collapse whitespace, NFKC normalize."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_lines_from_page(page: fitz.Page) -> list[dict]:
    """
    从 page.get_text("dict") 提取结构化行信息。
    每行返回 {text, font_size, font_name, is_bold, bbox}。
    """
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    lines: list[dict] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # text block only
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text_parts = []
            sizes = []
            fonts = []
            for span in spans:
                t = span.get("text", "")
                if t.strip():
                    text_parts.append(t)
                    sizes.append(span.get("size", 0))
                    fonts.append(span.get("font", ""))
            full_text = _normalize(" ".join(text_parts))
            if not full_text:
                continue
            avg_size = sum(sizes) / len(sizes) if sizes else 0
            font_str = " ".join(fonts).lower()
            is_bold = any(
                kw in font_str
                for kw in ["bold", "black", "heavy", "demi"]
            )
            lines.append({
                "text": full_text,
                "font_size": avg_size,
                "font_name": font_str,
                "is_bold": is_bold,
                "bbox": line.get("bbox", (0, 0, 0, 0)),
            })
    return lines


def _is_heading_match(text: str) -> bool:
    """判断 normalized text 是否匹配 References heading 变体。"""
    lower = text.lower().strip()
    # 去掉可能的编号前缀
    m = _NUM_PREFIX_RE.match(lower)
    core = m.group(1).strip() if m else lower
    return core in _HEADING_VARIANTS


def _is_appendix_match(text: str) -> bool:
    """判断 normalized text 是否匹配 Appendix heading 变体。"""
    lower = text.lower().strip()
    m = _NUM_PREFIX_RE.match(lower)
    core = m.group(1).strip() if m else lower
    # 精确匹配或以 "appendix " 开头（如 "Appendix A"）
    if core in _APPENDIX_VARIANTS:
        return True
    for variant in _APPENDIX_VARIANTS:
        if core.startswith(variant + " "):
            return True
    return False


def _compute_median_font_size(all_lines: list[dict]) -> float:
    """计算所有行的中位 font size。"""
    sizes = [ln["font_size"] for ln in all_lines if ln["font_size"] > 0]
    if not sizes:
        return 0
    sizes.sort()
    mid = len(sizes) // 2
    return sizes[mid]


# ── scoring ───────────────────────────────────────────────────

@dataclass
class _Candidate:
    page_index: int  # 0-based
    line_index: int
    heading_text: str
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)


def _score_candidate(
    cand: _Candidate,
    page_lines: list[dict],
    line_idx: int,
    page_index: int,
    total_pages: int,
    median_font_size: float,
    all_page_lines: dict[int, list[dict]],
) -> None:
    """为一个候选 heading 打分。"""

    heading_line = page_lines[line_idx]

    # ── 1. Heading evidence (+4) ──
    # 整行基本只包含 heading 变体（允许编号前缀）
    stripped = heading_line["text"].strip()
    if len(stripped) < 80:  # 真正的 heading 不会太长
        cand.score += 4
        cand.evidence.append(f"heading_match: '{stripped}'")

    # ── 2. PDF 后部 (+1) ──
    position_ratio = page_index / max(total_pages, 1)
    if position_ratio >= 0.45:
        cand.score += 1
        cand.evidence.append(f"position: {position_ratio:.2f}")

    # ── 3. Typography (+1) ──
    fs = heading_line["font_size"]
    if median_font_size > 0 and fs > median_font_size * 1.15:
        cand.score += 1
        cand.evidence.append(f"larger_font: {fs:.1f} vs median {median_font_size:.1f}")
    elif heading_line["is_bold"]:
        cand.score += 1
        cand.evidence.append("bold_font")

    # ── 4. Bibliography evidence (+2) ──
    # 收集 heading 之后的 20-40 行（可跨页）
    following_lines: list[str] = []
    # 当前页剩余行
    for fl in page_lines[line_idx + 1:]:
        following_lines.append(fl["text"])
    # 后续页面的行
    for next_pi in range(page_index + 1, min(page_index + 4, total_pages)):
        if next_pi in all_page_lines:
            for fl in all_page_lines[next_pi]:
                following_lines.append(fl["text"])
        if len(following_lines) >= 40:
            break

    bib_score = 0
    for fl_text in following_lines[:40]:
        fl_lower = fl_text.lower()
        if _YEAR_RE.search(fl_text):
            bib_score += 1
        if any(kw in fl_lower for kw in _BIB_KEYWORDS):
            bib_score += 1
        if _NUMBERED_REF_RE.match(fl_text) or _DOT_NUMBERED_REF_RE.match(fl_text):
            bib_score += 1

    if bib_score >= 5:
        cand.score += 2
        cand.evidence.append(f"bib_evidence: {bib_score}")
    elif bib_score >= 2:
        cand.score += 1
        cand.evidence.append(f"bib_evidence_weak: {bib_score}")

    # ── 5. TOC penalty (-3) ──
    page_text_lower = " ".join(ln["text"] for ln in page_lines).lower()
    is_toc = any(th in page_text_lower for th in _TOC_HEADINGS)
    dot_lines = sum(1 for ln in page_lines if _TOC_DOTLINE_RE.search(ln["text"]))
    if is_toc or dot_lines >= 5:
        cand.score -= 3
        cand.evidence.append(f"toc_penalty: toc={is_toc}, dot_lines={dot_lines}")


# ── main API ──────────────────────────────────────────────────

def detect_reference_start(pdf_path: str) -> int | None:
    """
    检测 References / Bibliography heading 所在的 1-based PDF 页码。

    如果 confidence 不足，返回 None（调用方应翻译全文）。
    """
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    if total_pages == 0:
        doc.close()
        return None

    # Step 1: 扫描范围 — 从 35% 处开始
    start_scan = max(0, int(total_pages * 0.35))

    # 预先提取所有扫描范围内的行
    all_page_lines: dict[int, list[dict]] = {}
    for pi in range(start_scan, total_pages):
        page = doc[pi]
        all_page_lines[pi] = _extract_lines_from_page(page)

    # 计算中位 font size（用于 typography 比较）
    all_lines_flat = []
    for lines in all_page_lines.values():
        all_lines_flat.extend(lines)
    median_font_size = _compute_median_font_size(all_lines_flat)

    # Step 2-4: 寻找并评分候选
    candidates: list[_Candidate] = []

    for pi in range(start_scan, total_pages):
        page_lines = all_page_lines[pi]
        for li, line_info in enumerate(page_lines):
            if _is_heading_match(line_info["text"]):
                cand = _Candidate(
                    page_index=pi,
                    line_index=li,
                    heading_text=line_info["text"],
                )
                _score_candidate(
                    cand, page_lines, li, pi, total_pages,
                    median_font_size, all_page_lines,
                )
                candidates.append(cand)

    doc.close()

    if not candidates:
        return None

    # 选择得分最高的候选；同分时取最靠后的（保守）
    best = max(candidates, key=lambda c: (c.score, c.page_index))

    # 置信度阈值：至少需要 5 分
    if best.score < 5:
        return None

    # 返回 1-based 页码
    return best.page_index + 1


def detect_reference_start_detailed(pdf_path: str, *, detect_appendix: bool = False) -> dict:
    """
    与 detect_reference_start 相同逻辑，但返回详细信息，供测试和日志使用。

    Args:
        pdf_path: PDF 文件路径
        detect_appendix: 是否同时检测 Appendix 起始页

    返回:
        {
            "pdf_path": str,
            "total_pages": int,
            "result_page": int | None,  # 1-based
            "appendix_page": int | None,  # 1-based（仅 detect_appendix=True 时有值）
            "candidates": [
                {
                    "page": int,  # 1-based
                    "heading": str,
                    "score": float,
                    "evidence": [str],
                },
                ...
            ],
        }
    """
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    result: dict = {
        "pdf_path": str(pdf_path),
        "total_pages": total_pages,
        "result_page": None,
        "appendix_page": None,
        "candidates": [],
    }

    if total_pages == 0:
        doc.close()
        return result

    start_scan = max(0, int(total_pages * 0.35))

    all_page_lines: dict[int, list[dict]] = {}
    for pi in range(start_scan, total_pages):
        page = doc[pi]
        all_page_lines[pi] = _extract_lines_from_page(page)

    all_lines_flat = []
    for lines in all_page_lines.values():
        all_lines_flat.extend(lines)
    median_font_size = _compute_median_font_size(all_lines_flat)

    candidates: list[_Candidate] = []
    appendix_page_found: int | None = None

    for pi in range(start_scan, total_pages):
        page_lines = all_page_lines[pi]
        for li, line_info in enumerate(page_lines):
            if _is_heading_match(line_info["text"]):
                cand = _Candidate(
                    page_index=pi,
                    line_index=li,
                    heading_text=line_info["text"],
                )
                _score_candidate(
                    cand, page_lines, li, pi, total_pages,
                    median_font_size, all_page_lines,
                )
                candidates.append(cand)

            # ⑧ appendix 检测 — 只记录第一次出现
            if detect_appendix and appendix_page_found is None:
                if _is_appendix_match(line_info["text"]):
                    # 基本 typography 检查：font size 需 >= 中位数
                    if line_info["font_size"] >= median_font_size * 0.95:
                        appendix_page_found = pi + 1  # 1-based

    doc.close()

    if detect_appendix and appendix_page_found is not None:
        result["appendix_page"] = appendix_page_found

    for c in candidates:
        result["candidates"].append({
            "page": c.page_index + 1,
            "heading": c.heading_text,
            "score": c.score,
            "evidence": c.evidence,
        })

    if candidates:
        best = max(candidates, key=lambda c: (c.score, c.page_index))
        if best.score >= 5:
            result["result_page"] = best.page_index + 1


    return result


def detect_appendix_after_references(pdf_path: str, ref_page: int) -> int | None:
    """Return the 1-based page of an appendix following the references.

    Detection is deliberately conservative: a missed heading should cause the
    caller to translate the uncertain tail, rather than silently drop it.
    Pages containing both references and an appendix must be translated whole.
    """
    if not isinstance(ref_page, int) or isinstance(ref_page, bool) or ref_page < 1:
        return None

    with fitz.open(pdf_path) as doc:
        if ref_page > len(doc):
            return None
        pages = {i: _extract_lines_from_page(doc[i])
                 for i in range(ref_page - 1, len(doc))}

    all_lines = [line for page in pages.values() for line in page]
    median = _compute_median_font_size(all_lines)

    def is_toc(lines: list[dict]) -> bool:
        texts = [line["text"].lower() for line in lines]
        return (any(text in _TOC_HEADINGS for text in texts)
                or sum(bool(_TOC_DOTLINE_RE.search(text)) for text in texts) >= 5)

    def heading_like(line: dict) -> bool:
        text = line["text"].strip()
        return (0 < len(text) <= 100 and not _TOC_DOTLINE_RE.search(text)
                and not text.endswith((".", ",", ";", "?", "!"))
                and (line["is_bold"] or not median
                     or line["font_size"] >= median * 1.05))

    def paired_title(lines: list[dict], index: int) -> bool:
        """Recognize a separate letter and title on the same baseline."""
        if index + 1 >= len(lines):
            return False
        marker, title = lines[index], lines[index + 1]
        x0, y0, x1, y1 = marker["bbox"]
        tx0, ty0, _, ty1 = title["bbox"]
        same_baseline = abs(y0 - ty0) <= max(2.0, (y1 - y0) * 0.3)
        close = x1 <= tx0 <= x1 + max(50.0, marker["font_size"] * 4)
        return (same_baseline and close and heading_like(marker)
                and heading_like(title) and bool(re.search(r"[A-Za-z]", title["text"]))
                and abs(marker["font_size"] - title["font_size"]) <= 1.0)

    # On the reference-start page, only inspect lines after the heading.
    # PyMuPDF preserves the source's reading order, including its columns.
    first_lines = pages[ref_page - 1]
    ref_indices = [i for i, line in enumerate(first_lines)
                   if _is_heading_match(line["text"])]
    first_offset = ref_indices[-1] + 1 if ref_indices else 0

    for pi, lines in pages.items():
        if is_toc(lines):
            continue
        offset = first_offset if pi == ref_page - 1 else 0
        for index in range(offset, len(lines)):
            line = lines[index]
            text = line["text"].strip()
            if _is_appendix_match(text) and heading_like(line):
                return pi + 1

            # ACL-style papers often use only "A  Related Work". Require
            # both a title and a subsequent A.1 subsection or B section so a
            # lone author initial, table label or citation is not enough.
            separate_heading = text == "A" and paired_title(lines, index)
            combined_heading = bool(re.fullmatch(r"A\s+[A-Za-z][^.!?]{1,90}", text)) and heading_like(line)
            if not (separate_heading or combined_heading):
                continue
            later = lines[index + (2 if separate_heading else 1):]
            for later_page in range(pi + 1, min(pi + 4, len(pages) + ref_page - 1)):
                later.extend(pages.get(later_page, []))
            has_subsection = any(re.match(r"^A\.\d+(?:\s|$)", item["text"])
                                 and heading_like(item) for item in later)
            has_next_section = any(
                (item["text"] == "B" and paired_title(later, j)) or
                (bool(re.fullmatch(r"B\s+[A-Za-z][^.!?]{1,90}", item["text"]))
                 and heading_like(item))
                for j, item in enumerate(later)
            )
            if has_subsection or has_next_section:
                return pi + 1
    return None


# ── CLI 测试入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python detector.py <pdf_path> [<pdf_path> ...]")
        sys.exit(1)

    for path in sys.argv[1:]:
        if not Path(path).is_file():
            print(f"SKIP: {path} (not found)")
            continue
        info = detect_reference_start_detailed(path)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        print()
