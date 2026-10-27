"""Runtime paths, configured once at startup before storage is imported."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Keep data beside the launch directory while debugging. An explicit value may
# be relative to that directory, absolute, or start with ~ for the user's home.
DATA_DIR = Path(os.getenv("AGENT_1_DATA_DIR") or ".agent-1").expanduser().resolve()
