from __future__ import annotations

import json
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts
from airflow_breeze_manager.utils import (
    allocate_ports,
    create_symlinks,
    find_alternative_port,
    get_all_projects,
    get_conflicting_ports,
    get_docker_compose_project_name,
    get_project,
    is_port_in_use,
    remove_symlinks,
)


def test_get_docker_compose_project_name() -> None:
    """Test Docker Compose project name generation."""
    assert get_docker_compose_project_name("my-feature") == "abm-my-feature"
    assert get_docker_compose_project_name("test_123") == "abm-test_123"


def test_is_port_in_use_free_port() -> None:
    """Test port checking for an unused port."""
    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        free_port = s.getsockname()[1]
        # Port is now bound but we're about to release it
    # Port should be free now
    assert not is_port_in_use(free_port)


def test_is_port_in_use_occupied_port() -> None:
    """Test port checking for a used port."""
    # Bind to a port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        # Port is in use
        assert is_port_in_use(port)


def test_find_alternative_port() -> None:
    """Test finding alternative port."""
    used_ports = {28180, 28181, 28182}
    alt_port = find_alternative_port(28180, 28999, used_ports)
    assert alt_port == 28183
    assert alt_port not in used_ports


def test_find_alternative_port_no_available() -> None:
    """Test finding alternative port when none available."""
    used_ports = set(range(28180, 28185))
    alt_port = find_alternative_port(28180, 28183, used_ports)
    assert alt_port is None


def test_get_conflicting_ports_no_conflicts() -> None:
    """Test port conflict detection with no conflicts."""
    ports = ProjectPorts(
        webserver=28180,
        flower=25655,
        postgres=25533,
        mysql=23406,
        redis=26479,
        ssh=12422,
    )
    conflicts = get_conflicting_ports(ports)
    # Should have no conflicts on typical system
    assert isinstance(conflicts, dict)


def test_allocate_ports_default() -> None:
    """Test port allocation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            ports = allocate_ports()
            # Should return default ports if no projects exist
            assert ports.webserver == 28180
            assert ports.flower == 25655
            assert ports.postgres == 25533


def test_allocate_ports_with_existing_projects() -> None:
    """Test port allocation with existing projects."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)

        # Create a fake project with ports
        project_dir = projects_dir / "test-project"
        project_dir.mkdir(parents=True)

        metadata = ProjectMetadata(
            name="test-project",
            branch="main",
            worktree_path="/tmp/test",
            ports=ProjectPorts(
                webserver=28180,
                flower=25655,
                postgres=25533,
                mysql=23406,
                redis=26479,
                ssh=12422,
            ),
        )

        with open(project_dir / ".abm", "w") as f:
            json.dump(metadata.to_dict(), f)

        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            ports = allocate_ports()
            # Should allocate next available ports
            assert ports.webserver == 28181
            assert ports.flower == 25656


def test_get_all_projects_empty() -> None:
    """Test getting all projects when none exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            projects = get_all_projects()
            assert projects == []


def test_get_all_projects_with_projects() -> None:
    """Test getting all projects."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)

        # Create two projects
        for name in ["project1", "project2"]:
            project_dir = projects_dir / name
            project_dir.mkdir(parents=True)

            metadata = ProjectMetadata(
                name=name,
                branch="main",
                worktree_path=f"/tmp/{name}",
                ports=ProjectPorts.default(),
            )

            with open(project_dir / ".abm", "w") as f:
                json.dump(metadata.to_dict(), f)

        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            projects = get_all_projects()
            assert len(projects) == 2
            assert {p.name for p in projects} == {"project1", "project2"}


def test_get_project_exists() -> None:
    """Test getting a specific project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        project_dir = projects_dir / "my-project"
        project_dir.mkdir(parents=True)

        metadata = ProjectMetadata(
            name="my-project",
            branch="feature/test",
            worktree_path="/tmp/my-project",
            ports=ProjectPorts.default(),
        )

        with open(project_dir / ".abm", "w") as f:
            json.dump(metadata.to_dict(), f)

        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            project = get_project("my-project")
            assert project is not None
            assert project.name == "my-project"
            assert project.branch == "feature/test"


def test_get_project_not_exists() -> None:
    """Test getting a non-existent project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        with patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir):
            project = get_project("nonexistent")
            assert project is None


def test_create_symlinks() -> None:
    """Test creating symlinks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir) / "project"
        worktree_dir = Path(tmpdir) / "worktree"
        project_dir.mkdir()
        worktree_dir.mkdir()

        # Create files in project dir
        (project_dir / "PROJECT.md").write_text("# Project")
        (project_dir / "CLAUDE.md").write_text("# Claude")

        create_symlinks(project_dir, worktree_dir, ["PROJECT.md", "CLAUDE.md"])

        # Check symlinks were created
        assert (worktree_dir / "PROJECT.md").is_symlink()
        assert (worktree_dir / "CLAUDE.md").is_symlink()
        assert (worktree_dir / "PROJECT.md").read_text() == "# Project"


def test_remove_symlinks() -> None:
    """Test removing symlinks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir) / "project"
        worktree_dir = Path(tmpdir) / "worktree"
        project_dir.mkdir()
        worktree_dir.mkdir()

        # Create source files and symlinks
        (project_dir / "PROJECT.md").write_text("# Project")
        (worktree_dir / "PROJECT.md").symlink_to(project_dir / "PROJECT.md")

        assert (worktree_dir / "PROJECT.md").exists()

        remove_symlinks(worktree_dir, ["PROJECT.md"])

        # Symlink should be removed
        assert not (worktree_dir / "PROJECT.md").exists()
        # Original file should still exist
        assert (project_dir / "PROJECT.md").exists()
