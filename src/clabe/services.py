"""Deprecated alias of [`clabe.settings`][clabe.settings]: ``clabe.services`` was renamed. Import from ``clabe.settings``;
this alias will be removed in a future release."""

import sys
import warnings

from . import settings as _renamed

warnings.warn(
    "clabe.services was renamed to clabe.settings; import from clabe.settings (this alias will be removed).",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _renamed
