import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QWidget

from frontend_v2.ui.views.form_view import FormField, FormView
from frontend_v2.utils.payload import RecordsPayload

APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("existing_read_only", [False, True])
@pytest.mark.parametrize("scrollable", [False, True])
def test_construction_does_not_show_standalone_controls(existing_read_only, scrollable):
    app = QApplication.instance()
    shown_windows = []

    class ShowObserver(QObject):
        def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
            if (
                event.type() == QEvent.Type.Show
                and isinstance(watched, QWidget)
                and watched.isWindow()
            ):
                shown_windows.append(type(watched).__name__)
            return False

    observer = ShowObserver()
    app.installEventFilter(observer)
    try:
        view = FormView(
            "Configs",
            "Help",
            "Empty",
            "id",
            "ID",
            [],
            existing_read_only=existing_read_only,
            scrollable=scrollable,
        )
        assert shown_windows == []
        view.show_empty()
        view.show()
        app.processEvents()
        assert shown_windows == ["FormView"]
        assert view._create.isVisible() == existing_read_only
        assert view._mode.isVisible() == existing_read_only
        assert not hasattr(view, "_edit")
        view.close()
    finally:
        app.removeEventFilter(observer)


def make_view():
    return FormView(
        "Configs",
        "Add a config",
        "No configs",
        "id",
        "ID",
        [FormField("capacity", "Capacity")],
        existing_read_only=True,
    )


def test_empty_config_table_still_allows_creating_the_first_record():
    view = make_view()
    view.show_empty()
    assert view._body.currentWidget() is view._content
    view._picker.setEditText("first")
    view._inputs["capacity"].setValue(10)
    saved = []
    view.submitted.connect(saved.append)
    view._apply.click()
    assert saved == [{"id": "first", "capacity": 10}]


def test_successful_create_becomes_read_only_on_refetch():
    view = make_view()
    view.show_empty()
    view._picker.setEditText("first")
    view.show_payload(RecordsPayload({"first": {"capacity": 10}}))
    assert not view._apply.isEnabled()
    assert not view._inputs["capacity"].isEnabled()
    view._picker.setEditText("second")
    assert view._apply.isEnabled()


def test_create_button_starts_editable_copy_and_preserves_unnamed_draft_on_refresh():
    view = make_view()
    saved = []
    view.submitted.connect(saved.append)
    records = RecordsPayload({"existing": {"capacity": 10}})
    view.show_payload(records)
    assert not view._inputs["capacity"].isEnabled()
    view._create.click()
    assert view._picker.currentText() == ""
    assert view._inputs["capacity"].value() == 10
    assert view._inputs["capacity"].isEnabled()
    view._inputs["capacity"].setValue(25)
    view.show_payload(records)
    assert view._picker.currentText() == ""
    assert view._inputs["capacity"].value() == 25
    view._picker.setEditText("My custom configuration")
    view._apply.click()
    assert saved == [{"id": "My custom configuration", "capacity": 25}]
    view.show_payload(
        RecordsPayload({**records.records, "My custom configuration": {"capacity": 25}})
    )
    assert not view._inputs["capacity"].isEnabled()
    assert not view._apply.isEnabled()
    view.close()


def test_create_button_stays_available_after_starting_a_new_config():
    view = make_view()
    records = RecordsPayload({"existing": {"capacity": 10}})
    view.show_payload(records)

    view._create.click()
    assert not view._create.isHidden()
    view._picker.setCurrentIndex(view._picker.findData("existing"))
    assert not view._create.isHidden()
    assert not view._inputs["capacity"].isEnabled()
    assert not view._apply.isEnabled()
    view.close()


def test_existing_configs_cannot_be_overwritten():
    view = make_view()
    view.show_payload(RecordsPayload({"existing": {"capacity": 10}}))
    saved = []
    view.submitted.connect(saved.append)

    view._creating = True
    view._picker.blockSignals(True)
    view._picker.setEditText("existing")
    view._picker.blockSignals(False)
    view._inputs["capacity"].setValue(25)
    view._submit()

    assert saved == []
    assert "read-only" in view._status.text()
    view.close()


def test_display_labels_preserve_saved_ids_and_read_only_rules():
    view = make_view()
    records = RecordsPayload(
        {"raw-id": {"capacity": 10}}, {"raw-id": "Readable name · 10 kWh"}
    )
    view.show_payload(records)
    assert view._picker.currentText() == "Readable name · 10 kWh"
    assert view._record_id() == "raw-id"
    assert not view._inputs["capacity"].isEnabled()
    assert "read-only" in view._mode.text()
    view._create.click()
    view._picker.setEditText("My custom name")
    view._inputs["capacity"].setValue(25)
    view.show_payload(records)
    assert view._record_id() == "My custom name"
    assert view._inputs["capacity"].value() == 25
    assert view._inputs["capacity"].isEnabled()
    saved = []
    view.submitted.connect(saved.append)
    view._apply.click()
    assert saved == [{"id": "My custom name", "capacity": 25}]
    view.close()
