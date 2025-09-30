from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from airflow_breeze_manager.cli import app
from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts

runner = CliRunner()


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
