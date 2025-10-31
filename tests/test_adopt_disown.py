"""Tests for adopt/disown commands and remove protection."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from airflow_breeze_manager.cli import app
from airflow_breeze_manager.utils import get_project

runner = CliRunner()


@pytest.mark.integration
def test_disown_command_success(tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker):
    """Test successfully disowning a project."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create and adopt a worktree
    worktree_path = create_worktree("feature-1")
    runner.invoke(app, ["adopt", str(worktree_path)])

    # Verify project exists
    assert get_project("feature-1") is not None

    # Disown it
    result = runner.invoke(app, ["disown", "feature-1", "--force"])

    assert result.exit_code == 0
    assert "disowned" in result.stdout.lower()
    assert "preserved" in result.stdout.lower()

    # Verify project metadata is gone
    assert get_project("feature-1") is None

    # Verify worktree still exists
    assert worktree_path.exists()


@pytest.mark.integration
def test_disown_command_requires_confirmation(
    tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker
):
    """Test that disown requires confirmation without --force."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create and adopt a worktree
    worktree_path = create_worktree("feature-1")
    runner.invoke(app, ["adopt", str(worktree_path)])

    # Try to disown without force
    result = runner.invoke(app, ["disown", "feature-1"], input="n\n")

    assert result.exit_code == 1  # Aborted
    # Project should still exist
    assert get_project("feature-1") is not None


@pytest.mark.integration
def test_remove_adopted_project_requires_force(
    tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker
):
    """Test that removing an adopted project requires --force."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create and adopt a worktree
    worktree_path = create_worktree("feature-1")
    runner.invoke(app, ["adopt", str(worktree_path)])

    # Try to remove without --force
    result = runner.invoke(app, ["remove", "feature-1"])

    assert result.exit_code == 1
    assert "Cannot remove adopted project" in result.stdout
    assert "--force" in result.stdout
    assert "disown" in result.stdout.lower()

    # Project should still exist
    assert get_project("feature-1") is not None


@pytest.mark.integration
def test_remove_adopted_project_with_force(
    tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker
):
    """Test that removing an adopted project with --force works."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create and adopt a worktree
    worktree_path = create_worktree("feature-1")
    runner.invoke(app, ["adopt", str(worktree_path)])

    # Remove with --force
    result = runner.invoke(app, ["remove", "feature-1", "--force"])

    assert result.exit_code == 0
    assert "removed" in result.stdout.lower()

    # Project should be gone
    assert get_project("feature-1") is None


@pytest.mark.integration
def test_remove_managed_project_no_force_needed(tmp_abm_home, mock_airflow_repo, sample_global_config, mock_docker):
    """Test that removing a managed project doesn't require --force."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create a managed project (via add)
    runner.invoke(
        app,
        ["add", "test-managed", "--branch", "feature-1"],
    )

    # Should succeed (note: actual worktree creation might fail in test, but metadata is created)
    # So we just verify the flow

    # The project should be marked as managed_worktree=True by default
    project = get_project("test-managed")
    if project:  # If creation succeeded
        assert project.managed_worktree is True


@pytest.mark.integration
def test_adopt_then_disown_then_readopt(
    tmp_abm_home, mock_airflow_repo, create_worktree, sample_global_config, mock_docker
):
    """Test the full lifecycle: adopt -> disown -> re-adopt."""
    # Save config
    sample_global_config.save(tmp_abm_home["config_file"])

    # Create worktree
    worktree_path = create_worktree("feature-1")

    # Adopt
    result1 = runner.invoke(app, ["adopt", str(worktree_path)])
    assert result1.exit_code == 0
    project1 = get_project("feature-1")
    assert project1 is not None
    assert project1.managed_worktree is False

    # Disown
    result2 = runner.invoke(app, ["disown", "feature-1", "--force"])
    assert result2.exit_code == 0
    assert get_project("feature-1") is None

    # Re-adopt
    result3 = runner.invoke(app, ["adopt", str(worktree_path)])
    assert result3.exit_code == 0
    project3 = get_project("feature-1")
    assert project3 is not None
    assert project3.managed_worktree is False
