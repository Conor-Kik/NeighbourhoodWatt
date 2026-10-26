from database_v2.data_access_objects.house_dao import HouseConfigDao
from tests.factories import HouseConfigFactory


def test_save_and_get_house_config(test_db):
    """Tests inserting, updating, and retrieving a house config."""
    dao = HouseConfigDao(test_db)

    config = HouseConfigFactory.build()

    # Insert
    dao.save_house_config(config)
    fetched = dao.get_house_config(config.house_config_id)

    assert fetched is not None
    assert fetched.pv_peak_capacity == config.pv_peak_capacity
    assert fetched.panel_azimuth == config.panel_azimuth

    updated = config.model_copy(update={"pv_peak_capacity": 10.0})
    dao.save_house_config(updated)

    fetched_again = dao.get_house_config(config.house_config_id)
    assert fetched_again.pv_peak_capacity == 10.0


def test_get_all_house_configs(test_db):
    """Tests retrieving all registered house configs."""
    dao = HouseConfigDao(test_db)

    config_1 = HouseConfigFactory.build(
        house_config_id="preset_A", pv_peak_capacity=5.0
    )
    config_2 = HouseConfigFactory.build(
        house_config_id="preset_B", pv_peak_capacity=10.0
    )

    dao.save_house_config(config_1)
    dao.save_house_config(config_2)

    all_configs = dao.get_all_house_configs()

    assert len(all_configs) == 2
    assert all_configs[0].house_config_id == "preset_A"
    assert all_configs[1].house_config_id == "preset_B"


def test_get_missing_house_config(test_db):
    """Tests that querying a non-existent config returns None without error."""
    dao = HouseConfigDao(test_db)
    assert dao.get_house_config("nonexistent_id") is None
