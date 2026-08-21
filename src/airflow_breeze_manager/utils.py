from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console

from airflow_breeze_manager.constants import PORT_RANGES, PROJECTS_DIR, REPO_SYMLINKED_DIRS
from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts

console = Console()


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    capture_output: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture_output,
        text=True,
        check=check,
        env=env,
    )


def get_all_projects() -> list[ProjectMetadata]:
    """Get all project metadata."""
    if not PROJECTS_DIR.exists():
        return []

    projects = []
    for project_dir in PROJECTS_DIR.iterdir():
        if project_dir.is_dir() and (project_dir / ".abm").exists():
            try:
                project = ProjectMetadata.load(project_dir)
                projects.append(project)
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to load project {project_dir.name}: {e}[/yellow]")
    return projects


def get_project(name: str) -> ProjectMetadata | None:
    """Get project metadata by name."""
    project_dir = PROJECTS_DIR / name
    if not project_dir.exists() or not (project_dir / ".abm").exists():
        return None
    return ProjectMetadata.load(project_dir)


def allocate_ports(exclude_projects: list[str] | None = None) -> ProjectPorts:
    """Allocate available ports for a new project."""
    existing_projects = get_all_projects()
    if exclude_projects:
        existing_projects = [p for p in existing_projects if p.name not in exclude_projects]

    used_ports = {
        "webserver": {p.ports.webserver for p in existing_projects},
        "flower": {p.ports.flower for p in existing_projects},
        "postgres": {p.ports.postgres for p in existing_projects},
        "mysql": {p.ports.mysql for p in existing_projects},
        "redis": {p.ports.redis for p in existing_projects},
        "ssh": {p.ports.ssh for p in existing_projects},
        "mssql": {p.ports.mssql for p in existing_projects},
        "rabbitmq": {p.ports.rabbitmq for p in existing_projects},
    }

    allocated_ports = {}
    for service, (min_port, max_port) in PORT_RANGES.items():
        # Try to find an available port in range
        for port in range(min_port, max_port + 1):
            if port not in used_ports[service]:
                allocated_ports[service] = port
                break
        else:
            raise RuntimeError(f"No available ports for {service} in range {min_port}-{max_port}")

    return ProjectPorts(**allocated_ports)


def get_git_current_branch(repo_path: Path) -> str:
    """Get the current git branch."""
    result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
    )
    return result.stdout.strip()


def git_worktree_exists(repo_path: Path, branch: str) -> bool:
    """Check if a git worktree exists for a branch."""
    result = run_command(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("branch refs/heads/") and line.split("/")[-1] == branch:
            return True
    return False


def git_branch_exists(repo_path: Path, branch: str) -> bool:
    """Check if a git branch exists."""
    result = run_command(
        ["git", "branch", "--list", branch],
        cwd=repo_path,
        capture_output=True,
    )
    return bool(result.stdout.strip())


def validate_airflow_worktree(worktree_path: Path, airflow_repo: Path) -> tuple[bool, str, str]:
    """Validate that a path is a valid Airflow git worktree.

    Args:
        worktree_path: Path to validate
        airflow_repo: Path to the configured Airflow repository

    Returns:
        Tuple of (is_valid, branch_name, error_message)
        If valid, branch_name is populated and error_message is empty
        If invalid, branch_name is empty and error_message contains reason
    """
    if not worktree_path.exists():
        return False, "", f"Path does not exist: {worktree_path}"

    if not worktree_path.is_dir():
        return False, "", f"Path is not a directory: {worktree_path}"

    # Check if it's a git directory
    git_dir = worktree_path / ".git"
    if not git_dir.exists():
        return False, "", f"Not a git repository: {worktree_path}"

    try:
        # Get the worktree's git directory (points to main repo)
        result = run_command(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=worktree_path,
            capture_output=True,
        )
        common_dir = Path(result.stdout.strip()).resolve()

        # Get the main repo's git directory
        result = run_command(
            ["git", "rev-parse", "--git-dir"],
            cwd=airflow_repo,
            capture_output=True,
        )
        main_git_dir = (airflow_repo / result.stdout.strip()).resolve()

        # Check if they point to the same repository
        if common_dir != main_git_dir:
            return False, "", f"Worktree is not from the configured Airflow repository: {airflow_repo}"

        # Get the branch name
        result = run_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
        )
        branch_name = result.stdout.strip()

        if not branch_name or branch_name == "HEAD":
            return False, "", "Worktree is in detached HEAD state"

        return True, branch_name, ""

    except subprocess.CalledProcessError as e:
        return False, "", f"Git command failed: {e}"
    except Exception as e:
        return False, "", f"Unexpected error: {e}"


