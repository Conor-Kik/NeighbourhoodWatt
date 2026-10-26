"""Cache cleanup is restricted to regular AEMO cache files."""

from pathlib import Path

import pytest

from backend_v2.aemo import cache_storage


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path.resolve() / ".cache" / "aemo"
    monkeypatch.setattr(cache_storage, "AEMO_CACHE_DIR", root)
    return root


def test_absent_cache_is_empty_and_not_created(cache):
    assert cache_storage.aemo_cache_size() == 0
    assert cache_storage.clear_aemo_cache() == 0
    assert not cache.exists()


def test_counts_and_clears_nested_cache_without_touching_other_data(cache):
    (cache / "raw").mkdir(parents=True)
    (cache / "archive.zip").write_bytes(b"a" * 100)
    (cache / "raw" / "table.csv").write_bytes(b"b" * 200)
    database = cache.parent.parent / "app_database.db"
    database.write_bytes(b"saved runs")
    other_cache = cache.parent / "weather.cache"
    other_cache.write_bytes(b"weather")
    assert cache_storage.aemo_cache_size() == 300
    assert cache_storage.clear_aemo_cache() == 0
    assert cache.is_dir()
    assert database.read_bytes() == b"saved runs"
    assert other_cache.read_bytes() == b"weather"


def test_links_are_not_followed_or_deleted(cache):
    cache.mkdir(parents=True)
    outside = cache.parent / "outside"
    outside.mkdir()
    (outside / "keep.db").write_bytes(b"keep")
    try:
        (cache / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    assert cache_storage.aemo_cache_size() == 0
    assert cache_storage.clear_aemo_cache() == 0
    assert (outside / "keep.db").read_bytes() == b"keep"
    assert (cache / "link").is_symlink()


def test_redirected_cache_root_is_rejected(cache):
    outside = cache.parent / "outside"
    outside.mkdir(parents=True)
    try:
        cache.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    with pytest.raises(ValueError, match="redirected"):
        cache_storage.clear_aemo_cache()


def test_delete_failure_is_reported(cache, monkeypatch):
    cache.mkdir(parents=True)
    locked = cache / "locked.zip"
    locked.write_bytes(b"still here")
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == locked:
            raise PermissionError("File is in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError, match="in use"):
        cache_storage.clear_aemo_cache()
    assert locked.exists()
