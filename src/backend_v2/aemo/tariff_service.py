"""Looks up the network import and export tariff rates for a location, date and time."""

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from database_v2.models.request_models import NemRegion

TARIFFS_FILE = (
    Path(__file__).resolve().parents[2] / "database_v2" / "seed_data" / "tariffs.json"
)

REGIONAL_TARIFF_LOCATIONS = {
    NemRegion.QLD: "brisbane",
    NemRegion.NSW: "sydney",
    NemRegion.VIC: "melbourne",
    NemRegion.SA: "adelaide",
    NemRegion.TAS: "hobart",
}


class NetworkTariffRates(BaseModel):
    """Variable network rates applicable to one local interval."""

    model_config = ConfigDict(frozen=True)

    import_aud_per_kwh: float
    export_aud_per_kwh: float


def _minutes(value: str) -> int:
    """Convert an HH:MM tariff time into minutes after midnight."""
    if value == "24:00":
        return 24 * 60
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _time_in_range(current: time, start: str, end: str) -> bool:
    """Return whether a time falls in a half-open, possibly overnight range."""
    minute = current.hour * 60 + current.minute
    start_minute = _minutes(start)
    end_minute = _minutes(end)
    if start_minute < end_minute:
        return start_minute <= minute < end_minute
    # A start after the end indicates a range crossing midnight.
    return minute >= start_minute or minute < end_minute


def _matches_time(current: time, expression: str) -> bool:
    """Match a tariff time against one or more human-readable ranges."""
    expression = expression.lower().strip()
    if expression == "all times":
        return True
    ranges = expression.replace(" and ", ",").split(",")
    return any(
        _time_in_range(current, *map(str.strip, period.split("-", 1)))
        for period in ranges
        if "-" in period
    )


def _matches_day(value: datetime, expression: str) -> bool:
    """Match a timestamp against the supported tariff day expressions."""
    expression = expression.lower().strip()
    if expression in {"daily", "all days"}:
        return True
    if expression in {"monday-friday", "working weekdays"}:
        return value.weekday() < 5
    if expression == "saturday-sunday":
        return value.weekday() >= 5
    return False


def _matches_month(value: datetime, months: list[str] | None) -> bool:
    """Match a timestamp against optional full month names."""
    return not months or value.strftime("%B") in months


def _has_conditional_export_charge(export: dict[str, Any]) -> bool:
    """Identify export charges that require unavailable usage context."""
    conditional_keys = {
        "free_export_kwh_per_day",
        "nominal_free_export_kwh_per_month",
        "midday_free_threshold",
        "free_allowance_kwh_per_day",
        "charge_above_allowance_c_per_kwh",
        "charge_above_threshold_c_per_kwh",
    }
    return bool(conditional_keys.intersection(export)) or any(
        key.startswith("applies_if_") for key in export
    )


class TariffService:
    """Loads tariff seed data once and resolves rates by timestamp."""

    def __init__(self, tariffs_file: Path = TARIFFS_FILE) -> None:
        """Load tariff definitions from seed data once per service instance."""
        with tariffs_file.open("r", encoding="utf-8") as file:
            self._cities: dict[str, dict[str, Any]] = json.load(file)["cities"]

    def rates_for(
        self,
        location_name: str,
        timestamp_utc: datetime,
        *,
        nem_region: NemRegion | None = None,
    ) -> NetworkTariffRates:
        """Return representative network additions, retaining named city profiles.

        Custom names use their NEM region's capital-city profile, including its
        tariff timezone. This is a modelling proxy, not a site-specific tariff.
        """
        if timestamp_utc.tzinfo is None:
            raise ValueError("timestamp_utc must be timezone-aware.")

        location_key = location_name.lower().strip()
        if location_key not in self._cities and nem_region is not None:
            location_key = REGIONAL_TARIFF_LOCATIONS[nem_region]
        try:
            city = self._cities[location_key]
        except KeyError as error:
            valid = ", ".join(sorted(self._cities))
            raise ValueError(
                f"Unknown tariff location '{location_name}'. Expected: {valid}"
            ) from error

        local_time = timestamp_utc.astimezone(ZoneInfo(city["timezone"]))
        tariff = self._tariff_for_date(city, local_time)
        import_rate = self._import_rate(tariff, local_time)
        export_rate = self._export_rate(tariff, local_time)
        return NetworkTariffRates(
            import_aud_per_kwh=float(import_rate["aud_per_kwh"]),
            export_aud_per_kwh=export_rate,
        )

    @staticmethod
    def _tariff_for_date(city: dict[str, Any], local_time: datetime) -> dict[str, Any]:
        """Select the tariff whose inclusive effective dates cover a timestamp."""
        for tariff in city["tariffs_by_financial_year"].values():
            if (
                datetime.fromisoformat(tariff["effective_from"]).date()
                <= local_time.date()
                <= datetime.fromisoformat(tariff["effective_to"]).date()
            ):
                return tariff
        raise ValueError(f"No tariff covers {local_time.date().isoformat()}.")

    @staticmethod
    def _import_rate(tariff: dict[str, Any], local_time: datetime) -> dict[str, Any]:
        """Select the most specific import rate, falling back when necessary."""
        fallback: dict[str, Any] | None = None
        for rate in tariff["import_energy_rates"]:
            if not _matches_month(local_time, rate.get("months")):
                continue
            if not _matches_day(local_time, rate["days"]):
                continue
            if rate["time"].lower().strip() == "all other times":
                fallback = rate
            elif _matches_time(local_time.time(), rate["time"]):
                return rate
        if fallback is not None:
            return fallback
        raise ValueError(f"No import tariff matches {local_time.isoformat()}.")

    @staticmethod
    def _export_rate(tariff: dict[str, Any], local_time: datetime) -> float:
        """Calculate the unconditional export reward or charge for an interval."""
        export = tariff["export_network_tariff"]
        if not export.get("available", False):
            return 0.0

        reward = 0.0
        reward_window = export.get("evening_reward_window")
        if reward_window and _matches_time(local_time.time(), reward_window):
            reward = float(export.get("export_reward_aud_per_kwh", 0.0))

        charge = 0.0
        charge_window = export.get("charge_window")
        # Usage-threshold charges need daily/monthly export totals unavailable here.
        if (
            not _has_conditional_export_charge(export)
            and charge_window
            and _matches_time(local_time.time(), charge_window)
        ):
            charge = float(export.get("export_charge_aud_per_kwh", 0.0))
        return reward - charge
