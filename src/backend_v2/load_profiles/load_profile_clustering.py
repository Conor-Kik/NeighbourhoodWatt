"""
This script contains all functions used to cluster load profiles (from the dataset) to
generate the distrubutions.
"""

from __future__ import annotations

from collections.abc import Iterable

import matplotlib
import numpy as np
from sklearn.cluster import KMeans  # type: ignore[import-untyped]
from tqdm import tqdm  # type: ignore[import-untyped]

matplotlib.use("Agg")

import backend_v2.load_profiles.load_profile_data_processing as data_processing
import backend_v2.load_profiles.load_profile_inputs as inputs
from backend_v2.load_profiles.load_profile_dataclasses import (
    ClusterData,
    ClusterKernels,
    LoadProfile,
    TimeKernel,
)


def get_attributes(
    lp: LoadProfile, boundaries: list[tuple[float, float]]
) -> list[float]:
    """
    Gets the attributes used for clustering for the given load profile.
    The attributes used for clustering is the sum of load readings over the given
    boundaries.

    Parameters:
        - lp [LoadProfile]: the load profile to get the clustering attributes for.
        - boundaries [list[tuple[int, int]]]: the list of time boundaries
            (start_time, end_time) which specify the attributes (as the sum of
            the load readings over the time interval).

    Returns:
        - [list[float]]: the list of attributes for which the load profile will be
            clustered by.
    """

    attributes: list[float] = list()
    for start_time, end_time in boundaries:
        if end_time >= start_time:
            mask = (start_time <= lp.hours) & (lp.hours < end_time)
        else:
            # then we cross the start of day so we must split up.
            mask = (lp.hours >= start_time) | (lp.hours < end_time)
        load_sum = np.sum(lp.load[mask])
        attributes.append(load_sum)

    return attributes


def fit_season_clusters(
    dataset_dir: str,
    season: int,
    customers: list[int],
    n_clusters: int,
    boundaries: list[tuple[float, float]],
) -> list[ClusterData]:
    """
    Fits the clusters for the given season for all across all customers.
    k-means clustering has been performed based on the load profiles attributes as
    computed using get_attributes (see above).

    Parameters:
        - dataset_dir [str]: the filepath to the parquet dataset.
        - season [int]: the season the load profiles will be split on before we can
            perform the clustering.
        - customers [list[int]]: the list of customer ids to include the load profiles
            for where we will cluster them all independently of there cluster.
        - n_clusters [int]: the number of clusters to use in the k-means clustering.
        - boundaries [list[tuple[int, int]]]: the time intervals used to cluster the
            load profiles (see get_attributes for more details).

    Returns:
        - [list[ClusterData]]: list of the cluster results for each cluster.
    """
    assert 0 <= season < 4

    # we only store the attributes (not the load profiles) to significantly reduce the
    # memory required.
    customer_dates: list[tuple[int, str]] = list()
    # compute the attributes for each load profile.
    attributes_list: list[Iterable[float]] = list()
    for customer in tqdm(
        customers, desc=f"season={season}: getting load profile attributes"
    ):
        load_profiles = data_processing.get_customer_load_profiles(
            dataset_dir, customer, season=season
        )
        customer_attrs = [get_attributes(lp, boundaries) for lp in load_profiles]
        attributes_list.extend(customer_attrs)

        dates = [lp.date for lp in load_profiles]
        customer_dates.extend([(customer, date) for date in dates])

    attributes = np.array(attributes_list)

    # perform the k-means clustering on the attributes.
    kmeans = KMeans(n_clusters=n_clusters).fit(attributes)
    labels = kmeans.labels_

    # package the results to into a list of ClusterData.
    cluster_datas: list[ClusterData] = list()
    for cluster in range(n_clusters):
        cluster_customer_dates = [
            (customer, date)
            for i, (customer, date) in enumerate(customer_dates)
            if labels[i] == cluster
        ]
        cluster_data = ClusterData(
            season=season,
            cluster=cluster,
            customer_dates=cluster_customer_dates,
        )
        cluster_datas.append(cluster_data)

    return cluster_datas


