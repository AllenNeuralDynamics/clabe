import warnings

from .aind_validators import validate_username as validate_aind_username  # noqa: F401

warnings.warn(
    "The 'clabe.utils.aind_auth' module is deprecated and will be removed in a future version. Use 'clabe.utils.aind_validators' instead.",
    FutureWarning,
    stacklevel=2,
)
