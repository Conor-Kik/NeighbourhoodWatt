"""Synthetic UI component benchmark; run with python -m tests.frontend_v2.benchmark_ui.

This measures SQLite reads, unchanged Buffer snapshots and six forced canvas
renders. It is intentionally separate from pytest's timing-independent tests.
"""

import json
import os
import platform
import sqlite3
import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from frontend_v2.ui.views.line_graph_view import LineGraphView
from frontend_v2.utils.buffer import Buffer
from frontend_v2.utils.payload import SeriesPayload


def timed(fn: Callable[[], object], n: int = 7) -> float:
    times = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(times), 2)


def benchmark(count: int) -> dict[str, float]:
    """Measure one fixture size using the same data on every repetition."""
    conn = sqlite3.connect(":memory:")
    conn.execute("create table samples(id integer, value real)")
    records = [(i, float(i % 100)) for i in range(count)]
    conn.executemany("insert into samples values (?,?)", records)
    buf = Buffer([("id", "i8"), ("value", "f8")], "id")
    buf.replace(records)
    x = [
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * i)
        for i in range(min(count, 2000))
    ]
    payload = SeriesPayload(
        "Time (UTC)",
        x,
        {str(i): [float((j + i) % 100) for j in range(len(x))] for i in range(4)},
    )
    views = [LineGraphView("Benchmark", "", "") for _ in range(6)]
    for view in views:
        view.resize(620, 300)
        view.show_payload(payload)
        view.canvas.draw()

    def draw_charts() -> None:
        for view in views:
            view.show_payload(payload)
            view.canvas.draw()

    measured = {
        "fetch_ms": timed(
            lambda: conn.execute("select * from samples order by id").fetchall()
        ),
        "unchanged_buffer_ms": timed(lambda: buf.replace(records)),
        "six_chart_update_draw_ms": timed(draw_charts),
    }
    for v in views:
        v.close()
    conn.close()
    return measured


def main() -> None:
    """Print benchmark medians and the execution host as JSON."""
    results: dict[str, object] = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "fixtures": "synthetic SQLite rows; six charts with four series each",
    }
    for count in (288, 105120):
        results[str(count)] = benchmark(count)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    main()
