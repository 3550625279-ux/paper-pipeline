# -*- coding: utf-8 -*-
"""PaperPipeline - interactive setup wizard.

Run this once (install.cmd calls it). It:
  1. locates the Zotero data directory
  2. locates the Python environment that has pdf2zh-next installed
  3. asks for the translation model credentials
  4. creates the Zotero collection new papers should land in
  5. writes config.json, state.json and the extension config
  6. optionally registers autostart
"""
from __future__ import annotations

import json
import os
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config  # noqa: E402

ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
LINE = "-" * 68
NL = chr(10)


# ------------------------------------------------------------------- helpers

def say(text: str = "") -> None:
    print(text, flush=True)


def head(title: str) -> None:
    say()
    say(LINE)
    say("  " + title)
    say(LINE)


def ask(prompt: str, default: str = "", required: bool = False) -> str:
    label = prompt + (f" [{default}]" if default else "")
    while True:
        try:
            raw = input(label + NL + "> ").strip()
        except EOFError:
            raw = ""
        value = raw or default
        if value or not required:
            return value
        say("  这一项不能为空，请再输一次。")


def ask_yes(prompt: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    try:
        raw = input(f"{prompt} [{hint}] ").strip().lower()
    except EOFError:
        raw = ""
    if not raw:
        return default_yes
    return raw in ("y", "yes", "1", "t", "true", "是")


def exists(path) -> bool:
    try:
        return Path(path).exists()
    except Exception:  # noqa: BLE001
        return False


def no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# -------------------------------------------------------------- 1. Zotero dir

def detect_zotero_data() -> str:
    """Read Zotero's own preferences to find the data directory."""
    appdata = os.environ.get("APPDATA") or ""
    profiles_root = Path(appdata) / "Zotero" / "Zotero" / "Profiles"
    if not profiles_root.is_dir():
        return ""
    for profile in sorted(profiles_root.glob("*.default")):
        prefs = profile / "prefs.js"
        if not prefs.is_file():
            continue
        try:
            text = prefs.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        use_custom = re.search(
            r'user_pref\("extensions\.zotero\.useDataDir",\s*(true|false)\)',
            text,
        )
        custom = re.search(
            r'user_pref\("extensions\.zotero\.dataDir",\s*"([^"]+)"\)',
            text,
        )
        if custom and (not use_custom or use_custom.group(1) == "true"):
            candidate = custom.group(1).replace("\\\\", "\\")
            if exists(candidate):
                return candidate
    return ""


# -------------------------------------------------------- 2. translator python

def looks_like_translator(python_exe: Path) -> bool:
    """Cheap check: does this env have pdf2zh_next installed?"""
    root = python_exe.parent
    probes = [root / "Lib" / "site-packages" / "pdf2zh_next"]
    for minor in ("3.13", "3.12", "3.11", "3.10"):
        probes.append(root / "lib" / f"python{minor}" / "site-packages" / "pdf2zh_next")
    return any(p.exists() for p in probes)


def scan_env_roots() -> list:
    home = Path(os.path.expanduser("~"))
    roots = [
        home / "miniconda3", home / "anaconda3", home / "miniforge3",
        home / "micromamba", home / ".conda",
        Path("C:/ProgramData/miniconda3"), Path("C:/ProgramData/Anaconda3"),
        Path("C:/ProgramData/miniforge3"),
    ]
    found = []
    for root in roots:
        envs = root / "envs"
        if not envs.is_dir():
            continue
        try:
            for env in envs.iterdir():
                exe = env / "python.exe"
                if exe.is_file() and looks_like_translator(exe):
                    found.append(exe)
        except Exception:  # noqa: BLE001
            continue
    current = Path(sys.executable)
    if looks_like_translator(current):
        found.insert(0, current)
    return found


def verify_translator(python_exe: Path):
    """Actually import the engine, to be sure the environment works."""
    code = (
        "import pdf2zh_next, babeldoc;"
        "print('pdf2zh-next', getattr(pdf2zh_next, '__version__', 'ok'));"
        "print('babeldoc', getattr(babeldoc, '__version__', 'ok'))"
    )
    try:
        proc = subprocess.run(
            [str(python_exe), "-c", code],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=240, creationflags=no_window(),
        )
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "")[-400:]
    return True, (proc.stdout or "").strip()


# ---------------------------------------------------------------- downloads

def detect_download_dirs() -> list:
    home = Path(os.path.expanduser("~"))
    cands = [home / "Downloads", home / "下载", home / "OneDrive" / "Downloads"]
    out = [str(p) for p in cands if p.is_dir()]
    return out or [str(home)]


