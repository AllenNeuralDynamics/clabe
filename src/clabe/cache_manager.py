"""Deprecated alias of [`clabe.cache`][clabe.cache]: ``clabe.cache_manager`` was renamed. Import from ``clabe.cache``;
this alias will be removed in a future release."""

import sys
import warnings

from . import cache as _renamed

warnings.warn(
    "clabe.cache_manager was renamed to clabe.cache; import from clabe.cache (this alias will be removed).",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _renamed
