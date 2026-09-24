import os
import sys
import pathlib

# Ensure the backend package is importable when running `pytest` from
# either the backend/ or repo root directory.
BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import tempfile

_tmp_db = pathlib.Path(tempfile.gettempdir()) / "agent_test.db"
if _tmp_db.exists():
    _tmp_db.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_db}")
os.environ.setdefault("DEMO_SITE_BASE_URL", "http://127.0.0.1:8001")
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")