# -------------------------------------------------------------------- Zotero

def new_collection_key(existing) -> str:
    while True:
        key = "".join(random.choice(ALPHABET) for _ in range(8))
        if key not in existing:
            return key


def zotero_running() -> bool:
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq zotero.exe", "/NH"],
            capture_output=True, text=True, timeout=40,
            creationflags=no_window(),
        )
        return "zotero.exe" in (proc.stdout or "").lower()
    except Exception:  # noqa: BLE001
        return False


def wait_zotero_closed() -> bool:
    if not zotero_running():
        return True
    say()
    say("  创建分类需要先完全退出 Zotero（不是关窗口）。")
    say("  退出后回来按回车继续。")
    try:
        input("> ")
    except EOFError:
        pass
    for _ in range(10):
        if not zotero_running():
            return True
        time.sleep(2)
    return not zotero_running()


def create_collection(data_dir: Path, name: str) -> dict:
    db = data_dir / "zotero.sqlite"
    if not db.is_file():
        raise RuntimeError(f"找不到数据库：{db}")
    backup = db.parent / f"zotero.sqlite.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(db, backup)
        say(f"  已备份数据库 -> {backup.name}")
    except Exception as exc:  # noqa: BLE001
        say(f"  备份失败（继续）：{exc}")

    con = sqlite3.connect(db)
    cur = con.cursor()
    row = cur.execute(
        "SELECT collectionID, key FROM collections "
        "WHERE collectionName = ? AND parentCollectionID IS NULL",
        (name,),
    ).fetchone()
    if row:
        con.close()
        say(f"  分类已存在：{name}（id={row[0]}）")
        return {"id": row[0], "key": row[1], "name": name}

    keys = {r[0] for r in cur.execute("SELECT key FROM collections")}
    key = new_collection_key(keys)
    lib = cur.execute(
        "SELECT libraryID FROM libraries WHERE type = 'user' LIMIT 1"
    ).fetchone()
    if not lib:
        con.close()
        raise RuntimeError("数据库里找不到用户文库")
    cur.execute(
        "INSERT INTO collections (collectionName, parentCollectionID, libraryID, "
        "key, version, synced, clientDateModified) "
        "VALUES (?, NULL, ?, ?, 0, 0, CURRENT_TIMESTAMP)",
        (name, lib[0], key),
    )
    cid = cur.lastrowid
    con.commit()
    con.close()
    say(f"  已创建分类：{name}（id={cid}）")
    return {"id": cid, "key": key, "name": name}


# --------------------------------------------------------------------- output

def write_extension_config(cfg: dict, token: str) -> Path:
    target = config.EXTENSION_DIR / "config.js"
    body = (
        "// Generated by setup.py - do not edit by hand." + NL
        + "// Re-run setup.py if the port or token changes." + NL
        + "var PP_CONFIG = "
        + json.dumps({
            "port": cfg["service"]["port"],
            "token": token,
            "tempCollection": cfg["zotero"]["tempCollection"],
        }, ensure_ascii=False, indent=2)
        + ";" + NL
    )
    target.write_text(body, encoding="utf-8")
    return target


def write_env_cmd(cfg: dict) -> Path:
    """So pipeline.cmd can find a Python without guessing."""
    pyw = cfg["paths"].get("pythonw") or sys.executable
    target = HERE / "env.cmd"
    lines = [
        "@echo off",
        f'set "PP_PYTHONW={pyw}"',
        f'set "PP_PYTHON={sys.executable}"',
        f'set "PP_PORT={cfg["service"]["port"]}"',
    ]
    target.write_text(chr(13).join([ln + NL for ln in lines]), encoding="utf-8")
    return target


