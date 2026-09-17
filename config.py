# -*- coding: utf-8 -*-
"""PaperPipeline - configuration loader.

Everything tunable lives in config.json next to this file. Set it up with
setup.py (or install.cmd); see README.md for what each field means.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LOG_DIR = APP_DIR / "logs"
WORK_DIR = APP_DIR / "work"
VENDOR_DIR = APP_DIR / "vendor"
EXTENSION_DIR = APP_DIR / "extension"

CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "state.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)


def _default_download_dirs() -> list[str]:
    """Guess where the browser puts downloads."""
    home = Path(os.path.expanduser("~"))
    candidates = [
        home / "Downloads",
        home / "OneDrive" / "Downloads",
        home / "下载",
    ]
    return [str(p) for p in candidates if p.exists()]


DEFAULTS: dict = {
    "service": {
        # Loopback port the local service listens on.
        "port": 8787,
    },
    "paths": {
        # pythonw.exe used for the silent autostart entry.
        "pythonw": "",
        # python.exe of the environment that has pdf2zh-next installed.
        "translatorPython": "",
        # Zotero data directory (the folder containing zotero.sqlite).
        "zoteroDataDir": "",
        # Folders searched for a PDF the browser already downloaded.
        "downloadDirs": [],
    },
    "translator": {
        # Any OpenAI-compatible endpoint.
        "openai_base_url": "",
        "openai_api_key": "",
        "openai_model": "",
        "lang_in": "en",
        "lang_out": "zh-CN",
        # Requests per second cap.
        "qps": 10,
        "pool_max_workers": 20,
        # Separate quota for the terminology-extraction pass.
        "term_qps": 10,
        "term_pool_max_workers": 20,
        # no_watermark | watermark | both
        "watermark_output_mode": "no_watermark",
        # Stop at the bibliography instead of translating references.
        "stop_at_references": True,
    },
    "zotero": {
        # Collection new papers land in. Created by setup.py.
        "tempCollection": "00 TEMP",
        # Tags applied to every imported item.
        "tags": ["待读", "自动入库"],
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    """Effective configuration: defaults merged with config.json."""
    raw = {}
    if CONFIG_FILE.exists():
        try:
            # utf-8-sig tolerates a BOM, which some editors add
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            raw = {}
    cfg = _merge(DEFAULTS, raw)
    if not cfg["paths"].get("downloadDirs"):
        cfg["paths"]["downloadDirs"] = _default_download_dirs()
    return cfg


def save_config(cfg: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(CONFIG_FILE)


# ---------------------------------------------------------------- accessors

def port() -> int:
    try:
        return int(load_config()["service"]["port"])
    except Exception:  # noqa: BLE001
        return 8787


def translator_settings() -> dict:
    cfg = load_config()["translator"]
    return {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in cfg.items()
    }


def translator_python() -> Path | None:
    raw = (load_config()["paths"].get("translatorPython") or "").strip()
    return Path(raw) if raw else None


def pythonw_path() -> Path | None:
    raw = (load_config()["paths"].get("pythonw") or "").strip()
    return Path(raw) if raw else None


def zotero_data_dir() -> Path | None:
    raw = (load_config()["paths"].get("zoteroDataDir") or "").strip()
    return Path(raw) if raw else None


def download_dirs() -> list[Path]:
    raw = load_config()["paths"].get("downloadDirs") or []
    return [Path(p) for p in raw if p]


def temp_collection_name() -> str:
    return load_config()["zotero"].get("tempCollection") or "00 TEMP"


def default_tags() -> list[str]:
    return list(load_config()["zotero"].get("tags") or [])


# ------------------------------------------------------------------- state

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    tmp.replace(STATE_FILE)
