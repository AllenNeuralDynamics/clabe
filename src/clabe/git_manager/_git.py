import logging
import shutil
from typing import List, Self

from git import Repo

from .. import ui

logger = logging.getLogger(__name__)

_HAS_GIT = shutil.which("git") is not None


class GitRepository(Repo):
    """
    A wrapper around the `git.Repo` class that provides additional methods
    for managing Git repositories and their submodules.
    """

    def __init__(self, *args, **kwargs):
        """
        Initializes the GitRepository instance and validates the presence of Git.
        """
        super().__init__(*args, **kwargs)
        self._validate_git()

    def reset_repo(self) -> Self:
        """
        Resets the repository to the last committed state.

        Returns:
            Self: The current instance for method chaining.
        """
        self.git.reset("--hard")
        return self

    def clean_repo(self) -> Self:
        """
        Cleans the repository by removing untracked files and directories.

        Returns:
            Self: The current instance for method chaining.
        """
        self.git.clean("-fd")
        return self

    def is_dirty_with_submodules(self) -> bool:
        """
        Checks if the repository or any of its submodules is dirty.

        Returns:
            bool: True if the repository or submodules have uncommitted changes.
        """
        _is_dirty_repo = self.is_dirty(untracked_files=True)
        if _is_dirty_repo:
            return True
        return any([submodule.repo.is_dirty(untracked_files=True) for submodule in self.submodules])

    @staticmethod
    def _get_changes(repo: Repo) -> List[str]:
        return [item.a_path for item in (repo.index.diff(None) + repo.index.diff("HEAD")) if item.a_path]

    def uncommitted_changes(self) -> List[str]:
        """
        Retrieves a list of unstaged and untracked files in the repository and its submodules.

        Returns:
            List[str]: A list of unstaged file paths.
        """
        untracked_files = self.untracked_files
        changes = self._get_changes(self)
        for submodule in self.submodules:
            changes.extend(self._get_changes(submodule.repo))
            untracked_files.extend(submodule.repo.untracked_files)
        return list(set(changes + untracked_files))

    def force_update_submodules(self) -> Self:
        """
        Updates all submodules to their latest state.

        Returns:
            Self: The current instance for method chaining.
        """
        self.submodule_update()
        return self

    def submodules_sync(self) -> Self:
        """
        Synchronizes submodule URLs with the parent repository.

        Returns:
            Self: The current instance for method chaining.
        """
        self.git.submodule("sync", "--recursive")
        return self

    def full_reset(self) -> Self:
        """
        Performs a full reset of the repository and its submodules.

        Returns:
            Self: The current instance for method chaining.
        """
        self.reset_repo().submodules_sync().force_update_submodules().clean_repo()
        _ = [GitRepository(str(sub.abspath)).full_reset() for sub in self.submodules]
        return self

    def try_prompt_full_reset(self, ui_helper: ui.UiHelper, force_reset: bool = False) -> Self:
        """
        Prompts the user to perform a full reset if the repository is dirty.

        Args:
            ui_helper (ui.UiHelper): The UI helper for user interaction.
            force_reset (bool): Whether to skip the prompt and force a reset.

        Returns:
            Self: The current instance for method chaining.
        """
        if force_reset:
            self.full_reset()
            return self
        if self.is_dirty_with_submodules():
            logger.info("Repository is dirty! %s", self.working_dir)
            logger.info("Uncommitted files: %s", self.uncommitted_changes())
            if not force_reset:
                is_reset = ui_helper.prompt_yes_no_question(prompt="Do you want to reset the repository?")
            else:
                is_reset = True
            if is_reset:
                logger.info("Full reset of repository and submodules: %s", self.working_dir)
                self.full_reset()
        return self

    @staticmethod
    def _validate_git() -> bool:
        """
        Validates the presence of the Git executable.

        Raises:
            RuntimeError: If Git is not installed.

        Returns:
            bool: True if Git is installed.
        """
        if not _HAS_GIT:
            logger.error("git executable not detected.")
            raise RuntimeError(
                "git is not installed in this computer. Please install git. https://git-scm.com/downloads"
            )
        return True


__all__ = [GitRepository]
