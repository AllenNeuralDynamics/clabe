import warnings

from .validators import validate_username as validate_aind_username  # noqa: F401

warnings.warn(
    "The 'clabe.aind.auth' module is deprecated and will be removed in a future version. Use 'clabe.aind.validators' instead.",
    FutureWarning,
    stacklevel=2,
)
