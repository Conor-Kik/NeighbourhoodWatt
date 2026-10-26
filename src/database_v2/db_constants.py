import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# defaulted values when running in python.
DB_PATH = BASE_DIR / "app_database.db"
SCHEMA_DIR = BASE_DIR / "schema"
SEED_DIR = BASE_DIR / "seed_data"

# if not _FROZEN then we are running from a terminal otherwise we are running from a
# compiled .exe so we need to change the pathing to adjust to that local pathing of the
# .exe.
_FROZEN = getattr(sys, "frozen", False)

# Safely check for PyInstaller's bundle directory
if hasattr(sys, "_MEIPASS"):
    base_path = Path(sys._MEIPASS)
else:
    base_path = Path(os.path.abspath("."))

if _FROZEN:  # then we are in the compiled .exe.
    # adjust the database path to that of the .exe's local paths.
    data_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GridSync"
    data_dir.mkdir(parents=True, exist_ok=True)
    DB_PATH = data_dir / "app_database.db"
    if not DB_PATH.exists():
        shutil.copy(base_path / "database_v2" / "app_database.db", DB_PATH)

    # adjust the seed directory path to that of the .exe's local paths.
    SEED_DIR = base_path / "database_v2" / "seed_data"
