"""Chart updates retain artists and avoid redundant canvas work."""

import os
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtWidgets import QApplication

from frontend_v2.ui.views.line_graph_view import LineGraphView
from frontend_v2.utils.payload import SeriesPayload

APP = QApplication.instance() or QApplication([])


def payload(values=(1.0, 2.0, 3.0)):
    return SeriesPayload(
        "Time (UTC)",
        [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i * 5) for i in range(3)],
        {"Power (kW)": list(values)},
    )


def test_changed_values_reuse_fill_line_and_time_locator():
    view = LineGraphView("Chart", "", "")
    view.show_payload(payload())
    view.canvas.draw()
    line = view._lines["Power (kW)"]
    fill = view._fills["Power (kW)"]
    locator = view.ax.xaxis.get_major_locator()
    view.show_payload(payload((3.0, 4.0, 5.0)))
    view.canvas.draw()
    assert view._lines["Power (kW)"] is line
    assert view._fills["Power (kW)"] is fill
    assert view.ax.xaxis.get_major_locator() is locator
    assert list(line.get_ydata()) == [3.0, 4.0, 5.0]
    assert len(view.ax.collections) == 1
    view.close()


def test_identical_payload_skips_draw_and_nan_values_compare_equal(monkeypatch):
    view = LineGraphView("Chart", "", "")
    view.show_payload(payload((1.0, np.nan, 3.0)))
    draws = []
    monkeypatch.setattr(view.canvas, "draw_idle", lambda: draws.append(True))
    view.show_payload(payload((1.0, np.nan, 3.0)))
    assert draws == []
    view.show_payload(payload((2.0, np.nan, 3.0)))
    assert draws == [True]
    view.close()


def test_empty_series_and_series_changes_rebuild_cleanly():
    view = LineGraphView("Chart", "", "")
    view.show_payload(payload())
    view.show_payload(SeriesPayload("Time", [], {}))
    view.show_payload(payload())
    view.canvas.draw()
    assert list(view._lines) == ["Power (kW)"]
    view.close()


def test_datetime_numeric_and_timezone_transitions_reset_axes():
    from datetime import timezone

    from matplotlib.dates import ConciseDateFormatter

    view = LineGraphView("Chart", "", "")
    view.show_payload(payload())
    date_axes = view.ax
    view.show_payload(SeriesPayload("Step", [0, 1, 2], {"Power (kW)": [1, 2, 3]}))
    assert view.ax is not date_axes
    assert not isinstance(view.ax.xaxis.get_major_formatter(), ConciseDateFormatter)
    view.show_payload(payload())
    assert isinstance(view.ax.xaxis.get_major_formatter(), ConciseDateFormatter)
    shifted = payload()
    shifted = SeriesPayload(
        "Time (AEST)",
        [t.astimezone(timezone(timedelta(hours=10))) for t in shifted.x_values],
        shifted.y_series,
    )
    date_axes = view.ax
    view.show_payload(shifted)
    assert view.ax is not date_axes
    view.canvas.draw()
    view.close()


def test_large_and_negative_values_remeasure_margins():
    view = LineGraphView("Chart", "", "")
    view.figure.set_size_inches(3, 2)
    view.show_payload(payload())
    view.canvas.draw()
    for values in ((1234567, 2234567, 3234567), (-1234567, -2234567, -3234567)):
        view.show_payload(payload(values))
        view.canvas.draw()
        bounds = view.ax.get_tightbbox(view.canvas.get_renderer())
        assert bounds.y1 <= view.figure.bbox.y1
        assert bounds.x0 >= view.figure.bbox.x0
    view.close()


def test_scientific_offset_does_not_overlap_legend_after_update():
    view = LineGraphView("Chart", "", "")
    view.figure.set_size_inches(5, 3)
    x = [0, 1, 2]
    view.show_payload(
        SeriesPayload(
            "Step",
            x,
            {"Community demand (kW)": [1, 2, 3], "PV generation (kW)": [2, 3, 4]},
        )
    )
    view.canvas.draw()
    view.show_payload(
        SeriesPayload(
            "Step",
            x,
            {
                "Community demand (kW)": [1234567, 2234567, 3234567],
                "PV generation (kW)": [2234567, 3234567, 4234567],
            },
        )
    )
    view.canvas.draw()
    renderer = view.canvas.get_renderer()
    offset = view.ax.yaxis.offsetText.get_window_extent(renderer)
    assert not any(
        offset.overlaps(legend.get_window_extent(renderer))
        for legend in view.figure.legends
    )
    view.close()


def test_issue_label_updates_without_redrawing_identical_curves(monkeypatch):
    from dataclasses import replace

    view = LineGraphView("Forecast", "", "Unavailable")
    view.show_payload(replace(payload(), subtitle="Issued at 09:00"))
    draws = []
    monkeypatch.setattr(view.canvas, "draw_idle", lambda: draws.append(True))
    view.show_payload(replace(payload(), subtitle="Issued at 09:30"))
    assert view._subtitle.text() == "Issued at 09:30"
    assert draws == []
    view.show_payload(SeriesPayload("Time", [], {}, subtitle="Forecast unavailable"))
    assert view._body.currentWidget() is view._empty
    assert view._subtitle.text() == "Forecast unavailable"
    view.show_payload(payload())
    assert view._body.currentWidget() is not view._empty
    view.show_empty()
    assert view._subtitle.text() == ""
    view.close()
