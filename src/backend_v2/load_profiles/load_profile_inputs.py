"""
This file contains all inputs and constants need for the load profile scripts.
"""

import os

from database_v2.db_constants import SEED_DIR

#### CLUSTERING INPUTS #################################################################
N_CLUSTERS = 1  # the number of clusters to group the load profiles on per season.
ATTRIBUTE_BOUNDARIES = [
    # these are the time boundaries used to get the attributes which we cluster by.
    # (start_time, end_time)
    (20, 3),
    (3, 5),
    (5, 9),
    (9, 12),
    (12, 14.5),
    (14.5, 20),
]


# the variables in the meta-data which will split on to form the clusters.
VARIABLES_TO_SPLIT_ON = [
    # all of these should be named as they appear in the household data in the
    # smart-grid smart-city dataset.
    "HAS_AIRCON",
    "HAS_POOLPUMP",
]

# the number of customers to include in the analysis for each group,
# as determined by VARIABLES_TO_SPLIT_ON.
NUMBER_OF_CUSTOMERS_PER_GROUP = 50

#### DATASET INPUTS ####################################################################
# note these must match whatever is in the dataset (you cannot arbitrary change them).
STEPS_PER_DAY = 48  # 30min intervals over a 24hr period.


#### PATHING INPUTS ####################################################################
# the path to the directories of this 'load_profiles' folder.
LOAD_PROFILE_DIR = os.path.dirname(os.path.abspath(__file__))

# filepath to the sgsc-ct_customer-household-data-revised.csv file (household meta-data)
# as saved in the smart-grid-smart-city dataset.
HOUSEHOLD_DATAPATH = (
    r"/mnt/d/smart-grid smart-city dataset/sgsc-ct_customer-household-data-revised.csv"
)

# filepath to the CD_INTERVAL_READING_ALL_NO_QUOTES.csv fie (meter readings)
# as saved in the smart-grid-smart-city dataset.
READINGS_DATAPATH = (
    r"/mnt/d/smart-grid smart-city dataset/CD_INTERVAL_READING_ALL_NO_QUOTES.csv"
)

# the desired out directory to store the transformed partitioned parquet dataset.
READINGS_PARQUET_OUTDIR = r"/mnt/d/smart-grid smart-city dataset/partitioned_parquet"

# the directory which will contain the load profile distributions for each kernel.
CLUSTER_KERNELS_DATA_FILEPATH = os.path.join(
    SEED_DIR,
    "load_profiles_cluster_kernels_distributions.npz",
)

# the filepath to save the load profile generator plot to.
PLOT_OUT_FILEPATH = os.path.join(LOAD_PROFILE_DIR, "plot_load_profile_generators.png")

#### MISCELLANEOUS #####################################################################
# list of seasons as ordered.
SEASONS_MAP = ["Summer", "Autumn", "Winter", "Spring"]

COLOURS = [
    # list of colours just used for plotting purposes.
    # ideally these should be distinct and unique.
    "#bc3b21",
    "#4465db",
    "#9bb932",
    "#9439b6",
    "#5dbe4e",
    "#cd61dc",
    "#548826",
    "#7455c5",
    "#cead3e",
    "#af72e3",
    "#5ebf7d",
    "#ca3ba1",
    "#3f8248",
    "#e879dc",
    "#9dad5f",
    "#6882e4",
    "#da8a2e",
    "#488ecb",
    "#e66637",
    "#4dbcdf",
    "#df365e",
    "#54c7b0",
    "#e95693",
    "#338c74",
    "#b63465",
    "#626b2a",
    "#964ba2",
    "#937a2b",
    "#665fa5",
    "#c9a369",
    "#c18dd5",
    "#98592a",
    "#9c9cde",
    "#c74b4b",
    "#da83b9",
    "#e18e66",
    "#a74983",
    "#e0828b",
    "#8f5478",
    "#9f4b56",
]
