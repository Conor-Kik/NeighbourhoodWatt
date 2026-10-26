"""theme.py
Colours shared across the UI. Solar, demand and battery each hold one hue;
grid exchange stays hue-free so import and export read as direction."""

# Theme colours for the application
THEME: dict[str, str] = {
    "solar": "#e0a020",
    "demand": "#4a90c8",
    "battery": "#52a878",
    "exchange_pos": "#eef1f5",
    "exchange_neg": "#23272e",
    "exchange_mid": "#7d8794",
    "curtail": "#d24a3a",
    "cost": "#7d8794",
    "alert": "#d24a3a",
    "ghost": "#8d97a6",
    "ink": "#eef1f5",
    "ink_dim": "#c2c2c2",
    "muted": "#8d97a6",
    "panel": "#1c1c1c",
    "track": "#23272e",
    "border": "#333b46",
    "canvas": "#141414",
    "accent": "#F9C322",
}

# Series keys in a payload map to these colours.
QUANTITY_COLOURS: dict[str, str] = {
    "PV (kW)": THEME["solar"],
    "Demand (kW)": THEME["demand"],
    "SOC (kWh)": THEME["battery"],
    "SOC (%)": THEME["battery"],
    "net": THEME["exchange_mid"],
    "Battery charge (kW)": THEME["battery"],
    "Curtailment (kW)": THEME["curtail"],
    "cost": THEME["cost"],
    "PV generation (kW)": THEME["solar"],
    "Grid import (kW)": THEME["exchange_pos"],
    "Grid export (kW)": THEME["exchange_mid"],
    "Curtailed PV (kW)": THEME["curtail"],
    "Battery charging (kW)": THEME["battery"],
    "Battery discharging (kW)": "#a8e0c0",
    "Grid import (AUD)": THEME["exchange_pos"],
    "Grid export (AUD)": THEME["exchange_mid"],
    "Battery degradation (AUD)": THEME["battery"],
    "Net grid (kW)": THEME["exchange_mid"],
    "Net battery (kW)": THEME["battery"],
}

# Debrief scenario names map to these colours, the same in every chart.
SCENARIO_COLOURS: dict[str, str] = {
    "optimised_battery": THEME["accent"],
    "reactive_battery": THEME["solar"],
    "perfect_foresight": "#9b7bd0",
    "no_battery_shared": THEME["muted"],
    "no_battery_individual": "#5c6572",
}
