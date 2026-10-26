"""Calculates battery power limits, energy use and stored energy for the controllers."""

from database_v2.models.optimiser_models import LiveControllerInput
from database_v2.models.request_models import BatteryConfig

POWER_TOLERANCE = 1e-12
STATE_TOLERANCE = 1e-9
# Dispatch below the existing 100 W load deadband is not useful battery action.
MIN_BATTERY_COMMAND_KW = 0.1


def battery_power_limits(
    battery: BatteryConfig,
    soc_kwh: float,
    step_hours: float,
) -> tuple[float, float]:
    """Calculate charge and discharge limits from stored energy, efficiency and step
    duration.
    """
    if (
        not battery.min_soc_kwh - STATE_TOLERANCE
        <= soc_kwh
        <= (battery.max_soc_kwh + STATE_TOLERANCE)
    ):
        raise ValueError("soc_kwh is outside the battery operating window")

    charge_by_soc = max(battery.max_soc_kwh - soc_kwh, 0.0) / (
        battery.efficiency * step_hours
    )
    discharge_by_soc = (
        battery.efficiency * max(soc_kwh - battery.min_soc_kwh, 0.0) / step_hours
    )
    return (
        min(battery.max_power_kw, charge_by_soc),
        min(battery.max_power_kw, discharge_by_soc),
    )


def battery_throughput_kwh(
    battery: BatteryConfig,
    command_kw: float,
    step_hours: float,
) -> float:
    """Calculate energy moved through the battery cells by a charge or discharge
    command.
    """
    charge_kw = max(-command_kw, 0.0)
    discharge_kw = max(command_kw, 0.0)
    return (
        battery.efficiency * charge_kw + discharge_kw / battery.efficiency
    ) * step_hours


def limit_command_by_throughput(
    battery: BatteryConfig,
    command_kw: float,
    step_hours: float,
    remaining_throughput_kwh: float,
) -> tuple[float, bool]:
    """Reduce commands to fit the remaining energy-use allowance and suppress tiny
    actions.
    """
    requested_kwh = battery_throughput_kwh(battery, command_kw, step_hours)
    limited = requested_kwh > remaining_throughput_kwh + POWER_TOLERANCE
    if limited:
        scale = remaining_throughput_kwh / requested_kwh
        command_kw *= max(min(scale, 1.0), 0.0)
    if abs(command_kw) < MIN_BATTERY_COMMAND_KW:
        command_kw = 0.0
    return command_kw, limited


def apply_throughput_limit(
    battery: BatteryConfig,
    command_kw: float,
    step_hours: float,
    remaining_throughput_kwh: float,
    reasons: list[str],
) -> tuple[float, float, float]:
    """Return the limited command, cell throughput and remaining allowance."""
    requested_kw = command_kw
    command_kw, limited = limit_command_by_throughput(
        battery, command_kw, step_hours, remaining_throughput_kwh
    )
    if limited:
        reasons.append("cycle-throughput-limit")
    elif requested_kw != 0.0 and command_kw == 0.0:
        reasons.append("battery-command-deadband")

    delivered_kwh = battery_throughput_kwh(battery, command_kw, step_hours)
    remaining_kwh = max(remaining_throughput_kwh - delivered_kwh, 0.0)
    return command_kw, delivered_kwh, remaining_kwh


def negative_price_curtailment(
    net_load_kw: float,
    command_kw: float,
    live_data: LiveControllerInput,
    reasons: list[str],
    deadband_kw: float,
) -> float:
    """Curtail surplus solar left after battery limits when export has a cost."""
    grid_exchange_kw = net_load_kw - command_kw
    if live_data.sell_tariff_aud_per_kwh < 0.0 and grid_exchange_kw < -deadband_kw:
        reasons.append("negative-export-price-curtailment")
        return min(-grid_exchange_kw, live_data.pv_kw)
    return 0.0


def next_soc_kwh(
    battery: BatteryConfig,
    soc_kwh: float,
    command_kw: float,
    step_hours: float,
) -> float:
    """Calculate stored energy after an action, accounting for losses and storage
    limits.
    """
    if command_kw >= 0.0:
        next_soc = soc_kwh - command_kw * step_hours / battery.efficiency
    else:
        next_soc = soc_kwh + (-command_kw) * step_hours * battery.efficiency
    return min(max(next_soc, battery.min_soc_kwh), battery.max_soc_kwh)
