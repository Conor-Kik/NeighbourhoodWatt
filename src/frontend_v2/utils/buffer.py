"""utils/buffer.py
Holds a piece of data and announces change via signal emission."""

from collections.abc import Sequence
from hashlib import blake2b
from typing import Any

import numpy as np

DEFAULT_MAXLEN = 4096  # high-quality on a laptop screen
CAPACITY_SLACK = 4  # extra slots per extend, as a fraction of records needed


class RawBuffer:
    """Storage and memory movement only: sorted append, pop, view.

    Knows nothing about primary keys, deduplication, or capacity policy.
    Records stay sorted by dtype field order, which numpy resolves
    lexicographically, so the leading dtype fields are the sort key.
    """

    def __init__(self, dtype: np.dtype):
        self._dtype = np.dtype(dtype)
        self._slots = np.empty(0, dtype=self._dtype)
        self._size = 0

    def __len__(self) -> int:
        return self._size

    @property
    def capacity(self) -> int:
        return self._slots.size

    def extend(self, records: Sequence[tuple]) -> None:
        """Merge records in and re-sort, growing capacity by the batch size.

        The DAO path hands over records already ordered by the sort key
        (SQL ends in ORDER BY the same fields), so the common case is one
        merge pass. Use B+ tree if too slow.
        """
        if not records:
            return

        incoming = np.array(records, dtype=self._dtype)
        needed = self._size + incoming.size
        if needed > self.capacity:
            grown = np.empty(
                needed + max(1, needed // CAPACITY_SLACK), dtype=self._dtype
            )
            grown[: self._size] = self._slots[: self._size]
            self._slots = grown

        self._slots[self._size : needed] = incoming
        self._size = needed
        self._slots[: self._size].sort()

    def pop(self, index: int = 0, count: int = 1) -> None:
        """Remove count records at index, closing the gap they leave behind."""
        if index < 0 or count < 1 or index + count > self._size:
            raise IndexError(
                f"cannot pop {count} record(s) at index {index} "
                f"from {self._size} stored"
            )

        self._slots[index : self._size - count] = self._slots[
            index + count : self._size
        ]
        self._size -= count

    def view(self) -> np.ndarray:
        """Stored records, sorted, as a view into the slots."""
        return self._slots[: self._size]

    def keep_every(self, stride: int) -> None:
        """Compact sampled records in one copy, retaining the newest record."""
        if stride < 1:
            raise ValueError("RawBuffer needs a positive stride")
        offset = (self._size - 1) % stride
        self._slots = self.view()[offset::stride].copy()
        self._size = self._slots.size


class SparseBuffer:
    """Samples every stride-th record of a RawBuffer, anchored at the newest.

    The newest record is always sampled, so the live edge of a plot never
    loses resolution as older records are thinned out.
    """

    def __init__(self, dtype: np.dtype):
        self._raw = RawBuffer(dtype)
        self._stride = 1
        self._sample_step = 1

    def __len__(self) -> int:
        return len(self._raw)

    @property
    def stride(self) -> int:
        return self._stride

    @property
    def offset(self) -> int:
        """Index of the first sampled record in stored order."""
        return (len(self._raw) - 1) % self._sample_step

    def extend(self, records: Sequence[tuple]) -> None:
        self._raw.extend(records)

    def pop(self, index: int = 0, count: int = 1) -> None:
        self._raw.pop(index, count)

    def set_stride(self, stride: int) -> None:
        """Resample every stride-th record without touching storage."""
        if stride < 1:
            raise ValueError(f"SparseBuffer needs a positive stride, got {stride}")
        self._stride = stride
        self._sample_step = stride

    def spread(self) -> None:
        """Double the stride and physically drop the skipped records.

        Halving the stored records is what keeps a long run bounded: the
        buffer only spreads again once it doubles back up to maxlen.
        """
        self._stride *= 2
        self._drop_unsampled()
        self._sample_step = 1

    def _drop_unsampled(self) -> None:
        """Halve storage in one copy instead of shifting it for every removal."""
        self._raw.keep_every(2)

    def view(self) -> np.ndarray:
        """Sampled records, sorted, oldest first."""
        return self._raw.view()[self.offset : None : self._sample_step]


class Buffer:
    """Policy wrapper: validates records, keeps the newest per primary key,
    and spreads the SparseBuffer once maxlen is reached."""

    def __init__(
        self,
        dtype: list[tuple[Any, Any]],
        pk: str | tuple[str, ...],
        maxlen: int = DEFAULT_MAXLEN,
    ):
        self._dtype = np.dtype(dtype)
        self._pk = (pk,) if isinstance(pk, str) else tuple(pk)
        self._maxlen = maxlen
        self._snapshot_digest: bytes | None = None
        self._dropped = 0
        if maxlen < 2:
            raise ValueError("Buffer maxlen must be at least 2")

        field_names = self._dtype.names or ()
        if not self._pk or self._pk != field_names[: len(self._pk)]:
            raise ValueError(
                f"Primary key must be the leading dtype fields: got {self._pk}, "
                f"leading fields are {field_names[: len(self._pk)]}"
            )

        self._sparse = SparseBuffer(self._dtype)

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    @property
    def pk(self) -> tuple[str, ...]:
        return self._pk

    @property
    def maxlen(self) -> int:
        return self._maxlen

    @property
    def dropped(self) -> int:
        """Records forgotten by spreading since the buffer was last cleared."""
        return self._dropped

    @property
    def stride(self) -> int:
        return self._sparse.stride

    def __len__(self) -> int:
        return len(self._sparse)

    def add(self, records: list[tuple]) -> None:
        """Store a whole fetch: newest wins on duplicate keys, then spread if full."""
        if not records:
            return

        self._snapshot_digest = None
        self._check_shape(records)
        newest_per_key = self._newest_per_key(records)
        self._remove_keys(set(newest_per_key))
        self._sparse.extend(list(newest_per_key.values()))
        self._spread_if_full()

    def clear(self) -> None:
        """Forget every stored record."""
        self._sparse = SparseBuffer(self._dtype)
        self._dropped = 0
        self._snapshot_digest = None

    def replace(self, records: list[tuple]) -> bool:
        """Replace a changed snapshot, returning whether it changed.

        A digest avoids retaining another full snapshot beside sampled history.
        Direct additions and clears invalidate it.
        """
        self._check_shape(records)
        # Validate conversions before discarding the previous snapshot.
        incoming = np.array(records, dtype=self._dtype)
        digest = blake2b(incoming.tobytes(), digest_size=16).digest()
        if digest == self._snapshot_digest:
            return False
        self.clear()
        self.add(records)
        self._snapshot_digest = digest
        return True

    def get(self) -> np.recarray:
        """Stored records, sorted by primary key, oldest first."""
        return np.ascontiguousarray(self._sparse.view()).view(np.recarray)

    def _check_shape(self, records: list[tuple]) -> None:
        """Fail loud when a fetch does not match the dtype."""
        expected = len(self._dtype.names or ())
        for index, record in enumerate(records):
            if len(record) != expected:
                raise ValueError(
                    f"Record {index} expected {expected} fields, got {len(record)}"
                )

    def _newest_per_key(self, records: list[tuple]) -> dict[tuple, tuple]:
        """Last record wins per primary key, so a re-fetch overwrites in place."""
        newest: dict[tuple, tuple] = {}
        for record in records:
            newest[record[: len(self._pk)]] = record
        return newest

    def _keys_of(self, stored: np.ndarray) -> list[tuple]:
        """Primary key of every stored record, as plain Python tuples."""
        return list(zip(*[stored[name] for name in self._pk], strict=False))

    def _remove_keys(self, keys: set[tuple]) -> None:
        """Drop stored records whose key is being replaced."""
        stale = [
            index
            for index, key in enumerate(self._keys_of(self._sparse.view()))
            if key in keys
        ]
        for index in reversed(stale):
            self._sparse.pop(index, 1)

    def _spread_if_full(self) -> None:
        """Thin the buffer once it reaches maxlen, counting what was forgotten."""
        while len(self) >= self.maxlen:
            stored_before = len(self)
            self._sparse.spread()
            self._dropped += stored_before - len(self)
