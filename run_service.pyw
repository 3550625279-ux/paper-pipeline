# -*- coding: utf-8 -*-
"""Silent launcher for the PaperPipeline service.

Used by the autostart entry and by the self-healing scheduled task. If the
service is already listening on the configured port this exits quietly, so the
watchdog can run on a timer without producing noise or duplicate processes.
"""
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import config  # noqa: E402


def already_running(port: int) -> bool:
    """True if something is already accepting connections on the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    try:
        port = config.port()
    except Exception:  # noqa: BLE001
        port = 8787
    if already_running(port):
        raise SystemExit(0)
    import service  # imported late so a no-op start stays cheap
    raise SystemExit(service.main())
