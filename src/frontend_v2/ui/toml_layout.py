"""toml_layout.py
Builds a page's Grid from a TOML layout file.

A page is described as data: each panel names its place, its view and its
selector. Views resolve through VIEWS and selectors through the selectors
package's registry, so the TOML never names a callable. Anything the schema,
VIEWS or the registry does not know is rejected, naming the file and the key.
"""

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.selectors import SELECTOR_FUNCTIONS
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.ui.grid import Grid, GridItem
from frontend_v2.ui.panel_controller import PanelController
from frontend_v2.ui.views.accent_view import AccentView
from frontend_v2.ui.views.bar_graph_view import BarGraphView
from frontend_v2.ui.views.line_graph_view import LineGraphView
from frontend_v2.ui.views.panel_view import PanelView

# View short name -> the PanelView subclass it builds
VIEWS: dict[str, type[PanelView]] = {
    "accent": AccentView,
    "bar": BarGraphView,
    "line": LineGraphView,
}

# Keys the page table may set, and keys every panel entry may set. "min_height"
# is the smallest pixels the page may be squeezed to, so a page taller than the
# window scrolls instead of crushing its charts.
PAGE_KEYS = frozenset({"id", "n_cols", "n_rows", "min_height", "panel"})
PANEL_KEYS = frozenset(
    {
        "id",
        "row",
        "col",
        "width",
        "height",
        "view",
        "title",
        "help_text",
        "empty_text",
        "read",
    }
)

# The extra keys each view takes, beyond the shared panel keys
VIEW_KEYS = {
    "accent": frozenset({"description", "detail_title", "subtitle"}),
    "bar": frozenset({"subtitle", "y_label"}),
    "line": frozenset({"subtitle", "y_label"}),
}

# A note placeholder inside a panel's text, e.g. "{TOTAL_NOTE}"
NOTE = re.compile(r"\{(\w+)\}")


def load_layout(
    data_manager: DataManager, path: Path, notes: Mapping[str, str]
) -> Grid:
    """Build the Grid described by the TOML file at path.

    notes fill {...} placeholders in panel texts. Raises ValueError naming the
    file and the offending key for any unknown key, view, selector or note."""
    layout = tomllib.loads(path.read_text(encoding="utf-8"))
    _reject_unknown(layout, PAGE_KEYS, "page", path)

    items = [_build_item(panel, path, notes) for panel in layout["panel"]]
    grid = Grid(
        data_manager,
        layout["id"],
        items,
        n_rows=layout["n_rows"],
        n_cols=layout["n_cols"],
    )
    grid.setMinimumHeight(layout.get("min_height", 0))
    return grid


def _build_item(panel: Mapping, path: Path, notes: Mapping[str, str]) -> GridItem:
    """Build one panel's GridItem, rejecting a view or selector it does not know
    and any key outside that view's schema."""
    name = panel["view"]
    if name not in VIEWS:
        raise ValueError(f'Unknown view "{name}" in {_panel_name(panel, path)}')
    _reject_unknown(panel, PANEL_KEYS | VIEW_KEYS[name], _panel_name(panel, path), path)

    select = SELECTOR_FUNCTIONS.get(panel["read"])
    if select is None:
        raise ValueError(
            f'Unknown selector "{panel["read"]}" in {_panel_name(panel, path)}'
        )

    controller = PanelController(
        id=panel["id"],
        view=_build_view(panel, path, notes),
        read=ReadBinding(select),
    )
    return GridItem(
        row=panel["row"],
        col=panel["col"],
        width=panel["width"],
        height=panel["height"],
        controller=controller,
    )


def _build_view(panel: Mapping, path: Path, notes: Mapping[str, str]) -> PanelView:
    """Build the PanelView the panel's view key names, filing its notes in."""
    kwargs = {
        "title": panel["title"],
        "help_text": _fill_notes(panel["help_text"], panel, path, notes),
        "empty_text": _fill_notes(panel["empty_text"], panel, path, notes),
    }
    for key in VIEW_KEYS[panel["view"]]:
        if key in panel:
            kwargs[key] = _fill_notes(panel[key], panel, path, notes)
    return VIEWS[panel["view"]](**kwargs)


def _reject_unknown(
    entry: Mapping, allowed: frozenset[str], where: str, path: Path
) -> None:
    """Raise naming the first disallowed key in entry."""
    unknown = set(entry) - allowed
    if unknown:
        raise ValueError(f'Unknown key "{min(unknown)}" in {where} of {path}')


def _fill_notes(text: str, panel: Mapping, path: Path, notes: Mapping[str, str]) -> str:
    """Replace {...} placeholders in a panel's text with the notes it was given,
    raising on a placeholder notes does not hold."""

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in notes:
            raise ValueError(f'Unknown note "{{{name}}}" in {_panel_name(panel, path)}')
        return notes[name]

    return NOTE.sub(fill, text)


def _panel_name(panel: Mapping, path: Path) -> str:
    """Where a panel error points: its id and the file."""
    return f'panel "{panel["id"]}" of {path}'
