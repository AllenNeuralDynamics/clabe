"""Tests for cached settings manager with auto-sync capabilities."""

import tempfile
from pathlib import Path

from clabe.cache_manager import CachedSettings, CacheManager, SyncStrategy
from clabe.cache_manager import _DEFAULT_MAX_HISTORY

class TestCachedSettings:
    """Tests for the generic CachedSettings class."""

    def test_add_single_value(self):
        """Test adding a single value to cache."""
        cache = CachedSettings[str](max_history=3)
        cache.add("first")
        assert cache.get_all() == ["first"]
        assert cache.get_latest() == "first"

    def test_add_multiple_values(self):
        """Test adding multiple values maintains order (newest first)."""
        cache = CachedSettings[str](max_history=_DEFAULT_MAX_HISTORY)
        cache.add("first")
        cache.add("second")
        cache.add("third")
        assert cache.get_all() == ["third", "second", "first"]
        assert cache.get_latest() == "third"

    def test_max_history_limit(self):
        """Test that oldest values are removed when limit is exceeded."""
        cache = CachedSettings[str](max_history=3)
        cache.add("first")
        cache.add("second")
        cache.add("third")
        cache.add("fourth")  # Should remove "first"

        assert cache.get_all() == ["fourth", "third", "second"]
        assert len(cache.get_all()) == 3

    def test_duplicate_values_moved_to_front(self):
        """Test that adding a duplicate moves it to the front."""
        cache = CachedSettings[str](max_history=_DEFAULT_MAX_HISTORY)
        cache.add("first")
        cache.add("second")
        cache.add("third")
        cache.add("first")  # Should move "first" to front

        assert cache.get_all() == ["first", "third", "second"]

    def test_clear(self):
        """Test clearing the cache."""
        cache = CachedSettings[str](max_history=3)
        cache.add("first")
        cache.add("second")
        cache.clear()

        assert cache.get_all() == []
        assert cache.get_latest() is None

    def test_get_latest_empty(self):
        """Test getting latest from empty cache returns None."""
        cache = CachedSettings[str](max_history=3)
        assert cache.get_latest() is None


