"""tests/frontend_v2/ui/test_form_fields.py
The Settings page's FormViews submit exactly the fields their setters save.

FormView submits the id plus one value per FormField. A FormField the setter does
not declare, or a setter field with no FormField, would fail at save time, so the
two lists are pinned together here. No widget is built, so no QApplication is
needed.
"""

from database_v2.data_access_objects.frontend_dao import FrontendDao
from frontend_v2.ui.form_fields import BATTERY_CONFIG_FIELDS, HOUSE_CONFIG_FIELDS


def test_battery_config_fields_match_its_setter():
    shown = {"battery_config_id"} | {field.key for field in BATTERY_CONFIG_FIELDS}
    assert shown == set(FrontendDao.set_battery_config.dtype.names)


def test_house_config_fields_match_its_setter():
    shown = {"house_config_id"} | {field.key for field in HOUSE_CONFIG_FIELDS}
    assert shown == set(FrontendDao.set_house_config.dtype.names)
