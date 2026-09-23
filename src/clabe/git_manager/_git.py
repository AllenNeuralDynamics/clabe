import logging
import os
import shutil
from pathlib import Path
from typing import Self

from git import Repo
from pydantic import BaseModel, Field

from .. import ui

logger = logging.getLogger(__name__)

_HAS_GIT = shutil.which("git") is not None


class GitRepositoryMetadata(BaseModel):
    """A serializable snapshot of a Git repository's provenance state.

    Paths are relative to the current working directory when the snapshot is made.
    """

    url: str | None = Field(description="Preferred remote URL, using origin when configured.")
    commit_sha: str | None = Field(description="Full SHA of the checked-out commit.")
    tag: str | None = Field(description="Tag attached exactly to the checked-out commit, if any.")
    describe: str | None = Field(description="Nearest reachable tag and commit distance from git describe.")
    branch: str | None = Field(description="Checked-out branch name, or null when HEAD is detached.")
    is_dirty: bool | None = Field(description="Whether tracked or untracked working-tree changes are present.")
    name: str = Field(description="Checkout directory name.")
    path: str = Field(description="Checkout path relative to the current working directory.")
    is_initialized: bool = Field(default=True, description="Whether the submodule working tree is initialized.")
    submodules: list["GitRepositoryMetadata"] = Field(
        default_factory=list,
        description="Metadata snapshots for declared submodules.",
    )


