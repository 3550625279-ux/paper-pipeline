# -*- coding: utf-8 -*-
"""Silent launcher for the PaperPipeline service (no console window)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import service  # noqa: E402

if __name__ == "__main__":
    sys.exit(service.main())
