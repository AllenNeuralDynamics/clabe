import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, CliApp, CliSubCommand

from .constants import TMP_DIR

logger = logging.getLogger(__name__)

T = TypeVar("T")
_DEFAULT_MAX_HISTORY = 9


class SyncStrategy(str, Enum):
    """Strategy for syncing cache to disk."""

    MANUAL = "manual"  # Only save when explicitly called
    AUTO = "auto"  # Save after every modification


class CachedSettings(BaseModel, Generic[T]):
    """
    Manages a cache of values with a configurable history limit.

    When a new value is added and the cache is full, the oldest value is removed.

    Attributes:
        values: List of cached values, newest first
        max_history: Maximum number of items to retain in cache

    Example:
        >>> cache = CachedSettings[str](max_history=3)
        >>> cache.add("first")
        >>> cache.add("second")
        >>> cache.get_all()
        ['second', 'first']
    """

    values: list[T] = Field(default_factory=list)
    max_history: int = Field(default=_DEFAULT_MAX_HISTORY, gt=0)

    def add(self, value: T) -> None:
        """
        Add a new value to the cache.

        If the value already exists, it's moved to the front.
        If the cache is full, the oldest value is removed.

        Args:
            value: The value to add to the cache
        """
        if value in self.values:
            self.values.remove(value)
        self.values.insert(0, value)

        if len(self.values) > self.max_history:
            self.values = self.values[: self.max_history]

    def get_all(self) -> list[T]:
        """
        Get all cached values.

        Returns:
            List of all cached values, newest first
        """
        return self.values.copy()

    def get_latest(self) -> T | None:
        """
        Get the most recently added value.

        Returns:
            The latest value, or None if cache is empty
        """
        return self.values[0] if self.values else None

    def clear(self) -> None:
        """Clear all values from the cache."""
        self.values = []


class CacheData(BaseModel):
    """Pydantic model for cache serialization."""

    caches: dict[str, CachedSettings[Any]] = Field(default_factory=dict)


