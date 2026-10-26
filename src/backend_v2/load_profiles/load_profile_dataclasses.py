"""
This scripts contains all of the dataclasses needed for the load profile scripts. It
also contains some external functions which are used to get or control some of the
dataclasses.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.neighbors import KernelDensity  # type: ignore[import-untyped]

import backend_v2.load_profiles.load_profile_helper_functions as helper_functions
import backend_v2.load_profiles.load_profile_inputs as inputs
from database_v2.models.data_models import (
    ForecastDemandRow,
    RealisedDemandRow,
)


#### DATACLASSES #######################################################################
@dataclass
class LoadProfile:
    """
    Contains all of the data for a single laod profile (id, time, load).
    """

    id: int  # unique identifier for the house or customer.
    time: NDArray[np.str_]  # series of times in the format "%Y-%m-%d %H:%M:%S".
    load: NDArray[
        np.float64
    ]  # series of demand (KWH) corresponding to the time series.

    def __post_init__(self) -> None:
        """
        Performs some validation checks on the inputs. To ensure all inputs are in the
        correct format.
        """
        # check that the time series corresponds to the correct load series.
        assert len(self.time) == len(self.load)
        assert len(self.time) == 48

        # check to make sure all datetimes are in the correct format.
        time_pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        assert all(re.match(time_pattern, t) for t in self.time)

        # check that all dates are the same.
        # i.e. the load profiles is over a single day.
        dates = [helper_functions.time2dt(t).date() for t in self.time]
        date = helper_functions.time2dt(self.time[0]).date()
        assert all(d == date for d in dates)

        return None

    @property
    def date(self) -> str:
        """the date of the load profile."""
        return str(helper_functions.time2dt(self.time[0]).date())

    @property
    def season(self) -> int:
        """the season of the load profile."""
        return helper_functions.get_season(self.time[0])

    @property
    def hours(self) -> NDArray[np.float64]:
        """the hours offset of the load profile from the starting time."""
        start_of_day = datetime.strptime(self.date, r"%Y-%m-%d")
        time_diffs = [helper_functions.time2dt(t) - start_of_day for t in self.time]
        hours = np.array([diff.total_seconds() / 3600 for diff in time_diffs])
        return hours


@dataclass(frozen=True)
class ClusterData:
    """
    Contains all of the results for a single cluster in a season.
    i.e. keeps track of what customers and dates where included in any given cluster.
    """

    season: int  # the season of the cluster.
    cluster: int  # the unqiue index of the cluster (realative to the season).
    # a list of customers and dates which have been included in the cluster.
    customer_dates: list[tuple[int, str]]


class TimeKernel:
    """
    A single epenchnikov kernel function for a given time in a given cluster.
    """

    def __init__(self, loads: NDArray[np.float64], grid_size: int = 201) -> None:
        """
        Initialise the TimeKernel.

        Parameters:
            - loads [NDArray[np.float64]]: a list of load readings for the given time in
                the cluster.
            - grid_size [int]: the resolution of the cdf.
        """
        h = helper_functions.compute_epenchnikov_bandwith(loads)

        kde = KernelDensity(kernel="epanechnikov", bandwidth=h)
        kde.fit(loads.reshape(-1, 1))

        low = np.min(loads) - h
        high = np.max(loads) + h
        if high <= low:
            high = low + 1e-3

        self._grid = np.linspace(low, high, grid_size)

        pdf = np.exp(kde.score_samples(self._grid.reshape(-1, 1)))
        cdf = np.concatenate(
            [[0.0], np.cumsum(0.5 * (pdf[1:] + pdf[:-1]) * np.diff(self._grid))]
        )
        cdf /= cdf[-1]
        self._cdf = cdf

        return None

    def percentile(self, q: float) -> float:
        """Returns the q quartile load reading for the given time."""
        percentile = float(np.interp(q, self._cdf, self._grid))
        return max(0, percentile)

    def sample(self) -> float:
        """Returns a randomly sampled load reading for the given time."""
        u = random.uniform(0.0, 1.0)
        sample = float(np.interp(u, self._cdf, self._grid))
        return max(0, sample)

    @cached_property
    def mean(self) -> float:
        """Expected load of the non-negative samples produced by sample()."""
        positive = self._grid > 0
        if not np.any(positive):
            return 0.0
        cdf_at_zero = np.interp(0.0, self._grid, self._cdf)
        positive_grid = np.concatenate(([0.0], self._grid[positive]))
        positive_cdf = np.concatenate(([cdf_at_zero], self._cdf[positive]))
        return float(np.trapezoid(positive_grid, positive_cdf))

    def get_data(self) -> NDArray[np.float64]:
        """ """
        data = np.array([self._grid, self._cdf])
        return data

    @classmethod
    def load_from_data(cls, data: NDArray[np.float64]) -> TimeKernel:
        """ """
        instance = cls.__new__(cls)
        instance._grid = data[0]
        instance._cdf = data[1]

        return instance


@dataclass(frozen=True)
class ClusterKernels:
    """
    A cluster kernel is a series of time kernel distributions for a given cluster and
    season. This is what we use to sample synthetic load profiles from in a given
    season and cluster.
    """

    id: int  # the unique id of the cluster (forms a primary key with season).
    season: int  # the season of the cluster (forms a primary key with id).
    # the list of time kernel distributions we sample from to get the synthetic
    # profiles.
    time_kernels: list[TimeKernel]
    # a map from the variables in the meta-data that we split on to get this cluster
    # to the value of that variable in this cluster.
    variables_map: dict[str, bool]

    def __len__(self) -> int:
        """
        returns the number of kernels in the cluster (i.e. the number of time steps
        in a single day).
        """
        return len(self.time_kernels)

    def sample_load(self) -> NDArray[np.float64]:
        """
        Get a randomly sampled load readings from all time kernels.
        """
        load = np.array([kernel.sample() for kernel in self.time_kernels])
        return load

    def sample_load_quartile(self, q: float) -> NDArray[np.float64]:
        """
        Same as ClusterKernels.sample_load but instead for randomly sampling from each
        time kernel we take the given quartile q reading from the underlying
        distribution.
        """
        load = np.array([kernel.percentile(q) for kernel in self.time_kernels])
        return load

    def mean_load(self) -> NDArray[np.float64]:
        """Expected load for each half-hour kernel."""
        return np.array([kernel.mean for kernel in self.time_kernels])

    def get_data(self) -> dict[str, Any]:
        """
        Return the data of the ClusterKernels instance as a dictionary (for saving
        purposes).
        """
        time_kernels_data = np.array(
            [kernel.get_data() for kernel in self.time_kernels]
        )

        data = {
            "id": self.id,
            "season": self.season,
            "time_kernels": time_kernels_data,
            "variables_map": self.variables_map,
        }

        return data

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> ClusterKernels:
        """
        Create an instance of ClusterKernels from the given data.

        Parameters:
            - data [dict[str: Any]]: a the data from a previous instance of
                ClusterKernels whose data we want to make into a new instance.

        Returns:
            - [ClusterKernels]: an instance of the with the given data.
        """
        # unpack the data.
        assert "id" in data.keys()
        id = data["id"]

        assert "season" in data.keys()
        season = data["season"]

        assert "time_kernels" in data.keys()
        time_kernels_data = data["time_kernels"]
        time_kernels: list[TimeKernel] = list()
        for time_kernel_data in time_kernels_data:
            time_kernel = TimeKernel.load_from_data(time_kernel_data)
            time_kernels.append(time_kernel)

        assert "variables_map" in data.keys()
        variables_map = data["variables_map"]

        return ClusterKernels(id, season, time_kernels, variables_map)


class LoadProfileGenerator:
    """ """

    def __init__(
        self,
        cluster_kernels_data_filepath: str = inputs.CLUSTER_KERNELS_DATA_FILEPATH,
        id: int = 0,
    ) -> None:
        """ """
        self.id = id

        self._cluster_kernels_data = np.load(
            cluster_kernels_data_filepath, allow_pickle=True
        )
        self._n_cluster_kernels = len(self._cluster_kernels_data["id"])
        self._cluster_kernels_cache: dict[
            tuple[int, tuple[tuple[str, bool], ...]], ClusterKernels
        ] = {}

        assert self._n_cluster_kernels >= 0

        # get the split variables and all possible options (combinations).
        self.split_variables = list(
            self._cluster_kernels_data["variables_map"][0].keys()
        )
        self.split_variables_options: set[tuple[bool]] = set()
        for i in range(self._n_cluster_kernels):
            values = tuple(self._cluster_kernels_data["variables_map"][i].values())
            self.split_variables_options.add(values)

        return None

    def __find_cluster_kernel(self, season: int, **kwargs) -> ClusterKernels:
        """ """
        cache_key = (season, tuple(sorted(kwargs.items())))
        if cache_key in self._cluster_kernels_cache:
            return self._cluster_kernels_cache[cache_key]

        found_cluster = False
        for i in range(self._n_cluster_kernels):
            if (
                self._cluster_kernels_data["season"][i] == season
                and self._cluster_kernels_data["variables_map"][i] == kwargs
            ):
                found_cluster = True
                break

        if not found_cluster:
            raise ValueError(
                f"Unable to find a cluster with season={season} and "
                + f"variables={kwargs}."
            )

        cluster_kernels_data = {
            key: values[i] for key, values in self._cluster_kernels_data.items()
        }

        cluster_kernel = ClusterKernels.from_data(cluster_kernels_data)
        self._cluster_kernels_cache[cache_key] = cluster_kernel

        return cluster_kernel

    def get_sample_load_profile(
        self, start_time: datetime | None = None, **kwargs
    ) -> LoadProfile:
        """ """
        time_series = helper_functions.get_day_time_series(start_time)
        season = helper_functions.get_season(time_series[0])
        cluster_kernel = self.__find_cluster_kernel(season, **kwargs)
        load = cluster_kernel.sample_load()
        return LoadProfile(self.id, time_series, load)

    def get_sample_load_quartile(
        self, q: float, start_time: datetime | None = None, **kwargs
    ) -> LoadProfile:
        """ """
        time_series = helper_functions.get_day_time_series(start_time)
        season = helper_functions.get_season(time_series[0])
        cluster_kernel = self.__find_cluster_kernel(season, **kwargs)
        load = cluster_kernel.sample_load_quartile(q)
        return LoadProfile(self.id, time_series, load)

    def get_mean_load_profile(
        self, start_time: datetime | None = None, **kwargs
    ) -> LoadProfile:
        """Return the expected profile of the sampled household load."""
        time_series = helper_functions.get_day_time_series(start_time)
        season = helper_functions.get_season(time_series[0])
        cluster_kernel = self.__find_cluster_kernel(season, **kwargs)
        return LoadProfile(self.id, time_series, cluster_kernel.mean_load())

    def get_realised_load_profile(
        self,
        house_config_id: str,
        start_time: datetime | None = None,
        n: int = 1,
        **kwargs,
    ) -> list[RealisedDemandRow]:
        """
        Gets a realised load profile as a list of RealisedDemandRow's.

        Parameters:
            - house_config_id [str]: the id to the house_id_configuration.
            - start_time [datetime | None]: the start time for for which we will fetch
                the next 24hr load profile for. If None then the current date is used.
            - n [int]: the number of houses with the house_config_id. We will generate
                n random load profiles then the average of all those load profiles will
                be returned.
            - kwargs: these must specify the value of all the configurable attributes in
                the house config which influence the load profiles. There fields are
                typically; 'has_aircon', 'has_poolpump', 'has_ev', ... ect.

        Returns:
            - [list[RealisedDemandRow]]: the list of the realised demand rows which
                contain the sampled load profile for the 24hr period.
        """
        if "has_ev" in kwargs.keys():
            has_ev = kwargs.pop("has_ev")
        else:
            has_ev = False  # by default we assume there is no EV.

        # get the average of n randomly sampled load profiles.
        loads = np.zeros((n, 48))
        for i in range(n):
            load_profile = self.get_sample_load_profile(start_time, **kwargs)
            loads[i] = load_profile.load
        time = (
            load_profile.time
        )  # they should all be the same so just take the last one
        avg_load = np.mean(loads, axis=0)

        if has_ev:
            helper_functions.add_ev_charging_to_load(avg_load, n)

        thirty_min_time = pd.date_range(
            start=str(time[0]),
            end=str(time[-1]),
            freq="30min",
        )
        thirty_min_hours_since = (
            thirty_min_time - pd.to_datetime(time[0])
        ) / pd.Timedelta(hours=1)

        # now we need to upsample the load profile so that it goes from 30min intervals
        # to 5min intervals.
        start_time_str = str(time[0])
        end_time = datetime.strptime(time[-1], r"%Y-%m-%d %H:%M:%S")
        end_time += timedelta(minutes=30)
        end_time_str = end_time.strftime(r"%Y-%m-%d %H:%M:%S")
        five_min_time = pd.date_range(
            start=start_time_str,
            end=end_time_str,
            freq="5min",
            inclusive="left",
        )
        five_min_hours_since = (
            five_min_time - pd.to_datetime(start_time_str)
        ) / pd.Timedelta(hours=1)

        upsampled_load = np.array(
            np.interp(five_min_hours_since, thirty_min_hours_since, avg_load)
        )

        rows: list[RealisedDemandRow] = list()
        for i, timestamp_pd in enumerate(five_min_time):
            row = RealisedDemandRow(
                timestamp_utc=timestamp_pd.to_pydatetime().replace(tzinfo=UTC),
                house_config_id=house_config_id,
                demand_kw=upsampled_load[i] * 2,  # source readings are 30min kWh.
            )
            rows.append(row)
        return rows

    def get_forecasted_load_profile(
        self, house_config_id: str, start_time: datetime | None = None, **kwargs
    ) -> list[ForecastDemandRow]:
        """ """
        if "has_ev" in kwargs.keys():
            has_ev = kwargs.pop("has_ev")
        else:
            has_ev = False  # by default we assume there is no EV.

        if start_time is None:
            start_time = datetime.now(UTC)
        elif start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        else:
            start_time = start_time.astimezone(UTC)
        start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        # Means add across houses, while medians understate a skewed aggregate.
        load_profile = self.get_mean_load_profile(start_time, **kwargs)

        if has_ev:
            helper_functions.add_forecasted_ev_charging_to_load(load_profile.load)

        rows: list[ForecastDemandRow] = list()
        for i, time in enumerate(load_profile.time):
            row = ForecastDemandRow(
                run_time_utc=start_time,
                target_time_utc=helper_functions.time2dt(time).replace(tzinfo=UTC),
                house_config_id=house_config_id,
                demand_kw=load_profile.load[i] * 2,  # convert 30min kWh to kW.
            )
            rows.append(row)
        return rows


#### DATACLASS FUNCTIONS ###############################################################


def save_cluster_kernels(
    cluster_kernels: list[ClusterKernels], out_filepath: str
) -> None:
    """
    Saves the list of cluster kernels to a single .npz file which contains all of their
    data.

    Parameters:
        - cluster_kernels [list[ClusterKernels]]: the list of cluster kernels whose data
            will be saved to the .npz file.
        - out_filepath [str]: the output filepath where the .npz file will be saved to.
            note this filepath must endwith '.npz'.
    """
    assert len(cluster_kernels) > 0
    assert out_filepath.endswith(".npz")

    cluster_kernels_datas = [ck.get_data() for ck in cluster_kernels]

    keys = list(cluster_kernels_datas[0].keys())
    combined_data: dict[str, list[Any]] = {key: list() for key in keys}

    for key in keys:
        for cluster_kernel_data in cluster_kernels_datas:
            combined_data[key].append(cluster_kernel_data[key])

    combined_data_numpy: dict[str, NDArray] = {
        key: np.array(values) for key, values in combined_data.items()
    }

    np.savez(out_filepath, **combined_data_numpy)  # type: ignore[arg-type]

    return None