def fit_kernels(load_profiles: list[LoadProfile]) -> list[TimeKernel]:
    """
    Given a list of load profiles (assumed to be the same cluster) fit the time kernels
    for each time step in the day by each load profiles reading at that time.

    Parameters:
        - load_profiles [list[LoadProfile]]: list of load_profiles in the same cluster
            which will be used to fit the kernels.

    Returns:
        - [list[TimeKernel]]: list of fitted time kernels for each timestep to the load
            profiles.
    """
    loads = np.array([lp.load for lp in load_profiles])
    kernels: list[TimeKernel] = list()
    for time_idx in range(inputs.STEPS_PER_DAY):
        time_loads = loads[:, time_idx]
        time_kernel = TimeKernel(time_loads)
        kernels.append(time_kernel)

    return kernels


def fit_cluster_kernel_from_cluster_data(
    dataset_dir: str,
    cluster_data: ClusterData,
    variables_map: dict[str, bool],
) -> ClusterKernels:
    """
    Fits all cluster kernels from the dataset for all seasons.

    Parameters:
        - dataset_dir [str]: the filepath to the parquet dataset.
        - cluster_data [ClusterData]: the meta-data for which tells us what information
            we need to make the ClusterKernel i.e. which load profiles, season and id.
        - variables_map [dict[str, any]]: a map from meta-data variables (as they appear
            in inputs.VARIABLES_TO_SPLIT_ON) to the value for that variable for the
            cluster kernels.

    Returns:
        - [ClusterKernels]: the fitted cluster kernel which is able to generate
            synthetic load readings.
    """

    # fetch the load profiles in that season cluster from the dataset.
    load_profiles = data_processing.get_customer_dates_load_profiles(
        dataset_dir, cluster_data.customer_dates
    )

    # fit the time kernels for the load profiles.
    time_kernels = fit_kernels(load_profiles)

    return ClusterKernels(
        cluster_data.cluster, cluster_data.season, time_kernels, variables_map
    )


def get_fitted_cluster_kernels(dataset_dir: str) -> list[ClusterKernels]:
    """
    From the given parquet dataset performs the following:
        - fetches all ids that we will use in the clustering.
        - perform the clustering for each season.
        - fits a cluster kernel for each cluster in each season.

    Parameters:
        - dataset_dir [str]: the filepath to the parquet dataset.

    Returns:
        - [list[ClusterKernels]]: a list all cluster kernels for each fitted
            cluster across all seasons.
    """
    all_customer_ids = data_processing.get_customers(dataset_dir)
    all_customer_ids = data_processing.filter_out_non_retail_customers(all_customer_ids)

    variable_combinations = data_processing.get_variable_combinations(
        inputs.VARIABLES_TO_SPLIT_ON
    )

    cluster_kernels: list[ClusterKernels] = list()
    for group_variable_combination in variable_combinations:
        # get the customer ids for this group by their variables combination.
        ids = data_processing.get_customer_group(
            all_customer_ids,
            inputs.VARIABLES_TO_SPLIT_ON,
            group_variable_combination,
            inputs.NUMBER_OF_CUSTOMERS_PER_GROUP,
        )

        group_var_map = {
            col.lower(): group_variable_combination[i] == "Y"
            for i, col in enumerate(inputs.VARIABLES_TO_SPLIT_ON)
        }
        print(f"Getting Season Clusters for group combination: {group_var_map}")

        # now we get a clusters for each season for the ids in the group.
        for season in range(4):
            # get the cluster data.
            season_clusters_data = fit_season_clusters(
                dataset_dir, season, ids, inputs.N_CLUSTERS, inputs.ATTRIBUTE_BOUNDARIES
            )

            # fit the cluster_kernels.
            for cluster_data in season_clusters_data:
                cluster_kernel = fit_cluster_kernel_from_cluster_data(
                    dataset_dir, cluster_data, group_var_map
                )
                cluster_kernels.append(cluster_kernel)

    return cluster_kernels
