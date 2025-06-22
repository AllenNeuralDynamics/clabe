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
    Picker class that handles the selection of rigs, sessions, and task logic from slims
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
        Initializes the picker with an optional launcher, UI helper, username, and password.

        Args:
            launcher (Optional[BehaviorLauncher]): The launcher instance.
            ui_helper (Optional[_UiHelperBase]): The UI helper instance
            slims_url(str): slims url. Defaults to dev version of slims if not provided
            username (str): slims username. Defaults to SLIMS_USERNAME environment variable if not provided
            password (str): slims password. Defaults to SLIMS_PASSWORD environment variable if not provided
            water_calculator (Callable): Function to calculate water amount. If None, the user will be prompted for the amount.
            **kwargs: Additional keyword arguments.
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
        Connect to Slims with optional username and password or use environment variables

        Args:
            url (str): slims url. Defaults to sandbox version of slims if not provided
            username (Optional[str]): slims username. Defaults to SLIMS_USERNAME environment variable if not provided
            password (Optional[str]): slims password. Defaults to SLIMS_PASSWORD environment variable if not provided

        Returns:
            SlimsClient: slims client instance.

        Raises:
            Exception: error in creation of client
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
        Test client connection by querying mouse id. Slims will fail silently if a client is initialized with bad
        credentials. This method checks if credentials are correct or if there are any other issues pulling Slims
        content. Ignore exception if mouse id does not exist

        Returns:
            Boolean if connection works

        Raises:
            Exception: If invalid credentials or error reading from slims
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
        Returns slims client being used to load session
        Returns:
            SlimsClient: slims client object
        """
        return self._slims_client

    @property
    def slims_mouse(self) -> SlimsMouseContent:
        """
        Returns slims mouse model being used to load session

        Returns:
            SlimsMouseContent: slims mouse model object
        """

        if self._slims_mouse is None:
            raise ValueError("Slims mouse instance not set.")

        return self._slims_mouse

    @property
    def slims_session(self) -> SlimsBehaviorSession:
        """
        Returns slims session model being used to load task logic

        Returns:
           SlimsBehaviorSession: slims session model object
        """

        if self._slims_session is None:
            raise ValueError("Slims session instance not set.")

        return self._slims_session

    @property
    def slims_rig(self) -> SlimsInstrument:
        """
        Returns slims instrument model being used to load rig configuration

        Returns:
           SlimsInstrument: slims instrument model object
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
        Add waterlog event to Slims

        Args:
            weight_g (float): animal weight in grams
            water_earned_ml (float): water earned during session in mL
            water_supplement_delivered_ml (float): supplemental water given in session mL
            water_supplement_recommended_ml (Optional[float]): optional recommended water amount

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
        Prompts the user to provide a rig name and find on slims. Deserialize latest attachment into rig schema model.

        Returns:
            TRig: The selected rig configuration.

        Raises:
            ValueError: If no attachment is found with slims rig model or if no valid attachment is found

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
        Prompts the user to select or create a session configuration.

        Returns:
            TSession: The created or selected session configuration.

        Raises:
            ValueError: If no session model is found on slims.
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
        Returns task_logic found as an attachment from session loaded from slims.

        Returns:
            TTaskLogic: Task logic found as an attachment from session loaded from slims.

        Raises:
            ValueError: If no valid task logic attachment is found.
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
        task_logic: TTaskLogic,  # TODO: use curriculum state instead of task logic down the road
        notes: Optional[str] = None,
        is_curriculum_suggestion: Optional[bool] = None,
        software_version: Optional[str] = None,
        schedule_date: Optional[datetime] = None,
    ) -> None:
        """
        Pushes behavior session to slims with logic for the next session

        Args:
            task_logic (TTaskLogic): task_logic to use for next session
            notes (Optional[str]): note for Slims session
            is_curriculum_suggestion (Optional[bool]): Whether mouse is on curriculum
            software_version (Optional[str]): software used to run session
            schedule_date (Optional[datetime]): date session will be run
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
        """

        self._slims_client = self._connect_to_slims(self._slims_url, self._slims_username, self._slims_password)
        self._test_client_connection()

    def prompt_experimenter(self, strict: bool = True) -> Optional[List[str]]:
        """
        Prompts the user to enter the experimenter's name(s).

        Args:
            strict (bool): Whether to enforce non-empty input.

        Returns:
            Optional[List[str]]: List of experimenter names.
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
        weight_difference = max(0, baseline_weight_g - weight_g)
        return max(weight_difference, minimum_daily_water - water_earned_ml, 0)

    def suggest_water(self) -> None:
        """
        Calculates the suggested water amount based on the current weight and water earned.

        Returns:
            float: The suggested water amount in mL.
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
        self.suggest_water()
