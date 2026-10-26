"""test_grid.py
Tests for validating panel positions and sizes."""

from types import SimpleNamespace

import pytest

from frontend_v2.ui.grid import (
    GridItem,
    validate_layout,
)


def item(panel_id: str, row: int, col: int, width: int, height: int) -> GridItem:
    """A GridItem whose controller only needs an id for these helpers."""
    return GridItem(row, col, width, height, SimpleNamespace(id=panel_id))  # type: ignore[arg-type]


def places(items: list[GridItem]) -> dict:
    """Each item's id mapped to its (row, col, width, height)."""
    return {i.controller.id: (i.row, i.col, i.width, i.height) for i in items}


@pytest.mark.parametrize("width,height", [(0, 1), (1, 0), (-1, 1)])
def test_nonpositive_panel_sizes_are_rejected(width, height):
    with pytest.raises(ValueError, match="positive size"):
        validate_layout([item("a", 0, 0, width, height)], 2, 2)


def test_validate_layout_accepts_the_code_defaults():
    four_panels = [
        item("a", 0, 0, 2, 2),
        item("b", 0, 2, 2, 2),
        item("c", 2, 0, 2, 2),
        item("d", 2, 2, 2, 2),
    ]
    validate_layout(four_panels, 4, 4)


def test_validate_layout_rejects_overlap():
    two_panels = [item("a", 0, 0, 2, 2), item("b", 1, 1, 2, 2)]
    with pytest.raises(ValueError, match="Item b is overlapping"):
        validate_layout(two_panels, 4, 4)


def test_validate_layout_rejects_out_of_bounds():
    one_panel = [item("a", 3, 3, 2, 2)]
    with pytest.raises(ValueError, match="Item a is out of bounds"):
        validate_layout(one_panel, 4, 4)
