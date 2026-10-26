"""Realised and forecast household PV generation.

Weather rows and house models go in, PV rows and snapshots come out. The
physics is pvlib's ModelChain with PVWatts-style module parameters. All times
are timezone-aware UTC.
"""

from datetime import datetime

import pandas as pd
import pvlib  # type: ignore[import-untyped]

from backend_v2.generation.pv_forecast_schedule import pv_cycle_windows
from backend_v2.weather.fetch_weather import ECMWF_24H_HORIZON_AVAILABILITY_DELAY
from database_v2.models.data_models import (
    ECMWFWeatherRow,
    ForecastPvSnapshot,
    OpenMeteoWeatherRow,
    RealisedPvRow,
)
from database_v2.models.request_models import HouseConfig, Location


def _build_times(start_utc: datetime, end_utc: datetime, freq: str) -> pd.DatetimeIndex:
    """Return the UTC interval starts in [start_utc, end_utc)."""

    return pd.date_range(
        pd.Timestamp(start_utc).tz_convert("UTC"),
        pd.Timestamp(end_utc).tz_convert("UTC"),
        freq=freq,
        inclusive="left",
    )


class PvGenerationService:
    """Calculate realised and forecast household PV output from weather."""

    def calculate_realised_pv(
        self,
        location: Location,
        house_config: HouseConfig,
        weather: list[OpenMeteoWeatherRow],
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[RealisedPvRow]:
        """Return 5-minute PV for [start_utc, end_utc).

        Ten-minute radiation means apply to the preceding interval. Temperature
        and wind are point estimates interpolated onto the 5-minute grid.
        """

        times = _build_times(start_utc, end_utc, "5min")
        if times.empty:
            return []
        weather_frame = (
            pd.DataFrame([row.model_dump() for row in weather])
            .set_index("timestamp_utc")
            .sort_index()
        )
        if weather_frame.index.has_duplicates:
            raise ValueError("Duplicate Open-Meteo weather timestamps.")
        required_radiation = pd.date_range(
            times[0].floor("10min") + pd.Timedelta(minutes=10),
            times[-1].floor("10min") + pd.Timedelta(minutes=10),
            freq="10min",
        )
        if not required_radiation.isin(weather_frame.index).all():
            raise ValueError("Missing 10-minute Open-Meteo radiation timestamps.")

        radiation = weather_frame[["ghi", "dhi", "dni"]].copy()
        radiation.index = pd.DatetimeIndex(radiation.index) - pd.Timedelta(minutes=10)
        radiation = radiation.reindex(times, method="ffill")
        conditions = (
            weather_frame[["air_temperature", "wind_speed"]]
            .reindex(weather_frame.index.union(times))
            .interpolate(method="time")
            .reindex(times)
        )
        frame = pd.concat([radiation, conditions], axis=1)
        if frame.isna().any().any():
            raise ValueError("Open-Meteo weather does not cover the PV run window.")
        pv_kw = self._simulate(location, house_config, frame)

        return [
            RealisedPvRow(
                timestamp_utc=timestamp,  # type: ignore[arg-type]
                house_config_id=house_config.house_config_id,
                location_name=location.location_name,
                pv_kw=value,
            )
            for timestamp, value in pv_kw.items()
        ]

    def calculate_forecast_pv(
        self,
        location: Location,
        house_config: HouseConfig,
        weather: list[ECMWFWeatherRow],
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[ForecastPvSnapshot]:
        """Return each needed ECMWF cycle once for [start_utc, end_utc)."""
        windows = pv_cycle_windows(start_utc, end_utc)
        if not windows:
            return []

        frame = pd.DataFrame([row.model_dump() for row in weather])
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
        frame["available_at_utc"] = pd.to_datetime(frame["available_at_utc"], utc=True)
        snapshots = []
        for available_at, (first_target, end_target) in windows.items():
            cycle = pd.Timestamp(available_at) - ECMWF_24H_HORIZON_AVAILABILITY_DELAY
            needed_times = _build_times(first_target, end_target, "30min")
            cycle_frame = (
                frame.loc[frame["available_at_utc"] == cycle]
                .set_index("timestamp_utc")
                .sort_index()
            )
            if cycle_frame.index.has_duplicates:
                raise ValueError(f"Duplicate ECMWF targets for cycle {cycle}.")
            cycle_frame = cycle_frame.reindex(needed_times)
            missing_weather = (
                cycle_frame[["ghi", "air_temperature", "wind_speed"]].isna().any().any()
            )
            if missing_weather:
                raise ValueError(
                    f"ECMWF cycle {cycle} is missing targets needed for a "
                    "48-period PV forecast."
                )
            values = self._simulate(location, house_config, cycle_frame)
            snapshots.append(
                ForecastPvSnapshot(
                    available_at_utc=available_at,
                    house_config_id=house_config.house_config_id,
                    location_name=location.location_name,
                    target_times_utc=list(needed_times.to_pydatetime()),
                    pv_kw=values.tolist(),
                )
            )
        return snapshots

    def _simulate(
        self, location: Location, house_config: HouseConfig, frame: pd.DataFrame
    ) -> pd.Series:
        """Run pvlib for one house and return AC power in kW."""
        frame = frame.rename(columns={"air_temperature": "temp_air"})
        pvlib_location = pvlib.location.Location(
            latitude=location.latitude,
            longitude=location.longitude,
            tz=location.timezone,
            name=location.location_name,
        )
        if "dni" not in frame:  # ECMWF only forecasts GHI, so split it with ERBS
            zenith = pvlib_location.get_solarposition(frame.index)["zenith"]
            split = pvlib.irradiance.erbs(
                ghi=frame["ghi"], zenith=zenith, datetime_or_doy=frame.index
            )
            frame = frame.assign(dni=split["dni"], dhi=split["dhi"])

        system = pvlib.pvsystem.PVSystem(
            surface_tilt=house_config.panel_tilt,
            surface_azimuth=house_config.panel_azimuth,
            module_parameters={
                "pdc0": house_config.pv_peak_capacity * 1000.0,
                "gamma_pdc": -0.004,
            },
            inverter_parameters={
                "pdc0": house_config.pv_inverter_ac_kw
                * 1000.0
                / house_config.pv_inverter_efficiency,
                "eta_inv_nom": house_config.pv_inverter_efficiency,
            },
            temperature_model_parameters=pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS[
                "sapm"
            ]["open_rack_glass_glass"],
        )
        model_chain = pvlib.modelchain.ModelChain(
            system,
            pvlib_location,
            aoi_model="physical",
            spectral_model="no_loss",
        )

        WEATHER_COLUMNS = ["ghi", "dni", "dhi", "temp_air", "wind_speed"]
        model_chain.run_model(frame[WEATHER_COLUMNS])
        return (model_chain.results.ac / 1000.0).round(4)
