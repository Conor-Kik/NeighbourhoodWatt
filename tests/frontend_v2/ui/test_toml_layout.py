"""The TOML layout loader rejects what its schema, VIEWS or the selector
registry does not know, naming the file and the offending key.

Every bad layout here fails before a view is built, so no QApplication is
needed and `load_layout`'s DataManager is never used.
"""

import re
from pathlib import Path

import pytest

from frontend_v2.ui.toml_layout import VIEWS, _build_view, load_layout

HEADER = """
id = "test-grid"
n_cols = 2
n_rows = 2
""".lstrip()

PANEL = """
[[panel]]
id = "pan"
row = 0
col = 0
width = 1
height = 1
view = "line"
title = "Title"
help_text = "Help"
empty_text = "Empty"
read = "select_household_day"
""".lstrip()


def write_layout(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "layout.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_optional_view_fields_keep_constructor_defaults(monkeypatch):
    captured = {}

    def build(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setitem(VIEWS, "line", build)
    _build_view(
        {
            "id": "pan",
            "view": "line",
            "title": "Title",
            "help_text": "Help",
            "empty_text": "Empty",
        },
        Path("layout.toml"),
        {},
    )
    assert captured == {"title": "Title", "help_text": "Help", "empty_text": "Empty"}


def test_unknown_panel_key_names_the_file_and_key(tmp_path):
    path = write_layout(tmp_path, HEADER + PANEL + 'colour = "red"\n')

    with pytest.raises(
        ValueError,
        match=re.escape(f'Unknown key "colour" in panel "pan" of {path}'),
    ):
        load_layout(None, path, {})


def test_unknown_page_key_names_the_file_and_key(tmp_path):
    path = write_layout(tmp_path, HEADER + "extra = 1\n" + PANEL)

    with pytest.raises(
        ValueError,
        match=re.escape(f'Unknown key "extra" in page of {path}'),
    ):
        load_layout(None, path, {})


def test_unknown_view_names_the_file_and_panel(tmp_path):
    path = write_layout(
        tmp_path, HEADER + PANEL.replace('view = "line"', 'view = "pie"')
    )

    with pytest.raises(
        ValueError,
        match=re.escape(f'Unknown view "pie" in panel "pan" of {path}'),
    ):
        load_layout(None, path, {})


def test_unknown_selector_names_the_file_and_panel(tmp_path):
    path = write_layout(
        tmp_path, HEADER + PANEL.replace("select_household_day", "select_missing")
    )

    with pytest.raises(
        ValueError,
        match=re.escape(f'Unknown selector "select_missing" in panel "pan" of {path}'),
    ):
        load_layout(None, path, {})


def test_unknown_note_names_the_file_and_panel(tmp_path):
    path = write_layout(
        tmp_path,
        HEADER + PANEL.replace('help_text = "Help"', 'help_text = "Hi {MISSING}"'),
    )

    with pytest.raises(
        ValueError,
        match=re.escape(f'Unknown note "{{MISSING}}" in panel "pan" of {path}'),
    ):
        load_layout(None, path, {})
