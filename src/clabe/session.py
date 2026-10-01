import logging
from collections.abc import Callable

from aind_behavior_services import Session

from . import ui
from .cache_manager import CacheManager
from .launcher import Launcher
from .utils.aind_validators import validate_username

logger = logging.getLogger(__name__)


class SessionBuilder:
    """
    Assembles a :class:`Session` by asking the user who is running and on which animal.

    This is the one genuinely interactive job with no database behind it, which
    is why it is not a store. It holds no state between calls, so a recovery
    flow can construct a session directly and narrow a store with
    ``store.scoped(subject=...)`` instead.

    Example:
        ```python
        session = SessionBuilder(launcher).build()
        store = store.scoped(subject=session.subject)
        ```
    """

    def __init__(
        self,
        launcher: Launcher,
        *,
        experimenter_validator: Callable[[str], str | None] | None = validate_username,
        use_cache: bool = True,
    ) -> None:
        """
        Args:
            launcher: Supplies the repository state and hardware-validation settings stamped onto the session.
            experimenter_validator: Validates each experimenter name, returning the canonical name to store
                or None to reject it. If ``None``, names are accepted as typed.
            use_cache: Whether to seed the prompts with previously entered values.
        """
        self._launcher = launcher
        self._experimenter_validator = experimenter_validator
        self._use_cache = use_cache
        self._cache_manager = CacheManager.get_instance()

    def build(self) -> Session:
        """
        Prompts for experimenter, subject and notes, and stamps the launcher's repository state.

        Returns:
            The assembled session.
        """
        experimenter = self.prompt_experimenter()
        subject = self.choose_subject()
        notes = ui.prompt_text(ui.TextRequest(label="Enter notes", field="notes"))
        settings = self._launcher.settings
        return Session(
            subject=subject,
            notes=notes,
            experimenter=experimenter,
            commit_hash=self._launcher.repository.head.commit.hexsha,
            allow_dirty_repo=settings.debug_mode or settings.allow_dirty,
            skip_hardware_validation=settings.skip_hardware_validation,
        )

    def choose_subject(self) -> str:
        """
        Prompts for a subject, offering previously used ones for autocompletion.

        Returns:
            The selected or entered subject name.
        """
        subject = ""
        while not subject:
            subject = ui.prompt_autocomplete(
                ui.AutoCompleteRequest(
                    label="Subject (type to filter, or enter a new one)",
                    options=self._cached_options("subjects"),
                    field="subject",
                )
            )
        self._cache_manager.add_to_cache("subjects", subject)
        return subject

    def prompt_experimenter(self, strict: bool = True) -> list[str]:
        """
        Prompts for the experimenter name(s), separated by commas or spaces.

        Args:
            strict: Whether to reject empty input.

        Returns:
            The validated experimenter names.
        """
        while True:
            entered = ui.prompt_autocomplete(
                ui.AutoCompleteRequest(
                    label="Experimenter name(s) (type to filter, comma-separated for multiple)",
                    options=self._cached_options("experimenters"),
                    field="experimenter",
                )
            )
            experimenter = entered.replace(",", " ").split()
            if strict and not experimenter:
                ui.notify("Experimenter name is not valid. Try again.", ui.MessageLevel.WARNING)
                continue
            validated, invalid = self._validate_names(experimenter)
            if invalid:
                ui.notify(
                    f"Experimenter name(s): {', '.join(invalid)}, is not valid. Try again", ui.MessageLevel.WARNING
                )
                continue
            self._cache_manager.add_to_cache("experimenters", ",".join(validated))
            return validated

    def _validate_names(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Validates every name, returning the canonicalized names and any names rejected."""
        if self._experimenter_validator is None:
            return names, []
        validated: list[str] = []
        invalid: list[str] = []
        for name in names:
            canonical = self._experimenter_validator(name)
            if canonical is None:
                invalid.append(name)
            else:
                validated.append(canonical)
        return validated, invalid

    def _cached_options(self, cache_name: str) -> list[str]:
        """Returns the previously entered values to offer for autocompletion."""
        if not self._use_cache:
            return []
        cached = self._cache_manager.try_get_cache(cache_name)
        return sorted(cached) if cached else []
