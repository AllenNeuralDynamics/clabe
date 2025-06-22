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

    This class extends the functionality of GitPython's Repo class with additional
    utilities for repository management, submodule handling, and cleanup operations.
    """

    def __init__(self, *args, **kwargs):
        """
        Initializes the GitRepository instance and validates the presence of Git.

        Args:
            *args: Arguments passed to the parent Repo class
            **kwargs: Keyword arguments passed to the parent Repo class

        Example:
            # Initialize with current directory
            repo = GitRepository()

            # Initialize with specific path
            repo = GitRepository(path="/path/to/repo")
        """
        super().__init__(*args, **kwargs)
        self._validate_git()

    def reset_repo(self) -> Self:
        """
        Resets the repository to the last committed state.

        Performs a hard reset to discard all uncommitted changes in the working directory
        and staging area, reverting to the last commit.

        Returns:
            Self: The current instance for method chaining.

        Example:
            repo = GitRepository("/path/to/repo")
            repo.reset_repo()  # Discards all uncommitted changes
        """
        self.git.reset("--hard")
        return self

    def clean_repo(self) -> Self:
        """
        Cleans the repository by removing untracked files and directories.

        Removes all untracked files and directories from the working tree that are
        not ignored by .gitignore rules.

        Returns:
            Self: The current instance for method chaining.

        Example:
            repo = GitRepository("/path/to/repo")
            repo.clean_repo()  # Removes all untracked files and directories
        """
        self.git.clean("-fd")
        return self

    def is_dirty_with_submodules(self) -> bool:
        """
        Checks if the repository or any of its submodules is dirty.

        A repository is considered dirty if it has uncommitted changes, including
        untracked files. This method also checks all submodules.

        Returns:
            bool: True if the repository or any submodules have uncommitted changes.

        Example:
            repo = GitRepository("/path/to/repo")
            if repo.is_dirty_with_submodules():
                print("Repository or submodules have uncommitted changes")
        """
        _is_dirty_repo = self.is_dirty(untracked_files=True)
        if _is_dirty_repo:
            return True
        return any([submodule.repo.is_dirty(untracked_files=True) for submodule in self.submodules])

    @staticmethod
    def _get_changes(repo: Repo) -> List[str]:
        """
        Gets a list of changed files in the repository.

        Args:
            repo: The Git repository to check for changes

        Returns:
            List[str]: List of file paths that have been modified
        """
        return [item.a_path for item in (repo.index.diff(None) + repo.index.diff("HEAD")) if item.a_path]

    def uncommitted_changes(self) -> List[str]:
        """
        Retrieves a list of unstaged and untracked files in the repository and its submodules.

        Combines modified files, staged changes, and untracked files from both the main
        repository and all submodules.

        Returns:
            List[str]: A list of file paths with uncommitted changes.

        Example:
            repo = GitRepository("/path/to/repo")
            changes = repo.uncommitted_changes()
            if changes:
                print(f"Uncommitted changes found: {changes}")
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

        Forces an update of all Git submodules to match the commit specified
        in the parent repository.

        Returns:
            Self: The current instance for method chaining.

        Example:
            repo = GitRepository("/path/to/repo")
            repo.force_update_submodules()  # Updates all submodules
        """
        self.submodule_update()
        return self

    def submodules_sync(self) -> Self:
        """
        Synchronizes submodule URLs with the parent repository.

        Updates the submodule URLs to match those defined in the parent repository's
        .gitmodules file.

        Returns:
            Self: The current instance for method chaining.

        Example:
            repo = GitRepository("/path/to/repo")
            repo.submodules_sync()  # Synchronizes submodule URLs
        """
        self.git.submodule("sync", "--recursive")
        return self

    def full_reset(self) -> Self:
        """
        Performs a full reset of the repository and its submodules.

        Executes a complete cleanup including resetting the repository, synchronizing
        submodules, updating them, and cleaning untracked files. Also recursively
        resets all submodules.

        Returns:
            Self: The current instance for method chaining.

        Example:
            repo = GitRepository("/path/to/repo")
            repo.full_reset()  # Complete cleanup of repo and submodules
        """
        self.reset_repo().submodules_sync().force_update_submodules().clean_repo()
        _ = [GitRepository(str(sub.abspath)).full_reset() for sub in self.submodules]
        return self

    def try_prompt_full_reset(self, ui_helper: ui.UiHelper, force_reset: bool = False) -> Self:
        """
        Prompts the user to perform a full reset if the repository is dirty.

        Checks if the repository has uncommitted changes and either prompts the user
        or automatically performs a full reset based on the force_reset parameter.

        Args:
            ui_helper: The UI helper for user interaction
            force_reset: Whether to skip the prompt and force a reset

        Returns:
            Self: The current instance for method chaining.

        Example:
            repo = GitRepository("/path/to/repo")
            ui_helper = ui.DefaultUIHelper()
            repo.try_prompt_full_reset(ui_helper)  # Prompts user if dirty
            repo.try_prompt_full_reset(ui_helper, force_reset=True)  # Forces reset
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

        Checks if Git is installed and available in the system PATH.

        Raises:
            RuntimeError: If Git is not installed or not found in PATH

        Returns:
            bool: True if Git is installed and available
        """
        if not _HAS_GIT:
            logger.error("git executable not detected.")
            raise RuntimeError(
                "git is not installed in this computer. Please install git. https://git-scm.com/downloads"
            )
        return True


__all__ = [GitRepository]