class GitRepository(Repo):
    """
    A wrapper around the `git.Repo` class that provides additional methods
    for managing Git repositories and their submodules.

    Extends the functionality of GitPython's Repo class with additional utilities
    for repository management, submodule handling, and cleanup operations.

    Methods:
        reset_repo: Resets the repository to the last committed state
        clean_repo: Cleans the repository by removing untracked files
        is_dirty_with_submodules: Checks if the repository or submodules are dirty
        uncommitted_changes: Returns a list of uncommitted changes
        init_and_update_submodules: Initializes and updates all submodules recursively
        submodules_sync: Synchronizes submodules
        full_reset: Performs a full reset of the repository and submodules
        try_prompt_full_reset: Prompts the user to perform a full reset
    """

    def __init__(self, *args, **kwargs):
        """
        Initializes the GitRepository instance and validates the presence of Git.

        Args:
            *args: Arguments passed to the parent Repo class
            **kwargs: Keyword arguments passed to the parent Repo class

        Example:
            ```python
            # Initialize with current directory
            repo = GitRepository()

            # Initialize with specific path
            repo = GitRepository(path="/path/to/repo")
            ```
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
            ```python
            repo = GitRepository("/path/to/repo")
            repo.reset_repo()  # Discards all uncommitted changes
            ```
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
            ```python
            repo = GitRepository("/path/to/repo")
            repo.clean_repo()  # Removes all untracked files and directories
            ```
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
            ```python
            repo = GitRepository("/path/to/repo")
            if repo.is_dirty_with_submodules():
                print("Repository or submodules have uncommitted changes")
            ```
        """
        _is_dirty_repo = self.is_dirty(untracked_files=True)
        if _is_dirty_repo:
            return True
        return any(submodule.repo.is_dirty(untracked_files=True) for submodule in self.submodules)

    def get_metadata(self) -> GitRepositoryMetadata:
        """Return a serializable snapshot of this repository and its submodules.

        The snapshot includes every declared submodule recursively when its parent
        is initialized. An uninitialized submodule records its configured URL and
        gitlink commit SHA with ``is_initialized=False``. ``branch`` is ``None``
        when a repository is in a detached-HEAD state, and ``tag`` is ``None``
        when HEAD has no exact tag. ``describe`` identifies the nearest reachable
        tag and the number of commits since it.

        Returns:
            GitRepositoryMetadata: Repository provenance information suitable for
                inclusion in experiment or data metadata.
        """
        return self._get_metadata(self)

    @classmethod
    def _get_metadata(cls, repo: Repo) -> GitRepositoryMetadata:
        """Build metadata for a repository and its initialized submodules."""
        remote = next((remote for remote in repo.remotes if remote.name == "origin"), None)
        if remote is None:
            remote = next(iter(repo.remotes), None)

        tags = repo.git.tag("--points-at", "HEAD").splitlines()
        return GitRepositoryMetadata(
            url=remote.url if remote else None,
            commit_sha=repo.head.commit.hexsha,
            tag=tags[0] if tags else None,
            describe=repo.git.describe("--tags", "--always"),
            branch=None if repo.head.is_detached else repo.active_branch.name,
            is_dirty=repo.is_dirty(untracked_files=True),
            name=Path(repo.working_tree_dir).name,
            path=cls._relative_path(repo.working_tree_dir),
            submodules=[cls._get_submodule_metadata(submodule) for submodule in repo.submodules],
        )

    @classmethod
    def _get_submodule_metadata(cls, submodule) -> GitRepositoryMetadata:
        """Build metadata for a submodule without requiring it to be initialized."""
        if not (Path(submodule.abspath) / ".git").exists():
            return GitRepositoryMetadata(
                url=submodule.url,
                commit_sha=submodule.hexsha,
                tag=None,
                describe=None,
                branch=None,
                is_dirty=None,
                name=Path(submodule.path).name,
                path=cls._relative_path(submodule.abspath),
                is_initialized=False,
            )
        return cls._get_metadata(submodule.module())

    @staticmethod
    def _relative_path(path: str) -> str:
        """Return a path relative to the current working directory."""
        return Path(os.path.relpath(path, start=Path.cwd())).as_posix()

    @staticmethod
    def _get_changes(repo: Repo) -> list[str]:
        """
        Gets a list of changed files in the repository.

        Args:
            repo: The Git repository to check for changes

        Returns:
            List[str]: List of file paths that have been modified
        """
        return [item.a_path for item in (repo.index.diff(None) + repo.index.diff("HEAD")) if item.a_path]

    def uncommitted_changes(self) -> list[str]:
        """
        Retrieves a list of unstaged and untracked files in the repository and its submodules.

        Combines modified files, staged changes, and untracked files from both the main
        repository and all submodules.

        Returns:
            List[str]: A list of file paths with uncommitted changes.

        Example:
            ```python
            repo = GitRepository("/path/to/repo")
            changes = repo.uncommitted_changes()
            if changes:
                print(f"Uncommitted changes found: {changes}")
            ```
        """
        untracked_files = self.untracked_files
        changes = self._get_changes(self)
        for submodule in self.submodules:
            changes.extend(self._get_changes(submodule.repo))
            untracked_files.extend(submodule.repo.untracked_files)
        return list(set(changes + untracked_files))

    def init_and_update_submodules(self, force: bool = True) -> Self:
        """
        Initializes and updates all submodules recursively.

        Executes `git submodule update --init --recursive` to ensure all submodules,
        including nested ones, are initialized and updated to the commit specified
        in the parent repository. This is idempotent - if submodules are already
        up to date, no changes are made.

        Args:
            force: If True, passes --force flag to checkout even if local changes
                would be overwritten in submodules. Defaults to True.

        Returns:
            Self: The current instance for method chaining.

        Raises:
            git.GitCommandError: If the git command fails (e.g., network error,
                invalid submodule configuration).

        Example:
            ```python
            repo = GitRepository("/path/to/repo")
            repo.init_and_update_submodules()  # Init and update all submodules
            repo.init_and_update_submodules(force=False)  # Without forcing checkout
            ```
        """
        if not self.submodules:
            logger.debug("No submodules found in repository: %s", self.working_dir)
            return self

        args = ["update", "--init", "--recursive"]
        if force:
            args.append("--force")
        self.git.submodule(*args)
        return self

    def submodules_sync(self) -> Self:
        """
        Synchronizes submodule URLs with the parent repository.

        Updates the submodule URLs to match those defined in the parent repository's
        .gitmodules file.

        Returns:
            Self: The current instance for method chaining.

        Example:
            ```python
            repo = GitRepository("/path/to/repo")
            repo.submodules_sync()  # Synchronizes submodule URLs
            ```
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
            ```python
            repo = GitRepository("/path/to/repo")
            repo.full_reset()  # Complete cleanup of repo and submodules
            ```
        """
        self.reset_repo().submodules_sync().init_and_update_submodules().clean_repo()
        _ = [GitRepository(str(sub.abspath)).full_reset() for sub in self.submodules]
        return self

    def try_prompt_full_reset(self, frontend: "ui.Frontend", force_reset: bool = False) -> Self:
        """
        Prompts the user to perform a full reset if the repository is dirty.

        Checks if the repository has uncommitted changes and either prompts the user
        or automatically performs a full reset based on the force_reset parameter.

        Args:
            frontend: The frontend mediating user interaction
            force_reset: Whether to skip the prompt and force a reset

        Returns:
            Self: The current instance for method chaining.

        Example:
            ```python
            repo = GitRepository("/path/to/repo")
            frontend = ui.default_frontend()
            repo.try_prompt_full_reset(frontend)  # Prompts user if dirty
            repo.try_prompt_full_reset(frontend, force_reset=True)  # Forces reset
            ```
        """
        if force_reset:
            self.full_reset()
            return self
        if self.is_dirty_with_submodules():
            logger.info("Repository is dirty! %s", self.working_dir)
            logger.info("Uncommitted files: %s", self.uncommitted_changes())
            if not force_reset:
                is_reset = frontend.prompt_confirm(ui.ConfirmRequest(label="Do you want to reset the repository?"))
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
