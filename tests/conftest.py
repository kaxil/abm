"""Shared pytest fixtures for ABM tests."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from airflow_breeze_manager.models import GlobalConfig, ProjectMetadata, ProjectPorts


@pytest.fixture
def tmp_abm_home(tmp_path, monkeypatch):
    """Create a temporary ABM home directory with proper structure."""
    abm_dir = tmp_path / ".airflow-breeze-manager"
    abm_dir.mkdir()

    projects_dir = abm_dir / "projects"
    projects_dir.mkdir()

    config_file = abm_dir / "config.json"

    # Patch the constants to use temporary directories using monkeypatch
    monkeypatch.setattr("airflow_breeze_manager.constants.ABM_DIR", abm_dir)
    monkeypatch.setattr("airflow_breeze_manager.constants.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("airflow_breeze_manager.constants.ABM_CONFIG_FILE", config_file)

    # Also patch in the modules that import these constants
    monkeypatch.setattr("airflow_breeze_manager.cli.ABM_DIR", abm_dir)
    monkeypatch.setattr("airflow_breeze_manager.cli.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("airflow_breeze_manager.cli.ABM_CONFIG_FILE", config_file)
    monkeypatch.setattr("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir)

    return {
        "abm_dir": abm_dir,
        "projects_dir": projects_dir,
        "config_file": config_file,
    }


@pytest.fixture
def mock_airflow_repo(tmp_path):
    """Create a mock Airflow git repository with branches and worktrees."""
    repo_dir = tmp_path / "airflow"
    repo_dir.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )

    # Create initial commit
    readme = repo_dir / "README.md"
    readme.write_text("# Apache Airflow\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )

    # Create some branches
    for branch in ["feature-1", "feature-2", "bugfix-123"]:
        subprocess.run(
            ["git", "branch", branch],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

    return {
        "repo_dir": repo_dir,
        "branches": ["main", "feature-1", "feature-2", "bugfix-123"],
    }


@pytest.fixture
def mock_worktree_base(tmp_path):
    """Create a temporary worktree base directory."""
    worktree_base = tmp_path / "worktrees"
    worktree_base.mkdir()
    return worktree_base


@pytest.fixture
def sample_global_config(mock_airflow_repo, mock_worktree_base):
    """Create a sample GlobalConfig for testing."""
    return GlobalConfig(
        schema_version=1,
        airflow_repo=str(mock_airflow_repo["repo_dir"]),
        worktree_base=str(mock_worktree_base),
    )


@pytest.fixture
def sample_project_ports():
    """Create sample project ports."""
    return ProjectPorts(
        webserver=28180,
        flower=25655,
        postgres=25533,
        mysql=23406,
        redis=26479,
        ssh=12422,
    )


@pytest.fixture
def sample_project(sample_project_ports, mock_worktree_base):
    """Create a sample project metadata for testing."""
    return ProjectMetadata(
        name="test-project",
        branch="feature-1",
        worktree_path=str(mock_worktree_base / "test-project"),
        ports=sample_project_ports,
        description="Test project",
        backend="sqlite",
        python_version="3.11",
        created_at="2025-01-01T00:00:00",
        frozen=False,
        managed_worktree=True,
    )


@pytest.fixture
def sample_adopted_project(sample_project_ports, mock_worktree_base):
    """Create a sample adopted project metadata for testing."""
    return ProjectMetadata(
        name="adopted-project",
        branch="feature-2",
        worktree_path=str(mock_worktree_base / "adopted-project"),
        ports=ProjectPorts(
            webserver=28181,
            flower=25656,
            postgres=25534,
            mysql=23407,
            redis=26480,
            ssh=12423,
        ),
        description="Adopted project",
        backend="sqlite",
        python_version="3.11",
        created_at="2025-01-01T00:00:00",
        frozen=False,
        managed_worktree=False,  # This is an adopted project
    )


@pytest.fixture
def mock_docker():
    """Mock Docker API for container operations."""
    with patch("docker.from_env") as mock_docker_from_env:
        mock_client = MagicMock()
        mock_docker_from_env.return_value = mock_client

        # Mock containers.list() to return empty list by default
        mock_client.containers.list.return_value = []

        yield mock_client


@pytest.fixture
def mock_git_commands():
    """Mock git command execution for testing."""
    with patch("airflow_breeze_manager.utils.run_command") as mock_run:

        def run_command_side_effect(cmd, *args, **kwargs):
            """Simulate git commands."""
            if cmd[0] == "git":
                if cmd[1] == "worktree" and cmd[2] == "list":
                    # Return empty worktree list
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                elif cmd[1] == "branch" and cmd[2] == "--list":
                    # Branch exists
                    return subprocess.CompletedProcess(cmd, 0, stdout=cmd[3] + "\n" if len(cmd) > 3 else "", stderr="")
                elif cmd[1] == "rev-parse" and cmd[2] == "--abbrev-ref":
                    # Return branch name
                    return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
                elif cmd[1] == "worktree" and cmd[2] == "add":
                    # Worktree add succeeds
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            # Default: command succeeds
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run.side_effect = run_command_side_effect
        yield mock_run


@pytest.fixture
def create_worktree(mock_airflow_repo, mock_worktree_base):
    """Helper fixture to create actual worktrees for testing."""

    def _create_worktree(branch: str, name: str | None = None):
        """Create a worktree for the given branch."""
        worktree_name = name or branch
        worktree_path = mock_worktree_base / worktree_name

        subprocess.run(
            ["git", "worktree", "add", str(worktree_path), branch],
            cwd=mock_airflow_repo["repo_dir"],
            check=True,
            capture_output=True,
        )

        return worktree_path

    return _create_worktree
