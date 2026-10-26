"""Build configuration packages defaults and resources without touching user data."""

import runpy
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("has_conda_lightgbm", [False, True])
def test_spec_packages_clean_defaults_and_schema_from_any_working_directory(
    tmp_path, monkeypatch, has_conda_lightgbm
):
    project = tmp_path / "project"
    source_db = project / "src/database_v2/app_database.db"
    source_db.parent.mkdir(parents=True)
    source_db.write_bytes(b"private working database")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))
    environment = tmp_path / "environment"
    monkeypatch.setattr(sys, "prefix", str(environment))
    conda_dll = environment / "Library/bin/lib_lightgbm.dll"
    if has_conda_lightgbm:
        conda_dll.parent.mkdir(parents=True)
        conda_dll.write_bytes(b"Conda LightGBM DLL")

    # Exercise the spec and real seed creation without running a whole freezer.
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_submodules = lambda *_args, **_kwargs: []
    hooks.collect_data_files = lambda *_args, **_kwargs: []
    hooks.collect_all = lambda *_args, **_kwargs: ([], [], [])
    for name in ("PyInstaller", "PyInstaller.utils"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    analysis_options = {}
    exe_options = {}
    collected_binaries = []

    def analysis(*args, **kwargs):
        analysis_options.update(kwargs)
        return SimpleNamespace(
            pure=[],
            scripts=[],
            binaries=[
                ("icuuc.dll", "foreign/icuuc.dll", "BINARY"),
                ("icuin.dll", "foreign/icuin.dll", "BINARY"),
                ("solver.dll", "solver.dll", "BINARY"),
            ],
            datas=kwargs["datas"],
        )

    def executable(*args, **kwargs):
        exe_options.update(kwargs)
        collected_binaries.extend(args[2])

    runpy.run_path(
        str(ROOT / "build/NeighbourhoodWatt.spec"),
        init_globals={
            "SPECPATH": str(project / "build"),
            "Analysis": analysis,
            "PYZ": lambda *_args: None,
            "EXE": executable,
        },
    )
    data = analysis_options["datas"]
    bundled_db = next(Path(path) for path, target in data if target == "database_v2")
    assert bundled_db != source_db
    assert source_db.read_bytes() == b"private working database"
    with closing(sqlite3.connect(bundled_db)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM simulation_request").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM battery_config").fetchone()[0] > 0
        )
        assert connection.execute("SELECT COUNT(*) FROM location").fetchone()[0] > 0
    assert (project / "src/database_v2/schema", "database_v2/schema") in data
    assert (project / "src/database_v2/seed_data", "database_v2/seed_data") in data
    expected_binaries = (
        [(str(conda_dll), "Library/bin")]
        if has_conda_lightgbm and sys.platform == "win32"
        else []
    )
    assert analysis_options["binaries"] == expected_binaries
    assert (project / "LICENSE", ".") in data
    assert (project / "THIRD_PARTY_NOTICES.md", ".") in data
    assert "gurobipy" in analysis_options["excludes"]
    assert exe_options["console"] is False
    assert ("solver.dll", "solver.dll", "BINARY") in collected_binaries
    for name in ("icuuc.dll", "icuin.dll"):
        assert any(entry[0] == name for entry in collected_binaries) == (
            sys.platform != "win32"
        )


def test_windowed_runtime_provides_streams_for_solver_libraries(monkeypatch):
    with monkeypatch.context() as context:
        context.setattr(sys, "stdout", None)
        context.setattr(sys, "stderr", None)
        context.setattr(sys, "frozen", True, raising=False)
        context.setenv("PYOMO_STRICT_PLUGINS", "1")
        runpy.run_path(str(ROOT / "build/pyomo_runtime_hook.py"))
        output, errors = sys.stdout, sys.stderr
    try:
        assert output.writable() and errors.writable()
        output.write("solver output")
        errors.flush()
    finally:
        output.close()
        errors.close()
