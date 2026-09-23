import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clabe.git_manager._git import GitRepository


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    git_dir = tmp_path / "test_repo"
    git_dir.mkdir()
    subprocess.run(["git", "init"], cwd=git_dir, check=True)
    (git_dir / "test_file.txt").write_text("hello")
    subprocess.run(["git", "add", "test_file.txt"], cwd=git_dir, check=True)
    try:
        subprocess.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=git_dir,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        # It's possible git is not configured with user.name and user.email
        if b"Please tell me who you are" in e.stderr:
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=git_dir,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=git_dir,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "initial commit"],
                cwd=git_dir,
                check=True,
            )
        else:
            raise
    return git_dir


def test_git_repository_init(temp_git_repo: Path):
    """Test GitRepository initialization."""
    repo = GitRepository(path=temp_git_repo)
    assert repo.working_dir == str(temp_git_repo)


def test_get_metadata(temp_git_repo: Path, monkeypatch):
    """Test get_metadata captures repository provenance."""
    monkeypatch.chdir(temp_git_repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/test-repo.git"],
        cwd=temp_git_repo,
        check=True,
    )
    subprocess.run(["git", "tag", "v1.0.0"], cwd=temp_git_repo, check=True)
    (temp_git_repo / "untracked_file.txt").write_text("untracked")

    metadata = GitRepository(path=temp_git_repo).get_metadata()

    assert metadata.url == "https://github.com/example/test-repo.git"
    assert metadata.commit_sha == GitRepository(path=temp_git_repo).head.commit.hexsha
    assert metadata.tag == "v1.0.0"
    assert metadata.describe == "v1.0.0"
    assert metadata.branch is not None
    assert metadata.is_dirty
    assert metadata.name == "test_repo"
    assert metadata.path == "."
    assert metadata.is_initialized
    assert metadata.submodules == []


def test_get_metadata_describes_commit_after_tag(temp_git_repo: Path, monkeypatch):
    """Test get_metadata records the nearest tag for commits after a tag."""
    monkeypatch.chdir(temp_git_repo)
    subprocess.run(["git", "tag", "v1.0.0"], cwd=temp_git_repo, check=True)
    (temp_git_repo / "test_file.txt").write_text("updated")
    subprocess.run(["git", "commit", "-am", "commit after tag"], cwd=temp_git_repo, check=True)

    metadata = GitRepository(path=temp_git_repo).get_metadata()

    assert metadata.tag is None
    assert metadata.describe.startswith("v1.0.0-1-g")


def test_get_metadata_includes_nested_submodules(temp_git_repo: Path, monkeypatch):
    """Test get_metadata recursively captures initialized submodules."""
    monkeypatch.chdir(temp_git_repo)
    grandchild = temp_git_repo.parent / "grandchild"
    child = temp_git_repo.parent / "child"
    for repository in (grandchild, child):
        repository.mkdir()
        subprocess.run(["git", "init"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repository, check=True)
        (repository / "file.txt").write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repository, check=True)

    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(grandchild), "modules/grandchild"],
        cwd=child,
        check=True,
    )
    subprocess.run(["git", "commit", "-am", "add grandchild"], cwd=child, check=True)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(child), "modules/child"],
        cwd=temp_git_repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-am", "add child"], cwd=temp_git_repo, check=True)

    metadata = GitRepository(path=temp_git_repo).get_metadata()

    assert [submodule.path for submodule in metadata.submodules] == ["modules/child"]
    assert [submodule.name for submodule in metadata.submodules] == ["child"]
    assert [submodule.path for submodule in metadata.submodules[0].submodules] == ["modules/child/modules/grandchild"]
    assert [submodule.name for submodule in metadata.submodules[0].submodules] == ["grandchild"]
    assert not metadata.submodules[0].submodules[0].is_initialized
    assert metadata.submodules[0].submodules[0].is_dirty is None


def test_reset_repo(temp_git_repo: Path):
    """Test reset_repo."""
    (temp_git_repo / "test_file.txt").write_text("world")
    repo = GitRepository(path=temp_git_repo)
    assert repo.is_dirty()
    repo.reset_repo()
    assert not repo.is_dirty()


def test_clean_repo(temp_git_repo: Path):
    """Test clean_repo."""
    (temp_git_repo / "untracked_file.txt").write_text("untracked")
    repo = GitRepository(path=temp_git_repo)
    assert "untracked_file.txt" in repo.untracked_files
    repo.clean_repo()
    assert "untracked_file.txt" not in repo.untracked_files


def test_is_dirty_with_submodules_clean(temp_git_repo: Path):
    """Test is_dirty_with_submodules on a clean repo."""
    repo = GitRepository(path=temp_git_repo)
    assert not repo.is_dirty_with_submodules()


def test_is_dirty_with_submodules_dirty_main(temp_git_repo: Path):
    """Test is_dirty_with_submodules on a dirty main repo."""
    (temp_git_repo / "test_file.txt").write_text("world")
    repo = GitRepository(path=temp_git_repo)
    assert repo.is_dirty_with_submodules()


def test_uncommitted_changes_clean(temp_git_repo: Path):
    """Test uncommitted_changes on a clean repo."""
    repo = GitRepository(path=temp_git_repo)
    assert repo.uncommitted_changes() == []


def test_uncommitted_changes_dirty(temp_git_repo: Path):
    """Test uncommitted_changes on a dirty repo."""
    (temp_git_repo / "test_file.txt").write_text("world")
    (temp_git_repo / "untracked_file.txt").write_text("untracked")
    repo = GitRepository(path=temp_git_repo)
    changes = repo.uncommitted_changes()
    assert "test_file.txt" in changes
    assert "untracked_file.txt" in changes


def test_full_reset(temp_git_repo: Path):
    """Test full_reset."""
    (temp_git_repo / "test_file.txt").write_text("world")
    (temp_git_repo / "untracked_file.txt").write_text("untracked")
    repo = GitRepository(path=temp_git_repo)
    repo.full_reset()
    assert not repo.is_dirty()
    assert not repo.untracked_files


def test_try_prompt_full_reset_no_reset(temp_git_repo: Path):
    """Test try_prompt_full_reset when user says no."""
    (temp_git_repo / "test_file.txt").write_text("world")
    repo = GitRepository(path=temp_git_repo)
    mock_ui = MagicMock()
    mock_ui.prompt_confirm.return_value = False
    repo.try_prompt_full_reset(mock_ui)
    assert repo.is_dirty()


def test_try_prompt_full_reset_yes_reset(temp_git_repo: Path):
    """Test try_prompt_full_reset when user says yes."""
    (temp_git_repo / "test_file.txt").write_text("world")
    repo = GitRepository(path=temp_git_repo)
    mock_ui = MagicMock()
    mock_ui.prompt_confirm.return_value = True
    repo.try_prompt_full_reset(mock_ui)
    assert not repo.is_dirty()


def test_try_prompt_full_reset_force_reset(temp_git_repo: Path):
    """Test try_prompt_full_reset with force_reset=True."""
    (temp_git_repo / "test_file.txt").write_text("world")
    repo = GitRepository(path=temp_git_repo)
    mock_ui = MagicMock()
    repo.try_prompt_full_reset(mock_ui, force_reset=True)
    assert not repo.is_dirty()
    mock_ui.prompt_confirm.assert_not_called()
