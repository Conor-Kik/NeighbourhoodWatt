import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class NemRegion(StrEnum):
    """Region identifiers used in AEMO NEM datasets."""

    NSW = "NSW1"
    QLD = "QLD1"
    VIC = "VIC1"
    SA = "SA1"
    TAS = "TAS1"


class ObjectiveMode(StrEnum):
    """Objective mode for the optimiser."""

    COST = "Cost"
    EMISSIONS = "Emissions"
    BALANCED = "Balanced"


class Location(BaseModel):
    """Maps to a single row in the 'location' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    location_name: str
    latitude: float
    longitude: float
    timezone: str
    nem_region: NemRegion


class BatteryConfig(BaseModel):
    """Maps to a single row in the 'battery_config' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    battery_config_id: str
    capacity_kwh: float
    max_power_kw: float
    efficiency: float
    min_soc_frac: float
    max_soc_frac: float
    replacement_cost_aud: float
    lifetime_cycles: int
    max_cycles_24h: float

    @property
    def degradation_aud_per_kwh(self) -> float:
        return self.replacement_cost_aud / (
            self.capacity_kwh * self.lifetime_cycles * 2
        )

    @property
    def min_soc_kwh(self) -> float:
        return self.capacity_kwh * self.min_soc_frac

    @property
    def max_soc_kwh(self) -> float:
        return self.capacity_kwh * self.max_soc_frac


class HouseConfig(BaseModel):
    """Maps to a single row in the 'house_config' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    house_config_id: str
    pv_peak_capacity: float
    panel_tilt: float
    panel_azimuth: float
    pv_inverter_ac_kw: float
    pv_inverter_efficiency: float
    has_aircon: bool
    has_poolpump: bool
    has_ev: bool


class SimulationRequest(BaseModel):
    """Maps to a single row in the 'simulation_request' table."""

    model_config = ConfigDict(from_attributes=True)

    request_id: str | None = None
    objective_mode: ObjectiveMode
    start_time_utc: datetime
    end_time_utc: datetime

    location_name: str

    battery_config_id: str
    houses: dict[str, int]  # house_config_id, quantity

    # Optional final AUD/kWh prices. None keeps the AEMO market price.
    fixed_buy_aud_per_kwh: float | None = None
    fixed_sell_aud_per_kwh: float | None = None

    @model_validator(mode="after")
    def validate_fixed_tariffs(self) -> "SimulationRequest":
        buy, sell = self.fixed_buy_aud_per_kwh, self.fixed_sell_aud_per_kwh
        if buy is not None and sell is not None and sell > buy:
            raise ValueError("Fixed sell price cannot exceed fixed buy price")
        return self

    def model_post_init(self, context):
        """Deterministically generates the request_id hash
        based on the exact configuration."""
        if not self.request_id:
            houses_str = str(sorted(self.houses.items()))

            fingerprint = (
                f"{self.objective_mode}|{self.location_name}|"
                f"{self.start_time_utc.isoformat()}|{self.end_time_utc.isoformat()}|"
                f"{self.battery_config_id}|{houses_str}"
            )
            # Only added when set, so market-price requests keep their old ids.
            if (
                self.fixed_buy_aud_per_kwh is not None
                or self.fixed_sell_aud_per_kwh is not None
            ):
                fingerprint += (
                    f"|buy={self.fixed_buy_aud_per_kwh}"
                    f"|sell={self.fixed_sell_aud_per_kwh}"
                )
            self.request_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[
                :16
            ]
