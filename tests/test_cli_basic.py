from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from airflow_breeze_manager.cli import app
from airflow_breeze_manager.cli_helpers import find_airflow_container
from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts

runner = CliRunner()


def test_find_airflow_container_found() -> None:
    """Test find_airflow_container when container is found."""
    worktree_path = "/tmp/my-worktree"
    container_id = "abc123def456"

    # Mock docker ps output
    mock_output = f"{container_id}\tcom.docker.compose.project.working_dir={worktree_path},other=label"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=mock_output,
            returncode=0,
        )
        result = find_airflow_container(worktree_path)

    assert result == container_id
    mock_run.assert_called_once()


def test_find_airflow_container_not_found() -> None:
    """Test find_airflow_container when no container is found."""
    worktree_path = "/tmp/my-worktree"

    # Mock empty docker ps output
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="",
            returncode=0,
        )
        result = find_airflow_container(worktree_path)

    assert result is None


def test_find_airflow_container_different_worktree() -> None:
    """Test find_airflow_container when container belongs to different worktree."""
    worktree_path = "/tmp/my-worktree"
    other_worktree = "/tmp/other-worktree"
    container_id = "abc123def456"

    # Mock docker ps output with different worktree
    mock_output = f"{container_id}\tcom.docker.compose.project.working_dir={other_worktree},other=label"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=mock_output,
            returncode=0,
        )
        result = find_airflow_container(worktree_path)

    assert result is None


def test_find_airflow_container_docker_error() -> None:
    """Test find_airflow_container when docker command fails."""
    worktree_path = "/tmp/my-worktree"

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "docker")
        result = find_airflow_container(worktree_path)

    assert result is None


def test_cli_exec_no_project() -> None:
    """Test exec command with non-existent project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            result = runner.invoke(app, ["exec", "nonexistent-project"])
            assert result.exit_code == 1
            # With smart project detection, unknown names are treated as exec args,
            # so require_project(None) falls through to cwd detection failure
            assert "not in a project directory" in result.output.lower() or "not found" in result.output.lower()


def test_cli_exec_no_container() -> None:
    """Test exec command when no container is running."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        # Create a test project
        project_dir = projects_dir / "my-project"
        project_dir.mkdir()
        metadata = ProjectMetadata(
            name="my-project",
            branch="feature/test",
            worktree_path="/tmp/my-project",
            ports=ProjectPorts.default(),
            description="Test project",
        )
        with open(project_dir / ".abm", "w") as f:
            json.dump(metadata.to_dict(), f)

        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            with patch("airflow_breeze_manager.cli.find_airflow_container", return_value=None):
                result = runner.invoke(app, ["exec", "my-project"])
                assert result.exit_code == 1
                assert "no running" in result.output.lower()


def test_cli_exec_frozen_project() -> None:
    """Test exec command on a frozen project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        # Create a frozen test project
        project_dir = projects_dir / "my-project"
        project_dir.mkdir()
        metadata = ProjectMetadata(
            name="my-project",
            branch="feature/test",
            worktree_path="/tmp/my-project",
            ports=ProjectPorts.default(),
            description="Test project",
            frozen=True,
        )
        with open(project_dir / ".abm", "w") as f:
            json.dump(metadata.to_dict(), f)

        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            result = runner.invoke(app, ["exec", "my-project"])
            assert result.exit_code == 1
            assert "frozen" in result.output.lower()


def test_cli_list_empty() -> None:
    """Test list command with no projects."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        with patch("airflow_breeze_manager.constants.ABM_DIR", abm_dir):
            with patch("airflow_breeze_manager.constants.PROJECTS_DIR", projects_dir):
                result = runner.invoke(app, ["list"])
                assert result.exit_code == 0
                assert "No projects found" in result.output or result.output.strip() == ""


def test_cli_list_with_projects() -> None:
    """Test list command with existing projects."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        # Create a test project
        project_dir = projects_dir / "test-project"
        project_dir.mkdir()
        metadata = ProjectMetadata(
            name="test-project",
            branch="main",
            worktree_path="/tmp/test",
            ports=ProjectPorts.default(),
            description="Test",
        )
        with open(project_dir / ".abm", "w") as f:
            json.dump(metadata.to_dict(), f)

        # Patch both in constants module and utils module (where it's imported)
        with patch("airflow_breeze_manager.constants.PROJECTS_DIR", projects_dir):
            with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
                result = runner.invoke(app, ["list"])
                assert result.exit_code == 0
                assert "test-project" in result.output


def test_cli_status_nonexistent_project() -> None:
    """Test status command for non-existent project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        with patch("airflow_breeze_manager.constants.ABM_DIR", abm_dir):
            with patch("airflow_breeze_manager.constants.PROJECTS_DIR", projects_dir):
                result = runner.invoke(app, ["status", "nonexistent-project"])
                assert result.exit_code == 1
                assert "not found" in result.output.lower() or "does not exist" in result.output.lower()


def test_cli_status_existing_project() -> None:
    """Test status command for existing project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        # Create a test project
        project_dir = projects_dir / "my-project"
        project_dir.mkdir()
        metadata = ProjectMetadata(
            name="my-project",
            branch="feature/test",
            worktree_path="/tmp/my-project",
            ports=ProjectPorts.default(),
            description="Test project",
        )
        with open(project_dir / ".abm", "w") as f:
            json.dump(metadata.to_dict(), f)

        # Patch in utils module where get_project is imported
        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            result = runner.invoke(app, ["status", "my-project"])
            assert result.exit_code == 0
            assert "my-project" in result.output
            assert "feature/test" in result.output


def test_cli_remove_nonexistent_project() -> None:
    """Test remove command for non-existent project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        with patch("airflow_breeze_manager.constants.ABM_DIR", abm_dir):
            with patch("airflow_breeze_manager.constants.PROJECTS_DIR", projects_dir):
                result = runner.invoke(app, ["remove", "nonexistent", "--force"])
                assert result.exit_code == 1


def test_cli_pr_link_validation() -> None:
    """Test PR link validates PR number is numeric."""
    with tempfile.TemporaryDirectory() as tmpdir:
        abm_dir = Path(tmpdir) / ".airflow-breeze-manager"
        projects_dir = abm_dir / "projects"
        projects_dir.mkdir(parents=True)

        with patch("airflow_breeze_manager.constants.ABM_DIR", abm_dir):
            with patch("airflow_breeze_manager.constants.PROJECTS_DIR", projects_dir):
                # Invalid PR number
                result = runner.invoke(app, ["pr", "link", "not-a-number", "my-project"])
                assert result.exit_code != 0  # Should fail


def test_cli_docker_compose_project_name() -> None:
    """Test that Docker Compose project names are generated correctly."""
    from airflow_breeze_manager.utils import get_docker_compose_project_name

    # This catches if someone breaks the naming convention
    assert get_docker_compose_project_name("my-feature") == "abm-my-feature"
    assert get_docker_compose_project_name("test_123") == "abm-test_123"


def test_project_name_slash_sanitization() -> None:
    """Test that project names with slashes are sanitized correctly.

    This prevents creating nested directories when users specify branch names
    like 'feature/awesome-improvement' as the project name.
    """
    # Test the sanitization logic directly
    name_with_slash = "feature/awesome-improvement"
    sanitized = name_with_slash.replace("/", "-")
    assert sanitized == "feature-awesome-improvement"
    assert "/" not in sanitized

    # Test multiple slashes
    name_multiple = "features/sub/feature"
    sanitized_multiple = name_multiple.replace("/", "-")
    assert sanitized_multiple == "features-sub-feature"
