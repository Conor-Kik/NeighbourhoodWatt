"""
This script is runnable and will be able to make the load profiles distributions by
processing the smart-grid smart-city dataset. Note that you must have this dataset
downloaded locally somewhere to be able to run this. After the dataset has been
processed and the clustering is complete a simple plot will be made to show the
resulting clusters and their distribution.
"""

import os

import matplotlib
import numpy as np
from matplotlib import pyplot as plt

import backend_v2.load_profiles.load_profile_clustering as clustering
import backend_v2.load_profiles.load_profile_data_processing as data_processing
import backend_v2.load_profiles.load_profile_helper_functions as helper_functions
import backend_v2.load_profiles.load_profile_inputs as inputs
from backend_v2.load_profiles.load_profile_dataclasses import (
    LoadProfileGenerator,
    save_cluster_kernels,
)

matplotlib.use("Agg")


#### VALIDATION (PLOTTING) FUNCTIONS ###################################################
def plot_load_profile_generator_outputs(
    load_profile_generator: LoadProfileGenerator, out_filepath: str | None = None
) -> None:
    """
    Plots the median and quartiles 0.25 and 0.75 for each load profile generators
    across all seasons. The plot is in a 2x2 grid with each axes being a seasons sample
    load profiles.

    Parameters:
        - load_profile_generator [LoadProfileGenerator]: the load profile generator used
            to sample all of the quantiles of load profiles.
        - out_filepath [str | None]: the out filepath to save the plot to. if None then
            the plot is displayed using plt.show()
    """
    hours = np.linspace(0, 24, inputs.STEPS_PER_DAY, endpoint=False)

    fig, axes = plt.subplots(2, 2, figsize=(12, 7), sharex=True, sharey=True)
    axes = axes.flatten()
    for season in range(4):
        dummy_season_time = helper_functions.get_dummy_time_series(season=season)
        start_time = helper_functions.time2dt(dummy_season_time[0])
        for i, variable_options in enumerate(
            load_profile_generator.split_variables_options
        ):
            split_variable_map = {
                var: variable_options[j]
                for j, var in enumerate(load_profile_generator.split_variables)
            }

            kernel_q25 = load_profile_generator.get_sample_load_quartile(
                0.25, start_time, **split_variable_map
            )
            kernel_median = load_profile_generator.get_sample_load_quartile(
                0.50, start_time, **split_variable_map
            )
            kernel_q75 = load_profile_generator.get_sample_load_quartile(
                0.75, start_time, **split_variable_map
            )

            axes[season].plot(
                hours,
                kernel_median.load,
                color=inputs.COLOURS[i % len(inputs.COLOURS)],
                label=f"{variable_options}",
            )
            axes[season].fill_between(
                hours,
                kernel_q25.load,
                kernel_q75.load,
                color=inputs.COLOURS[i % len(inputs.COLOURS)],
                alpha=0.2,
            )

        axes[season].grid()
        axes[season].set_xlim([0, 23.5])
        axes[season].set_title(inputs.SEASONS_MAP[season])
        axes[season].legend()

    axes[2].set_xlabel("Hours since Start of Day")
    axes[3].set_xlabel("Hours since Start of Day")
    axes[0].set_ylabel("Load (KWH)")
    axes[2].set_ylabel("Load (KWH)")

    fig.suptitle(
        "Median and Quartiles for Clusters in all Season. "
        + f"split on attributes: {load_profile_generator.split_variables}"
    )
    plt.tight_layout()

    if out_filepath is not None:
        plt.savefig(out_filepath)
        print(f"Saved: {out_filepath}")
        plt.close()
    else:
        plt.show()

    return None


#### MAIN ##############################################################################
def main() -> None:
    """
    Performs the following actions:
        - if there is no existing partitioned parquet dataset then one is generated
            from the smart-grid smart-city .csv file. otherwise we will just use the
            existing parquet dataset and nothing is done.
        - if saved (previous) cluster kernels exist then they are then we just load
            them in. Otherwise, we wil need to create cluster kernels which are to be
            fitted from the dataset. Once these have been made we save them and load
            them in for use.
        - build a sample load profile generator from the saved cluster kernels.
        - plot the loaded load profile generator quantiles, and save the plot.
    """

    if not os.path.exists(inputs.READINGS_PARQUET_OUTDIR):
        # the large .csv file is hard to query and work with so we split the dataset
        # into chunked parquet files by the customer id.
        # this parquet dataset dir is what will be used in the remaining analysis.
        data_processing.convert_csv_dataset_to_partitioned_parquet(
            inputs.READINGS_DATAPATH, inputs.READINGS_PARQUET_OUTDIR
        )

    if not os.path.exists(inputs.CLUSTER_KERNELS_DATA_FILEPATH):
        # then the cluster kernels have not been created so we must first make
        # then from the dataset.
        print(f"Could not find: {inputs.CLUSTER_KERNELS_DATA_FILEPATH}")
        print("Creating cluster kernels from dataset...")
        cluster_kernels = clustering.get_fitted_cluster_kernels(
            inputs.READINGS_PARQUET_OUTDIR
        )

        # save the load profile generators (so we don't have the perform the above
        # analysis every time.).
        print(f"Saving cluster kernels to {inputs.CLUSTER_KERNELS_DATA_FILEPATH}")
        save_cluster_kernels(cluster_kernels, inputs.CLUSTER_KERNELS_DATA_FILEPATH)

    # now that the cluster kernels have been saved we can just use them to to make a
    # load profile generator.
    print(
        f"Loading existing cluster kernels from {inputs.CLUSTER_KERNELS_DATA_FILEPATH}"
    )

    load_profile_generator = LoadProfileGenerator(inputs.CLUSTER_KERNELS_DATA_FILEPATH)

    plot_load_profile_generator_outputs(
        load_profile_generator, out_filepath=inputs.PLOT_OUT_FILEPATH
    )

    return None


if __name__ == "__main__":
    main()