class TestCacheManagerManualSync:
    """Tests for CacheManager with manual sync strategy."""

    def test_register_and_add(self):
        """Test registering a cache and adding values."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.register_cache("subjects", max_history=3)
        manager.add_to_cache("subjects", "mouse_001")
        manager.add_to_cache("subjects", "mouse_002")

        assert manager.get_cache("subjects") == ["mouse_002", "mouse_001"]

    def test_auto_register_on_add(self):
        """Test that adding to unregistered cache auto-registers it."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.add_to_cache("subjects", "mouse_001")
        assert manager.get_cache("subjects") == ["mouse_001"]

    def test_multiple_caches(self):
        """Test managing multiple independent caches."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.register_cache("subjects", max_history=3)
        manager.register_cache("experimenters", max_history=2)

        manager.add_to_cache("subjects", "mouse_001")
        manager.add_to_cache("subjects", "mouse_002")
        manager.add_to_cache("experimenters", "alice")
        manager.add_to_cache("experimenters", "bob")

        assert manager.get_cache("subjects") == ["mouse_002", "mouse_001"]
        assert manager.get_cache("experimenters") == ["bob", "alice"]

    def test_get_latest(self):
        """Test getting the latest value from a cache."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.add_to_cache("test", "first")
        manager.add_to_cache("test", "second")

        assert manager.get_latest("test") == "second"

    def test_get_latest_empty(self):
        """Test getting latest from empty cache returns None."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.register_cache("test", max_history=3)

        assert manager.get_latest("test") is None

    def test_clear_cache(self):
        """Test clearing a specific cache."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.add_to_cache("test", "value1")
        manager.add_to_cache("test", "value2")
        manager.clear_cache("test")

        assert manager.get_cache("test") == []

    def test_clear_all_caches(self):
        """Test clearing all caches at once."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.add_to_cache("subjects", "mouse_001")
        manager.add_to_cache("subjects", "mouse_002")
        manager.add_to_cache("experimenters", "alice")
        manager.add_to_cache("projects", "project_a")

        assert len(manager.caches) == 3
        assert manager.get_cache("subjects") == ["mouse_002", "mouse_001"]

        manager.clear_all_caches()

        assert manager.caches == {}
        assert len(manager.caches) == 0

    def test_singleton_behavior(self):
        """Test that get_instance returns the same instance."""
        manager1 = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager1.add_to_cache("test", "value1")

        manager2 = CacheManager.get_instance()
        assert manager2.get_cache("test") == ["value1"]
        assert manager1 is manager2


class TestCacheManagerAutoSync:
    """Tests for CacheManager with auto-sync to disk."""

    def test_auto_sync_on_add(self):
        """Test that AUTO sync saves after adding values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"

            manager = CacheManager.get_instance(reset=True, cache_path=path, sync_strategy=SyncStrategy.AUTO)
            manager.add_to_cache("subjects", "mouse_001")

            assert path.exists()

            manager2 = CacheManager.get_instance(reset=True, cache_path=path)
            assert manager2.get_cache("subjects") == ["mouse_001"]

    def test_auto_sync_on_clear(self):
        """Test that AUTO sync saves after clearing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"

            manager = CacheManager.get_instance(reset=True, cache_path=path, sync_strategy=SyncStrategy.AUTO)
            manager.add_to_cache("test", "value1")
            manager.clear_cache("test")

            # Reload and verify clear persisted
            manager2 = CacheManager.get_instance(reset=True, cache_path=path)
            assert manager2.get_cache("test") == []

    def test_auto_sync_on_clear_all(self):
        """Test that AUTO sync saves after clearing all caches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"

            manager = CacheManager.get_instance(reset=True, cache_path=path, sync_strategy=SyncStrategy.AUTO)
            manager.add_to_cache("subjects", "mouse_001")
            manager.add_to_cache("projects", "project_a")

            assert path.exists()

            manager.clear_all_caches()

            manager2 = CacheManager.get_instance(reset=True, cache_path=path)
            assert manager2.caches == {}

    def test_manual_sync_does_not_auto_save(self):
        """Test that MANUAL sync does not save automatically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"

            manager = CacheManager.get_instance(reset=True, cache_path=path, sync_strategy=SyncStrategy.MANUAL)
            manager.add_to_cache("test", "value1")

            # File should not exist yet
            assert not path.exists()

            # Explicit save
            manager.save()
            assert path.exists()

    def test_load_nonexistent_file(self):
        """Test loading from non-existent file returns empty manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent.json"
            manager = CacheManager.get_instance(reset=True, cache_path=path)

            assert manager.caches == {}

    def test_persistence_across_loads(self):
        """Test data persists correctly across save/load cycles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"

            manager1 = CacheManager.get_instance(reset=True, cache_path=path, sync_strategy=SyncStrategy.AUTO)
            manager1.add_to_cache("subjects", "mouse_001")
            manager1.add_to_cache("subjects", "mouse_002")
            manager1.add_to_cache("projects", "project_a")

            manager2 = CacheManager.get_instance(reset=True, cache_path=path)
            assert manager2.get_cache("subjects") == ["mouse_002", "mouse_001"]
            assert manager2.get_cache("projects") == ["project_a"]

            manager2.add_to_cache("subjects", "mouse_003")
            manager2.save()

            manager3 = CacheManager.get_instance(reset=True, cache_path=path)
            assert manager3.get_cache("subjects") == ["mouse_003", "mouse_002", "mouse_001"]

    def test_default_cache_path(self):
        """Test that default cache path is used when none specified."""
        manager = CacheManager.get_instance(reset=True, sync_strategy=SyncStrategy.MANUAL)
        manager.add_to_cache("test", "value")
        manager.save()

        manager2 = CacheManager.get_instance(reset=True)
        assert manager2.get_cache("test") == ["value"]

        manager.cache_path.unlink(missing_ok=True)
