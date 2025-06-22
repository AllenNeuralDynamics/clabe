from pydantic import Field
from pydantic_settings import CliImplicitFlag

from ..launcher.cli import BaseCliArgs


class BehaviorCliArgs(BaseCliArgs):
    """
    Extends the base CLI arguments with behavior-specific options.

    This class adds additional command-line arguments specific to behavior experiments,
    including options for data transfer and data mapping control.

    Attributes:
        skip_data_transfer (CliImplicitFlag[bool]): Whether to skip data transfer after the experiment
        skip_data_mapping (CliImplicitFlag[bool]): Whether to skip data mapping after the experiment

    Examples:
        # Create CLI args for a behavior experiment
        args = BehaviorCliArgs(..., skip_data_transfer=True, skip_data_mapping=False)
        # Access skip_data_transfer flag
        print(args.skip_data_transfer)
    """

    skip_data_transfer: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip data transfer after the experiment"
    )
    skip_data_mapping: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip data mapping after the experiment"
    )
