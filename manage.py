# -*- coding: utf-8 -*-
"""Small helper behind pipeline.cmd: status / stop / logs."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config  # noqa: E402


def health(port: int, timeout: int = 6):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=timeout
        ) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def pids_on_port(port: int) -> list:
    """Ask Windows which process owns the listening socket."""
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        return []
    out = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(
            f":{port}"
        ):
            try:
                out.append(int(parts[4]))
            except ValueError:
                pass
    return out


def cmd_status() -> int:
    port = config.port()
    info = health(port)
    print()
    if not info:
        print("  服务状态：未运行")
        print(f"  端口：{port}")
        print(f"  启动方式：双击 {HERE / 'pipeline.cmd'}（或 pipeline.cmd start）")
        print()
        return 1
    print("  服务状态：运行中")
    print(f"  版本：{info.get('version')}")
    print(f"  端口：{port}")
    print(f"  Zotero：{'已就绪' if info.get('zotero') else '未运行（任务会排队等待）'}")
    print(f"  收件箱分类：{info.get('tempCollection') or '（未识别，检查 Zotero）'}")
    py = config.translator_python()
    print(f"  翻译环境：{py if py else '（未配置）'}")
    if py and not py.exists():
        print("            ！这个路径不存在，请重新运行 install.cmd")
    ts = config.translator_settings()
    print(f"  翻译模型：{ts.get('openai_model') or '（未配置）'}")
    jobs = config.load_state().get("jobs") or {}
    if jobs:
        recent = sorted(jobs.values(), key=lambda j: j.get("updatedAt") or "",
                        reverse=True)[:5]
        print()
        print("  最近任务：")
        for job in recent:
            title = (job.get("record") or {}).get("title") or +                    (job.get("hint") or {}).get("title") or "(未命名)"
            print(f"    {job.get('state','?'):<10} {str(title)[:44]}")
    print()
    return 0


def cmd_stop() -> int:
    port = config.port()
    pids = pids_on_port(port)
    if not pids:
        print("  服务没有在运行。")
        return 0
    for pid in pids:
        print(f"  停止进程 {pid} …")
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:  # noqa: BLE001
            print(f"    失败：{exc}")
    time.sleep(1.5)
    print("  已停止。" if not health(port, 3) else "  仍在运行，请手动结束进程。")
    return 0


def cmd_logs() -> int:
    log = config.LOG_DIR / "service.log"
    if not log.is_file():
        print("  还没有日志。服务启动一次之后才会有。")
        return 0
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    print()
    for line in lines[-40:]:
        print("  " + line[:150])
    print()
    return 0


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if action == "status":
        return cmd_status()
    if action == "stop":
        return cmd_stop()
    if action == "logs":
        return cmd_logs()
    print("  未知命令：" + action)
    return 2


if __name__ == "__main__":
    sys.exit(main())