class CacheManager:
    """
    Thread-safe singleton cache manager with multiple named caches.

    Uses Pydantic for proper serialization/deserialization with automatic
    disk synchronization support. All operations are thread-safe.

    Example:
        >>> # Get singleton instance with manual sync (default)
        >>> manager = CacheManager.get_instance()
        >>> manager.add_to_cache("subjects", "mouse_001")
        >>> manager.save()  # Explicitly save
        >>>
        >>> # Get instance with auto sync - saves after every change
        >>> manager = CacheManager.get_instance(sync_strategy=SyncStrategy.AUTO)
        >>> manager.add_to_cache("subjects", "mouse_002")  # Automatically saved
        >>>
        >>> # Custom path
        >>> manager = CacheManager.get_instance(cache_path="custom/cache.json")
    """

    _instance: ClassVar["CacheManager | None"] = None
    _lock: ClassVar[threading.RLock] = threading.RLock()

    def __init__(
        self,
        cache_path: Path | str | None = None,
        sync_strategy: SyncStrategy = SyncStrategy.AUTO,
    ) -> None:
        """
        Initialize a CacheManager instance.

        Args:
            cache_path: Path to cache file. If None, uses default location.
            sync_strategy: Strategy for syncing to disk (MANUAL or AUTO)
        """
        self.caches: dict[str, CachedSettings[Any]] = {}
        self.sync_strategy: SyncStrategy = sync_strategy
        self.cache_path: Path = Path(cache_path) if cache_path else Path(TMP_DIR) / ".cache_manager.json"
        self._instance_lock: threading.RLock = threading.RLock()

    @classmethod
    def get_instance(
        cls,
        cache_path: Path | str | None = None,
        sync_strategy: SyncStrategy = SyncStrategy.AUTO,
        reset: bool = False,
    ) -> "CacheManager":
        """
        Get the singleton instance of CacheManager (thread-safe).

        Args:
            cache_path: Path to cache file. If None, uses default location.
            sync_strategy: Strategy for syncing to disk (MANUAL or AUTO)
            reset: If True, reset the singleton and create a new instance

        Returns:
            The singleton CacheManager instance
        """
        with cls._lock:
            if reset or cls._instance is None:
                if cache_path is None:
                    cache_path = Path(TMP_DIR) / ".cache_manager.json"
                else:
                    cache_path = Path(cache_path)

                instance = cls(cache_path=cache_path, sync_strategy=sync_strategy)

                if cache_path.exists():
                    try:
                        with cache_path.open("r", encoding="utf-8") as f:
                            cache_data = CacheData.model_validate_json(f.read())
                            instance.caches = cache_data.caches
                    except Exception as e:
                        logger.warning("Cache file %s is corrupted: %s. Creating new instance.", cache_path, e)

                cls._instance = instance

            return cls._instance

    def _auto_save(self) -> None:
        """Save to disk if auto-sync is enabled (caller must hold lock)."""
        if self.sync_strategy == SyncStrategy.AUTO:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        """Internal save method without locking (caller must hold lock)."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_data = CacheData(caches=self.caches)
        with self.cache_path.open("w", encoding="utf-8") as f:
            f.write(cache_data.model_dump_json(indent=2))

    def register_cache(self, name: str, max_history: int = _DEFAULT_MAX_HISTORY) -> None:
        """
        Register a new cache with a specific history limit (thread-safe).

        Args:
            name: Unique name for the cache
            max_history: Maximum number of items to retain
        """
        with self._instance_lock:
            if name not in self.caches:
                self.caches[name] = CachedSettings(max_history=max_history)
                self._auto_save()

    def add_to_cache(self, name: str, value: Any) -> None:
        """
        Add a value to a named cache (thread-safe).

        Args:
            name: Name of the cache
            value: Value to add

        Raises:
            KeyError: If cache name is not registered
        """
        with self._instance_lock:
            if name not in self.caches:
                self.caches[name] = CachedSettings(max_history=_DEFAULT_MAX_HISTORY)

            cache = self.caches[name]

            # we remove it first to avoid duplicates
            if value in cache.values:
                cache.values.remove(value)
            # but add it to the front
            cache.values.insert(0, value)

            if len(cache.values) > cache.max_history:
                cache.values = cache.values[: cache.max_history]

            self._auto_save()

    def get_cache(self, name: str) -> list[Any]:
        """
        Get all values from a named cache (thread-safe).

        Args:
            name: Name of the cache

        Returns:
            List of cached values, newest first

        Raises:
            KeyError: If cache name is not registered
        """
        with self._instance_lock:
            if name not in self.caches:
                raise KeyError(f"Cache '{name}' not registered.")
            return self.caches[name].values.copy()

    def try_get_cache(self, name: str) -> Any | None:
        """Attempt to get all values from a named cache, returning None if not found."""
        try:
            return self.get_cache(name)
        except KeyError:
            return None

    def get_latest(self, name: str) -> Any | None:
        """
        Get the most recent value from a named cache (thread-safe).

        Args:
            name: Name of the cache

        Returns:
            The latest value, or None if cache is empty

        Raises:
            KeyError: If cache name is not registered
        """
        with self._instance_lock:
            values = self.get_cache(name)
            return values[0] if values else None

    def clear_cache(self, name: str) -> None:
        """
        Clear all values from a named cache (thread-safe).

        Args:
            name: Name of the cache

        Raises:
            KeyError: If cache name is not registered
        """
        with self._instance_lock:
            if name not in self.caches:
                raise KeyError(f"Cache '{name}' not registered.")
            self.caches[name].values = []
            self._auto_save()

    def clear_all_caches(self) -> None:
        """Clear all caches (thread-safe)."""
        with self._instance_lock:
            self.caches = {}
            self._auto_save()

    def save(self) -> None:
        """
        Save all caches to disk using Pydantic serialization (thread-safe).

        This method is called automatically if sync_strategy is AUTO,
        or can be called manually for MANUAL strategy.
        """
        with self._instance_lock:
            self._save_unlocked()


class _ListCacheCli(BaseSettings):
    """CLI command to list all caches and their contents."""

    def cli_cmd(self):
        """Run the list cache CLI command."""
        manager = CacheManager.get_instance()
        if not manager.caches:
            logger.info("No caches available.")
        for name, cache in manager.caches.items():
            logger.info("Cache '%s': %s", name, cache.values)


class _ResetCacheCli(BaseSettings):
    """CLI command to reset all caches."""

    def cli_cmd(self):
        """Run the reset cache CLI command."""
        CacheManager.get_instance().clear_all_caches()
        logger.info("All caches have been cleared.")


class _CacheManagerCli(BaseSettings):
    """CLI application wrapper for the RPC server."""

    reset: CliSubCommand[_ResetCacheCli]
    list: CliSubCommand[_ListCacheCli]

    def cli_cmd(self):
        """Run the cache manager CLI."""
        CliApp.run_subcommand(self)
