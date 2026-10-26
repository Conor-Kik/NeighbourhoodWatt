from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeFloat = Annotated[float, Field(ge=0.0)]


class RealisedDemandRow(BaseModel):
    """Maps o a single row in 'realised_demand_5m' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    timestamp_utc: datetime
    house_config_id: str
    demand_kw: float = Field(ge=0.0)


class ForecastDemandRow(BaseModel):
    """Maps to a singe row in the 'forecast_demand_30m' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    run_time_utc: datetime
    target_time_utc: datetime
    house_config_id: str
    demand_kw: float = Field(ge=0.0)


class RealisedEmissionsRow(BaseModel):
    """Maps to a single row in the 'realised_emissions_5m' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    timestamp_utc: datetime
    location_name: str
    carbon_intensity_t_per_mwh: float = Field(ge=0.0)


class ForecastEmissionsSnapshot(BaseModel):
    """One emissions forecast snapshot and its parallel target/value series."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    run_time_utc: datetime
    location_name: str
    target_times_utc: list[datetime] = Field(min_length=1)
    carbon_intensities_t_per_mwh: list[NonNegativeFloat] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_forecast_lengths(self) -> "ForecastEmissionsSnapshot":
        if len(self.target_times_utc) != len(self.carbon_intensities_t_per_mwh):
            raise ValueError(
                "target_times_utc and carbon_intensities_t_per_mwh must have "
                "equal lengths"
            )
        return self


class CarbonPriceRow(BaseModel):
    """Maps to a single row in the 'carbon_price' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    calendar_year: int = Field(gt=2020)
    carbon_price_aud_per_t: float = Field(ge=0.0)


class RealisedPriceRow(BaseModel):
    """Maps to a single row in the 'realised_price_5m' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    timestamp_utc: datetime
    location_name: str
    buy_tariff_aud: float
    sell_tariff_aud: float


PriceForecastSource = Literal["aemo", "historical"]


class ForecastPriceSnapshot(BaseModel):
    """One price forecast snapshot and its parallel target/tariff series."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    run_time_utc: datetime
    location_name: str
    target_times_utc: list[datetime] = Field(min_length=1)
    buy_tariffs_aud: list[float] = Field(min_length=1)
    sell_tariffs_aud: list[float] = Field(min_length=1)
    forecast_sources: list[PriceForecastSource] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_forecast_lengths(self) -> "ForecastPriceSnapshot":
        lengths = {
            len(self.target_times_utc),
            len(self.buy_tariffs_aud),
            len(self.sell_tariffs_aud),
            len(self.forecast_sources),
        }
        if len(lengths) != 1:
            raise ValueError(
                "target_times_utc, buy_tariffs_aud, sell_tariffs_aud and "
                "forecast_sources must "
                "have equal lengths"
            )
        return self


class OpenMeteoWeatherRow(BaseModel):
    """Maps to a single row in the 'raw_openmeteo' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    timestamp_utc: datetime
    location_name: str
    ghi: float
    dhi: float
    dni: float
    air_temperature: float
    wind_speed: float


class ECMWFWeatherRow(BaseModel):
    """Maps to a single row in the 'raw_ecmwf' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    timestamp_utc: datetime
    available_at_utc: datetime
    location_name: str
    ghi: float
    air_temperature: float
    wind_speed: float


class RealisedPvRow(BaseModel):
    """Maps to a single row in the 'realised_pv_5m' table."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    timestamp_utc: datetime
    house_config_id: str
    location_name: str
    pv_kw: NonNegativeFloat


class ForecastPvSnapshot(BaseModel):
    """One causally available PV cycle and its parallel target/value series."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    available_at_utc: datetime
    house_config_id: str
    location_name: str
    target_times_utc: list[datetime] = Field(min_length=1)
    pv_kw: list[NonNegativeFloat] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_forecast_lengths(self) -> "ForecastPvSnapshot":
        if len(self.target_times_utc) != len(self.pv_kw):
            raise ValueError("target_times_utc and pv_kw must have equal lengths")
        return self
