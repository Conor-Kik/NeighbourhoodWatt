""" """

import os

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from numpy.typing import NDArray
from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]

import backend_v2.load_profiles.load_profile_data_processing as data_processing
import backend_v2.load_profiles.load_profile_inputs as inputs

TEST_COLUMNS = [
    "HAS_AIRCON",
    "NUM_ROOMS_HEATED",
    "HAS_POOLPUMP",
    "HAS_GAS_HEATING",
    "DWELLING_TYPE_CD",
]

PLOTS_OUTDIR = os.path.join(inputs.LOAD_PROFILE_DIR, "analysis_plots")


class HouseholdMetadata:
    def __init__(self, filepath: str, dataset_dir: str) -> None:
        assert os.path.exists(filepath)
        assert os.path.exists(dataset_dir)

        self._df = pd.read_csv(filepath)
        self._dataset_dir = dataset_dir

        # filter out any customers which do not appear in the readings data.
        readings_customers = data_processing.get_customers(dataset_dir)
        self._df = self._df[self._df["CUSTOMER_KEY"].isin(readings_customers)]

        return None

    def get_column_frequency(self, column: str) -> float:
        assert column in self._df.columns
        return self._df[column].count() / len(self._df)

    def plot_column_counts(self, column: str, outpath: str | None) -> None:
        assert column in self._df.columns

        self._df[column].value_counts().plot.bar(rot=45)

        plt.xlabel(column)
        plt.ylabel("count")
        plt.tight_layout()
        if outpath is None:
            plt.show()
        else:
            plt.savefig(outpath)
        plt.close()

    def compute_varience_explained_in_column(self, column: str) -> float:
        assert column in self._df.columns

        reduced_df = self._df.dropna(subset=[column])

        options = reduced_df[column].unique()
        assert len(options) == 2  # assume we are working with a binary split.

        # reduce the dataframe of customers to be a much smaller percentage so that
        # we can perform the analysis in a timely manner.
        # also we split such that the distribution of the column stays approx. the same.
        reduced_df, _ = train_test_split(
            reduced_df, train_size=0.005, stratify=reduced_df[column], random_state=42
        )

        options_loads: list[bool] = list()
        column_values = reduced_df[column].to_numpy()
        all_customers = reduced_df["CUSTOMER_KEY"].to_numpy()
        all_load_profiles: list[NDArray[np.float64]] = list()
        for i, customer in enumerate(all_customers):
            customer_load_profiles = data_processing.get_customer_load_profiles(
                self._dataset_dir, customer
            )
            customer_loads = np.array([lp.load for lp in customer_load_profiles])
            all_load_profiles.extend(customer_loads)

            customer_option = column_values[i] == options[0]
            options_loads.extend([customer_option] * len(customer_loads))

        all_loads = np.array(all_load_profiles)
        all_loads.reshape((-1, 48))

        overall_avg_load = np.mean(all_loads, axis=0)

        options_mask = np.array(options_loads)
        option0_loads = all_loads[options_mask]
        option0_avg_load = np.mean(option0_loads, axis=0)
        option1_loads = all_loads[1 - options_mask]
        option1_avg_load = np.mean(option1_loads, axis=0)

        # compute the R^2 value
        ss_total = ((all_loads - overall_avg_load) ** 2).sum()
        ss_between = (
            options_mask.sum() * ((option0_avg_load - overall_avg_load) ** 2).sum()
            + (1 - options_mask).sum()
            * ((option1_avg_load - overall_avg_load) ** 2).sum()
        )

        return ss_between / ss_total


def main() -> None:
    household_metadata = HouseholdMetadata(
        inputs.HOUSEHOLD_DATAPATH, inputs.READINGS_PARQUET_OUTDIR
    )

    print("---------------------------------------------------------------")
    print(f"column: {12 * ' '} freq:")
    for col in TEST_COLUMNS:
        freq = household_metadata.get_column_frequency(col)
        print(f"{col} {(20 - len(col)) * ' '}{freq:.3f}")
    print("---------------------------------------------------------------")

    for col in TEST_COLUMNS:
        plot_outpath = os.path.join(PLOTS_OUTDIR, f"{col}_count_bar.png")
        household_metadata.plot_column_counts(col, plot_outpath)

    print("---------------------------------------------------------------")
    print("column:           R^2:")
    aircon_r2 = household_metadata.compute_varience_explained_in_column("HAS_AIRCON")
    print(f"HAS_AIRCON        {aircon_r2:.3f}")
    poolpump_r2 = household_metadata.compute_varience_explained_in_column(
        "HAS_POOLPUMP"
    )
    print(f"HAS_POOLPUMP      {poolpump_r2:.3f}")
    gas_heating_r2 = household_metadata.compute_varience_explained_in_column(
        "HAS_GAS_HEATING"
    )
    print(f"HAS_GAS_HEATING   {gas_heating_r2:.3f}")
    print("---------------------------------------------------------------")

    return None


if __name__ == "__main__":
    if not os.path.exists(PLOTS_OUTDIR):
        os.mkdir(PLOTS_OUTDIR)
    main()
