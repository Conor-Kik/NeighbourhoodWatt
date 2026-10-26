# PyInstaller supplies the spec globals, including SPECPATH and build classes.
# ruff: noqa: F821
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from database_v2.db_initilisation import reset_db  # noqa: E402

# Package defaults only; never copy the developer's working database.
default_db = ROOT / ".cache" / "executable-seed" / "app_database.db"
default_db.parent.mkdir(parents=True, exist_ok=True)
reset_db(default_db)


def keep(name):
    parts = name.split(".")
    return not any(p in parts for p in ("tests", "test", "examples", "viewer"))


pyomo_imports = collect_submodules("pyomo", filter=keep)
app_imports = collect_submodules("frontend_v2")

hp_datas, hp_binaries, hp_hidden = collect_all("highspy")

# Conda keeps this DLL outside the package, beyond the standard LightGBM hook.
# Preserve its environment-relative path for Conda's frozen LightGBM loader.
lightgbm_binaries = []
conda_lightgbm = Path(sys.prefix) / "Library" / "bin" / "lib_lightgbm.dll"
if sys.platform == "win32" and conda_lightgbm.is_file():
    lightgbm_binaries.append((str(conda_lightgbm), "Library/bin"))

a = Analysis(
    [ROOT / "src/frontend_v2/main.py"],
    pathex=[ROOT / "src"],
    binaries=hp_binaries + lightgbm_binaries,
    datas=[
        (ROOT / "LICENSE", "."),
        (ROOT / "THIRD_PARTY_NOTICES.md", "."),
        (ROOT / "src/frontend_v2/ui/style.qss", "ui"),
        (ROOT / "src/frontend_v2/ui/drawing.ico", "ui"),
        (ROOT / "src/frontend_v2/ui/drawing.png", "ui"),
        (default_db, "database_v2"),
        (ROOT / "src/database_v2/schema", "database_v2/schema"),
        (ROOT / "src/database_v2/seed_data", "database_v2/seed_data"),
    ]
    + collect_data_files("pvlib")
    + collect_data_files("australian_postcodes")
    + hp_datas,
    hiddenimports=["lightgbm", "australian_postcodes.data"]
    + app_imports
    + pyomo_imports
    + hp_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "build/pyomo_runtime_hook.py")],
    excludes=["PySide6", "shiboken6", "tkinter", "pytest", "IPython", "gurobipy"],
    noarchive=False,
    optimize=0,
)

# Windows provides these ICU APIs. A copy found on PATH (for example from
# another Conda environment) can have incompatible exports and prevent Qt loading.
if sys.platform == "win32":
    a.binaries = [
        entry
        for entry in a.binaries
        if Path(entry[0]).name.lower() not in {"icuuc.dll", "icuin.dll"}
    ]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NeighbourhoodWatt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=r"%LOCALAPPDATA%\NeighbourhoodWatt\runtime",
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ROOT / "src/frontend_v2/ui/drawing.ico",
)
