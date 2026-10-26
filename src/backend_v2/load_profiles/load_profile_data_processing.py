"""
This script contains all of the function used to parse and read in the smart-grid
smart-city dataset used to make the synthetic load profiles.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
import pyarrow  # type: ignore[import-untyped]
import pyarrow.dataset as ds  # type: ignore[import-untyped]
from numpy.typing import NDArray
from tqdm import tqdm  # type: ignore[import-untyped]

import backend_v2.load_profiles.load_profile_helper_functions as helper_functions
import backend_v2.load_profiles.load_profile_inputs as inputs
from backend_v2.load_profiles.load_profile_dataclasses import (
    LoadProfile,
)


def convert_csv_dataset_to_partitioned_parquet(
    dataset_filepath: str,
    outdir: str,
) -> None:
    """
    Converts the goverment provided .csv readings file for the smart-grid smart-city
    dataset to a partitioned parquet dataset which is partitioned by by each cusomter.
    This allows the size of the dataset to be reduced as now we only include the
    id, timestamp and load (we can drop all other un-used columns).
    This dataset also allows for faster query speed because we can quickly fetch the
    the data for a single query.

    Parameters:
        - dataset_filepath [str]: the filepath to the readings .csv in the original
            dataset.
        - outdir [str]: the output direcotory where the partitioned parquet dataset will
            be saved.
    """

    # datasets constants.
    dataset_cols = ["id", "READING_DATETIME", " GENERAL_SUPPLY_KWH"]
    parquet_cols = ["id", "time", "load"]

    total_bytes = os.path.getsize(dataset_filepath)
    reader = pd.read_csv(dataset_filepath, chunksize=1_000_000, usecols=dataset_cols)
    with tqdm(
        total=total_bytes,
        unit="B",
        unit_scale=True,
        desc="Saving Chunks of Readings Dataset",
    ) as pbar:
        for i, chunk in enumerate(reader):
            chunk = chunk.rename(
                columns={
                    dataset_cols[i]: parquet_cols[i] for i in range(len(dataset_cols))
                }
            )
            chunk = chunk[parquet_cols]
            table = pyarrow.Table.from_pandas(chunk, preserve_index=False)

            ds.write_dataset(
                table,
                outdir,
                format="parquet",
                partitioning=["id"],
                partitioning_flavor="hive",
                existing_data_behavior="overwrite_or_ignore",
                basename_template=f"chunk{i}-{{i}}.parquet",
            )

            chunk_bytes = chunk.memory_usage(deep=False).sum()
            pbar.update(chunk_bytes)

    return None


def get_customers(dataset_dir: str) -> Iterable[int]:
    """
    Gets the list of all customers found in the parquet dataset.

    Parameters:
        - dataset_dir [str]: the filepath to the parquet dataset.

    Returns:
        - [Iterable[int]]: the list of all customers in the parquet dataset.
    """
    customer_folders = os.listdir(dataset_dir)
    ids = [
        int(folder.replace("id=", ""))
        for folder in customer_folders
        if folder.replace("id=", "").isdecimal()
    ]
    return ids


def filter_out_non_retail_customers(customers: Iterable[int]) -> list[int]:
    """
    Using the household data in the meta-data for the smart-grid smart-city dataset
    filter out all of hte customers who are not retail cusomters (that means they are
    likely the network or a control).

    Paramters:
        - customers [Iterable[int]]: the list of customers to be filtered.

    Returns:
        - [list[int]]: the filtered list of customers.
    """
    household_df = pd.read_csv(inputs.HOUSEHOLD_DATAPATH)
    household_df = household_df[
        household_df["CUSTOMER_KEY"].isin(customers)
        & (household_df["TRIAL_CUSTOMER_TYPE"] == "Retail")
    ]
    filtered_customers = household_df["CUSTOMER_KEY"].tolist()
    return filtered_customers


def get_variable_combinations(columns_to_split_on: list[Any]) -> list[tuple[Any]]:
    """ """
    household_df = pd.read_csv(inputs.HOUSEHOLD_DATAPATH)

    # remove columns which have nan for all columns_to_split_on
    household_df = household_df.dropna(subset=columns_to_split_on)

    # ensure that the inputted columns all appear in the meta-data.
    assert all(col in household_df.columns for col in columns_to_split_on)

    col_options = [tuple(household_df[col].unique()) for col in columns_to_split_on]

    # TODO: check to make sure this is doing what I want.
    combinations = list(itertools.product(*col_options))
    return combinations


def get_customer_group(
    base_customers: list[int],
    columns_to_split_on: list[str],
    column_values: tuple[Any],
    num_customers: int,
) -> list[int]:
    """ """
    household_df = pd.read_csv(inputs.HOUSEHOLD_DATAPATH)

    # ensure that the inputted columns all appear in the meta-data.
    assert all(col in household_df.columns for col in columns_to_split_on)

    customer_group: list[int] = list()
    for customer in base_customers:
        customer_df = household_df[household_df["CUSTOMER_KEY"] == customer]
        assert len(customer_df) == 1  # the customer key in unique.
        if all(
            customer_df[col].iloc[0] == column_values[i]
            for i, col in enumerate(columns_to_split_on)
        ):
            # then the customer is in the desired group.
            customer_group.append(customer)
            if len(customer_group) == num_customers:
                break

    # ensure that we have found the desired number of customers.
    assert len(customer_group) == num_customers

    return customer_group


def get_customer_data(
    dataset_dir: str, id: int
) -> tuple[NDArray[np.str_], NDArray[np.float64]]:
    """
    Gets the time and laod series for a given customer from the parquet dataset.

    Parameters:
        - dataset_dir [str]: the filepath to the parquet dataset.
        - id [int]: the id of the customer to fetch the data from.

    Returns:
        - [tuple[NDArray[np.str_], NDArray[np.float64]]]: the first element is the time
            series (all timestamps) in the dataset for the given customer. the second
            element is all load readings in the dataset for the given customer.
    """
    customer_dir = os.path.join(dataset_dir, f"id={id}")
    assert os.path.exists(customer_dir)
    df = pd.read_parquet(customer_dir, columns=["time", "load"])
    time = df["time"].to_numpy().astype(str)
    load = df["load"].to_numpy()

    return time, load


def get_customer_load_profiles(
    dataset_dir: str, id: int, season: int | None = None
) -> list[LoadProfile]:
    """
    Get all of the load profiles for a given customer and season (if specified) from
    the parquet dataset.

    Parameters:
        - dataset_dir [str]: the filepath to the parquet dataset.
        - id [int]: the id of the customer to fetch the data from.
        - season [int | None]: the index to the season we wish to get the load porifles
            for. if None then then the load profiles are fetched for all seasons.

    Returns:
        - [list[LoadProfile]]: a list of load profiles for the given customer and
            season.
    """

    # find and fetch the .parquet data from the dataset directory.
    time, load = get_customer_data(dataset_dir, id)

    # now split the time time_readings and load_readings into days.
    start_dt = helper_functions.time2dt(time[0])
    end_dt = helper_functions.time2dt(time[len(time) - 1])

    load_profiles: list[LoadProfile] = list()
    for d in range((end_dt - start_dt).days + 1):
        date = str((start_dt + timedelta(days=d)).date())

        # filter out the wanted seasons (if specified.)
        if season is not None and helper_functions.get_season(date) != season:
            continue

        day_mask = np.strings.startswith(time, date)
        try:
            lp = LoadProfile(id, time[day_mask], load[day_mask])
        except AssertionError:
            # likely the day did not cover the full 24hr time period or some other
            # error was found in the data for this day. So the load profile is invalid
            # and will be skipped over.
            continue
        load_profiles.append(lp)

    return load_profiles


def get_customer_dates_load_profiles(
    dataset_dir: str, customer_dates: list[tuple[int, str]]
) -> list[LoadProfile]:
    """
    Gets the load profiles from the parquet dataset for the given customer date
    pairings.

    Parameters:
        - dataset_dir [str]: the filepath to the parquet dataset.
        - customer_dates [list[tuple[int, str]]]: a list of (id, date) pairs
            for which we need to get a load profile corresponding to each pair.

    Returns:
        - [list[LoadProfile]]: a list of laod profiles corresponding to the customer
            date paris.
    """
    customers = set(c for c, _ in customer_dates)

    load_profiles: list[LoadProfile] = list()
    for customer in customers:
        # get the list of dates corresponding to the customer.
        dates = set(d for c, d in customer_dates if c == customer)
        time, load = get_customer_data(dataset_dir, customer)

        for date in dates:
            date_mask = np.strings.startswith(time, date)
            lp = LoadProfile(customer, time[date_mask], load[date_mask])
            load_profiles.append(lp)

    return load_profiles
