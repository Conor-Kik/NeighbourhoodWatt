import numpy as np
import pytest

from frontend_v2.utils.buffer import Buffer, RawBuffer, SparseBuffer

FORECAST_DTYPE = [
    ("run_time_utc", "U32"),
    ("target_time_utc", "U32"),
    ("demand_kw", "f8"),
]
FORECAST_PK = ("run_time_utc", "target_time_utc")


def forecast_row(run: str, target: str, demand_kw: float) -> tuple:
    return (
        f"2024-01-{run:02d}T00:00:00+00:00",
        f"2024-01-{target:02d}T12:00:00+00:00",
        demand_kw,
    )


def test_buffer_returns_records_ordered_by_composite_key():
    buffer = Buffer(FORECAST_DTYPE, FORECAST_PK)

    buffer.add(
        [forecast_row(3, 4, 1.0), forecast_row(1, 2, 2.0), forecast_row(2, 3, 3.0)]
    )

    assert buffer.get()[["run_time_utc", "target_time_utc"]].tolist() == [
        ("2024-01-01T00:00:00+00:00", "2024-01-02T12:00:00+00:00"),
        ("2024-01-02T00:00:00+00:00", "2024-01-03T12:00:00+00:00"),
        ("2024-01-03T00:00:00+00:00", "2024-01-04T12:00:00+00:00"),
    ]
    assert buffer.get()["demand_kw"].tolist() == [2.0, 3.0, 1.0]


def test_buffer_replaces_the_stored_record_when_the_composite_key_matches():
    buffer = Buffer(FORECAST_DTYPE, FORECAST_PK)

    buffer.add([forecast_row(1, 2, 2.0)])
    buffer.add([forecast_row(1, 2, 9.0), forecast_row(1, 3, 4.0)])

    stored = buffer.get()
    assert len(stored) == 2
    assert stored["demand_kw"].tolist() == [9.0, 4.0]


def test_buffer_add_takes_the_whole_fetch_in_one_call():
    buffer = Buffer(FORECAST_DTYPE, FORECAST_PK)

    buffer.add([forecast_row(day, day + 1, float(day)) for day in range(1, 13)])

    assert len(buffer) == 12


def test_buffer_drops_the_lowest_key_once_capacity_is_reached():
    buffer = Buffer(FORECAST_DTYPE, FORECAST_PK, maxlen=4)

    buffer.add([forecast_row(day, day + 1, float(day)) for day in range(1, 7)])

    assert len(buffer) == 3
    assert buffer.dropped == 3
    assert buffer.get()["demand_kw"].tolist() == [2.0, 4.0, 6.0]


def test_buffer_keeps_the_newest_record_when_it_spreads():
    buffer = Buffer(FORECAST_DTYPE, FORECAST_PK, maxlen=4)

    buffer.add([forecast_row(day, day + 1, float(day)) for day in range(1, 6)])

    assert buffer.get()["demand_kw"].tolist() == [1.0, 3.0, 5.0]
    assert buffer.stride == 2
    assert buffer.dropped == 2


def test_buffer_get_is_empty_before_the_first_add():
    buffer = Buffer(FORECAST_DTYPE, FORECAST_PK)

    assert len(buffer) == 0
    assert buffer.get().shape == (0,)

    buffer.clear()

    assert len(buffer) == 0


def test_buffer_rejects_a_primary_key_that_is_not_the_leading_dtype_fields():
    with pytest.raises(ValueError, match="leading dtype fields"):
        Buffer(FORECAST_DTYPE, ("target_time_utc", "demand_kw"))


def test_buffer_rejects_a_record_that_does_not_match_the_dtype():
    buffer = Buffer(FORECAST_DTYPE, FORECAST_PK)

    with pytest.raises(ValueError, match="expected 3 fields"):
        buffer.add([("2024-01-01T00:00:00+00:00", "2024-01-02T12:00:00+00:00")])


def test_raw_buffer_grows_by_the_number_of_records_given():
    raw = RawBuffer(np.dtype(FORECAST_DTYPE))

    assert raw.capacity == 0

    raw.extend([forecast_row(day, day + 1, float(day)) for day in range(1, 6)])

    assert len(raw) == 5
    assert raw.capacity == 6


