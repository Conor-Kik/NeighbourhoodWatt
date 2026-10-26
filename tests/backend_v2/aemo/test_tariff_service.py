from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from backend_v2.aemo.tariff_service import TariffService
from database_v2.models.request_models import NemRegion


def test_brisbane_peak_import_and_no_export_tariff() -> None:
    service = TariffService()
    local_time = datetime(2026, 8, 20, 17, 30, tzinfo=ZoneInfo("Australia/Brisbane"))

    rates = service.rates_for("brisbane", local_time.astimezone(UTC))

    assert rates.import_aud_per_kwh == pytest.approx(0.26318)
    assert rates.export_aud_per_kwh == 0.0


def test_sydney_resolves_local_time_and_export_reward() -> None:
    service = TariffService()
    local_time = datetime(2026, 1, 15, 17, 0, tzinfo=ZoneInfo("Australia/Sydney"))

    rates = service.rates_for("sydney", local_time.astimezone(UTC))

    assert rates.import_aud_per_kwh == pytest.approx(0.3489208687021483)
    assert rates.export_aud_per_kwh == pytest.approx(0.038551)


def test_tariff_lookup_validates_location_and_timezone() -> None:
    service = TariffService()

    with pytest.raises(ValueError, match="timezone-aware"):
        service.rates_for("brisbane", datetime(2026, 8, 20, 17, 30))
    with pytest.raises(ValueError, match="Unknown tariff location"):
        service.rates_for("unknown", datetime(2026, 8, 20, 7, 30, tzinfo=UTC))


@pytest.mark.parametrize(
    "region,city",
    [
        (NemRegion.QLD, "brisbane"),
        (NemRegion.NSW, "sydney"),
        (NemRegion.VIC, "melbourne"),
        (NemRegion.SA, "adelaide"),
        (NemRegion.TAS, "hobart"),
    ],
)
def test_custom_names_use_existing_regional_representative_profile(region, city):
    service = TariffService()
    moment = datetime(2026, 1, 15, 6, tzinfo=UTC)
    assert service.rates_for("My custom location", moment, nem_region=region) == (
        service.rates_for(city, moment)
    )


def test_named_canberra_profile_is_preserved_despite_sharing_nsw_region():
    service = TariffService()
    moment = datetime(2026, 1, 15, 6, tzinfo=UTC)
    canberra = service.rates_for("Canberra", moment, nem_region=NemRegion.NSW)
    assert canberra == service.rates_for("canberra", moment)
    assert canberra != service.rates_for(
        "Custom NSW site", moment, nem_region=NemRegion.NSW
    )
