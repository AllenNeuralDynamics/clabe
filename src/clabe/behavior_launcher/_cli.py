from pydantic import Field
from pydantic_settings import CliImplicitFlag

from ..launcher.cli import BaseCliArgs


class BehaviorCliArgs(BaseCliArgs):
    """Extends the base"""

    skip_data_transfer: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip data transfer after the experiment"
    )
    skip_data_mapping: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip data mapping after the experiment"
    )
