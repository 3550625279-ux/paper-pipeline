# -*- coding: utf-8 -*-
"""Translation worker. Runs inside the pdf2zh_next conda env.

Reads a job file, translates one PDF, streams JSON progress lines on stdout,
then reports the produced files. Must be launched as a subprocess because
babeldoc uses multiprocessing.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

PDF2ZH_PY = Path(sys.executable)


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------- settings build

def build_settings(pdf: Path, out_dir: Path, cfg: dict, *,
                   enhance_compatibility: bool = False,
                   ignore_cache: bool = False,
                   pages: str | None = None):
    from pdf2zh_next.config.cli_env_model import CLIEnvSettingsModel, to_settings_model
    from pdf2zh_next.config.model import (
        BasicSettings, PDFSettings, TranslationSettings,
    )
    from pdf2zh_next.config.translate_engine_model import OpenAISettings

    CLIEnvSettingsModel.to_settings_model = to_settings_model

    model = CLIEnvSettingsModel(
        report_interval=0.8,
        openai=True,
        openai_detail=OpenAISettings(
            openai_model=cfg["openai_model"],
            openai_base_url=cfg["openai_base_url"],
            openai_api_key=cfg["openai_api_key"],
        ),
        basic=BasicSettings(input_files={str(pdf)}, debug=False),
        translation=TranslationSettings(
            lang_in=cfg.get("lang_in", "en"),
            lang_out=cfg.get("lang_out", "zh-CN"),
            output=str(out_dir),
            qps=int(cfg.get("qps", 10)),
            pool_max_workers=int(cfg.get("pool_max_workers", 20)),
            term_qps=int(cfg.get("term_qps", cfg.get("qps", 10))),
            term_pool_max_workers=int(
                cfg.get("term_pool_max_workers", cfg.get("pool_max_workers", 20))
            ),
            ignore_cache=ignore_cache,
        ),
        pdf=PDFSettings(
            watermark_output_mode=cfg.get("watermark_output_mode", "no_watermark"),
            enhance_compatibility=enhance_compatibility,
            pages=pages,
        ),
    )
    return model.to_settings_model()


async def stream_translate(settings, pdf: Path, label: str) -> None:
    from pdf2zh_next.high_level import do_translate_async_stream

    last_sent = -1.0
    async for ev in do_translate_async_stream(settings, pdf):
        etype = ev.get("type")
        if etype == "progress_update":
            overall = float(ev.get("overall_progress") or 0.0)
            stage = str(ev.get("stage") or "")
            if overall - last_sent >= 0.4 or stage != getattr(
                    stream_translate, "_stage", None):
                stream_translate._stage = stage
                last_sent = overall
                emit({
                    "type": "progress",
                    "phase": label,
                    "stage": stage,
                    "current": ev.get("stage_current"),
                    "total": ev.get("stage_total"),
                    "overall": round(overall, 1),
                })
        elif etype == "progress_end":
            emit({
                "type": "progress",
                "phase": label,
                "stage": str(ev.get("stage") or ""),
                "current": ev.get("stage_total"),
                "total": ev.get("stage_total"),
                "overall": round(float(ev.get("overall_progress") or 0.0), 1),
            })
        elif etype == "finish":
            emit({"type": "log", "message": f"{label} 完成"})


# -------------------------------------------------------------- output helpers

def collect_outputs(out_dir: Path, stem: str) -> dict:
    found: dict[str, Path] = {}
    if not out_dir.exists():
        return found
    for p in out_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name.lower()
        if not name.startswith(stem.lower()):
            continue
        if name.endswith(".dual.pdf"):
            found["dual"] = p
        elif name.endswith(".mono.pdf"):
            found["mono"] = p
        elif name.endswith(".csv"):
            found["glossary"] = p
    return found


def preview_text(pdf: Path, page_limit: int = 6) -> str:
    try:
        import fitz
    except Exception:  # noqa: BLE001
        return ""
    try:
        with fitz.open(str(pdf)) as doc:
            chunks = []
            for i in range(min(page_limit, doc.page_count)):
                chunks.append(doc[i].get_text("text") or "")
        return "\n".join(chunks)
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------------- main flow

def run_job(job: dict) -> int:
    cfg = job["translatorConfig"]
    work = Path(job["workDir"])
    src = Path(job["sourcePdf"])
    work.mkdir(parents=True, exist_ok=True)
    out_dir = work / "translated"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Papers use a temp copy so output naming stays predictable.
    local = work / "original.pdf"
    if src.resolve() != local.resolve():
        shutil.copy2(src, local)

    # detector.py / quality_check.py ship in vendor/ next to this file
    vendor_dir = Path(job.get("vendorDir") or (Path(__file__).resolve().parent / "vendor"))
    if vendor_dir.exists() and str(vendor_dir) not in sys.path:
        sys.path.insert(0, str(vendor_dir))

    emit({"type": "phase", "phase": "detect", "message": "分析论文结构……"})

    pages_str = None
    ref_page = None
    if cfg.get("stop_at_references", True):
        try:
            from detector import detect_reference_start
            ref_page = detect_reference_start(str(local))
        except Exception as exc:  # noqa: BLE001
            emit({"type": "log", "message": f"参考文献检测失败：{exc}"})
            ref_page = None
        if ref_page:
            pages_str = f"1-{ref_page}"
            emit({"type": "log",
                  "message": f"参考文献从第 {ref_page} 页开始，翻译 1-{ref_page}"})
        else:
            emit({"type": "log", "message": "未识别到参考文献边界，翻译全文"})

    stem = local.stem
    t0 = time.time()

    emit({"type": "phase", "phase": "translate", "message": "正在翻译……"})
    settings = build_settings(local, out_dir, cfg, pages=pages_str)
    asyncio.run(stream_translate(settings, local, "translate"))

    files = collect_outputs(out_dir, stem)
    untranslated: list[dict] = []
    if files.get("mono") and cfg.get("stop_at_references", True):
        emit({"type": "phase", "phase": "quality", "message": "检查漏翻……"})
        try:
            from quality_check import check_untranslated_prose
            check_pages = list(range(1, (ref_page or 999) + 1))
            untranslated = check_untranslated_prose(
                str(files["mono"]), pages_to_check=check_pages,
            )
        except Exception as exc:  # noqa: BLE001
            emit({"type": "log", "message": f"漏翻检测失败：{exc}"})

    if untranslated:
        emit({"type": "warn",
              "message": f"发现 {len(untranslated)} 处疑似漏翻，用兼容模式重试"})
        for f in list(files.values()):
            try:
                f.unlink()
            except Exception:  # noqa: BLE001
                pass
        emit({"type": "phase", "phase": "retry",
              "message": "兼容模式重试中……"})
        settings2 = build_settings(
            local, out_dir, cfg,
            enhance_compatibility=True, ignore_cache=True,
            pages=pages_str,
        )
        asyncio.run(stream_translate(settings2, local, "retry"))
        files = collect_outputs(out_dir, stem)
        try:
            from quality_check import check_untranslated_prose
            untranslated = check_untranslated_prose(
                str(files.get("mono", "")) if files.get("mono") else "",
                pages_to_check=list(range(1, (ref_page or 999) + 1)),
            ) if files.get("mono") else []
        except Exception:  # noqa: BLE001
            untranslated = []

    elapsed = time.time() - t0
    pages = 0
    try:
        import fitz
        with fitz.open(str(local)) as doc:
            pages = doc.page_count
    except Exception:  # noqa: BLE001
        pass

    emit({
        "type": "done",
        "files": {k: str(v) for k, v in files.items()},
        "original": str(local),
        "pages": pages,
        "translatedPages": ref_page or pages,
        "untranslated": len(untranslated),
        "seconds": round(elapsed, 1),
    })
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        emit({"type": "error", "message": "usage: worker_translate.py <jobfile>"})
        return 2
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        return run_job(job)
    except Exception as exc:  # noqa: BLE001
        import traceback
        emit({"type": "error", "message": str(exc),
              "trace": traceback.format_exc()[-1500:]})
        return 1


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
