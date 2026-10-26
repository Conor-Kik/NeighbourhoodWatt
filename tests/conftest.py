import sqlite3

import pytest

from database_v2.db_initilisation import reset_db
from tests.factories import (
    BatteryConfigFactory,
    HouseConfigFactory,
    LocationFactory,
)


@pytest.fixture
def test_db(tmp_path):
    """Provides a completely isolated, temporary, empty SQLite database."""

    test_db_path = tmp_path / "test_application.db"

    reset_db(db_path=test_db_path, seed_db=False)

    yield test_db_path


@pytest.fixture
def dummy_location(test_db):
    loc = LocationFactory.build()

    with sqlite3.connect(test_db) as conn:
        conn.execute(
            "INSERT INTO location (location_name, latitude, longitude, "
            "timezone, nem_region) VALUES (?, ?, ?, ?, ?)",
            (
                loc.location_name,
                loc.latitude,
                loc.longitude,
                loc.timezone,
                loc.nem_region,
            ),
        )
    return loc


@pytest.fixture
def dummy_battery_config(test_db):
    config = BatteryConfigFactory.build()

    with sqlite3.connect(test_db) as conn:
        conn.execute(
            """
            INSERT INTO battery_config (battery_config_id, capacity_kwh,
                max_power_kw, efficiency, min_soc_frac, max_soc_frac,
                replacement_cost_aud, lifetime_cycles, max_cycles_24h)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config.battery_config_id,
                config.capacity_kwh,
                config.max_power_kw,
                config.efficiency,
                config.min_soc_frac,
                config.max_soc_frac,
                config.replacement_cost_aud,
                config.lifetime_cycles,
                config.max_cycles_24h,
            ),
        )
    return config


@pytest.fixture
def dummy_house_config(test_db):
    config = HouseConfigFactory.build()

    with sqlite3.connect(test_db) as conn:
        conn.execute(
            """
            INSERT INTO house_config (house_config_id, pv_peak_capacity,
            panel_tilt, panel_azimuth, pv_inverter_ac_kw, pv_inverter_efficiency,
            has_aircon, has_poolpump, has_ev)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config.house_config_id,
                config.pv_peak_capacity,
                config.panel_tilt,
                config.panel_azimuth,
                config.pv_inverter_ac_kw,
                config.pv_inverter_efficiency,
                config.has_aircon,
                config.has_poolpump,
                config.has_ev,
            ),
        )
    return config
