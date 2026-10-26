"""form_fields.py
Fields shown by each FormView. Keys match the FrontendDao setter's fields."""

from frontend_v2.ui.views.form_view import FormField

# Everything a house config's setter takes, other than its id
HOUSE_CONFIG_FIELDS = [
    FormField("pv_peak_capacity", "PV peak capacity", unit="kW", maximum=100, step=0.1),
    FormField("panel_tilt", "Panel tilt", unit="°", maximum=90, decimals=1),
    FormField("panel_azimuth", "Panel azimuth", unit="°", maximum=360, decimals=1),
    FormField("pv_inverter_ac_kw", "Inverter size", unit="kW", maximum=100, step=0.1),
    FormField(
        "pv_inverter_efficiency",
        "Inverter efficiency",
        unit="%",
        maximum=100,
        step=0.5,
        decimals=1,
        scale=100,
    ),
    FormField("has_aircon", "Air conditioning", kind=bool),
    FormField("has_poolpump", "Pool pump", kind=bool),
    FormField("has_ev", "Electric vehicle", kind=bool),
]

# Everything a battery config's setter takes, other than its id
BATTERY_CONFIG_FIELDS = [
    FormField("capacity_kwh", "Capacity", unit="kWh", step=10, decimals=1),
    FormField("max_power_kw", "Charge/discharge", unit="kW", step=10, decimals=1),
    FormField(
        "efficiency",
        "Efficiency",
        unit="%",
        maximum=100,
        step=0.5,
        decimals=1,
        scale=100,
    ),
    FormField("min_soc_frac", "Minimum SOC", unit="%", maximum=100, scale=100),
    FormField("max_soc_frac", "Maximum SOC", unit="%", maximum=100, scale=100),
    FormField(
        "replacement_cost_aud",
        "Replacement cost",
        unit="AUD",
        maximum=100_000_000,
        step=1000,
        decimals=0,
    ),
    FormField(
        "lifetime_cycles",
        "Cycle life",
        kind=int,
        unit="cycles",
        maximum=100_000,
        step=100,
    ),
    FormField(
        "max_cycles_24h",
        "Max cycles per day",
        unit="cycles",
        maximum=10,
        step=0.1,
    ),
]
