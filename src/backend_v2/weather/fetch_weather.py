import datetime
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

import dynamical_catalog
import numpy as np
import openmeteo_requests
import pandas as pd
import pvlib  # type: ignore[import-untyped]
import requests_cache
from openmeteo_requests import Client as OpenMeteoClient
from retry_requests import retry  # type: ignore[import-untyped]
from timezonefinder import TimezoneFinder

from database_v2.models.data_models import (
    ECMWFWeatherRow,
    OpenMeteoWeatherRow,
)

##### CONSTANTS ########################################################################
OPENMETEO_GENERAL_WEATHER_FEATURES = [
    "temperature_2m",
    "wind_speed_10m",
]

OPENMETEO_SATELLITE_WEATHER_FEATURES = [
    "shortwave_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
]

ECMWF_WEATHER_FEATURES = [
    "downward_short_wave_radiation_flux_surface",
    "temperature_2m",
    "wind_v_10m",
    "wind_u_10m",
]

ECMWF_FEATURES_ALIGN_MAP = {
    "downward_short_wave_radiation_flux_surface": "shortwave_radiation",
    "temperature_2m": "temperature_2m",
    "wind_speed_10m": "wind_speed_10m",
}

OPENMETEO_GENERAL_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPENMETEO_SATELLITE_RADIATION_ARCHIVE_URL = (
    "https://satellite-api.open-meteo.com/v1/archive"
)
MAX_SATELLITE_GAP_SAMPLES = 6  # At most one hour of missing 10-minute readings.


ECMWF_ENSEMBLE_ARCHIVE_START_UTC = pd.Timestamp("2024-04-01 00:00:00", tz="UTC")
ECMWF_24H_HORIZON_AVAILABILITY_DELAY = datetime.timedelta(hours=6, minutes=44)
# The previous cycle remains in use until 06:44 the next day. A run at 06:30
# needs targets through 06:00 the following day (54 hours after initialization).
ECMWF_ROLLING_PV_HORIZON = datetime.timedelta(hours=54)


## LIB #################################################################################
##### OPENMETEO ########################################################################
def connect_to_openmeteo_api() -> OpenMeteoClient:
    """
    Connects to the open-meteo api and returns the client which is needed to fetch
    the data.
    """
    cache_session = requests_cache.CachedSession(".cache", expire_after=120)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    return openmeteo_requests.Client(session=retry_session)


##### ECMWF LIB ########################################################################
def get_clear_sky_ghi(
    latitude: float, longitude: float, date: str
) -> tuple[list[str], np.ndarray]:
    """
    Using pvlib fetch the clear sky GHI and corresponding times series for the given
    location and day.

    In 30 minute intervals. The times are in the format %Y-%m-%d %H:%M:%S.

    Parameters:
        latitude [float]: the latitude of the given location.
        longitude [float]: the longitude of the given location.
        date [str]: the date to fetch the data for. Must be in the format YYYY-MM-DD.

        Returns:
            [tuple[np.ndarray, np.ndarray]]: the first array is time sequence fo the
                clear sky ghi this is datetime string in the format %Y-%m-%d %H:%M:%S.
                The second array is the clear sky GHI sequence.
    """
    # check the date is the correct format (YYYY-MM-DD).
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}$", date
    ), f"{date=} is not in the required format YYYY-MM-DD."

    timezone = TimezoneFinder().timezone_at(lng=longitude, lat=latitude)
    pvlib_location = pvlib.location.Location(
        latitude=latitude, longitude=longitude, tz=timezone
    )
    times = pd.date_range(
        start=f"{date} 00:00",
        end=f"{date} 23:59",
        freq=f"{int(60 * 0.5)}min",
        tz=timezone,
    )
    clear_sky = pvlib_location.get_clearsky(times)
    time_strings = [dt.strftime("%Y-%m-%d %H:%M:%S") for dt in times]
    return time_strings, clear_sky["ghi"]


def latest_available_ecmwf_ensemble_init_time(
    available_at: datetime.datetime | pd.Timestamp,
) -> pd.Timestamp:
    """Return the newest archived ECMWF ENS 00 UTC cycle usable at ``available_at``.

    The Dynamical 51-member historical archive used here contains the 00 UTC ENS
    initialization only. For a 24-hour rolling horizon, the cycle is treated as
    available once ECMWF's Day-1 batch has been disseminated, approximately
    6 hours 44 minutes after initialization.
    """
    timestamp = pd.Timestamp(available_at)
    if timestamp.tzinfo is None:
        raise ValueError("available_at must be timezone-aware.")

    available_utc = timestamp.tz_convert("UTC")
    candidate = available_utc.normalize()
    ready_time = candidate + ECMWF_24H_HORIZON_AVAILABILITY_DELAY
    if available_utc < ready_time:
        candidate -= pd.Timedelta(days=1)

    if candidate < ECMWF_ENSEMBLE_ARCHIVE_START_UTC:
        raise ValueError(
            "The archived 51-member ECMWF ENS dataset starts at "
            f"{ECMWF_ENSEMBLE_ARCHIVE_START_UTC.isoformat()}; requested usable "
            f"cycle would be {candidate.isoformat()}."
        )

    return candidate


