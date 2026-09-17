# -*- coding: utf-8 -*-
"""Self check: verifies the configuration without translating anything.

Run:  python selftest.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config  # noqa: E402

OK = "  [OK]   "
BAD = "  [!!]   "


def check_health(port: int):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=8
        ) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}


def main() -> int:
    problems = 0
    print()
    print("PaperPipeline 自检")
    print("=" * 60)

    # ---------------------------------------------------------------- config
    print()
    print("配置文件")
    if config.CONFIG_FILE.exists():
        print(OK + str(config.CONFIG_FILE))
    else:
        print(BAD + "config.json 不存在，请先运行 install.cmd")
        return 1
    cfg = config.load_config()

    # ------------------------------------------------------ translator env
    print()
    print("翻译环境")
    py = config.translator_python()
    if not py:
        print(BAD + "paths.translatorPython 未填写")
        problems += 1
    elif not py.is_file():
        print(BAD + f"路径不存在：{py}")
        problems += 1
    else:
        print(OK + str(py))

    # ---------------------------------------------------------- model creds
    print()
    print("翻译模型")
    ts = config.translator_settings()
    missing = [
        k for k in ("openai_base_url", "openai_api_key", "openai_model")
        if not ts.get(k)
    ]
    if missing:
        print(BAD + "缺少：" + ", ".join(missing))
        problems += 1
    else:
        print(OK + f"接口 {ts['openai_base_url']}")
        print(OK + f"模型 {ts['openai_model']}")
        print("       密钥 " + ts["openai_api_key"][:6] + "…" + str(len(
            ts["openai_api_key"])) + " 字符")

    # ---------------------------------------------------------------- zotero
    print()
    print("Zotero")
    zdir = config.zotero_data_dir()
    if zdir and (zdir / "zotero.sqlite").is_file():
        print(OK + f"数据目录 {zdir}")
    else:
        print(BAD + f"数据目录无效：{zdir}")
        problems += 1

    # --------------------------------------------------------------- service
    print()
    print("本地服务")
    port = config.port()
    info = check_health(port)
    if info.get("_error"):
        print(BAD + f"端口 {port} 没有响应（{info['_error'][:60]}）")
        print("       启动方式：双击 pipeline.cmd")
    else:
        print(OK + f"运行中，版本 {info.get('version')}")
        print(OK + "Zotero 连接：" + (
            "就绪" if info.get("zotero") else "未运行（任务会排队）"))
        print(OK + "收件箱分类：" + str(info.get("tempCollection") or "未识别"))

    # ------------------------------------------------------------- extension
    print()
    print("Chrome 插件")
    ext_cfg = config.EXTENSION_DIR / "config.js"
    if ext_cfg.is_file():
        text = ext_cfg.read_text(encoding="utf-8", errors="replace")
        if str(port) in text and "token" in text:
            print(OK + f"配置已生成（端口 {port}）")
        else:
            print(BAD + "插件里的端口与 config.json 不一致，重跑 install.cmd")
            problems += 1
    else:
        print(BAD + "extension/config.js 不存在，重跑 install.cmd")
        problems += 1

    # ------------------------------------------------------------ vendor
    print()
    print("质量检查模块（可选增强）")
    for name in ("detector.py", "quality_check.py"):
        target = config.VENDOR_DIR / name
        print((OK if target.is_file() else BAD) + name)

    print()
    print("=" * 60)
    if problems:
        print(f"发现 {problems} 个问题，见上面的 [!!] 行。")
    else:
        print("全部通过。")
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