def test_raw_buffer_sorts_records_on_placement():
    raw = RawBuffer(np.dtype(FORECAST_DTYPE))

    raw.extend([forecast_row(3, 4, 1.0)])
    raw.extend([forecast_row(1, 2, 2.0), forecast_row(2, 3, 3.0)])

    assert raw.view()["demand_kw"].tolist() == [2.0, 3.0, 1.0]


def test_raw_buffer_pops_by_position_and_closes_the_gap():
    raw = RawBuffer(np.dtype(FORECAST_DTYPE))

    raw.extend([forecast_row(day, day + 1, float(day)) for day in range(1, 5)])
    raw.pop()
    raw.pop(0, 2)

    assert raw.view()["demand_kw"].tolist() == [4.0]

    with pytest.raises(IndexError, match="cannot pop"):
        raw.pop(0, 2)


def test_sparse_buffer_keeps_every_stride_th_point():
    sparse = SparseBuffer(np.dtype(FORECAST_DTYPE))

    sparse.extend([forecast_row(day, day + 1, float(day)) for day in range(1, 9)])
    sparse.set_stride(3)

    assert sparse.view()["demand_kw"].tolist() == [2.0, 5.0, 8.0]
    assert sparse.stride == 3
    assert sparse.offset == 1


def test_sparse_buffer_spread_halves_records_and_keeps_the_newest():
    sparse = SparseBuffer(np.dtype(FORECAST_DTYPE))

    sparse.extend([forecast_row(day, day + 1, float(day)) for day in range(1, 9)])
    sparse.spread()

    assert sparse.view()["demand_kw"].tolist() == [2.0, 4.0, 6.0, 8.0]
    assert sparse.stride == 2


def test_repeated_spreads_halve_remaining_records():
    sparse = SparseBuffer(np.dtype([("id", "i4")]))
    sparse.extend([(i,) for i in range(16)])
    sparse.spread()
    sparse.spread()
    assert sparse.view()["id"].tolist() == [3, 7, 11, 15]
    assert sparse.stride == 4


def test_repeated_snapshot_replacement_preserves_resolution():
    buffer = Buffer([("id", "i4")], "id", maxlen=8)
    rows = [(i,) for i in range(32)]
    buffer.replace(rows)
    expected = buffer.get().copy()
    for _ in range(12):
        buffer.replace(rows)
        np.testing.assert_array_equal(buffer.get(), expected)
    assert 0 < len(buffer) < buffer.maxlen
    assert buffer.dropped == len(rows) - len(buffer)


def test_clear_restores_sampling_for_a_new_run():
    buffer = Buffer([("id", "i4")], "id", maxlen=8)
    buffer.add([(i,) for i in range(32)])
    buffer.clear()
    assert buffer.stride == 1
    assert buffer.dropped == 0
    buffer.add([(i,) for i in range(8)])
    assert buffer.get()["id"].tolist() == [1, 3, 5, 7]


def test_empty_snapshot_removes_previous_records():
    buffer = Buffer([("id", "i4")], "id")
    buffer.add([(1,)])
    buffer.replace([])
    assert len(buffer) == 0


def test_spreading_releases_the_unsampled_storage():
    sparse = SparseBuffer(np.dtype([("id", "i4")]))
    sparse.extend([(i,) for i in range(4096)])
    sparse.spread()
    assert len(sparse) == sparse._raw.capacity == 2048
    assert sparse.view()["id"][-1] == 4095


def test_snapshot_digest_is_invalidated_by_add_and_clear():
    buffer = Buffer([("id", "i4"), ("value", "f8")], "id")
    rows = [(1, 2.0)]
    assert buffer.replace(rows)
    assert not buffer.replace(rows)
    buffer.add([(2, 3.0)])
    assert buffer.replace(rows)
    assert buffer.get().tolist() == rows
    buffer.clear()
    assert buffer.replace(rows)


def test_identical_nan_snapshot_does_not_count_as_a_change():
    buffer = Buffer([("id", "i4"), ("value", "f8")], "id")
    assert buffer.replace([(1, float("nan"))])
    assert not buffer.replace([(1, float("nan"))])
