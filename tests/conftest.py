"""Runs before any test module: points HOME at a throw-away folder so the tests can never
touch the real system, then makes the project importable."""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HOME"] = tempfile.mkdtemp(prefix="lmp-test-home-")
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_DIRS",
            "XDG_CURRENT_DESKTOP"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
