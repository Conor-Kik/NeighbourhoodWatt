from datetime import UTC, datetime, timedelta

from polyfactory import Use
from polyfactory.factories.pydantic_factory import ModelFactory

from database_v2.models.data_models import (
    ECMWFWeatherRow,
    ForecastDemandRow,
    ForecastEmissionsSnapshot,
    ForecastPriceSnapshot,
    ForecastPvSnapshot,
    OpenMeteoWeatherRow,
    RealisedDemandRow,
    RealisedEmissionsRow,
    RealisedPriceRow,
    RealisedPvRow,
)
from database_v2.models.request_models import (
    BatteryConfig,
    HouseConfig,
    Location,
    ObjectiveMode,
    SimulationRequest,
)


class LocationFactory(ModelFactory[Location]):
    __model__ = Location

    location_name = "Brisbane"
    latitude = -27.47
    longitude = 153.02
    timezone = "Australia/Brisbane"
    nem_region = "QLD1"


class BatteryConfigFactory(ModelFactory[BatteryConfig]):
    __model__ = BatteryConfig

    battery_config_id = "tesla_powerwall_3"
    capacity_kwh = 13.5
    max_power_kw = 5.0
    efficiency = 0.9
    min_soc_frac = 0.05
    max_soc_frac = 1.0
    replacement_cost_aud = 12000.0
    lifetime_cycles = 3650
    max_cycles_24h = 1.0


class HouseConfigFactory(ModelFactory[HouseConfig]):
    __model__ = HouseConfig

    house_config_id = "standard_family_6kw"
    pv_peak_capacity = 6.0
    panel_tilt = 20.0
    panel_azimuth = 0.0
    pv_inverter_ac_kw = 5.0
    pv_inverter_efficiency = 0.96
    has_aircon = True
    has_poolpump = False
    has_ev = True


class SimulationRequestFactory(ModelFactory[SimulationRequest]):
    __model__ = SimulationRequest

    request_id = "test_req_123"
    objective_mode = ObjectiveMode.COST
    start_time_utc = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end_time_utc = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)
    location_name = Use(lambda: LocationFactory.build().location_name)
    battery_config_id = Use(lambda: BatteryConfigFactory.build().battery_config_id)
    # houses is dict[house_config_id -> quantity]
    houses = Use(lambda: {HouseConfigFactory.build().house_config_id: 1})
    # Default to AEMO market prices
    fixed_buy_aud_per_kwh = None
    fixed_sell_aud_per_kwh = None


class RealisedDemandRowFactory(ModelFactory[RealisedDemandRow]):
    __model__ = RealisedDemandRow

    timestamp_utc = datetime(2026, 1, 1, tzinfo=UTC)
    house_config_id = Use(lambda: HouseConfigFactory.build().house_config_id)
    demand_kw = 1.0


class ForecastDemandRowFactory(ModelFactory[ForecastDemandRow]):
    __model__ = ForecastDemandRow

    run_time_utc = datetime(2026, 1, 1, tzinfo=UTC)
    target_time_utc = datetime(2026, 1, 1, tzinfo=UTC)
    house_config_id = Use(lambda: HouseConfigFactory.build().house_config_id)
    demand_kw = 1.0


class RealisedEmissionsRowFactory(ModelFactory[RealisedEmissionsRow]):
    __model__ = RealisedEmissionsRow

    timestamp_utc = datetime(2026, 1, 1, tzinfo=UTC)
    location_name = Use(lambda: LocationFactory.build().location_name)
    carbon_intensity_t_per_mwh = 0.7


class ForecastEmissionsSnapshotFactory(ModelFactory[ForecastEmissionsSnapshot]):
    __model__ = ForecastEmissionsSnapshot

    run_time_utc = datetime(2026, 1, 1, tzinfo=UTC)
    location_name = Use(lambda: LocationFactory.build().location_name)
    target_times_utc = [
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=30 * index)
        for index in range(48)
    ]
    carbon_intensities_t_per_mwh = [0.7] * 48


class OpenMeteoWeatherRowFactory(ModelFactory[OpenMeteoWeatherRow]):
    __model__ = OpenMeteoWeatherRow

    timestamp_utc = datetime(2026, 1, 1, tzinfo=UTC)
    location_name = Use(lambda: LocationFactory.build().location_name)
    ghi = 0.0
    dhi = 0.0
    dni = 0.0
    air_temperature = 0.0
    wind_speed = 0.0


class ECMWFWeatherRowFactory(ModelFactory[ECMWFWeatherRow]):
    __model__ = ECMWFWeatherRow

    timestamp_utc = datetime(2026, 1, 1, tzinfo=UTC)
    available_at_utc = datetime(2026, 1, 1, tzinfo=UTC)
    location_name = Use(lambda: LocationFactory.build().location_name)
    ghi = 0.0
    air_temperature = 0.0
    wind_speed = 0.0


class RealisedPriceRowFactory(ModelFactory[RealisedPriceRow]):
    __model__ = RealisedPriceRow

    timestamp_utc = datetime(2026, 1, 1, tzinfo=UTC)
    location_name = Use(lambda: LocationFactory.build().location_name)
    buy_tariff_aud = 0.25
    sell_tariff_aud = 0.08


class ForecastPriceSnapshotFactory(ModelFactory[ForecastPriceSnapshot]):
    __model__ = ForecastPriceSnapshot

    run_time_utc = datetime(2026, 1, 1, tzinfo=UTC)
    location_name = Use(lambda: LocationFactory.build().location_name)
    target_times_utc = [
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=30 * index)
        for index in range(48)
    ]
    buy_tariffs_aud = [0.25] * 48
    sell_tariffs_aud = [0.08] * 48
    forecast_sources = ["aemo"] * 48


class RealisedPvRowFactory(ModelFactory[RealisedPvRow]):
    __model__ = RealisedPvRow

    timestamp_utc = datetime(2026, 1, 1, tzinfo=UTC)
    house_config_id = Use(lambda: HouseConfigFactory.build().house_config_id)
    location_name = Use(lambda: LocationFactory.build().location_name)
    pv_kw = 1.0


class ForecastPvSnapshotFactory(ModelFactory[ForecastPvSnapshot]):
    __model__ = ForecastPvSnapshot

    available_at_utc = datetime(2026, 1, 1, tzinfo=UTC)
    house_config_id = Use(lambda: HouseConfigFactory.build().house_config_id)
    location_name = Use(lambda: LocationFactory.build().location_name)
    target_times_utc = [
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=30 * index)
        for index in range(48)
    ]
    pv_kw = [2.0] * 48
