"""bindings/selectors/records.py
Selectors that return a RecordsPayload: editable records keyed by id."""

import re
from math import isfinite

from database_v2.models.request_models import BatteryConfig
from frontend_v2.bindings.needs_decorator import needs
from frontend_v2.utils.payload import RecordsPayload


@needs({"get_all_house_configs"})
def select_house_config_records(data: dict) -> RecordsPayload:
    """Every house config, keyed by house_config_id, for editing."""
    payload = _config_records(data["get_all_house_configs"], "house_config_id")
    return RecordsPayload(
        payload.records,
        {key: house_config_label(record) for key, record in payload.records.items()},
    )


@needs({"get_all_battery_configs"})
def select_battery_config_records(data: dict) -> RecordsPayload:
    """Every battery config, keyed by battery_config_id, for editing."""
    payload = _config_records(data["get_all_battery_configs"], "battery_config_id")
    for record in payload.records.values():
        record["degradation_aud_per_kwh"] = battery_wear_rate(record)
    return RecordsPayload(
        payload.records,
        {key: battery_config_label(record) for key, record in payload.records.items()},
    )


def house_config_label(record: dict) -> str:
    """Use a readable preset name and actual specifications; preserve custom names."""
    config_id = str(record["house_config_id"])
    preset = re.fullmatch(r"house-(\d+)--.+", config_id)
    name = f"House {preset[1]}" if preset else config_id
    return (
        f"{name} · {record['pv_peak_capacity']:g} kW solar · "
        f"{record['pv_inverter_ac_kw']:g} kW inverter"
    )


def battery_config_label(record: dict) -> str:
    """Present battery names and units without changing saved identifiers."""
    config_id = str(record["battery_config_id"])
    presets = {
        "regional-5mw": "Regional battery",
        "suburban-400kwh": "Suburban battery",
        "suburban-450kwh": "Suburban battery",
        "sungrow-ps255cs-2h": "Sungrow PS255CS (2h)",
        "sungrow-ps255cs-4h": "Sungrow PS255CS (4h)",
        "tesla-mp2x1-2h": "Tesla MP2x1 (2h)",
        "tesla-mp2x1-4h": "Tesla MP2x1 (4h)",
        "tesla-mp2x1-4h-c012": "Tesla MP2x1 (4h, C012)",
    }
    capacity, power = record["capacity_kwh"], record["max_power_kw"]
    energy = f"{capacity / 1000:g} MWh" if capacity >= 1000 else f"{capacity:g} kWh"
    output = f"{power / 1000:g} MW" if power >= 1000 else f"{power:g} kW"
    return f"{presets.get(config_id, config_id)} · {energy} · {output}"


def _config_records(configs, id_field: str) -> RecordsPayload:
    """Every config row keyed by its id, with each field as a plain Python
    value."""
    return RecordsPayload(
        records={
            str(row[id_field]): {name: row[name].item() for name in configs.dtype.names}
            for row in sorted(
                configs,
                key=lambda row: [
                    int(part) if part.isdigit() else part
                    for part in re.split(r"(\d+)", str(row[id_field]).casefold())
                ],
            )
        }
    )


def battery_wear_rate(record: dict) -> float | None:
    """Use the battery model's wear rate, or None for incomplete/invalid inputs."""
    try:
        battery = BatteryConfig(**record)
        if (
            not isfinite(battery.capacity_kwh)
            or battery.capacity_kwh <= 0
            or battery.lifetime_cycles <= 0
        ):
            return None
        rate = battery.degradation_aud_per_kwh
    except (ValueError, TypeError, ZeroDivisionError):
        return None
    return rate if isfinite(rate) and rate >= 0 else None