def resolve_project_from_path(worktree_path: Path) -> str | None:
    """Find if a worktree path is already managed by ABM.

    Args:
        worktree_path: Path to check

    Returns:
        Project name if found, None otherwise
    """
    worktree_path = worktree_path.resolve()
    all_projects = get_all_projects()

    for project in all_projects:
        if Path(project.worktree_path).resolve() == worktree_path:
            return project.name

    return None


def create_symlinks(project_dir: Path, worktree_path: Path, files: list[str]) -> None:
    """Create symlinks from project directory to worktree.

    Handles both files and directories. If source doesn't exist, creates an empty file.

    A file that already exists in the worktree and was not created by ABM (for example
    Airflow's committed ``CLAUDE.md`` symlink to ``AGENTS.md``) is left untouched, so the
    repo's own file is never clobbered.
    """
    for file in files:
        source = project_dir / file
        target = worktree_path / file

        # Create source file if it doesn't exist (files only)
        if not source.exists():
            source.touch()

        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                # Don't remove non-symlink directories
                continue
            # Preserve a file/symlink ABM did not create. Overwriting it -- e.g. Airflow's
            # tracked ``CLAUDE.md`` -> ``AGENTS.md`` -- would hide the repo's own file and
            # leave the worktree with an uncommitted change.
            console.print(f"[yellow]Leaving existing '{file}' in the worktree untouched (not managed by ABM).[/yellow]")
            continue

        # Create symlink
        target.symlink_to(source)


def remove_symlinks(worktree_path: Path, files: list[str]) -> None:
    """Remove symlinks from worktree."""
    for file in files:
        target = worktree_path / file
        if target.is_symlink():
            target.unlink()


def symlink_repo_dirs(repo_path: Path, worktree_path: Path, quiet: bool = False) -> None:
    """Symlink directories from the main repo into a worktree.

    For each directory in REPO_SYMLINKED_DIRS (.claude, .cursor, etc.), creates
    a symlink in the worktree pointing to the original in the main repo. Skips
    directories that don't exist in the source or already exist as real directories
    in the worktree.
    """
    for dir_name in REPO_SYMLINKED_DIRS:
        source = repo_path / dir_name
        target = worktree_path / dir_name

        if not source.exists():
            if not quiet:
                console.print(f"[dim]Note: {dir_name} not found in main Airflow repo (it's gitignored)[/dim]")
            continue

        if target.exists() and not target.is_symlink():
            if not quiet:
                console.print(f"[yellow]Warning: {dir_name} exists as a directory, not creating symlink[/yellow]")
            continue

        if target.is_symlink():
            target.unlink()

        target.symlink_to(source)
        if not quiet:
            console.print(f"[dim]-> Created {dir_name} symlink to {source}[/dim]")


def remove_repo_dir_symlinks(worktree_path: Path, quiet: bool = False) -> None:
    """Remove repo directory symlinks from a worktree."""
    for dir_name in REPO_SYMLINKED_DIRS:
        target = worktree_path / dir_name
        if target.is_symlink():
            target.unlink()
            if not quiet:
                console.print(f"[dim]-> Removed {dir_name} symlink[/dim]")


def build_breeze_command(
    breeze_cmd: list[str],
    worktree_path: Path,
) -> list[str]:
    """Build the actual command to invoke breeze, using uv run when possible.

    If dev/breeze/pyproject.toml exists in the worktree, wraps the command
    with ``uv run --project`` so breeze runs from the worktree's own package.
    Otherwise returns the original command for global execution.

    Set ABM_USE_GLOBAL_BREEZE=1 to force using the globally installed breeze.
    """
    if os.environ.get("ABM_USE_GLOBAL_BREEZE", "").lower() in ("1", "true", "yes"):
        return breeze_cmd

    breeze_project = worktree_path / "dev" / "breeze"
    if (breeze_project / "pyproject.toml").exists():
        return ["uv", "run", "--project", str(breeze_project), *breeze_cmd]

    return breeze_cmd


def get_docker_compose_project_name(project_name: str) -> str:
    """Get Docker Compose project name for isolation."""
    # Based on breeze pattern: airflow-test-{project_name}
    # We use a different prefix to avoid conflicts with breeze
    return f"abm-{project_name}"


