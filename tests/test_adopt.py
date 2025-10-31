"""Tests for the adopt command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from airflow_breeze_manager.cli import app
from airflow_breeze_manager.utils import get_project, resolve_project_from_path, validate_airflow_worktree

runner = CliRunner()


def test_validate_airflow_worktree_valid(mock_airflow_repo, create_worktree):
    """Test validating a valid Airflow worktree."""
    worktree_path = create_worktree("feature-1")

    is_valid, branch, error_msg = validate_airflow_worktree(worktree_path, Path(mock_airflow_repo["repo_dir"]))

    assert is_valid is True
    assert branch == "feature-1"
    assert error_msg == ""


def test_validate_airflow_worktree_nonexistent(mock_airflow_repo, tmp_path):
    """Test validating a non-existent path."""
    nonexistent = tmp_path / "nonexistent"

    is_valid, branch, error_msg = validate_airflow_worktree(nonexistent, Path(mock_airflow_repo["repo_dir"]))

    assert is_valid is False
    assert branch == ""
    assert "does not exist" in error_msg


def test_validate_airflow_worktree_not_git(mock_airflow_repo, tmp_path):
    """Test validating a non-git directory."""
    regular_dir = tmp_path / "regular_dir"
    regular_dir.mkdir()

    is_valid, branch, error_msg = validate_airflow_worktree(regular_dir, Path(mock_airflow_repo["repo_dir"]))

    assert is_valid is False
    assert branch == ""
    assert "Not a git repository" in error_msg


def test_validate_airflow_worktree_wrong_repo(mock_airflow_repo, tmp_path):
    """Test validating a worktree from a different repository."""
    # Create another git repo
    other_repo = tmp_path / "other_repo"
    other_repo.mkdir()
    subprocess.run(["git", "init"], cwd=other_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=other_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=other_repo,
        check=True,
        capture_output=True,
    )

    # Create a commit
    readme = other_repo / "README.md"
    readme.write_text("Other repo")
    subprocess.run(["git", "add", "."], cwd=other_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial"],
        cwd=other_repo,
        check=True,
        capture_output=True,
    )

    is_valid, branch, error_msg = validate_airflow_worktree(other_repo, Path(mock_airflow_repo["repo_dir"]))

    assert is_valid is False
    assert branch == ""
    assert "not from the configured Airflow repository" in error_msg


def test_resolve_project_from_path_existing(tmp_abm_home, sample_project):
    """Test resolving an existing project from its worktree path."""
    # Save the project
    project_dir = tmp_abm_home["projects_dir"] / sample_project.name
    project_dir.mkdir(parents=True, exist_ok=True)
    sample_project.save(project_dir)

    # Resolve it
    result = resolve_project_from_path(Path(sample_project.worktree_path))

    assert result == sample_project.name


@pytest.mark.integration
def test_adopt_command_success(tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker):
    """Test successfully adopting a worktree."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create a worktree
    worktree_path = create_worktree("feature-1")

    # Adopt it
    result = runner.invoke(app, ["adopt", str(worktree_path)])

    assert result.exit_code == 0
    assert "adopted as project" in result.stdout.lower()

    # Verify project was created
    project = get_project("feature-1")
    assert project is not None
    assert project.branch == "feature-1"
    assert project.managed_worktree is False
    assert Path(project.worktree_path) == worktree_path


@pytest.mark.integration
def test_adopt_command_custom_name(tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker):
    """Test adopting a worktree with a custom project name."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create a worktree
    worktree_path = create_worktree("feature-1")

    # Adopt with custom name
    result = runner.invoke(app, ["adopt", str(worktree_path), "--name", "my-custom-name"])

    assert result.exit_code == 0
    assert "my-custom-name" in result.stdout

    # Verify project was created with custom name
    project = get_project("my-custom-name")
    assert project is not None
    assert project.branch == "feature-1"
    assert project.name == "my-custom-name"
    assert project.managed_worktree is False


@pytest.mark.integration
def test_adopt_command_idempotent(tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker):
    """Test that adopting the same worktree twice is idempotent."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create and adopt a worktree
    worktree_path = create_worktree("feature-1")
    runner.invoke(app, ["adopt", str(worktree_path)])

    # Adopt again
    result = runner.invoke(app, ["adopt", str(worktree_path)])

    assert result.exit_code == 0
    assert "already managed" in result.stdout.lower()
    assert "idempotent" in result.stdout.lower()


@pytest.mark.integration
def test_adopt_command_invalid_path(tmp_abm_home, sample_global_config, tmp_path):
    """Test adopting an invalid path."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Try to adopt non-existent path
    result = runner.invoke(app, ["adopt", str(tmp_path / "nonexistent")])

    assert result.exit_code == 1
    assert "Invalid worktree" in result.stdout


@pytest.mark.integration
def test_adopt_command_project_name_conflict(
    tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, sample_project, mock_docker
):
    """Test adopting when project name already exists."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create existing project
    project_dir = tmp_abm_home["projects_dir"] / "feature-1"
    project_dir.mkdir(parents=True, exist_ok=True)
    sample_project.name = "feature-1"
    sample_project.branch = "feature-1"
    sample_project.save(project_dir)

    # Try to adopt worktree with same branch name
    worktree_path = create_worktree("feature-1", "feature-1-worktree")

    result = runner.invoke(app, ["adopt", str(worktree_path)])

    assert result.exit_code == 1
    assert "already exists" in result.stdout
    assert "Use --name" in result.stdout


@pytest.mark.integration
def test_adopt_command_sanitizes_branch_names(tmp_abm_home, mock_airflow_repo, sample_global_config, mock_docker):
    """Test that branch names with slashes are sanitized."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create branch with slashes
    repo_dir = Path(sample_global_config.airflow_repo)
    subprocess.run(
        ["git", "branch", "feature/sub-feature"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )

    # Create worktree
    worktree_base = Path(sample_global_config.worktree_base)
    worktree_path = worktree_base / "feature-sub"
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "feature/sub-feature"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )

    # Adopt it
    result = runner.invoke(app, ["adopt", str(worktree_path)])

    assert result.exit_code == 0

    # Verify project name is sanitized
    project = get_project("feature-sub-feature")
    assert project is not None
    assert project.branch == "feature/sub-feature"
    assert "/" not in project.name