## CLASSES #############################################################################
##### DATA FETCHING ####################################################################
class WeatherDataRetrieval:
    """ """

    def __init__(self, openmeteo_client: OpenMeteoClient | None = None) -> None:
        """ " """
        if openmeteo_client is None:
            openmeteo_client = connect_to_openmeteo_api()
        self._openmeteo_client = openmeteo_client

        return None

    def __get_openmeteo_api_weather_data(
        self,
        api_url: str,
        api_parameters: dict,
    ) -> tuple[pd.DataFrame, pd.Timedelta]:
        """Return source values indexed by their actual UTC API timestamps."""

        # Fetch the data from the open-meteo api (using the client).
        responses = self._openmeteo_client.weather_api(api_url, params=api_parameters)
        response = responses[0]

        # Extract the data from the api's response.
        weather_results = response.Hourly()
        interval = pd.Timedelta(seconds=weather_results.Interval())
        if interval <= pd.Timedelta(0):
            raise ValueError("Open-Meteo returned a non-positive time interval.")

        # Extract the features from the api's response.
        features_data = {
            feature: weather_results.Variables(i).ValuesAsNumpy()
            for i, feature in enumerate(api_parameters["hourly"])
        }

        sample_count = len(next(iter(features_data.values())))
        if any(len(values) != sample_count for values in features_data.values()):
            raise ValueError("Open-Meteo returned weather arrays of different lengths.")
        times = pd.date_range(
            start=pd.to_datetime(weather_results.Time(), unit="s", utc=True),
            periods=sample_count,
            freq=interval,
        )

        # Convert wind speed km/h to m/s if it is present in the features.
        if "wind_speed_10m" in features_data:
            features_data["wind_speed_10m"] = 0.277778 * features_data["wind_speed_10m"]

        return pd.DataFrame(features_data, index=times), interval

    def fetch_openmeteo_weather(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        location_name: str,
        latitude: float,
        longitude: float,
    ) -> list[OpenMeteoWeatherRow]:
        """ """

        # Convert start and end times to pandas Timestamps for easier manipulation.
        start = pd.Timestamp(start_time)
        end = pd.Timestamp(end_time)

        # Validate the inputs.
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start_time and end_time must be timezone-aware.")
        if end < start:
            raise ValueError("end_time must be on or after start_time.")

        # Convert start and end times to UTC.
        start = start.tz_convert("UTC")
        end = end.tz_convert("UTC")
        source_start = start.floor("10min")
        source_end = end.ceil("10min")

        # ----- General Weather Data (temp, wind_speed) -----
        # Define the parameters for the general open-meteo api request.
        general_params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": source_start.isoformat()[:10],
            "end_date": source_end.isoformat()[:10],
            "timezone": "UTC",
            "hourly": OPENMETEO_GENERAL_WEATHER_FEATURES,
        }

        general_data, _ = self.__get_openmeteo_api_weather_data(
            api_url=OPENMETEO_GENERAL_ARCHIVE_URL,
            api_parameters=general_params,
        )

        # ----- Satellite Weather Data (shortwave_radiation, diffuse_radiation, etc.) --
        # define the parameters for the open-meteo satellite api request.
        satellite_params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": source_start.isoformat()[:10],
            "end_date": source_end.isoformat()[:10],
            "timezone": "UTC",
            "hourly": OPENMETEO_SATELLITE_WEATHER_FEATURES,
            "models": "satellite_radiation_seamless",
            "temporal_resolution": "native",
        }

        satellite_data, satellite_interval = self.__get_openmeteo_api_weather_data(
            api_url=OPENMETEO_SATELLITE_RADIATION_ARCHIVE_URL,
            api_parameters=satellite_params,
        )

        if satellite_interval != pd.Timedelta(minutes=10):
            raise ValueError(
                "Realised PV requires 10-minute satellite radiation; "
                f"Open-Meteo returned {satellite_interval}."
            )

        # Radiation is a backward average at its timestamp. Keep that timestamp;
        # the PV service will align it with the two preceding 5-minute intervals.
        times = pd.date_range(source_start, source_end, freq=satellite_interval)
        radiation = satellite_data.reindex(times)
        # The satellite archive occasionally has short missing runs. Interpolate
        # only gaps bounded by observations; keep longer or edge gaps as errors.
        radiation = radiation.interpolate(
            method="time", limit=MAX_SATELLITE_GAP_SAMPLES, limit_area="inside"
        )
        if general_data.empty or (
            general_data.index.min() > source_start
            or general_data.index.max() < source_end
        ):
            raise RuntimeError("Open-Meteo did not cover the requested weather window.")
        general = (
            general_data.reindex(general_data.index.union(times))
            .interpolate(method="time")
            .reindex(times)
        )
        if radiation.isna().any().any() or general.isna().any().any():
            raise RuntimeError("Open-Meteo did not cover the requested weather window.")

        values = pd.concat(
            [
                radiation[
                    [
                        "shortwave_radiation",
                        "diffuse_radiation",
                        "direct_normal_irradiance",
                    ]
                ],
                general[["temperature_2m", "wind_speed_10m"]],
            ],
            axis=1,
        ).to_numpy(dtype=float)
        return [
            OpenMeteoWeatherRow(
                timestamp_utc=timestamp.to_pydatetime(),
                location_name=location_name,
                ghi=row[0],
                dhi=row[1],
                dni=row[2],
                air_temperature=row[3],
                wind_speed=row[4],
            )
            for timestamp, row in zip(times, values, strict=True)
        ]

    def __get_emcwf_day_forecast(
        self,
        init_time: pd.Timestamp,
        location_name: str,
        latitude: float,
        longitude: float,
    ) -> list[ECMWFWeatherRow]:
        """ """

        end_time = init_time + ECMWF_ROLLING_PV_HORIZON
        end = pd.Timestamp(end_time).tz_convert("UTC")

        # Generate the target times for the requested window with the specified
        # time step.
        target_times = pd.date_range(
            start=init_time,
            end=end,
            freq=pd.Timedelta(hours=0.5),
        )

        # Convert target times to UTC and calculate lead hours relative to the
        # initialization time.
        target_utc = target_times.tz_convert("UTC")
        target_lead_hours = (target_utc - init_time).total_seconds().to_numpy() / 3600.0

        # Validate that the requested forecast window does not begin before the ECMWF
        # initialization.
        if float(target_lead_hours.min()) < -1e-9:
            raise ValueError(
                "Requested forecast window begins before the ECMWF initialization."
            )

        # Determine the lead hours for the ECMWF ENS data to fetch, which are in 3-hour
        # blocks.
        first_lead = max(0, int(np.floor(float(target_lead_hours.min()) / 3.0) * 3))
        last_lead = int(np.floor(float(target_lead_hours.max()) / 3.0) * 3 + 3)
        lead_hours = list(range(first_lead, last_lead + 1, 3))
        target_lead_times = [datetime.timedelta(hours=h) for h in lead_hours]

        # Fetch the ECMWF ENS data for the specified initialization time, lead times,
        # and location.
        ds = dynamical_catalog.open("ecmwf-ifs-ens-forecast-15-day-0-25-degree")
        ds = ds[ECMWF_WEATHER_FEATURES]
        point_data = ds.sel(
            init_time=init_time.strftime(r"%Y-%m-%d %H:%M:%S"),
            lead_time=target_lead_times,
            latitude=latitude,
            longitude=longitude,
            method="nearest",
        ).load()

        # Convert the fetched data to a pandas DataFrame and calculate lead hours
        # relative to the initialization time.
        df = point_data.to_dataframe().reset_index()

        # Calculate lead hours relative to the initialization time for each row in the
        # DataFrame.
        valid_times_utc = pd.to_datetime(df["valid_time"], utc=True)

        # Calculate lead hours relative to the initialization time for each row in the
        # DataFrame.
        df["lead_hours"] = (valid_times_utc - init_time).dt.total_seconds() / 3600.0

        # Calculate wind speed from u and v components if available.
        if "wind_u_10m" in df.columns and "wind_v_10m" in df.columns:
            df["wind_speed_10m"] = np.sqrt(
                df["wind_u_10m"] ** 2 + df["wind_v_10m"] ** 2
            )
            df = df.drop(columns=["wind_u_10m", "wind_v_10m"])

        # Rename the columns to align with the WeatherSeries model.
        df = df.rename(columns=ECMWF_FEATURES_ALIGN_MAP)

        # Create a pvlib Location object for the specified latitude and longitude to
        # compute clear sky GHI.
        pvlib_location = pvlib.location.Location(
            latitude=latitude,
            longitude=longitude,
            tz="UTC",
        )

        # Compute the clear sky GHI for the target times using pvlib.
        clear_ghi = pvlib_location.get_clearsky(target_times)["ghi"].to_numpy(
            dtype=float
        )

        # process the data and save it to a single dictionary.
        ensemble_datas: list[dict[str, list]] = list()

        # Iterate over each ensemble member and reshape the GHI data to match the target
        # times (this is where we peform the needed interpolations).
        for ensemble_member in range(51):
            ensemble_data: dict[str, list[float]] = {
                "shortwave_radiation": [],
                "temperature_2m": [],
                "wind_speed_10m": [],
            }

            # Select the data for the current ensemble member and sort by lead hours.
            member = df[df["ensemble_member"] == ensemble_member].sort_values(
                "lead_hours"
            )

            # Check if the member data is empty and raise an error if it is.
            if member.empty:
                raise RuntimeError(
                    f"ECMWF ensemble member {ensemble_member} was not returned."
                )

            # Extract the measured lead hours, GHI, temperature, and wind speed for the
            # current ensemble member.
            measured_leads = member["lead_hours"].to_numpy(dtype=float)
            measured_ghi = member["shortwave_radiation"].to_numpy(dtype=float)
            measured_temperature = member["temperature_2m"].to_numpy(dtype=float)
            measured_wind = member["wind_speed_10m"].to_numpy(dtype=float)

            # Initialize an array to hold the reshaped GHI values for the target times,
            # filled with NaN.
            ghi_values = np.full(len(target_times), np.nan, dtype=np.float64)

            # Reshape the measured GHI values to match the target lead hours while
            # preserving the block mean.
            for block_end_hour, mean_ghi in zip(
                measured_leads, measured_ghi, strict=False
            ):
                # Determine the start hour of the current 3-hour block and create a mask
                # for the target lead hours that fall within this block.
                block_start_hour = float(block_end_hour) - 3.0
                block_mask = (target_lead_hours >= block_start_hour) & (
                    target_lead_hours < float(block_end_hour)
                )

                # If there are no target lead hours within this block, continue to the
                # next block.
                if not np.any(block_mask):
                    continue

                # Compute the clear sky GHI values for the target lead hours that fall
                # within this block and calculate the mean clear sky GHI for this block.
                block_clear_sky = clear_ghi[block_mask]
                clear_sky_mean = float(block_clear_sky.mean())
                if (
                    not np.isfinite(mean_ghi)
                    or float(mean_ghi) <= 0.0
                    or clear_sky_mean <= 1e-9
                ):
                    ghi_values[block_mask] = 0.0
                else:
                    ghi_values[block_mask] = (
                        float(mean_ghi) * block_clear_sky / clear_sky_mean
                    )

            # Check for any unresolved GHI values (NaN) and raise an error if any target
            # times with clear sky GHI greater than 1e-9 are unresolved.
            unresolved = np.isnan(ghi_values)
            if np.any(unresolved & (clear_ghi > 1e-9)):
                missing_times = list(target_times[unresolved & (clear_ghi > 1e-9)])
                raise RuntimeError(
                    f"ECMWF member {ensemble_member} cannot cover GHI timestamps: "
                    f"{missing_times}"
                )

            # For any still unresolved GHI values (NaN), set those GHI values to 0.0.
            ghi_values[unresolved] = 0.0

            # Interpolate the measured temperature and wind speed values to match the
            # target lead hours.
            temperature_values = np.interp(
                target_lead_hours, measured_leads, measured_temperature
            )
            wind_values = np.interp(target_lead_hours, measured_leads, measured_wind)

            # Append the reshaped GHI, interpolated temperature, and interpolated wind
            ensemble_data["shortwave_radiation"].extend(ghi_values)
            ensemble_data["temperature_2m"].extend(temperature_values)
            ensemble_data["wind_speed_10m"].extend(wind_values)

            ensemble_datas.append(ensemble_data)

        # average the predictions for all ensmbles.
        # because we are no longer doing any of the clustering.
        ensemble_shortwave_radiations = np.array(
            [ensemble_data["shortwave_radiation"] for ensemble_data in ensemble_datas]
        )
        avg_shortwave_radiation = np.mean(ensemble_shortwave_radiations, axis=0)

        ensemble_temperatures = np.array(
            [ensemble_data["temperature_2m"] for ensemble_data in ensemble_datas]
        )
        avg_temperature = np.mean(ensemble_temperatures, axis=0)

        ensemble_wind_speed = np.array(
            [ensemble_data["wind_speed_10m"] for ensemble_data in ensemble_datas]
        )
        avg_wind_speed = np.mean(ensemble_wind_speed, axis=0)

        avg_ensemble_data = {
            "shortwave_radiation": avg_shortwave_radiation,
            "temperature_2m": avg_temperature,
            "wind_speed_10m": avg_wind_speed,
        }

        # now format the data into a list of OpenMeteoWeatherRow's.
        weather_rows: list[ECMWFWeatherRow] = list()

        for i in range(len(target_times)):
            weather_rows.append(
                ECMWFWeatherRow(
                    timestamp_utc=target_times[i],
                    available_at_utc=init_time,
                    location_name=location_name,
                    ghi=avg_ensemble_data["shortwave_radiation"][i],
                    air_temperature=avg_ensemble_data["temperature_2m"][i],
                    wind_speed=avg_ensemble_data["wind_speed_10m"][i],
                )
            )

        return weather_rows

    def fetch_ecmwf_weather(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        location_name: str,
        latitude: float,
        longitude: float,
    ) -> list[ECMWFWeatherRow]:
        """ """
        # because we want the previous days forecast we must decreaset the start time by
        # a day.
        start_time = start_time - datetime.timedelta(hours=24)

        # Convert start and end times to pandas Timestamps for easier manipulation.
        start = pd.Timestamp(start_time)
        end = pd.Timestamp(end_time)

        # Validate the inputs.
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start_time and end_time must be timezone-aware.")
        if end < start:
            raise ValueError("end_time must be on or after start_time.")

        # Convert start and end times to UTC.
        start = pd.Timestamp(start_time).tz_convert("UTC")
        end = pd.Timestamp(end_time).tz_convert("UTC")

        weather_rows: list[ECMWFWeatherRow] = list()
        curr_init_time = latest_available_ecmwf_ensemble_init_time(available_at=start)

        # Validate that the requested cycle is the 00 UTC archived cycle and is not
        # before the archive start.
        if curr_init_time != curr_init_time.normalize():
            raise ValueError(
                "The historical Dynamical ECMWF ENS archive contains"
                + "00 UTC cycles only."
            )

        # Validate that the requested cycle is not before the archive start.
        if curr_init_time < ECMWF_ENSEMBLE_ARCHIVE_START_UTC:
            raise ValueError(
                "ECMWF ENS archive begins at "
                f"{ECMWF_ENSEMBLE_ARCHIVE_START_UTC.isoformat()}."
            )

        # Generate the target times for the requested window with the specified
        # time step.
        target_times = pd.date_range(
            start=start,
            end=end,
            freq=pd.Timedelta(hours=0.5),
        )

        # Validate that the target times are within the forecast window of the requested
        # cycle.
        if len(target_times) == 0:
            raise ValueError("Requested ECMWF forecast window contains no timestamps.")

        # Convert target times to UTC and calculate lead hours relative to the
        # initialization time.
        targets_utc = target_times.tz_convert("UTC")

        # get the oldest 24hr forecast.
        current_init_time_weather_rows = self.__get_emcwf_day_forecast(
            curr_init_time,
            location_name,
            latitude,
            longitude,
        )
        # save this forecast.
        weather_rows.extend(current_init_time_weather_rows)

        for target_utc in targets_utc:
            latest_init_time = latest_available_ecmwf_ensemble_init_time(target_utc)
            if curr_init_time != latest_init_time:
                # then there is a more available forecast for this current time.
                # so me must fetch the more recent 24hr forecast.
                curr_init_time = latest_init_time
                current_init_time_weather_rows = self.__get_emcwf_day_forecast(
                    curr_init_time,
                    location_name,
                    latitude,
                    longitude,
                )
                weather_rows.extend(current_init_time_weather_rows)

        return weather_rows

    def fetch_ecmwf_cycles(
        self,
        cycles: Iterable[datetime.datetime],
        location_name: str,
        latitude: float,
        longitude: float,
    ) -> list[ECMWFWeatherRow]:
        """Fetch only the requested archived forecast cycles."""

        weather_rows: list[ECMWFWeatherRow] = []
        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(
                    self.__get_emcwf_day_forecast,
                    # arguments.
                    pd.Timestamp(cycle).tz_convert("UTC"),
                    location_name,
                    latitude,
                    longitude,
                )
                for cycle in sorted(set(cycles))
            ]

            for future in as_completed(futures):
                weather_rows.extend(future.result())

        return weather_rows
