"""Deprecated alias of [`clabe.repository`][clabe.repository]: ``clabe.git_manager`` was renamed. Import from ``clabe.repository``;
this alias will be removed in a future release."""

import sys
import warnings

from . import repository as _renamed

warnings.warn(
    "clabe.git_manager was renamed to clabe.repository; import from clabe.repository (this alias will be removed).",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _renamed