def set_autostart(pythonw: str, entry: Path) -> bool:
    cmd = f'"{pythonw}" "{entry}"'
    try:
        proc = subprocess.run(
            ["reg", "add",
             "HKCU" + chr(92) + "Software" + chr(92) + "Microsoft" + chr(92)
             + "Windows" + chr(92) + "CurrentVersion" + chr(92) + "Run",
             "/v", "PaperPipeline", "/t", "REG_SZ", "/d", cmd, "/f"],
            capture_output=True, text=True, timeout=40,
            creationflags=no_window(),
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def remove_autostart() -> None:
    try:
        subprocess.run(
            ["reg", "delete",
             "HKCU" + chr(92) + "Software" + chr(92) + "Microsoft" + chr(92)
             + "Windows" + chr(92) + "CurrentVersion" + chr(92) + "Run",
             "/v", "PaperPipeline", "/f"],
            capture_output=True, text=True, timeout=40,
            creationflags=no_window(),
        )
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------- main

def main() -> int:
    say()
    say("=" * 68)
    say("  PaperPipeline 安装向导")
    say("  在论文页面一键翻译，把原文 / 双语 / 中文 / 术语表送进 Zotero")
    say("=" * 68)

    if sys.version_info < (3, 9):
        say()
        say("  需要 Python 3.9 或更新版本才能运行本服务。")
        return 1

    cfg = config.load_config()

    # ----------------------------------------------------------------- Zotero
    head("第 1 步 / 共 5 步 · Zotero 数据目录")
    say("  Zotero 把文献库存在一个「数据目录」里，里面是数据库和 PDF。")
    say("  我需要知道它在哪，才能新建一个收件箱分类。")
    detected = detect_zotero_data()
    if detected:
        say(f"  自动检测到：{detected}")
    else:
        say("  没能自动检测到，请手动填写。")
        say("  找法：Zotero → 编辑 → 设置 → 高级 → 文件和文件夹")
        say("        看「数据目录位置」，通常是 C:" + chr(92) + "Users"
            + chr(92) + "你的名字" + chr(92) + "Zotero")
    while True:
        zdir = ask("Zotero 数据目录", detected, required=not detected)
        if exists(Path(zdir) / "zotero.sqlite"):
            break
        say(f"  这里没有 zotero.sqlite：{zdir}")
        say("  请确认路径，或先打开一次 Zotero 让它生成。")
        detected = ""
    cfg["paths"]["zoteroDataDir"] = zdir
    say(f"  OK  {zdir}")

    # ------------------------------------------------------------ Python 环境
    head("第 2 步 / 共 5 步 · 翻译引擎环境")
    say("  翻译交给 pdf2zh-next，它需要单独装在一个 Python 环境里。")
    say("  还没装的话，看 README.md 的「准备翻译环境」一节。")
    say()
    found = scan_env_roots()
    if found:
        say("  发现这些可用的环境：")
        for i, p in enumerate(found, 1):
            say(f"    {i}) {p}")
        default_py = str(found[0])
    else:
        say("  没有自动找到。请填装有 pdf2zh-next 的 python.exe 完整路径。")
        default_py = ""
    while True:
        raw = ask("翻译环境的 python.exe", default_py, required=not default_py)
        exe = Path(raw)
        if not exe.is_file():
            say(f"  文件不存在：{exe}")
            default_py = ""
            continue
        say("  正在验证（首次可能十几秒）…")
        ok, info = verify_translator(exe)
        if ok:
            say("  OK  环境可用")
            for line in info.splitlines():
                say("        " + line)
            cfg["paths"]["translatorPython"] = str(exe)
            break
        say("  这个环境里没法导入 pdf2zh_next：")
        say("      " + info.replace(NL, " ")[:300])
        default_py = ""

    # ------------------------------------------------------------------ 模型
    head("第 3 步 / 共 5 步 · 翻译模型")
    say("  翻译质量取决于用哪个大模型。需要一个 OpenAI 兼容接口，")
    say("  也就是三样东西：接口地址、密钥、模型名。")
    say()
    say("  常见选择（任选其一，密钥去对应官网申请）：")
    say("    DeepSeek     地址 https://api.deepseek.com/v1")
    say("                 模型 deepseek-chat")
    say("    阿里通义      地址 https://dashscope.aliyuncs.com/compatible-mode/v1")
    say("                 模型 qwen-plus")
    say("    OpenAI       地址 https://api.openai.com/v1")
    say("                 模型 gpt-4o-mini")
    say("    本地 Ollama   地址 http://localhost:11434/v1")
    say("                 模型 qwen2.5:14b")
    say()
    say("  成本参考：翻一篇 10 页论文约几万 token，通常几分钱。")
    keep_url = cfg["translator"].get("openai_base_url", "")
    keep_model = cfg["translator"].get("openai_model", "")
    base_url = ask("接口地址 base_url", keep_url, required=not keep_url)
    api_key = ask("API 密钥", cfg["translator"].get("openai_api_key", ""),
                  required=True)
    model = ask("模型名", keep_model, required=not keep_model)
    cfg["translator"]["openai_base_url"] = base_url
    cfg["translator"]["openai_api_key"] = api_key
    cfg["translator"]["openai_model"] = model

    say()
    say("  翻译范围：论文后半是参考文献，一般不必翻译。")
    cfg["translator"]["stop_at_references"] = ask_yes(
        "  自动跳过参考文献，只翻正文？", True
    )

    say()
    say("  并发速度：qps = 每秒最多发几个翻译请求。")
    say("  付费接口用 10 合适；免费或限流严的接口建议 2-4。")
    qps_raw = ask("每秒请求数 qps", str(cfg["translator"]["qps"]))
    try:
        cfg["translator"]["qps"] = max(1, int(qps_raw))
    except ValueError:
        cfg["translator"]["qps"] = 10
    cfg["translator"]["term_qps"] = cfg["translator"]["qps"]

    # -------------------------------------------------------------- 下载目录
    head("第 4 步 / 共 5 步 · 下载目录")
    say("  如果你已用浏览器下过这篇论文的 PDF，我会直接复用，不再下载。")
    dds = detect_download_dirs()
    say(f"  自动检测到：{', '.join(dds)}")
    if ask_yes("  就用这些目录？", True):
        cfg["paths"]["downloadDirs"] = dds
    else:
        raw = ask("  填写目录（多个用分号分隔）", ";".join(dds))
        cfg["paths"]["downloadDirs"] = [
            p.strip() for p in raw.split(";") if p.strip()
        ]

    # --------------------------------------------------------------- 收件箱
    head("第 5 步 / 共 5 步 · Zotero 收件箱分类")
    say("  新论文先进一个「收件箱」分类，你读完再拖到正式分类。")
    say("  这样分类不会成为负担，而且这个分类还留着你读论文的顺序。")
    coll = ask("收件箱分类名", cfg["zotero"]["tempCollection"])
    cfg["zotero"]["tempCollection"] = coll

    # -------------------------------------------------------------- 写入配置
    head("写入配置")
    port = cfg["service"]["port"]
    say(f"  服务端口：{port}（被占用就改 config.json 的 service.port）")

    state = config.load_state()
    if not state.get("token"):
        state["token"] = secrets.token_hex(16)
    state["port"] = port
    state.setdefault("jobs", {})
    state.setdefault("order", [])

    pyw_candidate = Path(sys.executable).with_name("pythonw.exe")
    if not cfg["paths"].get("pythonw"):
        cfg["paths"]["pythonw"] = str(pyw_candidate) if pyw_candidate.is_file() else ""

    config.save_config(cfg)
    say(f"  OK  配置 -> {config.CONFIG_FILE}")
    ext_cfg = write_extension_config(cfg, state["token"])
    say(f"  OK  插件配置 -> {ext_cfg}")

    # -------------------------------------------------------------- 建分类
    head("创建 Zotero 分类")
    if wait_zotero_closed():
        try:
            state["tempCollection"] = create_collection(Path(zdir), coll)
        except Exception as exc:  # noqa: BLE001
            say(f"  创建失败：{exc}")
            say("    可以先跳过，之后重跑 install.cmd 再试。")
            state["tempCollection"] = None
    else:
        say("  Zotero 仍在运行，已跳过。关掉后重跑 install.cmd 即可。")
        state["tempCollection"] = None

    config.save_state(state)
    say(f"  OK  状态 -> {config.STATE_FILE}")
    say(f"  OK  环境变量 -> {write_env_cmd(cfg)}")

    # ------------------------------------------------------------- 开机自启
    head("开机自启")
    if cfg["paths"]["pythonw"]:
        say("  可以让服务开机静默启动，这样平时完全不用管它。")
        if ask_yes("  设置为开机自启？", True):
            if set_autostart(cfg["paths"]["pythonw"], HERE / "run_service.pyw"):
                say("  OK  已加入开机自启")
            else:
                say("  写注册表失败。可手动把 run_service.pyw 的快捷方式")
                say("  放进启动文件夹（Win+R 输入 shell:startup）")
        else:
            remove_autostart()
            say("  已跳过。以后双击 pipeline.cmd start 手动启动。")
    else:
        say("  没找到 pythonw.exe，跳过。")

    # ------------------------------------------------------------------ 结尾
    head("完成")
    say("  还差最后一步：装 Chrome 插件")
    say()
    say("    1. 打开 chrome://extensions")
    say("    2. 右上角打开「开发者模式」")
    say("    3. 点「加载已解压的扩展程序」")
    say(f"    4. 选择目录：{config.EXTENSION_DIR}")
    say()
    say("  然后启动服务：双击 " + str(HERE / "pipeline.cmd"))
    say()
    say("  打开论文页（arXiv / ACL 这类），右下角会出现按钮。")
    say()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say()
        say("已取消。")
        sys.exit(130)
