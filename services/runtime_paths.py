"""Runtime paths for local use and a mounted production volume."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_ROOT = Path(os.environ.get("SOLAR_SYSTEM_STORAGE_DIR") or PROJECT_ROOT)
DATA_DIRECTORY = STORAGE_ROOT / "data"
LOG_DIRECTORY = STORAGE_ROOT / "logs"