def is_port_in_use(port: int) -> bool:
    """Check if a port is in use."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def get_conflicting_ports(ports: ProjectPorts) -> dict[str, int]:
    """Check which ports are already in use."""
    conflicts = {}
    for service in ["webserver", "flower", "postgres", "mysql", "redis", "ssh", "mssql", "rabbitmq"]:
        port = getattr(ports, service)
        if is_port_in_use(port):
            conflicts[service] = port
    return conflicts


def find_alternative_port(start_port: int, max_port: int, used_ports: set[int]) -> int | None:
    """Find an alternative port in the range that's not in use."""
    for port in range(start_port, max_port + 1):
        if port not in used_ports and not is_port_in_use(port):
            return port
    return None


def check_webserver_health(port: int, timeout: float = 1.0) -> str:
    """Check if the Airflow webserver is reachable on the given port.

    Probes /api/v2/version then /api/v1/version without auth headers.
    Treats 200, 401, and 403 as healthy (server is responding to HTTP
    requests — 401/403 just means auth is required).

    Uses a short timeout (default 1s) since this targets localhost only.

    Returns:
        HEALTH_HEALTHY or HEALTH_UNHEALTHY constant.
    """
    import urllib.error
    import urllib.request

    from airflow_breeze_manager.constants import HEALTH_HEALTHY, HEALTH_UNHEALTHY

    for version in ("v2", "v1"):
        url = f"http://localhost:{port}/api/{version}/version"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 401, 403):
                    return HEALTH_HEALTHY
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return HEALTH_HEALTHY
        except (urllib.error.URLError, OSError):
            continue

    return HEALTH_UNHEALTHY


def stop_project_containers(worktree_path: str) -> None:
    """Stop all Docker containers for a specific ABM project."""
    import docker

    client = docker.from_env()
    containers = client.containers.list()

    stopped_count = 0
    for container in containers:
        labels = container.labels
        working_dir = labels.get("com.docker.compose.project.working_dir", "")

        # Check if this container belongs to the specified worktree
        if working_dir.startswith(str(worktree_path)):
            try:
                # Refresh container to get latest state
                container.reload()
                container.stop(timeout=10)
                container.remove()
                stopped_count += 1
                console.print(f"[dim]Stopped: {container.name}[/dim]")
            except docker.errors.NotFound:
                # Container already gone, that's fine
                pass
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to stop container {container.name}: {e}[/yellow]")

    if stopped_count > 0:
        console.print(f"[green]✓ Stopped {stopped_count} container(s)[/green]")
    else:
        console.print("[dim]No running containers found for this project[/dim]")


def get_running_containers() -> dict[str, dict[str, Any]]:
    """Get running Docker containers for ABM projects.

    Returns:
        Dict mapping project names to container info dict with keys:
        - 'services': list of service names
        - 'is_start_airflow': bool indicating if running full Airflow
    """
    import docker

    try:
        client = docker.from_env()
        containers = client.containers.list()

        # Get all ABM projects to map worktree paths to project names
        all_projects = get_all_projects()
        worktree_to_project = {str(Path(p.worktree_path)): p.name for p in all_projects}

        # Group containers by ABM project (using working directory)
        project_containers: dict[str, dict[str, Any]] = {}

        for container in containers:
            labels = container.labels

            # Get the working directory from compose labels
            working_dir = labels.get("com.docker.compose.project.working_dir", "")

            # Check if this container is from an ABM project worktree
            for worktree_path, project_name in worktree_to_project.items():
                if working_dir.startswith(worktree_path):
                    service_name = labels.get("com.docker.compose.service", "unknown")

                    if project_name not in project_containers:
                        project_containers[project_name] = {
                            "services": [],
                            "is_start_airflow": False,
                        }

                    project_containers[project_name]["services"].append(service_name)

                    # Check if running start-airflow (has tmux/mprocs and multiple airflow processes)
                    # Also detects headless mode (airflow processes without a terminal multiplexer)
                    try:
                        top_output = container.top()
                        processes = top_output.get("Processes", [])
                        for process in processes:
                            # Process is a list: [PID, USER, TIME, COMMAND]
                            if len(process) > 3:
                                cmd_str = str(process[3]).lower()
                                if "tmux" in cmd_str or "mprocs" in cmd_str:
                                    project_containers[project_name]["is_start_airflow"] = True
                                    break
                                # Headless mode: airflow processes running directly
                                if "airflow" in cmd_str and ("scheduler" in cmd_str or "api-server" in cmd_str):
                                    project_containers[project_name]["is_start_airflow"] = True
                                    break
                    except Exception:
                        # If we can't check, assume shell
                        pass

                    break

        return project_containers
    except Exception:
        # If Docker is not available or any error, return empty dict
        return {}
