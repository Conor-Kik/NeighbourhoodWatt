"""Measures disk space used by downloaded AEMO files and removes them when requested."""

from collections.abc import Iterator
from pathlib import Path

from backend_v2.aemo.aemo_constants import AEMO_CACHE_DIR


def _walk_error(error: OSError) -> None:
    """Raise file-access errors so cache inspection cannot silently skip failures."""
    raise error


def _cache_files() -> Iterator[Path]:
    """Find regular cache files without following links outside the cache folder."""
    root = AEMO_CACHE_DIR.absolute()
    # A redirected cache must never turn cleanup into deletion elsewhere.
    if root.resolve() != root or root.is_symlink() or root.is_junction():
        raise ValueError("AEMO cache is redirected; cleanup is unavailable.")
    if not root.exists():
        return
    for directory, folders, files in root.walk(
        follow_symlinks=False, on_error=_walk_error
    ):
        folders[:] = [
            name
            for name in folders
            if not (directory / name).is_symlink()
            and not (directory / name).is_junction()
        ]
        for name in files:
            path = directory / name
            if (
                not path.is_symlink()
                and not path.is_junction()
                and path.resolve().is_relative_to(root)
                and path.is_file()
            ):
                yield path


def aemo_cache_size() -> int:
    """Total bytes in regular cache files; links and junctions are excluded."""
    return sum(path.stat().st_size for path in _cache_files())


def clear_aemo_cache() -> int:
    """Remove cache files only, retaining the directory for later downloads.

    The caller must prevent simulations/downloads during this operation. Errors
    propagate so a partial cleanup cannot be reported as successful.
    """
    for path in _cache_files():
        path.unlink()
    return aemo_cache_size()
