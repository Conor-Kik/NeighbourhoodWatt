import pytest

from database_v2.utils.register_dtype import register_dtype


def test_pk_defaults_to_leading_dtype_field():
    """A getter without a declared pk exposes pk as its leading dtype field."""

    @register_dtype([("id", "i4"), ("value", "i4")])
    def get_values():
        return [{"id": 1, "value": 2}]

    assert get_values.pk == ("id",)
    assert get_values() == [(1, 2)]


def test_string_pk_is_normalised_to_a_one_tuple():
    """A pk given as a bare field name is stored as a one-tuple."""

    @register_dtype([("id", "i4"), ("value", "i4")], pk="value")
    def get_values():
        return [{"id": 1, "value": 2}]

    assert get_values.pk == ("value",)


def test_composite_pk_is_kept_in_declaration_order():
    """A composite pk names several fields and keeps the declared order."""

    @register_dtype(
        [
            ("run_time_utc", "U32"),
            ("target_time_utc", "U32"),
            ("demand_kw", "f8"),
        ],
        pk=("run_time_utc", "target_time_utc"),
    )
    def get_current_forecast_data():
        return [
            {
                "run_time_utc": "2026-10-09T00:00:00+00:00",
                "target_time_utc": "2026-10-09T00:30:00+00:00",
                "demand_kw": 1.5,
            },
            {
                "run_time_utc": "2026-10-09T00:00:00+00:00",
                "target_time_utc": "2026-10-09T01:00:00+00:00",
                "demand_kw": 2.5,
            },
        ]

    assert get_current_forecast_data.pk == ("run_time_utc", "target_time_utc")
    assert get_current_forecast_data() == [
        ("2026-10-09T00:00:00+00:00", "2026-10-09T00:30:00+00:00", 1.5),
        ("2026-10-09T00:00:00+00:00", "2026-10-09T01:00:00+00:00", 2.5),
    ]


def test_pk_rejects_fields_the_dtype_does_not_have():
    """pk must name dtype fields, and the error names the offending fields."""

    with pytest.raises(ValueError, match="Unknown pk fields given to register_dtype"):
        register_dtype([("id", "i4")], pk=("missing",))


def test_pk_rejects_duplicate_fields():
    """pk fields must be distinct, a repeated field is a declaration error."""

    with pytest.raises(ValueError, match="Duplicate pk fields"):
        register_dtype([("id", "i4")], pk=("id", "id"))
