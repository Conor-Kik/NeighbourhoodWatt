"""
This file contains helper functions which are being used in the load profile scripts.
"""

import random
import re
from datetime import datetime, time, timedelta

import numpy as np
from numpy.typing import NDArray


def time2dt(time: str) -> datetime:
    """convert a time string to a datetime object."""
    return datetime.strptime(time, r"%Y-%m-%d %H:%M:%S")


def date2dt(date: str) -> datetime:
    """convert a date string to a datetime object."""
    return datetime.strptime(date, r"%Y-%m-%d")


def get_season(time: str) -> int:
    """
    Returns the season index from a given time or date string.
    Assumes the season is for the southern-hemisphere.
    """
    # check if the input is a date or time string and get the month.
    time_pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
    if re.match(time_pattern, time):
        month = time2dt(time).month
    date_pattern = r"^\d{4}-\d{2}-\d{2}$"
    if re.match(date_pattern, time):
        month = date2dt(time).month

    if month in [12, 1, 2]:
        return 0  # summer (in southern-hemisphere).
    if month in [3, 4, 5]:
        return 1  # autumn (in southern-hemisphere).
    if month in [6, 7, 8]:
        return 2  # winter (in southern-hemisphere).
    else:
        return 3  # spring (in southern-hemisphere).


def get_day_time_series(
    start_time: datetime | None = None, delta_t_min: float = 30
) -> NDArray[np.str_]:
    """
    From a give start_time gets a full day time series from that starting day.

    Parameters:
        start_time [str]: the start time or just any time in the desired date.
        delta_t_min [float]: the time step in minutes between each element in
            the final series.
    """
    if start_time is None:
        start_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # ensure that the start time is the start of a day.
    start_time = datetime.combine(start_time.date(), time.min)

    end_time = start_time + timedelta(days=1)

    current_time = start_time

    time_series: list[str] = []
    while current_time < end_time:
        time_series.append(current_time.strftime(r"%Y-%m-%d %H:%M:%S"))
        current_time += timedelta(minutes=delta_t_min)

    return np.array(time_series)


def get_dummy_time_series(season: int | None = None) -> NDArray[np.str_]:
    """
    Get a dummy time series over a 24hr period with 30min intervals. This is primarily
    used for plotting.

    Parameters:
        - season [int | None]: the season the dates must fall in. If None the current
            date/season will be used.

    Returns:
        - [NDArray[np.str_]]: an array of 30min intervals over an entire day in the
            format "%Y-%m-%d %H:%M:%S".
    """
    assert season is None or (0 <= season < 4)
    if season is None:
        current_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        if season == 0:
            month = 12
        if season == 1:
            month = 3
        if season == 2:
            month = 6
        if season == 3:
            month = 9

        year = datetime.now().year
        current_time = datetime(year=year, month=month, day=1)

    end_time = current_time + timedelta(days=1)

    time_series: list[str] = []
    while current_time < end_time:
        time_series.append(current_time.strftime(r"%Y-%m-%d %H:%M:%S"))
        current_time += timedelta(minutes=30)

    return np.array(time_series)


def compute_epenchnikov_bandwith(x: NDArray[np.float64]) -> float:
    """
    Computes the epenchikov bandwidth parameter from the data.
    Note this uses the defaulted MATLAB method to try to replicate the paper as the
    exact bandwidth was not specified.
    """
    n = len(x)
    if n < 2:
        return 1e-3
    sigma = np.std(x)
    iqr = np.subtract(*np.percentile(x, [75, 25]))
    spread = min(sigma, iqr / 1.34) if iqr > 0 else sigma
    spread = max(spread, 1e-6)
    h = 2.34 * spread * n ** (-1.0 / 5.0)
    return max(h, 1e-3)


def add_ev_charging_to_load(load: NDArray[np.float64], n_samples: int = 1) -> None:
    """
    Adds an EV charging peak to a 24hr load profile.
    """
    assert len(load) == 48  # assume we have a single day at 30min intervals.

    # EV constants.
    capacity = 60  # assume we have an average 60kWh battery capacity in our EV.
    charge_rate = 7.4  # assume a standard 7.4kW charger.
    target_SoC = 1  # assume we want to charge the EV to 100%.

    for _ in range(n_samples):
        # randomly get the start time.
        # we just assume a uniform distribution between [0, 4].
        start_idx = random.randint(0, 8)  # note this is 2 * the start time.

        # randomly get the starting charge of the EV.
        # we just assume a beta distribution with; alpha=5.5 and beta=2.5.
        SoC = np.random.beta(5.5, 2.5)

        energy_needed = (target_SoC - SoC) * capacity

        i = start_idx

        while energy_needed >= 1e-9:
            energy = min(charge_rate * 0.5, energy_needed)
            load[i] += energy / n_samples
            energy_needed -= energy
            i += 1

    return None


def add_forecasted_ev_charging_to_load(load: NDArray[np.float64]) -> None:
    """
    Using the same assumptions as the above function 'add_ev_charging_to_load' add the
    expected ev charging profile to the load.
    """

    assert len(load) == 48  # assume we have a single day at 30min intervals.

    # EV constants.
    capacity = 60  # assume we have an average 60kWh battery capacity in our EV.
    charge_rate = 7.4  # assume a standard 7.4kW charger.
    target_SoC = 1  # assume we want to charge the EV to 100%.

    # randomly get the start time.
    # we just assume a uniform distribution between [0, 4].
    start_idx = 4  # the average start time is 2am which is index 4 (30min freq).

    # randomly get the starting charge of the EV.
    # we just assume a beta distribution with; alpha=5.5 and beta=2.5.
    SoC = 0.6875  # the average of this distribution is 0.6875.

    energy_needed = (target_SoC - SoC) * capacity

    i = start_idx

    while energy_needed >= 1e-9:
        energy = min(charge_rate * 0.5, energy_needed)
        load[i] += energy
        energy_needed -= energy
        i += 1

    return None
