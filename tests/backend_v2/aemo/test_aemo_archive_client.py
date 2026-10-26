from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import pytest

from backend_v2.aemo.aemo_archive_client import (
    AemoArchiveClient,
    market_time_to_utc,
    utc_to_market_time,
)
from backend_v2.aemo.aemo_constants import AemoArchiveTable


def test_monthly_unit_archive_preserves_data_and_removes_control_row(monkeypatch):
    archive = BytesIO()
    with ZipFile(archive, "w") as zipped:
        zipped.writestr(
            "units.csv",
            (
                "C,AEMO,metadata\n"
                "I,PREDISPATCH,UNIT_SOLUTION,5,DUID,PERIODID,TOTALCLEARED\n"
                "D,PREDISPATCH,UNIT_SOLUTION,5,COAL,1,100\n"
                "D,PREDISPATCH,UNIT_SOLUTION,5,SOLAR,1,50\n"
                "C,END OF REPORT,5\n"
            ),
        )
    archive.seek(0)
    client = AemoArchiveClient()
    monkeypatch.setattr(client, "download_archive", lambda *_: archive)
    result = client._load_archive_month(
        AemoArchiveTable.PREDISPATCHLOAD,
        datetime(2026, 7, 1),
        ["DUID", "PERIODID", "TOTALCLEARED"],
    )
    assert result["DUID"].tolist() == ["COAL", "SOLAR"]
    assert result["TOTALCLEARED"].tolist() == [100, 50]


@pytest.mark.parametrize(
    "table",
    [
        AemoArchiveTable.PREDISPATCHPRICE,
        AemoArchiveTable.PREDISPATCHLOAD,
    ],
)
def test_load_archive_table_loads_each_month_in_window_once(
    monkeypatch: pytest.MonkeyPatch,
    table: AemoArchiveTable,
) -> None:
    client = AemoArchiveClient()
    loaded_months: list[datetime] = []

    def fake_load(
        table: AemoArchiveTable,
        run_datetime: datetime,
        usecols: list[str] | None = None,
    ) -> pd.DataFrame:
        loaded_months.append(run_datetime)
        return pd.DataFrame({"month": [run_datetime.month]})

    monkeypatch.setattr(client, "_load_archive_month", fake_load)

    result = client.load_archive_table(
        table,
        datetime(2026, 1, 31),
        datetime(2026, 3, 1),
    )

    assert loaded_months == [
        datetime(2026, 1, 1),
        datetime(2026, 2, 1),
        datetime(2026, 3, 1),
    ]
    assert result["month"].tolist() == [1, 2, 3]


def test_market_time_conversion_is_shared_and_reversible() -> None:
    market_time = datetime(2026, 8, 20, 4, 30)

    utc_time = market_time_to_utc(market_time)

    assert utc_time == datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    assert utc_to_market_time(utc_time) == market_time
