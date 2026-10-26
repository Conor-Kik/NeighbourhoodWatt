"""Postcodes prefill the existing location form without changing its save shape."""

import pytest

from database_v2.data_access_objects.frontend_dao import FrontendDao
from frontend_v2.ui.pages.setup.location_editor import LocationEditor
from tests.frontend_v2.ui.test_settings_page import APP, FakeManager


@pytest.fixture
def editor():
    widget = LocationEditor(FakeManager())
    yield widget
    widget.close()
    APP.processEvents()


def choose(editor, postcode, suburb):
    editor.postcode.setText(postcode)
    index = editor.locality.findText(suburb)
    assert index > 0
    editor.locality.setCurrentIndex(index)


def test_create_button_starts_new_location_from_read_only_selection(editor):
    from tests.frontend_v2.bindings.selectors.test_community_objectives import records

    editor.data_manager.values["get_all_locations"] = records(
        "get_all_locations",
        [
            {
                "location_name": "Brisbane",
                "latitude": -27.47,
                "longitude": 153.02,
                "timezone": "Australia/Brisbane",
                "nem_region": "QLD1",
            }
        ],
    )
    editor.on_data_updated({"get_all_locations"})
    editor.selector_combo.setCurrentText("Brisbane")
    assert editor.inputs["location_name"].isReadOnly()
    assert not editor.save_btn.isEnabled()
    assert editor.create_btn.text() == "Create new"
    editor.create_btn.click()
    assert editor.inputs["location_name"].text() == ""
    assert not editor.inputs["location_name"].isReadOnly()
    assert editor.save_btn.isEnabled()
    assert not editor.lookup_widget.isHidden()
    editor.inputs["location_name"].setText("My location")
    editor.on_data_updated({"get_all_locations"})
    assert editor.inputs["location_name"].text() == "My location"


def test_multiple_suburbs_require_choice_and_save_only_existing_fields(editor):
    editor.inputs["location_name"].setText("My draft")
    editor.postcode.setText("4059")
    assert editor.locality.count() > 2
    assert editor.inputs["location_name"].text() == "My draft"
    editor.locality.setCurrentIndex(editor.locality.findText("Kelvin Grove · QLD"))
    assert editor.inputs["location_name"].text() == "Kelvin Grove"
    assert editor.inputs["latitude"].text() == "-27.450"
    assert editor.inputs["longitude"].text() == "153.010"
    assert editor.inputs["timezone"].text() == "Australia/Brisbane"
    assert editor.inputs["nem_region"].text() == "QLD1"
    editor.inputs["latitude"].setText("-27.451")  # Approximate values stay editable.
    editor.on_save()
    row = editor.data_manager.writes[-1]["set_location"][0]
    assert set(row) == set(FrontendDao.set_location.dtype.names)
    assert row["latitude"] == "-27.451"


@pytest.mark.parametrize(
    "postcode,suburb,timezone",
    [
        ("2880", "Broken Hill · NSW", "Australia/Broken_Hill"),
        ("2898", "Lord Howe Island · NSW", "Australia/Lord_Howe"),
    ],
)
def test_coordinate_timezone_handles_exceptions_to_state_default(
    editor, postcode, suburb, timezone
):
    choose(editor, postcode, suburb)
    assert editor.inputs["timezone"].text() == timezone
    assert editor.inputs["nem_region"].text() == "NSW1"


def test_missing_coordinates_clear_previous_values_and_allow_manual_entry(editor):
    choose(editor, "4059", "Kelvin Grove · QLD")
    choose(editor, "4000", "Brisbane City · QLD")
    assert editor.inputs["location_name"].text() == "Brisbane City"
    assert editor.inputs["latitude"].text() == ""
    assert editor.inputs["longitude"].text() == ""
    assert "unavailable" in editor.lookup_status.text()


@pytest.mark.parametrize("postcode", ["0000", "0200", "0800", "6000"])
def test_unknown_po_box_or_unsupported_postcodes_preserve_manual_draft(
    editor, postcode
):
    editor.inputs["location_name"].setText("My draft")
    editor.postcode.setText(postcode)
    if editor.locality.count() > 1:
        editor.locality.setCurrentIndex(1)
    assert editor.postcode.text() == postcode  # Leading zeros stay intact.
    assert editor.inputs["location_name"].text() == "My draft"
    assert (
        "not found" in editor.lookup_status.text()
        or "outside" in editor.lookup_status.text()
    )


def test_lookup_draft_survives_refresh_and_saved_locations_remain_read_only(editor):
    choose(editor, "2880", "Broken Hill · NSW")
    editor.data_manager.updated_keys.emit({"get_all_locations"})
    assert editor.postcode.text() == "2880"
    assert editor.inputs["timezone"].text() == "Australia/Broken_Hill"
    record = {field: widget.text() for field, widget in editor.inputs.items()}
    editor.records_cache["Broken Hill"] = record
    editor.on_selection_changed("Broken Hill")
    assert editor.lookup_widget.isHidden()
    assert editor.inputs["latitude"].isReadOnly()
    assert not editor.save_btn.isEnabled()
    assert editor.state_combo.currentText() == "NSW"
