"""line_graph_view.py
Panel that draws one line per series."""

from dataclasses import replace
from datetime import datetime

import numpy as np
from matplotlib import dates as plt_dates
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from frontend_v2.ui.theme import QUANTITY_COLOURS, THEME
from frontend_v2.ui.views.panel_view import (
    GRAPH_FONT_SIZE,
    GraphCanvas,
    PanelView,
    add_graph_legend,
)
from frontend_v2.utils.payload import SeriesPayload


class LineGraphView(PanelView):
    """One line per series against a shared x axis. Draws a SeriesPayload; x
    values may be datetimes or numbers."""

    PAYLOAD_TYPE = SeriesPayload

    DEFAULT_FIGSIZE = (5, 3)
    DEFAULT_DPI = 100

    def __init__(
        self,
        title: str,
        help_text: str,
        empty_text: str,
        subtitle: str = "",
        y_label: str = "",
        min_width: int = 0,
        min_height: int = 0,
    ):
        # Measure margins on the first draw and on resize, then reuse them.
        self.figure = Figure(
            figsize=self.DEFAULT_FIGSIZE, dpi=self.DEFAULT_DPI, layout="constrained"
        )
        self.canvas = GraphCanvas(self.figure)
        self.ax: Axes | None = None
        # Labels the y axis, with its unit, whatever the number of series
        self.y_label = y_label
        self._default_subtitle = subtitle

        super().__init__(
            self.canvas,
            title,
            help_text,
            empty_text,
            subtitle,
            min_width=min_width,
            min_height=min_height,
        )

        # Columns that fit one width wrap wrongly at another
        self.canvas.mpl_connect("resize_event", self._on_resize)
        self.canvas.mpl_connect("draw_event", self._freeze_layout)

        self._lines = {}
        self._fills = {}
        self._last_payload: SeriesPayload | None = None

    def _draw(self, payload: SeriesPayload) -> None:
        subtitle = payload.subtitle or self._default_subtitle
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))
        if not len(payload.x_values):
            self._last_payload = None
            super().show_empty()
            return
        if self._unchanged(payload):
            return
        # Update lines in place unless the series have changed
        if (
            self.ax
            and self._lines.keys() == payload.y_series.keys()
            and self._last_payload is not None
            and self._last_payload.colours == (payload.colours or None)
            and self._axis_kind(self._last_payload.x_values)
            == self._axis_kind(payload.x_values)
        ):
            self._update(payload)
        else:
            self._full_draw(payload)
        self._last_payload = replace(
            payload,
            x_values=np.array(payload.x_values, copy=True),
            y_series={
                key: np.array(values, copy=True)
                for key, values in payload.y_series.items()
            },
            colours=dict(payload.colours) if payload.colours else None,
        )
        self.canvas.draw_idle()

    def show_empty(self) -> None:
        """Clear stale source metadata together with unavailable chart data."""
        self._last_payload = None
        self._subtitle.setText(self._default_subtitle)
        self._subtitle.setVisible(bool(self._default_subtitle))
        super().show_empty()

    def _unchanged(self, payload: SeriesPayload) -> bool:
        """Avoid drawing when time changes leave the plotted data unchanged."""
        previous = self._last_payload
        return (
            previous is not None
            and previous.x_label == payload.x_label
            and previous.colours == (payload.colours or None)
            and self._axis_kind(previous.x_values) == self._axis_kind(payload.x_values)
            and previous.y_series.keys() == payload.y_series.keys()
            and np.array_equal(previous.x_values, payload.x_values)
            and all(
                np.array_equal(previous.y_series[key], values, equal_nan=True)
                for key, values in payload.y_series.items()
            )
        )

    def _full_draw(self, payload: SeriesPayload) -> None:
        ax = self._reset_axes()
        x = payload.x_values
        series = payload.y_series

        colours = payload.colours or {}

        self._lines = {}
        self._fills = {}
        for key, values in series.items():
            # Unpack since ax.plot returns [Line2D]
            (line,) = ax.plot(
                x,
                values,
                label=key,
                color=colours.get(key, QUANTITY_COLOURS.get(key)),
            )
            self._fills[key] = ax.fill_between(
                x, values, color=line.get_color(), alpha=0.2
            )
            self._lines[key] = line

        # A zero line makes signed (net) series readable
        ax.axhline(0, color=THEME["border"], linewidth=1)

        ax.set_xlabel(payload.x_label, color=THEME["muted"])
        self._label_y_axis(ax, series)
        self._format_time_axis(ax, x)
        add_graph_legend(self.figure)

    def _update(self, payload: SeriesPayload) -> None:
        assert self.ax is not None  # Only called after a full draw
        for key, values in payload.y_series.items():
            self._lines[key].set_data(payload.x_values, values)
            self._fills[key].set_data(
                self.ax.convert_xunits(payload.x_values), values, 0
            )

        if payload.x_label != self._last_payload.x_label:
            self.figure.set_layout_engine("constrained")
        self.ax.set_xlabel(payload.x_label, color=THEME["muted"])
        self._label_y_axis(self.ax, payload.y_series)
        self.ax.relim()
        self.ax.autoscale_view()
        self._fit_margins()

    def _fit_margins(self) -> None:
        """Remeasure when new tick text clips or collides with a legend."""
        renderer = self.canvas.get_renderer()
        bounds = self.ax.get_tightbbox(renderer)
        if bounds is None:
            return
        inside = self.figure.bbox.contains(
            bounds.x0, bounds.y0
        ) and self.figure.bbox.contains(bounds.x1, bounds.y1)
        overlaps_legend = any(
            bounds.overlaps(legend.get_window_extent(renderer))
            for legend in self.figure.legends
        )
        if not inside or overlaps_legend:
            self.figure.set_layout_engine("constrained")

    def _reset_axes(self) -> Axes:
        """Clear canvas and return one themed axes."""
        self.figure.clear()
        self.figure.set_layout_engine("constrained")
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

    def _on_resize(self, _event) -> None:
        """Recalculate plot margins and legend wrapping when geometry changes."""
        self.figure.set_layout_engine("constrained")
        add_graph_legend(self.figure)

    def _freeze_layout(self, _event) -> None:
        """Keep measured margins until resize or a full axes rebuild."""
        self.figure.set_layout_engine("none")

    @staticmethod
    def _axis_kind(x) -> tuple[bool, object]:
        is_time = bool(len(x)) and isinstance(x[0], datetime | np.datetime64)
        return is_time, getattr(x[0], "tzinfo", None) if is_time else None

    @staticmethod
    def _format_time_axis(ax: Axes, x) -> None:
        """Use concise date ticks if x holds datetimes, in their timezone if they
        have one."""
        if len(x) > 0 and isinstance(x[0], datetime | np.datetime64):
            zone = getattr(x[0], "tzinfo", None)
            locator = plt_dates.AutoDateLocator(tz=zone)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(
                plt_dates.ConciseDateFormatter(locator, tz=zone)
            )

    def _label_y_axis(self, ax: Axes, series: dict) -> None:
        """Label the y axis with y_label, or else the only series' label; several
        series without a y_label are told apart by the legend instead."""
        label = self.y_label or (next(iter(series)) if len(series) == 1 else "")
        if label:
            ax.set_ylabel(label, color=THEME["muted"])
