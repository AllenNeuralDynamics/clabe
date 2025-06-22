import importlib.util
import logging
import os
from datetime import datetime
from typing import Callable, List, Optional

from pydantic import ValidationError
from typing_extensions import override

from .. import __version__, ui
from ..behavior_launcher._launcher import BehaviorLauncher, ByAnimalFiles
from ..launcher._base import TRig, TSession, TTaskLogic

if importlib.util.find_spec("aind_slims_api") is None:
    raise ImportError(
        "The 'aind_slims_api' package is required to use this module. "
        "Install the optional dependencies defined in `project.toml` "
        "by running `pip install .[aind-services]`"
    )

from aind_slims_api import SlimsClient, exceptions
from aind_slims_api.models import SlimsBehaviorSession, SlimsInstrument, SlimsMouseContent, SlimsWaterlogResult

_BehaviorPickerAlias = ui.PickerBase[BehaviorLauncher[TRig, TSession, TTaskLogic], TRig, TSession, TTaskLogic]

logger = logging.getLogger(__name__)

SLIMS_USERNAME = os.environ.get("SLIMS_USERNAME", None)
SLIMS_PASSWORD = os.environ.get("SLIMS_PASSWORD", None)
SLIMS_URL: str = os.environ.get("SLIMS_URL", None)

logger.debug("SLIMS_USERNAME: %s", SLIMS_USERNAME)
logger.debug("SLIMS_PASSWORD: %s", SLIMS_PASSWORD)
logger.debug("SLIMS_URL: %s", SLIMS_URL)

if (SLIMS_USERNAME is None) or (SLIMS_PASSWORD is None):
    logger.warning("SLIMS_USERNAME and/or SLIMS_PASSWORD not found in environment variables.")

if SLIMS_URL is None:
    logger.warning("SLIMS_URL not found in environment variables. Defaulting to the Sandbox instance of Slims.")
    SLIMS_URL = "https://aind-test.us.slims.agilent.com/slimsrest/"


