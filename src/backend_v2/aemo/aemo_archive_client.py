"""Downloads and reads AEMO electricity market data for the other services.

Public PREDISPATCHIS reports do not contain the private UNIT_SOLUTION table.
PREDISPATCHLOAD therefore uses its monthly archive, like the other tables.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zipfile import ZipFile

import pandas as pd
import requests
from nemosis import dynamic_data_compiler

from backend_v2.aemo.aemo_constants import (
    AEMO_ARCHIVE_URL,
    AEMO_CACHE_DIR,
    AEMO_MARKET_TIMEZONE,
    ARCHIVE_FOLDER_BY_TABLE,
    RAW_DATA_CACHE,
    AemoArchiveTable,
)
from database_v2.models.request_models import NemRegion

logger = logging.getLogger(__name__)


def market_time_to_utc(value: datetime) -> datetime:
    """Convert a naive AEMO market timestamp to an aware UTC timestamp."""
    if value.tzinfo is not None:
        raise ValueError("AEMO market time must be naive.")
    return value.replace(tzinfo=AEMO_MARKET_TIMEZONE).astimezone(UTC)


def utc_to_market_time(value: datetime, name: str = "timestamp") -> datetime:
    """Convert an aware timestamp to naive AEMO market time."""
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware.")
    return value.astimezone(AEMO_MARKET_TIMEZONE).replace(tzinfo=None)


def normalise_region(region: str | NemRegion) -> str:
    """Return a validated uppercase AEMO region identifier."""
    value = str(region).upper()
    valid_regions = {item.value for item in NemRegion}
    if value not in valid_regions:
        expected = ", ".join(sorted(valid_regions))
        raise ValueError(f"Invalid region '{value}'. Expected one of: {expected}")
    return value


def _next_month(value: datetime) -> datetime:
    """Return the first day of the month following ``value``."""
    if value.month == 12:
        return datetime(value.year + 1, 1, 1)
    return datetime(value.year, value.month + 1, 1)


class AemoArchiveClient:
    """Handles AEMO ZIP downloads and table extraction."""

    def __init__(self) -> None:
        """Create the shared local archive cache when necessary."""
        self.cache_dir = AEMO_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _download_to_path(url: str, destination: Path, timeout: int) -> Path:
        """Download a monthly archive to a temporary file, then cache it atomically."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_suffix(destination.suffix + ".tmp")

        with requests.get(url, timeout=timeout) as response:
            response.raise_for_status()
            temp_path.write_bytes(response.content)

        temp_path.replace(destination)
        return destination

    def _get_url_and_filename(
        self, table: AemoArchiveTable, run_datetime: datetime
    ) -> tuple[str, str]:
        """Build the monthly MMSDM archive URL and remote ZIP filename."""
        month_start = run_datetime.strftime("%Y%m") + "010000"
        folder = ARCHIVE_FOLDER_BY_TABLE[table]

        # AEMO changed its monthly archive filename convention in August 2024.
        if run_datetime < datetime(2024, 8, 1):
            filename = f"PUBLIC_DVD_{table}_{month_start}.zip"
        elif folder.endswith("_ALL_DATA"):
            filename = f"PUBLIC_ARCHIVE#{table}#ALL#FILE01#{month_start}.zip"
        else:
            filename = f"PUBLIC_ARCHIVE#{table}#FILE01#{month_start}.zip"

        url = (
            f"{AEMO_ARCHIVE_URL}/{run_datetime.year}/"
            f"MMSDM_{run_datetime.year}_{run_datetime.month:02d}/"
            f"MMSDM_Historical_Data_SQLLoader/{folder}/{quote(filename, safe='')}"
        )
        return url, filename

    def download_archive(self, table: AemoArchiveTable, run_datetime: datetime) -> Path:
        """Return a cached monthly MMSDM archive, downloading it when absent."""
        url, filename = self._get_url_and_filename(table, run_datetime)
        archive_path = self.cache_dir / filename.replace("#", "_")

        if archive_path.exists():
            logger.debug(
                "Using cached %s archive for %s", table, run_datetime.strftime("%Y-%m")
            )
            return archive_path

        logger.info(
            "Downloading %s archive for %s", table, run_datetime.strftime("%Y-%m")
        )
        downloaded = self._download_to_path(url, archive_path, timeout=300)
        logger.info("Cached %s archive at %s", table, downloaded)
        return downloaded

    def _load_archive_month(
        self,
        table: AemoArchiveTable,
        run_datetime: datetime,
        usecols: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read one table CSV from a monthly MMSDM archive."""
        archive_path = self.download_archive(table, run_datetime)

        with ZipFile(archive_path) as archive:
            csv_files = [
                name for name in archive.namelist() if name.lower().endswith(".csv")
            ]
            if len(csv_files) != 1:
                raise RuntimeError(
                    f"Expected exactly one CSV in {table} archive, "
                    f"found {len(csv_files)}."
                )
            with archive.open(csv_files[0]) as file_obj:
                data = pd.read_csv(
                    file_obj,
                    skiprows=1,
                    usecols=usecols,
                    low_memory=False,
                )

        # AEMO CSVs end with a control/trailer row rather than a data record.
        result = data.iloc[:-1].copy()
        logger.debug("Loaded %d %s archive rows", len(result), table)
        return result

    def load_archive_table(
        self,
        table: AemoArchiveTable,
        start_datetime: datetime,
        end_datetime: datetime | None = None,
        usecols: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read every monthly MMSDM archive touched by the requested window."""
        end_datetime = end_datetime or start_datetime
        if end_datetime < start_datetime:
            raise ValueError("end_datetime cannot precede start_datetime.")

        cursor = datetime(start_datetime.year, start_datetime.month, 1)
        last_month = datetime(end_datetime.year, end_datetime.month, 1)
        frames: list[pd.DataFrame] = []

        while cursor <= last_month:
            frames.append(self._load_archive_month(table, cursor, usecols))
            cursor = _next_month(cursor)

        if not frames:
            raise RuntimeError(f"No {table} archive data was loaded.")

        return frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)

    @staticmethod
    def load_interval_table(
        *,
        table_name: str,
        start_market: datetime,
        end_market: datetime,
        select_columns: list[str],
        filter_cols: list[str],
        filter_values: tuple[Any, ...],
    ) -> pd.DataFrame:
        """Fetch an exact interval-data window through NEMOSIS."""
        RAW_DATA_CACHE.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Loading AEMO %s intervals from %s to %s",
            table_name,
            start_market.isoformat(),
            end_market.isoformat(),
        )

        # NEMOSIS emits verbose INFO logs for each downloaded interval file.
        root_logger = logging.getLogger()
        previous_level = root_logger.level
        root_logger.setLevel(max(previous_level, logging.WARNING))

        try:
            data = dynamic_data_compiler(
                start_time=start_market.strftime("%Y/%m/%d %H:%M:%S"),
                end_time=end_market.strftime("%Y/%m/%d %H:%M:%S"),
                table_name=table_name,
                raw_data_location=str(RAW_DATA_CACHE),
                select_columns=select_columns,
                filter_cols=filter_cols,
                filter_values=filter_values,
                keep_csv=False,
            )
        finally:
            root_logger.setLevel(previous_level)
        logger.info("Loaded %d AEMO %s interval rows", len(data), table_name)
        return data
