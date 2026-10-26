"""bar_graph_view.py
Panel that draws grouped bars, one group per category."""

from textwrap import fill

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from frontend_v2.ui.theme import QUANTITY_COLOURS, THEME
from frontend_v2.ui.views.panel_view import (
    GRAPH_FONT_SIZE,
    GraphCanvas,
    PanelView,
    add_graph_legend,
)
from frontend_v2.utils.payload import CategoricalPayload


class BarGraphView(PanelView):
    """Grouped bars: one group per x label, one bar per series. Draws a
    CategoricalPayload."""

    PAYLOAD_TYPE = CategoricalPayload
    DEFAULT_FIGSIZE = (5, 3)
    DEFAULT_DPI = 100
    BAR_GROUP_WIDTH = 0.8
    # Longest line of a category label, so neighbouring labels do not collide
    LABEL_WIDTH = 10
    # Beyond this many categories, labels are tilted instead of wrapped
    MAX_WRAPPED_LABELS = 4
    # Stacked segments shorter than this share of the total span go unlabelled
    MIN_LABELLED_SHARE = 0.07

    def __init__(
        self,
        title: str,
        help_text: str,
        empty_text: str,
        subtitle: str = "",
        y_label: str = "",
        min_width: int = 0,
        min_height: int = 0,
        show_empty_when_no_categories: bool = False,
        stacked: bool = False,
    ):
        # Constrained layout is redone on every draw, so it follows the canvas as
        # it resizes; tight_layout would fix the margins at the first draw's size
        self.figure = Figure(
            figsize=self.DEFAULT_FIGSIZE, dpi=self.DEFAULT_DPI, layout="constrained"
        )
        self.canvas = GraphCanvas(self.figure)
        self.ax: Axes | None = None
        # Labels the y axis, with its unit, whatever the number of series
        self.y_label = y_label
        self._default_title = title
        self._default_subtitle = subtitle
        self._show_empty_when_no_categories = show_empty_when_no_categories
        # Stack the series into one bar per label, with segment values written on
        self.stacked = stacked

        super().__init__(
            self.canvas,
            title,
            help_text,
            empty_text,
            subtitle,
            min_width=min_width,
            min_height=min_height,
        )
        self.detail = QLabel(self)
        self.detail.setObjectName("textValue")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        self.detail.hide()
        self.layout().addWidget(self.detail)

        # Columns that fit one width wrap wrongly at another
        self.canvas.mpl_connect("resize_event", lambda _: add_graph_legend(self.figure))

    def _draw(self, payload: CategoricalPayload) -> None:
        """Always a full redraw, since the number of bars can change between
        payloads."""
        if self._show_empty_when_no_categories and not payload.x_labels:
            self.show_empty()
            return
        self._title.setText(payload.title or self._default_title)
        subtitle = (
            payload.subtitle if payload.subtitle is not None else self._default_subtitle
        )
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))
        self.detail.setText(payload.detail)
        self.detail.setVisible(bool(payload.detail))
        ax = self._reset_axes()
        x = payload.x_labels
        series = payload.y_values

        positions = np.arange(len(x))
        groups = 1 if self.stacked else max(len(series), 1)
        bar_width = self.BAR_GROUP_WIDTH / groups
        # Stacked segments grow away from zero, so each sign keeps its own base
        above, below = np.zeros(len(x)), np.zeros(len(x))
        # Offset each series so its bars sit side by side, centred on the tick
        start = -self.BAR_GROUP_WIDTH / 2 + bar_width / 2

        segments = []
        for i, (key, values) in enumerate(series.items()):
            heights = np.asarray(values, dtype=float)
            if self.stacked:
                bottom = np.where(heights >= 0, above, below)
                above += np.where(heights >= 0, heights, 0)
                below += np.where(heights < 0, heights, 0)
            bars = ax.bar(
                positions + (0 if self.stacked else start + i * bar_width),
                heights,
                width=bar_width,
                bottom=bottom if self.stacked else None,
                label=key,
                color=None
                if self.stacked
                else payload.x_colours or QUANTITY_COLOURS.get(key),
            )
            segments.append((bars, heights))

        if self.stacked and len(x):
            span = float(above.max() - below.min()) or 1.0
            for bars, heights in segments:
                ax.bar_label(
                    bars,
                    labels=[
                        f"{value:,.2f}"
                        if abs(value) >= self.MIN_LABELLED_SHARE * span
                        else ""
                        for value in heights
                    ],
                    label_type="center",
                    fontsize=GRAPH_FONT_SIZE,
                    color=THEME["ink"],
                )

            # Opposite signs stack separately; neither endpoint is the net sum.
            totals = above + below
            ax.scatter(
                positions,
                totals,
                marker="D",
                s=16,
                color=THEME["ink"],
                edgecolors=THEME["panel"],
                zorder=3,
                label="Net total",
            )
            for position, top, total in zip(positions, above, totals, strict=True):
                ax.annotate(
                    f"Net: {total:,.2f}",
                    (position, top),
                    xytext=(0, 6),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=GRAPH_FONT_SIZE,
                    color=THEME["ink"],
                )
            ax.margins(y=0.15)

        if len(x) > self.MAX_WRAPPED_LABELS:
            # Too many bars to wrap each name beside its neighbours, so tilt them
            ax.set_xticks(positions, labels=[str(label) for label in x])
            ax.tick_params(axis="x", labelrotation=30)
            for tick in ax.get_xticklabels():
                tick.set_horizontalalignment("right")
        else:
            # Bracketed parts and long names wrap onto more lines, so labels fit
            # unrotated
            ax.set_xticks(
                positions,
                labels=[
                    "\n".join(
                        fill(line, self.LABEL_WIDTH, break_long_words=False)
                        for line in str(label).replace(" (", "\n(").split("\n")
                    )
                    for label in x
                ],
            )

        self._label_y_axis(ax, series, payload.y_label)
        add_graph_legend(self.figure)

        self.canvas.draw_idle()

    def show_empty(self) -> None:
        self._title.setText(self._default_title)
        self._subtitle.setText(self._default_subtitle)
        self._subtitle.setVisible(bool(self._default_subtitle))
        self.detail.clear()
        self.detail.hide()
        super().show_empty()

    def _reset_axes(self) -> Axes:
        """Clear canvas and return one themed axes."""
        self.figure.clear()
        self.figure.set_facecolor(THEME["panel"])

        ax = self.figure.add_subplot()
        ax.set_facecolor(THEME["panel"])
        ax.tick_params(colors=THEME["muted"], labelsize=GRAPH_FONT_SIZE)

        for spine in ax.spines.values():
            spine.set_color(THEME["border"])

        # Faint horizontal gridlines, behind the data, to read values off
        ax.grid(True, axis="y", color=THEME["border"], linewidth=0.5)
        ax.set_axisbelow(True)

        self.ax = ax
        return ax

    def _label_y_axis(self, ax: Axes, series: dict, y_label: str | None = None) -> None:
        """Label the y axis with y_label, or else the only series' label; several
        series without a y_label are told apart by the legend instead."""
        label = y_label if y_label is not None else self.y_label
        label = label or (next(iter(series)) if len(series) == 1 else "")
        if label:
            ax.set_ylabel(label, color=THEME["muted"])