class SlimsPicker(_BehaviorPickerAlias[TRig, TSession, TTaskLogic]):
    """
    Picker class that handles the selection of rigs, sessions, and task logic from SLIMS.

    This class integrates with the SLIMS laboratory information management system to fetch
    experiment configurations, manage mouse records, and handle water logging for behavior experiments.

    Examples:
        # Initialize picker with launcher and SLIMS credentials
        picker = SlimsPicker(launcher=my_launcher, username="user", password="pass")
        # Connect to SLIMS and test connection
        picker.initialize()
        # Pick rig, session, and task logic from SLIMS
        rig = picker.pick_rig()
        session = picker.pick_session()
        task_logic = picker.pick_task_logic()
        # Finalize picker (suggest water and write waterlog)
        picker.finalize()
    """

    def __init__(
        self,
        launcher: Optional[BehaviorLauncher[TRig, TSession, TTaskLogic]] = None,
        *,
        ui_helper: Optional[ui.DefaultUIHelper] = None,
        slims_url: str = SLIMS_URL,
        username: Optional[str] = SLIMS_USERNAME,
        password: Optional[str] = SLIMS_PASSWORD,
        water_calculator: Optional[Callable[["SlimsPicker"], float]] = None,
        **kwargs,
    ):
        """
        Initializes the SLIMS picker with connection parameters and optional water calculator.

        Args:
            launcher: The launcher instance associated with the picker
            ui_helper: Helper for user interface interactions
            slims_url: SLIMS server URL. Defaults to dev version if not provided
            username: SLIMS username. Defaults to SLIMS_USERNAME environment variable
            password: SLIMS password. Defaults to SLIMS_PASSWORD environment variable
            water_calculator: Function to calculate water amount. If None, user will be prompted
            **kwargs: Additional keyword arguments

        Examples:
            # Initialize with explicit credentials
            picker = SlimsPicker(launcher=my_launcher, username="user", password="pass")
            # Initialize with environment variables
            picker = SlimsPicker(launcher=my_launcher)
            # Initialize with custom water calculator
            def calc_water(picker): return 1.5
            picker = SlimsPicker(launcher=my_launcher, water_calculator=calc_water)
        """

        super().__init__(launcher, ui_helper=ui_helper, **kwargs)
        # initialize properties

        self._slims_username = username
        self._slims_password = password
        self._slims_url = slims_url
        self._slims_client: SlimsClient = None
        self._slims_mouse: SlimsMouseContent = None
        self._slims_session: SlimsBehaviorSession = None
        self._slims_rig: SlimsInstrument = None

        self._water_calculator = water_calculator

    @staticmethod
    def _connect_to_slims(
        url: str = SLIMS_URL,
        username: Optional[str] = SLIMS_USERNAME,
        password: Optional[str] = SLIMS_PASSWORD,
    ) -> SlimsClient:
        """
        Connect to SLIMS with optional username and password or use environment variables.

        Args:
            url: SLIMS server URL. Defaults to sandbox version if not provided
            username: SLIMS username. Defaults to SLIMS_USERNAME environment variable
            password: SLIMS password. Defaults to SLIMS_PASSWORD environment variable

        Returns:
            SlimsClient: Configured SLIMS client instance

        Raises:
            RuntimeError: If client creation fails due to connection or authentication issues
        """

        try:
            logger.info("Attempting to connect to Slims")
            slims_client = SlimsClient(
                url=url,
                username=username,
                password=password,
            )

        except Exception as e:
            raise RuntimeError(f"Exception trying to create Slims client: {e}.\n") from e

        return slims_client

    def _test_client_connection(self) -> bool:
        """
        Test client connection by querying a test mouse ID.

        SLIMS fails silently with bad credentials, so this method validates the connection
        by attempting to fetch a record and handling the expected exceptions.

        Returns:
            bool: True if connection is successful

        Raises:
            SlimsAPIException: If invalid credentials or other SLIMS errors occur
        """

        try:
            self.slims_client.fetch_model(SlimsMouseContent, barcode="00000000")
            logger.info("Successfully connected to Slims")
            return True

        except exceptions.SlimsRecordNotFound:  # bypass exception if mouse doesn't exist
            logger.info("Successfully connected to Slims")
            return True

        except exceptions.SlimsAPIException as e:
            logger.warning(f"Exception trying to read from Slims: {e}.\n")
            return False

    @property
    def slims_client(self) -> SlimsClient:
        """
        Returns the SLIMS client being used for session operations.

        Returns:
            SlimsClient: The configured SLIMS client object
        """
        return self._slims_client

    @property
    def slims_mouse(self) -> SlimsMouseContent:
        """
        Returns the SLIMS mouse model being used for the current session.

        Returns:
            SlimsMouseContent: The current mouse/subject record from SLIMS

        Raises:
            ValueError: If SLIMS mouse instance is not set
        """

        if self._slims_mouse is None:
            raise ValueError("Slims mouse instance not set.")

        return self._slims_mouse

    @property
    def slims_session(self) -> SlimsBehaviorSession:
        """
        Returns the SLIMS session model being used for task logic loading.

        Returns:
            SlimsBehaviorSession: The current behavior session record from SLIMS

        Raises:
            ValueError: If SLIMS session instance is not set
        """

        if self._slims_session is None:
            raise ValueError("Slims session instance not set.")

        return self._slims_session

    @property
    def slims_rig(self) -> SlimsInstrument:
        """
        Returns the SLIMS instrument model being used for rig configuration.

        Returns:
            SlimsInstrument: The current rig/instrument record from SLIMS

        Raises:
            ValueError: If SLIMS rig instance is not set
        """

        if self._slims_rig is None:
            raise ValueError("Slims rig instance not set.")

        return self._slims_rig

    def write_waterlog(
        self,
        weight_g: float,
        water_earned_ml: float,
        water_supplement_delivered_ml: float,
        water_supplement_recommended_ml: Optional[float] = None,
    ) -> None:
        """
        Add waterlog event to SLIMS with the specified water and weight measurements.

        Args:
            weight_g: Animal weight in grams
            water_earned_ml: Water earned during session in mL
            water_supplement_delivered_ml: Supplemental water given in session in mL
            water_supplement_recommended_ml: Optional recommended water amount in mL

        Examples:
            # Write a waterlog entry to SLIMS
            picker.write_waterlog(weight_g=25.0, water_earned_ml=1.2, water_supplement_delivered_ml=0.5)
        """

        if self.launcher.session_schema is not None:
            # create model
            model = SlimsWaterlogResult(
                mouse_pk=self.slims_mouse.pk,
                date=self.launcher.session_schema.date,
                weight_g=weight_g,
                operator=", ".join(self.launcher.session_schema.experimenter),
                water_earned_ml=water_earned_ml,
                water_supplement_delivered_ml=water_supplement_delivered_ml,
                water_supplement_recommended_ml=water_supplement_recommended_ml,
                total_water_ml=water_earned_ml + water_supplement_delivered_ml,
                comments=self.launcher.session_schema.notes,
                workstation=self.launcher.rig_schema.rig_name,
                sw_source="clabe",
                sw_version=__version__,
                test_pk=self._slims_client.fetch_pk("Test", test_name="test_waterlog"),
            )

            self.slims_client.add_model(model)

    def pick_rig(self) -> TRig:
        """
        Prompts the user to provide a rig name and fetches configuration from SLIMS.

        Searches SLIMS for the specified rig and deserializes the latest attachment
        into a rig schema model.

        Returns:
            TRig: The selected rig configuration from SLIMS

        Raises:
            ValueError: If no rig is found in SLIMS or no valid attachment exists

        Examples:
            # Pick a rig configuration from SLIMS
            rig = picker.pick_rig()
        """

        while True:
            rig = None
            while rig is None:
                # TODO: use env vars to determine rig name
                rig = self.ui_helper.input("Enter rig name: ")
                try:
                    self._slims_rig = self.slims_client.fetch_model(SlimsInstrument, name=rig)
                except exceptions.SlimsRecordNotFound:
                    logger.error(f"Rig {rig} not found in Slims. Try again.")
                    rig = None

            i = slice(-1, None)
            attachments = self.slims_client.fetch_attachments(self.slims_rig)
            while True:
                # attempt to fetch rig_model attachment from slims
                try:
                    attachment = attachments[i]
                    if not attachment:  # catch empty list
                        ValueError("No attachments exist ([]).")

                    elif len(attachment) > 1:
                        att_names = [attachment.name for attachment in attachment]
                        att = self.ui_helper.prompt_pick_from_list(
                            att_names,
                            prompt="Choose an attachment:",
                            allow_0_as_none=True,
                        )
                        attachment = [attachment[att_names.index(att)]]

                    rig_model = self.slims_client.fetch_attachment_content(attachment[0]).json()
                except (IndexError, ValueError) as exc:
                    raise ValueError(f"No rig configuration found attached to rig model {rig}") from exc

                # validate and return model and retry if validation fails
                try:
                    return self.launcher.rig_schema_model(**rig_model)

                except ValidationError as e:
                    # remove last tried attachment
                    index = attachments.index(attachment[0])
                    del attachments[index]

                    if not attachments:  # attachment list empty
                        raise ValueError(f"No valid rig configuration found attached to rig model {rig}") from e
                    else:
                        logger.error(
                            f"Validation error for last rig configuration found attached to rig model {rig}: "
                            f"{e}. Please pick a different configuration."
                        )
                        i = slice(-11, None)

    def pick_session(self) -> TSession:
        """
        Prompts the user to select or create a session configuration using SLIMS data.

        Fetches mouse information from SLIMS and creates a session configuration
        with experimenter details and session metadata.

        Returns:
            TSession: The created session configuration with SLIMS integration

        Raises:
            ValueError: If no session model is found in SLIMS for the specified mouse

        Examples:
            # Pick a session configuration from SLIMS
            session = picker.pick_session()
        """

        experimenter = self.prompt_experimenter(strict=True)
        if self.launcher.subject is not None:
            logging.info("Subject provided via CLABE: %s", self.launcher.settings.subject)
            subject = self.launcher.subject
        else:
            subject = None
            while subject is None:
                subject = self.ui_helper.input("Enter subject name: ")
                try:
                    self._slims_mouse = self._slims_client.fetch_model(SlimsMouseContent, barcode=subject)
                except exceptions.SlimsRecordNotFound:
                    logger.warning("No Slims mouse with barcode %s. Please re-enter.", subject)
                    subject = None
            self.launcher.subject = subject

        assert subject is not None

        sessions = self.slims_client.fetch_models(SlimsBehaviorSession, mouse_pk=self.slims_mouse.pk)
        try:
            self._slims_session = sessions[-1]
        except IndexError as exc:  # empty list returned from slims
            raise ValueError(f"No session found on slims for mouse {subject}.") from exc

        notes = self.ui_helper.prompt_text("Enter notes: ")

        return self.launcher.session_schema_model(
            experiment="",  # Will be set later
            root_path=str(self.launcher.data_dir.resolve())
            if not self.launcher.group_by_subject_log
            else str(self.launcher.data_dir.resolve() / subject),
            subject=subject,
            notes=notes + "\n" + (self.slims_session.notes if self.slims_session.notes else ""),
            experimenter=experimenter if experimenter is not None else [],
            commit_hash=self.launcher.repository.head.commit.hexsha,
            allow_dirty_repo=self.launcher.is_debug_mode or self.launcher.allow_dirty,
            skip_hardware_validation=self.launcher.skip_hardware_validation,
            experiment_version="",  # Will be set later
        )

    def pick_task_logic(self) -> TTaskLogic:
        """
        Returns task logic found as an attachment from the session loaded from SLIMS.

        Attempts to load task logic from CLI first, then from SLIMS session attachments.

        Returns:
            TTaskLogic: Task logic configuration from SLIMS session attachments

        Raises:
            ValueError: If no valid task logic attachment is found in the SLIMS session

        Examples:
            # Pick task logic from SLIMS session attachments
            task_logic = picker.pick_task_logic()
        """

        try:
            return self.launcher.task_logic_schema

        except ValueError:
            # check attachments from loaded session
            attachments = self.slims_client.fetch_attachments(self.slims_session)
            try:  # get most recently added task_logic
                response = [
                    self.slims_client.fetch_attachment_content(attach).json()
                    for attach in attachments
                    if attach.name == ByAnimalFiles.TASK_LOGIC.value
                ][0]
            except IndexError as exc:  # empty attachment list with loaded session
                raise ValueError(
                    "No task_logic model found on with loaded slims session for mouse"
                    f" {self.launcher.subject}. Please add before continuing."
                ) from exc

            return self.launcher.task_logic_schema_model(**response)

    def write_behavior_session(
        self,
        task_logic: TTaskLogic,
        notes: Optional[str] = None,
        is_curriculum_suggestion: Optional[bool] = None,
        software_version: Optional[str] = None,
        schedule_date: Optional[datetime] = None,
    ) -> None:
        """
        Pushes a new behavior session to SLIMS with task logic for the next session.

        Args:
            task_logic: Task logic configuration to use for the next session
            notes: Optional notes for the SLIMS session
            is_curriculum_suggestion: Whether the mouse is following a curriculum
            software_version: Software version used to run the session
            schedule_date: Date when the session will be run

        Examples:
            # Write a new behavior session to SLIMS
            picker.write_behavior_session(task_logic=task_logic, notes="Session notes")
        """

        logger.info("Writing next session to slims.")

        session_schema = self.launcher.session_schema

        # create session
        added_session = self.slims_client.add_model(
            SlimsBehaviorSession(
                mouse_pk=self.slims_mouse.pk,
                task=session_schema.experiment,
                task_schema_version=task_logic.version,
                instrument_pk=self.slims_rig.pk,
                # trainer_pks   #   TODO: We could add this if we decided to look up experimenters on slims
                is_curriculum_suggestion=is_curriculum_suggestion,
                notes=notes,
                software_version=software_version,
                date=schedule_date,
            )
        )

        # add trainer_state as an attachment
        self._slims_client.add_attachment_content(
            record=added_session, name=ByAnimalFiles.TASK_LOGIC.value, content=task_logic.model_dump()
        )

    @override
    def initialize(self) -> None:
        """
        Initializes the picker by connecting to SLIMS and testing the connection.

        Establishes the SLIMS client connection and validates connectivity before
        proceeding with picker operations.

        Examples:
            # Initialize the picker and connect to SLIMS
            picker.initialize()
        """

        self._slims_client = self._connect_to_slims(self._slims_url, self._slims_username, self._slims_password)
        self._test_client_connection()

    def prompt_experimenter(self, strict: bool = True) -> Optional[List[str]]:
        """
        Prompts the user to enter the experimenter's name(s).

        Accepts multiple experimenter names separated by commas or spaces.

        Args:
            strict: Whether to enforce non-empty input

        Returns:
            Optional[List[str]]: List of experimenter names
        """

        experimenter: Optional[List[str]] = None
        while experimenter is None:
            _user_input = self.ui_helper.prompt_text("Experimenter name: ")
            experimenter = _user_input.replace(",", " ").split()
            if strict & (len(experimenter) == 0):
                logger.error("Experimenter name is not valid.")
                experimenter = None
            else:
                return experimenter
        return experimenter  # This line should be unreachable

    @staticmethod
    def _calculate_suggested_water(
        weight_g: float, water_earned_ml: float, baseline_weight_g: float, minimum_daily_water: float = 1.0
    ):
        """
        Calculates suggested water amount based on weight difference and earned water.

        Args:
            weight_g: Current weight in grams
            water_earned_ml: Water earned during session in mL
            baseline_weight_g: Baseline weight in grams
            minimum_daily_water: Minimum daily water requirement in mL

        Returns:
            float: Suggested water amount in mL
        """

        weight_difference = max(0, baseline_weight_g - weight_g)
        return max(weight_difference, minimum_daily_water - water_earned_ml, 0)

    def suggest_water(self) -> None:
        """
        Calculates and suggests water amount based on current weight and water earned.

        Prompts user for current weight and water earned, calculates suggested amount,
        and optionally writes the waterlog to SLIMS.
        """

        # Get the baseline weight from the mouse model, should in theory be handled
        # by the user asynchronously
        baseline_weight_g = self.slims_mouse.baseline_weight_g

        if self._water_calculator is None:

            def _water_prompt(launcher: "SlimsPicker", /) -> float:
                return self.ui_helper.prompt_float("Enter water amount (mL): ")

            water_calculator = _water_prompt

        else:
            water_calculator = self._water_calculator

        water_earned_ml = water_calculator(self)

        # I guess we can't automate this for now, so we just prompt the user
        # for the current weight
        weight_g = float(self.ui_helper.input("Enter current weight (g): "))

        suggested_water_ml = self._calculate_suggested_water(weight_g, water_earned_ml, baseline_weight_g)
        logger.info("Suggested water amount: %s mL", suggested_water_ml)
        _is_upload = self.ui_helper.prompt_yes_no_question("Do you want to write the waterlog to SLIMS? (Y/N): ")
        if _is_upload:
            self.write_waterlog(
                weight_g=weight_g,
                water_earned_ml=water_earned_ml,
                water_supplement_delivered_ml=suggested_water_ml,
                water_supplement_recommended_ml=suggested_water_ml,
            )
        return

    def finalize(self):
        """
        Finalizes the picker by suggesting water amount and handling waterlog.

        Called at the end of the experiment to manage post-session water calculations
        and SLIMS waterlog entries.
        """

        self.suggest_water()
