"""Stores shared settings such as time intervals, data paths and forecast inputs."""

from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from database_v2.db_constants import SEED_DIR

# ---------------------------------------------------------------------------
# Market timing
# ---------------------------------------------------------------------------

# AEMO market timestamps use Brisbane time and therefore do not observe DST.
AEMO_MARKET_TIMEZONE = ZoneInfo("Australia/Brisbane")

AEMO_DISPATCH_INTERVAL = timedelta(minutes=5)
AEMO_PREDISPATCH_INTERVAL = timedelta(minutes=30)

AEMO_PREDISPATCH_PERIODS_PER_DAY = int(timedelta(days=1) / AEMO_PREDISPATCH_INTERVAL)

# A nominal half-hour predispatch run may be published shortly after the
# corresponding half-hour boundary.
FORECAST_PUBLICATION_GRACE = timedelta(minutes=4)
PRICE_PUBLICATION_DELAY = timedelta(minutes=5)


# ---------------------------------------------------------------------------
# AEMO archive locations
# ---------------------------------------------------------------------------

# Monthly MMSDM archive.
AEMO_ARCHIVE_URL = "https://www.nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM"

# ---------------------------------------------------------------------------
# Cache paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]

AEMO_CACHE_DIR = PROJECT_ROOT / ".cache" / "aemo"

RAW_DATA_CACHE = AEMO_CACHE_DIR / "raw"
MODEL_ROOT = SEED_DIR / "lightgbm"

# Shared lookback for emissions profiles and missing-price forecast targets.
FORECAST_HISTORY_DAYS = 7
# Conservative margin on DISPATCHLOAD's next-day (~8am) publication lag.
EMISSIONS_HISTORY_DELAY = timedelta(days=2)
# Includes the seven-day feature window and delayed emissions observations.
FEATURE_HISTORY_DAYS = 10


# ---------------------------------------------------------------------------
# Saved forecast model feature order and regional archive columns
# ---------------------------------------------------------------------------

FEATURE_COLUMNS = [
    "h",
    "target_slot",
    "target_weekday",
    "target_month",
    "target_weekend",
    "target_day_sin",
    "target_day_cos",
    "target_year_sin",
    "target_year_cos",
    "baseline_price",
    "price_is_historical",
    "baseline_emissions",
    "price_revision",
    "price_last",
    "price_mean_24h",
    "price_std_24h",
    "price_mean_7d",
    "price_negative_fraction_24h",
    "price_age_hours",
    "price_same_slot_1d",
    "price_same_slot_7d",
    "emissions_last",
    "emissions_mean_24h",
    "emissions_std_24h",
    "emissions_mean_7d",
    "emissions_age_hours",
    "emissions_same_slot_3d",
    "emissions_same_slot_7d",
]

RENEWABLE_FEATURE_COLUMNS = [
    "forecast_solar_available_mw",
    "forecast_wind_available_mw",
    "forecast_demand_mw",
    "forecast_residual_demand_mw",
    "forecast_renewable_share",
    "generation_forecast_age_hours",
    "generation_forecast_missing",
]

FEATURE_COLUMNS_BY_KIND = {
    "price": FEATURE_COLUMNS,
    "emissions": [*FEATURE_COLUMNS, *RENEWABLE_FEATURE_COLUMNS],
}

REGIONAL_FORECAST_REQUIRED = [
    "PREDISPATCHSEQNO",
    "RUNNO",
    "REGIONID",
    "PERIODID",
    "INTERVENTION",
    "DATETIME",
    "LASTCHANGED",
]
REGIONAL_FORECAST_VALUES = {
    "SS_SOLAR_AVAILABILITY": "forecast_solar_available_mw",
    "SS_WIND_AVAILABILITY": "forecast_wind_available_mw",
    "TOTALDEMAND": "forecast_demand_mw",
}


# ---------------------------------------------------------------------------
# Archive tables
# ---------------------------------------------------------------------------


class AemoArchiveTable(StrEnum):
    """Historical archive tables consumed by the backend."""

    PREDISPATCHPRICE = "PREDISPATCHPRICE"
    PREDISPATCHLOAD = "PREDISPATCHLOAD"
    PREDISPATCHREGIONSUM = "PREDISPATCHREGIONSUM"

    GENUNITS = "GENUNITS"
    DUDETAILSUMMARY = "DUDETAILSUMMARY"
    DUALLOC = "DUALLOC"

    DISPATCHPRICE = "DISPATCHPRICE"


# Monthly MMSDM SQLLoader folders.
ARCHIVE_FOLDER_BY_TABLE: dict[AemoArchiveTable, str] = {
    AemoArchiveTable.PREDISPATCHPRICE: "PREDISP_ALL_DATA",
    AemoArchiveTable.PREDISPATCHLOAD: "PREDISP_ALL_DATA",
    AemoArchiveTable.PREDISPATCHREGIONSUM: "PREDISP_ALL_DATA",
    AemoArchiveTable.GENUNITS: "DATA",
    AemoArchiveTable.DUDETAILSUMMARY: "DATA",
    AemoArchiveTable.DUALLOC: "DATA",
    AemoArchiveTable.DISPATCHPRICE: "DATA",
}
