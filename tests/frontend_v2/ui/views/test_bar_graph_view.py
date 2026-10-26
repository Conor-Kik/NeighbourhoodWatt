"""Stacked bars draw one bar per label, with signs kept apart."""

import pytest
from PyQt6.QtWidgets import QApplication

from frontend_v2.ui.views.bar_graph_view import BarGraphView
from frontend_v2.utils.payload import CategoricalPayload

APP = QApplication.instance() or QApplication([])


def test_stacked_bars_stack_each_sign_away_from_zero():
    view = BarGraphView("Cost", "help", "empty", stacked=True)
    view._draw(
        CategoricalPayload(
            x_labels=["A", "B"],
            y_values={"Bill": [-10.0, 4.0], "Wear": [3.0, 2.0]},
        )
    )
    bars = [(p.get_x(), p.get_y(), p.get_height()) for p in view.ax.patches]
    assert len(bars) == 4
    # Wear is positive, so sits on the zero line for A and on top of the bill for B
    assert bars[2][1] == 0 and bars[3][1] == 4.0
    assert view.ax.collections[0].get_offsets().tolist() == [[0.0, -7.0], [1.0, 6.0]]
    view.close()


@pytest.mark.parametrize(
    "bill,wear,total",
    [
        (-100.0, 90.0, -10.0),
        (-90.0, 100.0, 10.0),
        (4.0, 2.0, 6.0),
        (-100.0, 0.0, -100.0),
        (-90.0, 90.0, 0.0),
        (0.0, 0.0, 0.0),
    ],
)
def test_stacked_bars_show_signed_net_cost(bill, wear, total):
    view = BarGraphView("Cost", "help", "empty", stacked=True)
    view._draw(
        CategoricalPayload(
            x_labels=["Optimised"], y_values={"Bill": [bill], "Wear": [wear]}
        )
    )
    view.canvas.draw()
    assert view.ax.collections[0].get_offsets().tolist() == [[0.0, total]]
    assert f"Net: {total:,.2f}" in [text.get_text() for text in view.ax.texts]
    assert "Net total" in view.ax.get_legend_handles_labels()[1]
    view.close()


def test_empty_stacked_chart_draws_without_totals():
    view = BarGraphView("Cost", "help", "empty", stacked=True)
    view._draw(CategoricalPayload(x_labels=[], y_values={"Bill": [], "Wear": []}))
    view.canvas.draw()
    assert not view.ax.collections
    assert not view.ax.texts
    view.close()


def test_switching_goal_updates_chart_labels_and_terminal_adjusted_total():
    view = BarGraphView(
        "Operating cost breakdown",
        "help",
        "empty",
        stacked=True,
        y_label="AUD",
        subtitle="Default context",
    )
    view.show_payload(
        CategoricalPayload(
            x_labels=["Optimised"],
            y_values={
                "Bill": [-102],
                "Wear": [90],
                "Carbon": [20],
            },
            title="Balanced operating score",
            y_label="AUD equivalent",
            subtitle="Terminal-adjusted estimates · lower is better",
        )
    )
    view.canvas.draw()
    assert view._title.text() == "Balanced operating score"
    assert view.ax.get_ylabel() == "AUD equivalent"
    assert view._subtitle.text() == "Terminal-adjusted estimates · lower is better"
    assert view.ax.collections[0].get_offsets().tolist() == [[0.0, 8.0]]
    assert "Net: 8.00" in [text.get_text() for text in view.ax.texts]
    view.show_payload(
        CategoricalPayload(
            x_labels=["Optimised"],
            y_values={"Bill": [-100], "Wear": [90]},
            title="Economic operating cost",
            y_label="AUD",
            subtitle="Recorded totals · before remaining-energy estimates",
        )
    )
    assert view._title.text() == "Economic operating cost"
    assert view.ax.get_ylabel() == "AUD"
    assert (
        view._subtitle.text() == "Recorded totals · before remaining-energy estimates"
    )
    assert len(view.ax.patches) == 2
    assert view.ax.collections[0].get_offsets().tolist() == [[0.0, -10.0]]
    view.show_empty()
    assert view._title.text() == "Operating cost breakdown"
    assert view._subtitle.text() == "Default context"
    view.show_payload(CategoricalPayload(x_labels=["A"], y_values={"Bill": [1]}))
    assert view._title.text() == "Operating cost breakdown"
    assert view.ax.get_ylabel() == "AUD"
    view.close()
