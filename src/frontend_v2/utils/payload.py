"""utils/payload.py
Dataclass payloads for ReadBindings and PanelViews."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Plain lists or numpy columns straight from a Buffer
Values = Sequence | np.ndarray


@dataclass(frozen=True)
class SeriesPayload:
    """Payload regards series-based data. All y-lists must share the same
    x-axes. Each y_series key is that series' label, with its unit.

    Example:
        series_pl = SeriesPayload(
            x_label = "Time (UTC)",
            x_values = [ datetime(...), datetime(...), ... ],
            y_series = {
                "Solar irradiance (W/m²)": [ 2.6, 5.2, ... ],
                "Average battery (%)": [ 0.1, 0.1, ... ],
            }
        )
    """

    x_label: str
    x_values: Values
    y_series: dict[str, Values]
    # Colour per y_series key; a series missing here gets the theme's colour
    colours: dict[str, str] | None = None
    # Optional source context, such as the forecast issue displayed by a chart.
    subtitle: str = ""


@dataclass(frozen=True)
class TabularPayload:
    """Payload regards tabular data.

    Example:
        table_pl = TabularPayload(
            headers = ["house", "has_solar", "battery (PV)"],
            rows = [
                ("H1", False, 0.1),
                ("H2", False, 0.2),
                ("H3", False, 0.3)
            ]
        )
    """

    headers: list[str]
    rows: list[tuple]


@dataclass(frozen=True)
class CategoricalPayload:
    """Payload regards categorical data. Each y_values list holds one value per
    x label. Each y_values key is that series' label, with its unit.

    Example:
        categorical_pl = CategoricalPayload(
            x_labels = ["House 1", "House 2", "House 3"],
            y_values = {
                "PV capacity (kW)": [ 6.6, 10.0, 5.0 ],
                "Battery capacity (kWh)": [ 13.5, 0.0, 10.0 ],
            }
        )
    """

    x_labels: list[str]
    y_values: dict[str, Values]
    # Colour per x label, for a payload of one series; overrides the series'
    x_colours: list[str] | None = None
    # Optional interpretation displayed beneath the comparison chart.
    detail: str = ""
    # Optional labels for charts whose metric changes with the saved objective.
    title: str | None = None
    y_label: str | None = None
    subtitle: str | None = None


@dataclass(frozen=True)
class RecordsPayload:
    """Payload regards editable records, keyed by id. Each record maps a field
    name to its saved value.

    Example:
        records_pl = RecordsPayload(
            records = {
                "house-1": {"pv_peak_capacity": 6.6, "has_aircon": False, ...},
                "house-2": {"pv_peak_capacity": 10.0, "has_aircon": True, ...},
            }
        )
    """

    records: dict[str, dict[str, Any]]
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ChoicesPayload:
    """Payload regards picking one record. choices maps each id to the label
    shown for it; current is the saved pick, or None if nothing is picked.

    Example:
        choices_pl = ChoicesPayload(
            choices = {
                "406fec1bb57264b0": "Brisbane · 2026-01-01 → 2026-01-03",
                "78dc43bb8b77cb35": "Brisbane · 2026-01-01 → 2026-01-03",
            },
            current = "406fec1bb57264b0",
        )
    """

    choices: dict[str, str]
    current: str | None
