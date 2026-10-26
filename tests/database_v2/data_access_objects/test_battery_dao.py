from database_v2.data_access_objects.battery_dao import BatteryDao
from tests.factories import BatteryConfigFactory


def test_save_and_get_battery_config(test_db):
    """Tests inserting, updating, and retrieving a battery config."""
    dao = BatteryDao(test_db)

    dummy_battery = BatteryConfigFactory.build()

    dao.save_battery_config(dummy_battery)
    fetched = dao.get_battery_config(dummy_battery.battery_config_id)

    assert fetched is not None
    assert fetched.capacity_kwh == dummy_battery.capacity_kwh
    assert fetched.max_power_kw == dummy_battery.max_power_kw

    updated_battery = dummy_battery.model_copy(update={"capacity_kwh": 14.0})
    dao.save_battery_config(updated_battery)

    fetched_again = dao.get_battery_config(dummy_battery.battery_config_id)
    assert fetched_again.capacity_kwh == 14.0


def test_get_all_battery_configs(test_db):
    """Tests retrieving all registered battery configs sorted by ID."""
    dao = BatteryDao(test_db)

    batt_a = BatteryConfigFactory.build(battery_config_id="battery_a")
    batt_b = BatteryConfigFactory.build(battery_config_id="battery_b")

    dao.save_battery_config(batt_b)
    dao.save_battery_config(batt_a)

    all_configs = dao.get_all_battery_configs()

    assert len(all_configs) == 2
    assert all_configs[0].battery_config_id == "battery_a"
    assert all_configs[1].battery_config_id == "battery_b"


def test_get_missing_battery_config(test_db):
    """Tests that querying a non-existent battery config returns None."""
    dao = BatteryDao(test_db)
    assert dao.get_battery_config("nonexistent_battery") is None
