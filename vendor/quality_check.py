"""
PaperPipeline - 译后漏翻检测器

对 mono PDF 逐页提取文本，检测大段英文 prose。
用于在 pdf2zh_next exit code 不可信的情况下，独立验证翻译质量。

不使用 LLM，纯本地 PyMuPDF 文本分析。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


# ── 排除 pattern ─────────────────────────────────────────────

# 这些内容即使是英文也不应被标记为"漏翻"
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_DOI_RE = re.compile(r"\b10\.\d{4,}/\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_CODE_INDICATORS = re.compile(
    r"(?:def |class |import |from |return |if __name__|print\(|"
    r"self\.|lambda |raise |try:|except |for .+ in |while )",
    re.IGNORECASE,
)
# 作者列表常见 pattern：多个人名用逗号分隔
_AUTHOR_LIST_RE = re.compile(
    r"^[A-Z][a-z]+ [A-Z][a-z]+(?:\s*,\s*[A-Z][a-z]+ [A-Z][a-z]+){2,}"
)
# 参考文献条目
_REF_ENTRY_RE = re.compile(r"^\s*\[\d+\]\s")
# 数学/公式 token
_MATH_HEAVY_RE = re.compile(r"[=∑∏∫≤≥∈∀∃→←↔⊂⊃∩∪]")


def _is_exclusion_line(line: str) -> bool:
    """判断一行是否属于不应被标记为漏翻的内容。"""
    stripped = line.strip()
    if not stripped:
        return True
    # URL / DOI / Email
    if _URL_RE.search(stripped) and len(stripped) < 200:
        return True
    if _DOI_RE.search(stripped):
        return True
    if _EMAIL_RE.search(stripped):
        return True
    # 代码
    if _CODE_INDICATORS.search(stripped):
        return True
    # 参考文献条目
    if _REF_ENTRY_RE.match(stripped):
        return True
    # 数学公式密集行
    math_chars = len(_MATH_HEAVY_RE.findall(stripped))
    if math_chars >= 3:
        return True
    # 很短的行（标题、页眉、单个单词等）
    if len(stripped.split()) <= 4:
        return True
    return False


def _count_english_words(text: str) -> int:
    """计算文本中的英文单词数。"""
    # 匹配连续的 ASCII 字母序列（至少 2 个字母）
    words = re.findall(r"\b[a-zA-Z]{2,}\b", text)
    return len(words)


def _english_char_ratio(text: str) -> float:
    """计算文本中 ASCII 字母占所有非空白字符的比例。"""
    non_ws = re.sub(r"\s", "", text)
    if not non_ws:
        return 0.0
    ascii_letters = sum(1 for c in non_ws if c.isascii() and c.isalpha())
    return ascii_letters / len(non_ws)


# ── 连续英文段落检测 ─────────────────────────────────────────

@dataclass
class UntranslatedBlock:
    """一个疑似未翻译的连续英文文本块。"""
    page: int  # 1-based
    english_words: int
    english_ratio: float
    sample: str  # 前 120 个字符
    line_count: int


def _detect_blocks_on_page(
    page_text: str,
    page_num: int,  # 1-based
    word_threshold: int = 25,
    ratio_threshold: float = 0.60,
) -> list[UntranslatedBlock]:
    """
    检测单页中的连续英文文本块。

    策略：将页面文本按行分组，连续的非排除英文行合并为一个 block，
    然后对每个 block 计算英文指标。
    """
    lines = page_text.split("\n")
    blocks: list[UntranslatedBlock] = []

    current_english_lines: list[str] = []

    def _flush():
        if not current_english_lines:
            return
        combined = " ".join(current_english_lines)
        eng_words = _count_english_words(combined)
        eng_ratio = _english_char_ratio(combined)
        if eng_words >= word_threshold and eng_ratio >= ratio_threshold:
            sample = combined[:120].strip()
            if len(combined) > 120:
                sample += "…"
            blocks.append(UntranslatedBlock(
                page=page_num,
                english_words=eng_words,
                english_ratio=round(eng_ratio, 3),
                sample=sample,
                line_count=len(current_english_lines),
            ))

    for line in lines:
        stripped = line.strip()
        if _is_exclusion_line(stripped):
            _flush()
            current_english_lines = []
            continue

        # 判断这一行是否主要是英文
        eng_ratio = _english_char_ratio(stripped)
        eng_words = _count_english_words(stripped)
        if eng_ratio >= 0.5 and eng_words >= 3:
            current_english_lines.append(stripped)
        else:
            _flush()
            current_english_lines = []

    _flush()
    return blocks


# ── 主 API ────────────────────────────────────────────────────

def check_untranslated_prose(
    mono_pdf_path: str,
    pages_to_check: list[int] | None = None,
    english_word_threshold: int = 25,
    english_ratio_threshold: float = 0.60,
) -> list[dict]:
    """
    检测 mono PDF 中疑似未翻译的英文大段。

    Args:
        mono_pdf_path: mono PDF 文件路径
        pages_to_check: 要检查的 1-based 页码列表；None 表示检查所有页
        english_word_threshold: 连续英文单词数阈值
        english_ratio_threshold: 英文字符占比阈值

    Returns:
        [
            {
                "page": 1,             # 1-based
                "english_words": 47,
                "english_ratio": 0.85,
                "sample": "In this paper, we propose...",
                "line_count": 5,
            },
            ...
        ]
    """
    path = Path(mono_pdf_path)
    if not path.is_file():
        return []

    doc = fitz.open(str(path))
    results: list[dict] = []

    for page_idx in range(len(doc)):
        page_num = page_idx + 1  # 1-based
        if pages_to_check is not None and page_num not in pages_to_check:
            continue

        page = doc[page_idx]
        text = page.get_text("text")
        if not text or not text.strip():
            continue

        blocks = _detect_blocks_on_page(
            text, page_num,
            word_threshold=english_word_threshold,
            ratio_threshold=english_ratio_threshold,
        )
        for b in blocks:
            results.append({
                "page": b.page,
                "english_words": b.english_words,
                "english_ratio": b.english_ratio,
                "sample": b.sample,
                "line_count": b.line_count,
            })

    doc.close()
    return results


# ── CLI 测试入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python quality_check.py <mono_pdf_path> [<page1> <page2> ...]")
        sys.exit(1)

    pdf = sys.argv[1]
    pages = [int(p) for p in sys.argv[2:]] if len(sys.argv) > 2 else None

    results = check_untranslated_prose(pdf, pages_to_check=pages)
    if results:
        print(f"检测到 {len(results)} 处疑似未翻译段落：")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("未检测到漏翻。")
