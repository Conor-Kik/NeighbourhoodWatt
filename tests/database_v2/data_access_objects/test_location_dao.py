from database_v2.data_access_objects.location_dao import LocationDao
from tests.factories import LocationFactory


def test_save_and_get_location(test_db):
    """Tests inserting, updating, and retrieving a location."""
    dummy_location = LocationFactory.build()
    dao = LocationDao(test_db)

    dao.save_location(dummy_location)
    fetched = dao.get_location(dummy_location.location_name)

    assert fetched is not None
    assert fetched.latitude == dummy_location.latitude
    assert fetched.nem_region == dummy_location.nem_region

    updated_location = dummy_location.model_copy(update={"latitude": -27.50})
    dao.save_location(updated_location)

    fetched_again = dao.get_location(dummy_location.location_name)
    assert fetched_again.latitude == -27.50


def test_get_all_locations(test_db):
    """Tests retrieving all registered locations sorted alphabetically."""
    dao = LocationDao(test_db)

    loc_sydney = LocationFactory.build(location_name="Sydney")
    loc_adelaide = LocationFactory.build(location_name="Adelaide")

    dao.save_location(loc_sydney)
    dao.save_location(loc_adelaide)

    all_locations = dao.get_all_locations()

    assert len(all_locations) == 2
    assert all_locations[0].location_name == "Adelaide"
    assert all_locations[1].location_name == "Sydney"


def test_get_missing_location(test_db):
    """Tests that querying a non-existent location returns None."""
    dao = LocationDao(test_db)
    assert dao.get_location("Atlantis") is None
