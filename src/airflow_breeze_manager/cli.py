from __future__ import annotations

import builtins
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from airflow_breeze_manager.cli_helpers import cleanup_breeze_containers, find_airflow_container
from airflow_breeze_manager.constants import (
    ABM_CONFIG_FILE,
    ABM_DIR,
    DEFAULT_AIRFLOW_REPO,
    DEFAULT_WORKTREE_BASE,
    HEADLESS_POLL_INTERVAL,
    HEADLESS_READY_TIMEOUT,
    HEALTH_HEALTHY,
    HEALTH_NOT_APPLICABLE,
    HEALTH_NOT_RUNNING,
    PROJECTS_DIR,
    SCHEMA_VERSION,
    SYMLINKED_FILES,
)
from airflow_breeze_manager.models import GlobalConfig, ProjectMetadata
from airflow_breeze_manager.output import (
    API_ERROR,
    BRANCH_NOT_FOUND,
    DOCKER_ERROR,
    INVALID_INPUT,
    INVALID_WORKTREE,
    NOT_INITIALIZED,
    PORT_CONFLICT,
    PROJECT_EXISTS,
    PROJECT_FROZEN,
    PROJECT_NOT_FOUND,
    WORKTREE_EXISTS,
    is_json_mode,
    json_error,
    json_success,
    safe_confirm,
    safe_prompt,
    set_agent_mode,
)
from airflow_breeze_manager.utils import (
    allocate_ports,
    check_webserver_health,
    console,
    create_symlinks,
    find_alternative_port,
    get_all_projects,
    get_conflicting_ports,
    get_docker_compose_project_name,
    get_project,
    get_running_containers,
    git_branch_exists,
    git_worktree_exists,
    remove_symlinks,
    resolve_project_from_path,
    run_command,
    stop_project_containers,
    validate_airflow_worktree,
)

app = typer.Typer(
    help="Manage multiple Airflow development environments with isolated breeze instances.\n\n"
    "Agent mode: pass --json/--yes [bold]before[/bold] the command, e.g. abm --json list.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    epilog="[dim]EXAMPLES[/dim]\n\n"
    "  abm shell proj[dim]                      # interactive shell (ports forwarded)[/dim]\n\n"
    "  abm run proj pytest tests/[dim]          # one-off command (no ports, auto-cleanup)[/dim]\n\n"
    "  abm start-airflow proj[dim]              # interactive Airflow (mprocs, needs TTY)[/dim]\n\n"
    "  abm start-airflow proj --headless[dim]   # headless Airflow (no TTY needed)[/dim]\n\n"
    "  abm stop-airflow proj[dim]               # stop headless Airflow[/dim]\n\n"
    "  abm --json list[dim]                     # projects as JSON[/dim]\n\n"
    "  abm --json status my-project[dim]        # details + running state[/dim]\n\n"
    "  abm --yes remove proj -f[dim]            # skip confirmation[/dim]",
)


# ---------------------------------------------------------------------------
# Global --json / --yes flags via Typer callback
# ---------------------------------------------------------------------------


@app.callback()
def main(
    json: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON (implies --yes, non-interactive)"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip all confirmation prompts"),
    ] = False,
) -> None:
    """Manage multiple Airflow development environments with isolated breeze instances."""
    set_agent_mode(json_mode=json, yes_mode=yes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_config() -> GlobalConfig:
    """Get or create global configuration."""
    if not ABM_CONFIG_FILE.exists():
        if is_json_mode():
            json_error("ABM not initialized. Run 'abm init' first.", NOT_INITIALIZED)
        console.print("[red]ABM not initialized. Run 'abm init' first.[/red]")
        raise typer.Exit(1)
    config = GlobalConfig.load(ABM_CONFIG_FILE)
    if not config:
        if is_json_mode():
            json_error("Failed to load configuration.", NOT_INITIALIZED)
        console.print("[red]Failed to load configuration.[/red]")
        raise typer.Exit(1)
    return config


def require_project(name: str | None = None) -> tuple[ProjectMetadata, Path]:
    """Get project metadata or exit if not found."""
    if name is None:
        # Try to detect from current directory
        cwd = Path.cwd()
        for project in get_all_projects():
            if str(cwd).startswith(project.worktree_path):
                name = project.name
                break
        if name is None:
            if is_json_mode():
                json_error("Not in a project directory. Specify project name.", PROJECT_NOT_FOUND)
            console.print("[red]Not in a project directory. Specify project name.[/red]")
            raise typer.Exit(1)

    project_or_none = get_project(name)
    if project_or_none is None:
        if is_json_mode():
            json_error(f"Project '{name}' not found.", PROJECT_NOT_FOUND)
        console.print(f"[red]Project '{name}' not found.[/red]")
        raise typer.Exit(1)

    # At this point, project_or_none is guaranteed to be ProjectMetadata (not None)
    project_dir = PROJECTS_DIR / name
    return project_or_none, project_dir


def _resolve_port_conflicts(project: ProjectMetadata, project_dir: Path, conflicts: dict[str, int]) -> None:
    """Try to auto-resolve port conflicts or exit.

    In --json/--yes mode, automatically finds alternative ports.
    In interactive mode, asks the user.
    """
    if not conflicts:
        return

    if is_json_mode():
        # Auto-resolve in agent mode
        _auto_resolve_ports(project, project_dir, conflicts)
        return

    console.print("[red]Port conflict detected![/red]\n")
    console.print("The following ports are already in use:")
    for service, port in conflicts.items():
        console.print(f"  {service}: {port}")

    console.print("\n[yellow]This usually means:[/yellow]")
    console.print("  1. Another breeze instance is running (run 'abm cleanup')")
    console.print("  2. Another ABM project is running (check 'abm list')")
    console.print("  3. Some other service is using these ports")

    console.print("\n[cyan]Quick fixes:[/cyan]")
    console.print("  Run: abm cleanup")
    console.print("  Or: abm docker down <other-project>")
    console.print("  Or: lsof -i :<port> to see what's using it")

    if safe_confirm("\nTry to automatically find alternative ports?", default=True):
        _auto_resolve_ports(project, project_dir, conflicts)
    else:
        raise typer.Exit(1)


def _auto_resolve_ports(project: ProjectMetadata, project_dir: Path, conflicts: dict[str, int]) -> None:
    """Find alternative ports for conflicting services."""
    from airflow_breeze_manager.constants import PORT_RANGES

    new_ports = {}
    all_existing = get_all_projects()
    used_ports = {
        service: {getattr(p.ports, service) for p in all_existing if p.name != project.name}
        for service in ["webserver", "flower", "postgres", "mysql", "redis", "ssh", "mssql", "rabbitmq"]
    }

    failed = []
    for service in conflicts.keys():
        min_port, max_port = PORT_RANGES[service]
        alt_port = find_alternative_port(min_port, max_port, used_ports[service])
        if alt_port:
            new_ports[service] = alt_port
        else:
            failed.append(service)

    if failed:
        if is_json_mode():
            json_error(
                f"Could not find alternative ports for: {', '.join(failed)}",
                PORT_CONFLICT,
            )
        console.print(f"\n[red]Could not find alternative ports for: {', '.join(failed)}[/red]")
        console.print("Port ranges exhausted. Please clean up containers or adjust port ranges.")
        raise typer.Exit(1)

    # Update project ports
    for service, port in new_ports.items():
        setattr(project.ports, service, port)
    project.save(project_dir)

    if not is_json_mode():
        console.print("\n[green]Updated ports:[/green]")
        for service, port in new_ports.items():
            console.print(f"  {service}: {port}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command(rich_help_panel="Core Commands")
def init(
    airflow_repo: Annotated[
        str | None,
        typer.Option(help="Path to Airflow repository"),
    ] = None,
    worktree_base: Annotated[
        str | None,
        typer.Option(help="Base directory for worktrees"),
    ] = None,
) -> None:
    """Initialize Airflow Breeze Manager."""
    if ABM_CONFIG_FILE.exists():
        config = GlobalConfig.load(ABM_CONFIG_FILE)
        if is_json_mode():
            data = config.to_dict() if config else {}
            data["already_initialized"] = True
            json_success(data)
        console.print("[yellow]ABM already initialized.[/yellow]")
        if config:
            console.print(f"   Airflow repo: {config.airflow_repo}")
            console.print(f"   Worktree base: {config.worktree_base}")
        return

    # Try to detect Airflow repo
    if airflow_repo is None:
        # 1. Try current directory
        cwd = Path.cwd()
        if (cwd / ".git").exists() and (cwd / "airflow-core").exists():
            if not is_json_mode():
                console.print(f"[cyan]Detected Airflow repository in current directory: {cwd}[/cyan]")
            if safe_confirm("Use this as the Airflow repository?", default=True):
                airflow_repo = str(cwd)

        # 2. Fall back to default
        if airflow_repo is None:
            default_path = Path(DEFAULT_AIRFLOW_REPO).expanduser()
            if default_path.exists() and (default_path / ".git").exists():
                if not is_json_mode():
                    console.print(f"[cyan]Found Airflow repository at default location: {default_path}[/cyan]")
                if safe_confirm("Use this as the Airflow repository?", default=True):
                    airflow_repo = str(default_path)

        # 3. Ask user / use default in agent mode
        if airflow_repo is None:
            if not is_json_mode():
                console.print("[yellow]Could not auto-detect Airflow repository.[/yellow]")
            airflow_repo = safe_prompt("Enter path to Airflow repository", default=DEFAULT_AIRFLOW_REPO)

    if worktree_base is None:
        default_worktree = Path(DEFAULT_WORKTREE_BASE).expanduser()
        if not is_json_mode():
            console.print(f"[cyan]Worktrees will be created in: {default_worktree}[/cyan]")
        if not safe_confirm("Use this location?", default=True):
            worktree_base = safe_prompt("Enter base directory for worktrees", default=DEFAULT_WORKTREE_BASE)
        else:
            worktree_base = str(default_worktree)

    # Validate Airflow repo
    repo_path = Path(airflow_repo).expanduser().resolve()
    if not repo_path.exists():
        if is_json_mode():
            json_error(f"Directory does not exist: {repo_path}", INVALID_INPUT)
        console.print(f"[red]Directory does not exist: {repo_path}[/red]")
        raise typer.Exit(1)

    if not (repo_path / ".git").exists():
        if is_json_mode():
            json_error(f"Not a git repository: {repo_path}", INVALID_INPUT)
        console.print(f"[red]Not a git repository: {repo_path}[/red]")
        console.print("Expected to find .git directory")
        raise typer.Exit(1)

    # Check if it looks like Airflow
    if not (repo_path / "airflow-core").exists() and not (repo_path / "airflow").exists():
        if not is_json_mode():
            console.print("[yellow]Warning: This doesn't look like an Airflow repository[/yellow]")
            console.print(f"Expected to find 'airflow-core' or 'airflow' directory in {repo_path}")
        if not safe_confirm("Continue anyway?", default=False):
            raise typer.Exit(1)

    # Create directories
    ABM_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    Path(worktree_base).expanduser().mkdir(parents=True, exist_ok=True)

    # Save configuration
    config = GlobalConfig(
        schema_version=SCHEMA_VERSION,
        airflow_repo=str(repo_path),
        worktree_base=str(Path(worktree_base).expanduser().resolve()),
    )
    config.save(ABM_CONFIG_FILE)

    if is_json_mode():
        json_success(config.to_dict())

    console.print("[green]Airflow Breeze Manager initialized![/green]")
    console.print(f"   Airflow repo: {repo_path}")
    console.print(f"   Worktree base: {worktree_base}")


@app.command(rich_help_panel="Core Commands")
def add(
    name: Annotated[str, typer.Argument(help="Project name")],
    branch: Annotated[
        str | None,
        typer.Option("--branch", "-b", help="Git branch name (defaults to project name)"),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="Project description"),
    ] = None,
    backend: Annotated[
        str,
        typer.Option(help="Database backend"),
    ] = "sqlite",
    python_version: Annotated[
        str,
        typer.Option(help="Python version"),
    ] = "3.12",
    create_branch: Annotated[
        bool,
        typer.Option("--create-branch", help="Create new branch if it doesn't exist"),
    ] = False,
) -> None:
    """Add a new project with isolated environment."""
    config = get_config()

    # Sanitize project name - replace slashes with dashes (like branch names "feature/foo" -> "feature-foo")
    project_name = name.replace("/", "-")

    # Warn if name was changed
    if project_name != name and not is_json_mode():
        console.print(f"[yellow]Note: Project name sanitized from '{name}' to '{project_name}'[/yellow]")
        console.print("[dim]Slashes in project names are not allowed (they create nested directories)[/dim]")

    # Check if project already exists
    if get_project(project_name):
        if is_json_mode():
            json_error(f"Project '{project_name}' already exists.", PROJECT_EXISTS)
        console.print(f"[red]Project '{project_name}' already exists.[/red]")
        raise typer.Exit(1)

    branch = branch or name
    repo_path = Path(config.airflow_repo)
    worktree_path = Path(config.worktree_base) / project_name

    # Check if worktree already exists
    if worktree_path.exists():
        if is_json_mode():
            json_error(f"Worktree path already exists: {worktree_path}", WORKTREE_EXISTS)
        console.print(f"[red]Worktree path already exists: {worktree_path}[/red]")
        raise typer.Exit(1)

    # Check if branch exists
    if not git_branch_exists(repo_path, branch):
        if create_branch:
            if not is_json_mode():
                console.print(f"Creating new branch: {branch}")
            run_command(["git", "branch", branch], cwd=repo_path)
        else:
            if is_json_mode():
                json_error(
                    f"Branch '{branch}' does not exist. Use --create-branch to create it.",
                    BRANCH_NOT_FOUND,
                )
            console.print(f"[red]Branch '{branch}' does not exist. Use --create-branch to create it.[/red]")
            raise typer.Exit(1)

    # Check if worktree for branch already exists
    if git_worktree_exists(repo_path, branch):
        if is_json_mode():
            json_error(f"Worktree for branch '{branch}' already exists.", WORKTREE_EXISTS)
        console.print(f"[yellow]Worktree for branch '{branch}' already exists.[/yellow]")
        console.print("Remove existing worktree first: git worktree remove <path>")
        raise typer.Exit(1)

    # Allocate ports
    ports = allocate_ports()

    # Create worktree
    if not is_json_mode():
        console.print(f"Creating worktree at {worktree_path}...")
    run_command(["git", "worktree", "add", str(worktree_path), branch], cwd=repo_path)

    # Create project directory
    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create project metadata
    project = ProjectMetadata(
        name=project_name,
        branch=branch,
        worktree_path=str(worktree_path),
        ports=ports,
        description=description or f"Airflow development for {branch}",
        backend=backend,
        python_version=python_version,
        created_at=datetime.now().isoformat(),
    )
    project.save(project_dir)

    # Create PROJECT.md template
    project_md = project_dir / "PROJECT.md"
    if not project_md.exists():
        project_md.write_text(f"""# {project_name}

## Description
{project.description}

## Branch
`{branch}`

## Ports
- Webserver: {ports.webserver}
- Flower: {ports.flower}
- Postgres: {ports.postgres}
- MySQL: {ports.mysql}
- MSSQL: {ports.mssql}
- Redis: {ports.redis}
- RabbitMQ: {ports.rabbitmq}
- SSH: {ports.ssh}

## Notes
Add your notes here...
""")

    # Create CLAUDE.md template for AI assistant context
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(f"""# Project Context for AI Assistants

## Project: {project_name}

### Branch
`{branch}`

### Description
{project.description}

### Development Environment
- **Python**: {python_version}
- **Backend**: {backend}
- **Webserver**: http://localhost:{ports.webserver}

### What I'm Working On
<!-- Add context about what you're building, the problem you're solving, etc. -->


### Key Files/Areas
<!-- List the main files or directories relevant to this work -->


### Testing Strategy
<!-- How to test the changes -->


### Notes & Decisions
<!-- Important decisions, gotchas, things to remember -->


### Related Issues/PRs
<!-- Links to related GitHub issues, discussions, etc. -->

""")

    # Create Breeze environment config for passing env vars into container
    breeze_config_dir = worktree_path / "files" / "airflow-breeze-config"
    breeze_config_dir.mkdir(parents=True, exist_ok=True)

    env_file = breeze_config_dir / "environment_variables.env"
    env_vars = []

    # Set instance name for UI identification
    env_vars.append(f'AIRFLOW__API__INSTANCE_NAME="ABM: {project_name}"')

    # Use project-specific database name for Postgres/MySQL isolation
    if backend in ("postgres", "mysql"):
        # Sanitize project name for database naming (replace hyphens with underscores)
        db_name = f"airflow_{project_name.replace('-', '_')}"
        env_vars.append("# Database isolation - each project gets its own database")
        env_vars.append(f"ABM_DB_NAME={db_name}")
        if backend == "postgres":
            env_vars.append(
                f"AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://postgres:airflow@postgres/{db_name}"
            )
            env_vars.append(f"AIRFLOW__CELERY__RESULT_BACKEND=db+postgresql://postgres:airflow@postgres/{db_name}")
        else:  # mysql
            env_vars.append(f"AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=mysql://root@mysql:3306/{db_name}")
            env_vars.append(f"AIRFLOW__CELERY__RESULT_BACKEND=db+mysql://root@mysql:3306/{db_name}")

    env_file.write_text("\n".join(env_vars) + "\n")

    # Create init script to create database if it doesn't exist
    if backend in ("postgres", "mysql"):
        init_script = breeze_config_dir / "init.sh"
        if backend == "postgres":
            script_content = f"""#!/bin/bash
# Create database if it doesn't exist
if [ "${{BACKEND}}" = "postgres" ]; then
    echo "Ensuring database '{db_name}' exists..."
    PGPASSWORD=airflow psql -h postgres -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = '{db_name}'" | grep -q 1 || {{
        echo "Creating database '{db_name}'..."
        PGPASSWORD=airflow psql -h postgres -U postgres -c "CREATE DATABASE {db_name};"
    }}
fi
"""
        else:  # mysql
            script_content = f"""#!/bin/bash
# Create database if it doesn't exist
if [ "${{BACKEND}}" = "mysql" ]; then
    echo "Ensuring database '{db_name}' exists..."
    mysql -h mysql -u root -e "CREATE DATABASE IF NOT EXISTS {db_name};"
fi
"""
        init_script.write_text(script_content)
        init_script.chmod(0o755)

    # Create symlinks for ABM-managed files
    create_symlinks(project_dir, worktree_path, SYMLINKED_FILES)

    # Create .cursor symlink to main Airflow repo (if it exists)
    # This allows Cursor to work immediately without manual setup
    airflow_cursor_dir = Path(config.airflow_repo) / ".cursor"
    worktree_cursor_link = worktree_path / ".cursor"

    if airflow_cursor_dir.exists():
        # Remove existing .cursor if it's a regular directory (shouldn't happen, but be safe)
        if worktree_cursor_link.exists() and not worktree_cursor_link.is_symlink():
            if not is_json_mode():
                console.print("[yellow]Warning: .cursor exists as a directory, not creating symlink[/yellow]")
        elif worktree_cursor_link.is_symlink():
            # Already a symlink, update it
            worktree_cursor_link.unlink()
            worktree_cursor_link.symlink_to(airflow_cursor_dir)
        else:
            # Create new symlink
            worktree_cursor_link.symlink_to(airflow_cursor_dir)
            if not is_json_mode():
                console.print(f"[dim]-> Created .cursor symlink to {airflow_cursor_dir}[/dim]")
    elif not is_json_mode():
        console.print("[dim]Note: .cursor not found in main Airflow repo (it's gitignored)[/dim]")

    if is_json_mode():
        json_success(project.to_rich_dict())

    console.print(f"[green]Project '{project_name}' created successfully![/green]")
    console.print(f"   Branch: {branch}")
    console.print(f"   Worktree: {worktree_path}")
    console.print(f"   Webserver: http://localhost:{ports.webserver}")


@app.command(rich_help_panel="Core Commands")
def adopt(
    worktree_path: Annotated[str, typer.Argument(help="Path to existing worktree to adopt")],
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Project name (defaults to branch name)"),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="Project description"),
    ] = None,
    backend: Annotated[
        str,
        typer.Option(help="Database backend"),
    ] = "sqlite",
    python_version: Annotated[
        str,
        typer.Option(help="Python version"),
    ] = "3.12",
) -> None:
    """Adopt an existing worktree into ABM management.

    This allows you to import worktrees created manually or by other tools
    into ABM. The worktree must be from the configured Airflow repository.

    The command is idempotent - running it multiple times on the same worktree
    will not cause errors.
    """
    config = get_config()
    worktree = Path(worktree_path).resolve()

    # Validate worktree belongs to configured Airflow repo
    is_valid, branch, error_msg = validate_airflow_worktree(worktree, Path(config.airflow_repo))
    if not is_valid:
        if is_json_mode():
            json_error(f"Invalid worktree: {error_msg}", INVALID_WORKTREE)
        console.print(f"[red]Invalid worktree: {error_msg}[/red]")
        raise typer.Exit(1)

    # Check if already managed by ABM (idempotent behavior)
    existing_project = resolve_project_from_path(worktree)
    if existing_project:
        if is_json_mode():
            proj, _ = require_project(existing_project)
            json_success(proj.to_rich_dict())
        console.print(f"[yellow]Worktree is already managed as project '{existing_project}'[/yellow]")
        console.print("[dim]Nothing to do (idempotent)[/dim]")
        return

    # Determine project name
    project_name = name or branch
    project_name = project_name.replace("/", "-")  # Sanitize branch names like "feature/foo"

    # Check if project name already exists
    if get_project(project_name):
        if is_json_mode():
            json_error(f"Project '{project_name}' already exists.", PROJECT_EXISTS)
        console.print(f"[red]Project '{project_name}' already exists.[/red]")
        console.print("[yellow]Hint: Use --name to specify a different project name[/yellow]")
        raise typer.Exit(1)

    # Allocate ports
    if not is_json_mode():
        console.print(f"Adopting worktree: {worktree}")
        console.print(f"Branch: {branch}")
        console.print(f"Project name: {project_name}")
    ports = allocate_ports()

    # Create project directory
    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create project metadata with managed_worktree=False
    project = ProjectMetadata(
        name=project_name,
        branch=branch,
        worktree_path=str(worktree),
        ports=ports,
        description=description or f"Adopted Airflow development for {branch}",
        backend=backend,
        python_version=python_version,
        created_at=datetime.now().isoformat(),
        managed_worktree=False,  # This is the key difference from 'add'
    )
    project.save(project_dir)

    # Create PROJECT.md template
    project_md = project_dir / "PROJECT.md"
    if not project_md.exists():
        project_md.write_text(f"""# {project_name}

## Description
{project.description}

## Branch
`{branch}`

## Ports
- Webserver: {ports.webserver}
- Flower: {ports.flower}
- Postgres: {ports.postgres}
- MySQL: {ports.mysql}
- MSSQL: {ports.mssql}
- Redis: {ports.redis}
- RabbitMQ: {ports.rabbitmq}
- SSH: {ports.ssh}

## Notes
Add your notes here...
""")

    # Create CLAUDE.md template for AI assistant context
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(f"""# Project Context for AI Assistants

## Project: {project_name}

### Branch
`{branch}`

### Description
{project.description}

### Development Environment
- **Python**: {python_version}
- **Backend**: {backend}
- **Webserver**: http://localhost:{ports.webserver}

### What I'm Working On
<!-- Add context about what you're building, the problem you're solving, etc. -->


### Key Files/Areas
<!-- List the main files or directories relevant to this work -->


### Testing Strategy
<!-- How to test the changes -->


### Notes & Decisions
<!-- Important decisions, gotchas, things to remember -->


### Related Issues/PRs
<!-- Links to related GitHub issues, discussions, etc. -->

""")

    # Create Breeze environment config for passing env vars into container
    breeze_config_dir = worktree / "files" / "airflow-breeze-config"
    breeze_config_dir.mkdir(parents=True, exist_ok=True)

    env_file = breeze_config_dir / "environment_variables.env"
    env_vars = []

    # Set instance name for UI identification
    env_vars.append(f'AIRFLOW__API__INSTANCE_NAME="ABM: {project_name}"')

    # Use project-specific database name for Postgres/MySQL isolation
    if backend in ("postgres", "mysql"):
        # Sanitize project name for database naming (replace hyphens with underscores)
        db_name = f"airflow_{project_name.replace('-', '_')}"
        env_vars.append("# Database isolation - each project gets its own database")
        env_vars.append(f"ABM_DB_NAME={db_name}")
        if backend == "postgres":
            env_vars.append(
                f"AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://postgres:airflow@postgres/{db_name}"
            )
            env_vars.append(f"AIRFLOW__CELERY__RESULT_BACKEND=db+postgresql://postgres:airflow@postgres/{db_name}")
        else:  # mysql
            env_vars.append(f"AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=mysql://root@mysql:3306/{db_name}")
            env_vars.append(f"AIRFLOW__CELERY__RESULT_BACKEND=db+mysql://root@mysql:3306/{db_name}")

    env_file.write_text("\n".join(env_vars) + "\n")

    # Create init script to create database if it doesn't exist
    if backend in ("postgres", "mysql"):
        init_script = breeze_config_dir / "init.sh"
        if backend == "postgres":
            script_content = f"""#!/bin/bash
# Create database if it doesn't exist
if [ "${{BACKEND}}" = "postgres" ]; then
    echo "Ensuring database '{db_name}' exists..."
    PGPASSWORD=airflow psql -h postgres -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = '{db_name}'" | grep -q 1 || {{
        echo "Creating database '{db_name}'..."
        PGPASSWORD=airflow psql -h postgres -U postgres -c "CREATE DATABASE {db_name};"
    }}
fi
"""
        else:  # mysql
            script_content = f"""#!/bin/bash
# Create database if it doesn't exist
if [ "${{BACKEND}}" = "mysql" ]; then
    echo "Ensuring database '{db_name}' exists..."
    mysql -h mysql -u root -e "CREATE DATABASE IF NOT EXISTS {db_name};"
fi
"""
        init_script.write_text(script_content)
        init_script.chmod(0o755)

    # Create symlinks for ABM-managed files
    create_symlinks(project_dir, worktree, SYMLINKED_FILES)

    # Create .cursor symlink to main Airflow repo (if it exists)
    airflow_cursor_dir = Path(config.airflow_repo) / ".cursor"
    worktree_cursor_link = worktree / ".cursor"

    if airflow_cursor_dir.exists():
        if worktree_cursor_link.exists() and not worktree_cursor_link.is_symlink():
            if not is_json_mode():
                console.print("[yellow]Warning: .cursor exists as a directory, not creating symlink[/yellow]")
        elif worktree_cursor_link.is_symlink():
            worktree_cursor_link.unlink()
            worktree_cursor_link.symlink_to(airflow_cursor_dir)
        else:
            worktree_cursor_link.symlink_to(airflow_cursor_dir)
            if not is_json_mode():
                console.print(f"[dim]-> Created .cursor symlink to {airflow_cursor_dir}[/dim]")

    if is_json_mode():
        json_success(project.to_rich_dict())

    console.print(f"[green]Worktree adopted as project '{project_name}'![/green]")
    console.print(f"   Branch: {branch}")
    console.print(f"   Worktree: {worktree}")
    console.print(f"   Webserver: http://localhost:{ports.webserver}")
    console.print("[dim]Note: Worktree was not created by ABM and will be protected from removal[/dim]")


@app.command(rich_help_panel="Core Commands")
def list() -> None:
    """List all projects."""
    projects = get_all_projects()

    if is_json_mode():
        # Detect current project
        current_project_name = None
        try:
            cwd = Path.cwd()
            for project in projects:
                if cwd == Path(project.worktree_path) or cwd.is_relative_to(Path(project.worktree_path)):
                    current_project_name = project.name
                    break
        except (ValueError, OSError):
            pass

        running_containers = get_running_containers()
        project_list = []
        for project in sorted(projects, key=lambda p: p.name):
            d = project.to_rich_dict()
            container_info = running_containers.get(project.name, {})
            services = container_info.get("services", [])
            is_start_airflow = container_info.get("is_start_airflow", False)
            if services:
                d["running"] = "airflow" if is_start_airflow else "shell"
            else:
                d["running"] = None
            # Enrich with health check
            if d["running"] == "airflow":
                d["health"] = check_webserver_health(project.ports.webserver)
            elif d["running"] == "shell":
                d["health"] = HEALTH_NOT_APPLICABLE
            else:
                d["health"] = HEALTH_NOT_RUNNING
            project_list.append(d)

        json_success({"projects": project_list, "current_project": current_project_name})

    if not projects:
        console.print("No projects found. Create one with 'abm add <name>'")
        return

    # Detect current project from cwd
    current_project_name = None
    try:
        cwd = Path.cwd()
        for project in projects:
            if cwd == Path(project.worktree_path) or cwd.is_relative_to(Path(project.worktree_path)):
                current_project_name = project.name
                break
    except (ValueError, OSError):
        pass

    # Get running containers
    running_containers = get_running_containers()

    table = Table(title="Airflow Breeze Manager Projects")
    table.add_column("", style="white", width=2)  # Active indicator
    table.add_column("Name", style="cyan")
    table.add_column("Branch", style="yellow")
    table.add_column("Py", style="bright_blue")  # Python version
    table.add_column("Backend", style="magenta")
    table.add_column("API", style="green")  # API/Webserver port
    table.add_column("Running", style="bright_green")
    table.add_column("PR", style="blue")
    table.add_column("Flags", style="white")  # Frozen, etc.

    for project in sorted(projects, key=lambda p: p.name):
        flags = []
        if project.frozen:
            flags.append("frozen")

        # PR link (clickable if linked)
        if project.pr_number:
            pr_url = f"https://github.com/apache/airflow/pull/{project.pr_number}"
            pr_str = f"[link={pr_url}]#{project.pr_number}[/link]"
        else:
            pr_str = "-"

        flags_str = " ".join(flags) if flags else "-"

        # Active indicator
        active = "->" if project.name == current_project_name else ""

        # Running status & API URL
        container_info = running_containers.get(project.name, {})
        services = container_info.get("services", [])
        is_start_airflow = container_info.get("is_start_airflow", False)

        if services:
            if is_start_airflow:
                # Full Airflow environment (start-airflow with tmux)
                health = check_webserver_health(project.ports.webserver)
                if health == HEALTH_HEALTHY:
                    running = "airflow"
                else:
                    running = "[red]airflow![/red]"
                # Make API port clickable when running
                api_url = f"http://localhost:{project.ports.webserver}"
                api_display = f"[link={api_url}]:{project.ports.webserver}[/link]"
            else:
                # Just shell or other services
                running = "shell"
                api_display = f":{project.ports.webserver}"
        else:
            running = "-"
            api_display = f":{project.ports.webserver}"

        table.add_row(
            active,
            project.name,
            project.branch,
            project.python_version,
            project.backend,
            api_display,
            running,
            pr_str,
            flags_str,
        )

    console.print(table)

    # Show footer with helpful info
    footer_parts = []
    if current_project_name:
        footer_parts.append(f"-> Currently in: {current_project_name}")

    # Count running projects by type
    airflow_count = sum(1 for info in running_containers.values() if info.get("is_start_airflow"))
    shell_count = sum(
        1 for info in running_containers.values() if info.get("services") and not info.get("is_start_airflow")
    )

    status_parts = []
    if airflow_count > 0:
        status_parts.append(f"{airflow_count} airflow")
    if shell_count > 0:
        status_parts.append(f"{shell_count} shell")

    if status_parts:
        footer_parts.append(" | ".join(status_parts))

    if footer_parts:
        console.print(f"\n[dim]{' | '.join(footer_parts)}[/dim]")


@app.command(rich_help_panel="Core Commands")
def status(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
) -> None:
    """Show project status."""
    project, project_dir = require_project(project_name)

    if is_json_mode():
        data = project.to_rich_dict()
        # Enrich with running state
        running_containers = get_running_containers()
        container_info = running_containers.get(project.name, {})
        services = container_info.get("services", [])
        is_start_airflow = container_info.get("is_start_airflow", False)
        if services:
            data["running"] = "airflow" if is_start_airflow else "shell"
        else:
            data["running"] = None
        # Enrich with health check
        if data["running"] == "airflow":
            data["health"] = check_webserver_health(project.ports.webserver)
        elif data["running"] == "shell":
            data["health"] = HEALTH_NOT_APPLICABLE
        else:
            data["health"] = HEALTH_NOT_RUNNING
        json_success(data)

    console.print(f"[bold cyan]{project.name}[/bold cyan]")
    console.print(f"  Branch: {project.branch}")
    console.print(f"  Worktree: {project.worktree_path}")
    console.print(f"  Backend: {project.backend}")
    console.print(f"  Python: {project.python_version}")
    console.print(f"  Created: {project.created_at}")
    console.print(f"  Frozen: {'Yes' if project.frozen else 'No'}")
    if project.pr_number:
        console.print(f"  PR: #{project.pr_number}")

    console.print("\n[bold]Ports:[/bold]")
    console.print(f"  API/Webserver: {project.ports.webserver}")
    console.print(f"  Flower: {project.ports.flower}")
    console.print(f"  Postgres: {project.ports.postgres}")
    console.print(f"  MySQL: {project.ports.mysql}")
    console.print(f"  MSSQL: {project.ports.mssql}")
    console.print(f"  Redis: {project.ports.redis}")
    console.print(f"  RabbitMQ: {project.ports.rabbitmq}")
    console.print(f"  SSH: {project.ports.ssh}")

    console.print("\n[bold]URLs:[/bold]")
    console.print(f"  API/Web: http://localhost:{project.ports.webserver}")
    console.print(f"  Flower: http://localhost:{project.ports.flower}")
    console.print(f"  SSH: ssh -p {project.ports.ssh} airflow@localhost")

    # Running state & health
    running_containers = get_running_containers()
    container_info = running_containers.get(project.name, {})
    services = container_info.get("services", [])
    is_start_airflow = container_info.get("is_start_airflow", False)
    if services:
        running = "airflow" if is_start_airflow else "shell"
    else:
        running = None

    console.print("\n[bold]Running:[/bold]")
    if running == "airflow":
        health = check_webserver_health(project.ports.webserver)
        health_str = "[green]healthy[/green]" if health == HEALTH_HEALTHY else "[red]unhealthy[/red]"
        console.print(f"  Status: airflow ({health_str})")
    elif running == "shell":
        console.print("  Status: shell")
    else:
        console.print("  Status: [dim]not running[/dim]")


@app.command(rich_help_panel="Core Commands")
def remove(
    project_name: Annotated[str, typer.Argument(help="Project name")],
    keep_docs: Annotated[
        bool,
        typer.Option("--keep-docs", help="Keep PROJECT.md for later use"),
    ] = False,
    delete_branch: Annotated[
        bool,
        typer.Option("--delete-branch", help="Delete the git branch (WARNING: destructive)"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation"),
    ] = False,
) -> None:
    """Remove a project."""
    project, project_dir = require_project(project_name)
    config = get_config()

    # Protect adopted worktrees from accidental removal
    if not project.managed_worktree and not force:
        if is_json_mode():
            json_error(
                f"Cannot remove adopted project '{project_name}' without --force",
                PROJECT_NOT_FOUND,
            )
        console.print(f"[red]Cannot remove adopted project '{project_name}' without --force[/red]")
        console.print("[yellow]This worktree was not created by ABM (it was adopted)[/yellow]")
        console.print(
            f"[dim]Hint: Use 'abm disown {project_name}' to remove ABM management but keep the worktree[/dim]"
        )
        console.print(
            f"[dim]Or use 'abm remove {project_name} --force' to remove everything including the worktree[/dim]"
        )
        raise typer.Exit(1)

    if not force:
        msg = f"Remove project '{project_name}'"
        if delete_branch:
            msg += f" and DELETE branch '{project.branch}'"
        msg += "?"
        confirm = safe_confirm(msg)
        if not confirm:
            raise typer.Abort()

    worktree_path = Path(project.worktree_path)

    # Stop Docker containers by targeting this specific worktree
    if not is_json_mode():
        console.print("Stopping Docker containers...")
    stop_project_containers(str(worktree_path))

    # Remove symlinks (including .cursor)
    if worktree_path.exists():
        remove_symlinks(worktree_path, SYMLINKED_FILES)

        # Also remove .cursor symlink if it exists
        cursor_link = worktree_path / ".cursor"
        if cursor_link.is_symlink():
            cursor_link.unlink()
            if not is_json_mode():
                console.print("[dim]-> Removed .cursor symlink[/dim]")

    # Remove worktree
    if not is_json_mode():
        console.print("Removing worktree...")
    run_command(
        ["git", "worktree", "remove", str(worktree_path), "--force"],
        cwd=Path(config.airflow_repo),
        check=False,
    )

    # Delete branch if requested
    if delete_branch:
        if not is_json_mode():
            console.print(f"[yellow]Deleting branch '{project.branch}'...[/yellow]")
        result = run_command(
            ["git", "branch", "-D", project.branch],
            cwd=Path(config.airflow_repo),
            check=False,
        )
        if result and result.returncode == 0:
            if not is_json_mode():
                console.print(f"[green]Branch '{project.branch}' deleted[/green]")
        elif not is_json_mode():
            console.print(f"[red]Failed to delete branch '{project.branch}'[/red]")
            console.print("[dim]The branch may not exist or may be currently checked out[/dim]")

    # Remove project directory
    if keep_docs:
        # Only keep PROJECT.md
        for item in project_dir.iterdir():
            if item.name != "PROJECT.md":
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        if is_json_mode():
            json_success({"project": project_name, "removed": True, "kept_docs": True})
        console.print(f"Project removed (kept PROJECT.md in {project_dir})")
    else:
        shutil.rmtree(project_dir)
        if is_json_mode():
            json_success({"project": project_name, "removed": True, "kept_docs": False})
        console.print(f"Project '{project_name}' removed completely")


@app.command(rich_help_panel="Core Commands")
def disown(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation"),
    ] = False,
) -> None:
    """Remove ABM management but keep the worktree.

    This is the opposite of 'adopt' - it removes ABM's metadata, symlinks,
    and container configuration, but preserves the worktree directory itself.

    Use this when you want to manage the worktree manually or with another tool.
    """
    project, project_dir = require_project(project_name)

    if not force:
        confirm = safe_confirm(f"Remove ABM management of '{project.name}' (worktree will be kept)?")
        if not confirm:
            raise typer.Abort()

    worktree_path = Path(project.worktree_path)

    # Stop Docker containers
    if not is_json_mode():
        console.print("Stopping Docker containers...")
    stop_project_containers(str(worktree_path))

    # Remove symlinks
    if worktree_path.exists():
        if not is_json_mode():
            console.print("Removing ABM symlinks...")
        remove_symlinks(worktree_path, SYMLINKED_FILES)

        # Also remove .cursor symlink if it exists
        cursor_link = worktree_path / ".cursor"
        if cursor_link.is_symlink():
            cursor_link.unlink()
            if not is_json_mode():
                console.print("[dim]-> Removed .cursor symlink[/dim]")

        # Remove breeze config directory (ABM-specific)
        breeze_config_dir = worktree_path / "files" / "airflow-breeze-config"
        if breeze_config_dir.exists():
            shutil.rmtree(breeze_config_dir)
            if not is_json_mode():
                console.print("[dim]-> Removed breeze config directory[/dim]")

    # Remove project metadata directory
    project_data = project.to_rich_dict()
    shutil.rmtree(project_dir)

    if is_json_mode():
        json_success({"project": project_data, "disowned": True, "worktree_preserved": str(worktree_path)})

    console.print(f"[green]Project '{project.name}' disowned[/green]")
    console.print(f"   Worktree preserved at: {worktree_path}")
    console.print("   PROJECT.md moved to worktree (if it existed)")
    console.print("[dim]You can now manage this worktree manually or re-adopt it later[/dim]")


@app.command(rich_help_panel="Environment Commands")
def shell(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
    extra_args: Annotated[
        builtins.list[str] | None,
        typer.Argument(help="Extra arguments passed to breeze shell"),
    ] = None,
) -> None:
    """Enter breeze shell for a project.

    Opens a long-running container with full environment initialization and port
    forwarding. Use for interactive development or any workflow that needs
    host-accessible ports (API, database, etc.).

    Use 'abm run' instead for one-off commands (tests, scripts) that don't need
    ports or environment initialization.
    """
    project, project_dir = require_project(project_name)

    if project.frozen:
        if is_json_mode():
            json_error(
                f"Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'",
                PROJECT_FROZEN,
            )
        console.print(
            f"[yellow]Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'[/yellow]"
        )
        raise typer.Exit(1)

    # Check for port conflicts BEFORE starting breeze
    conflicts = get_conflicting_ports(project.ports)
    _resolve_port_conflicts(project, project_dir, conflicts)

    worktree_path = Path(project.worktree_path)

    # Set environment variables for port isolation
    env = os.environ.copy()

    # Update with project-specific ports and instance name
    port_env = project.ports.to_env_dict(project_name=project.name)
    env.update(port_env)

    # Set compose project name for container isolation
    compose_project = get_docker_compose_project_name(project.name)
    env["COMPOSE_PROJECT_NAME"] = compose_project

    breeze_cmd = [
        "breeze",
        "shell",
        "--python",
        project.python_version,
        "--backend",
        project.backend,
    ]
    if extra_args:
        breeze_cmd.extend(extra_args)

    if is_json_mode():
        # Don't launch shell — return the env vars and command for the agent
        json_success(
            {
                "project": project.to_rich_dict(),
                "worktree_path": str(worktree_path),
                "env": port_env,
                "compose_project_name": compose_project,
                "breeze_command": breeze_cmd,
            }
        )

    console.print(f"[green]Entering breeze shell for '{project.name}'...[/green]")
    console.print("[cyan]Configuration:[/cyan]")
    console.print(f"  API: http://localhost:{project.ports.webserver}")
    console.print(f"  SSH: localhost:{project.ports.ssh}")
    console.print(f"  Python: {project.python_version}")
    console.print(f"  Backend: {project.backend}")
    console.print(f"  Compose project: {compose_project}")

    # Run breeze shell with project-specific python and backend
    os.chdir(worktree_path)
    os.execvpe("breeze", breeze_cmd, env)


@app.command(
    name="exec",
    rich_help_panel="Environment Commands",
    context_settings={
        "allow_extra_args": True,
        "allow_interspersed_args": False,
        "ignore_unknown_options": True,
    },
)
def exec_command(
    ctx: typer.Context,
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
) -> None:
    """Join the interactive shell of a running Airflow container.

    This command is useful for connecting to an already running container,
    for example one started with 'abm shell' or 'abm start-airflow'.

    Examples:
        abm exec
        abm exec my-project bash
        abm exec python -c "print('hello')"    # auto-detect project
    """
    # Build exec args from extra args captured by Click context
    exec_args: builtins.list[str] | None = builtins.list(ctx.args) or None

    # If project_name was provided, check if it's actually a known project or part of the command
    resolved_project_name: str | None = None
    if project_name is not None:
        if get_project(project_name):
            resolved_project_name = project_name
        else:
            # Not a known project - treat it as the first word of the exec args
            exec_args = [project_name, *(exec_args or [])]
            resolved_project_name = None

    project, _ = require_project(resolved_project_name)

    if project.frozen:
        if is_json_mode():
            json_error(
                f"Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'",
                PROJECT_FROZEN,
            )
        console.print(
            f"[yellow]Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'[/yellow]"
        )
        raise typer.Exit(1)

    worktree_path = project.worktree_path
    container_id = find_airflow_container(worktree_path)

    if not container_id:
        if is_json_mode():
            json_error(
                f"No running Airflow container found for project '{project.name}'",
                DOCKER_ERROR,
            )
        console.print(f"[red]No running Airflow container found for project '{project.name}'[/red]")
        console.print("[dim]Start the container first with 'abm shell' or 'abm start-airflow'[/dim]")
        raise typer.Exit(1)

    # Build the docker exec command
    cmd_to_run = [
        "docker",
        "exec",
        "-it",
        container_id,
        "/opt/airflow/scripts/docker/entrypoint_exec.sh",
    ]

    if exec_args:
        cmd_to_run.extend(exec_args)

    if is_json_mode():
        # In JSON mode, run without -it and capture output
        cmd_to_run_no_tty = [
            "docker",
            "exec",
            container_id,
            "/opt/airflow/scripts/docker/entrypoint_exec.sh",
        ]
        if exec_args:
            cmd_to_run_no_tty.extend(exec_args)
        process = subprocess.run(cmd_to_run_no_tty, capture_output=True, text=True, check=False)
        json_success(
            {
                "project": project.name,
                "container_id": container_id,
                "exit_code": process.returncode,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "command": cmd_to_run_no_tty,
            }
        )

    console.print(f"[green]Joining Airflow container for '{project.name}'...[/green]")

    exec_result = subprocess.run(cmd_to_run, check=False)
    sys.exit(exec_result.returncode)


@app.command(
    rich_help_panel="Environment Commands",
    context_settings={
        "allow_extra_args": True,
        "allow_interspersed_args": False,
        "ignore_unknown_options": True,
    },
)
def run(
    ctx: typer.Context,
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
    forward_ports: Annotated[
        bool,
        typer.Option("--forward-ports", help="Forward container ports to host."),
    ] = False,
) -> None:
    """Run a one-off command in the breeze environment and exit.

    Uses 'breeze run' under the hood: skips environment initialization and
    auto-cleans up after exit. Ideal for tests, scripts, and CI. Port
    forwarding is off by default but can be enabled with --forward-ports.

    Use 'abm shell' instead when you need host-accessible ports or full
    environment initialization (e.g. running Airflow services).

    Examples:
        abm run my-project pytest tests/
        abm run pytest tests/                                          # auto-detect project
        abm run python -c "import airflow; print(airflow.__version__)"  # flags pass through
    """
    # Build the command from extra args captured by Click context
    command: builtins.list[str] = builtins.list(ctx.args)

    # If project_name was provided, check if it's actually a known project or part of the command
    resolved_project_name: str | None = None
    if project_name is not None:
        if get_project(project_name):
            resolved_project_name = project_name
        else:
            # Not a known project - treat it as the first word of the command
            command = [project_name, *command]
            resolved_project_name = None

    project, _ = require_project(resolved_project_name)

    if project.frozen:
        if is_json_mode():
            json_error(f"Project '{project.name}' is frozen. Thaw it first.", PROJECT_FROZEN)
        console.print(f"[yellow]Project '{project.name}' is frozen. Thaw it first.[/yellow]")
        raise typer.Exit(1)

    if not command:
        if is_json_mode():
            json_error("Specify a command to run", INVALID_INPUT)
        console.print("[red]Specify a command to run[/red]")
        raise typer.Exit(1)

    worktree_path = Path(project.worktree_path)

    # Set environment variables
    env = os.environ.copy()
    env.update(project.ports.to_env_dict(project_name=project.name))
    compose_project = get_docker_compose_project_name(project.name)
    env["COMPOSE_PROJECT_NAME"] = compose_project

    breeze_cmd = [
        "breeze",
        "run",
        "--python",
        project.python_version,
        "--backend",
        project.backend,
    ]
    if forward_ports:
        breeze_cmd.append("--forward-ports")
    breeze_cmd.extend(command)

    if is_json_mode():
        # Use subprocess.run with capture_output instead of os.execvpe
        process = subprocess.run(
            breeze_cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=worktree_path,
            env=env,
        )
        json_success(
            {
                "project": project.name,
                "exit_code": process.returncode,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "command": breeze_cmd,
            }
        )

    # Run breeze run with project-specific python and backend
    os.chdir(worktree_path)
    os.execvpe(
        "breeze",
        breeze_cmd,
        env,
    )


docker_app = typer.Typer(help="Docker commands", no_args_is_help=True)
app.add_typer(docker_app, name="docker", rich_help_panel="Environment Commands")


@docker_app.command("up")
def docker_up(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name"),
    ] = None,
) -> None:
    """Start Docker containers."""
    project, _ = require_project(project_name)
    worktree_path = Path(project.worktree_path)

    env = os.environ.copy()
    env.update(project.ports.to_env_dict(project_name=project.name))
    compose_project = get_docker_compose_project_name(project.name)

    if not is_json_mode():
        console.print(f"[green]Starting containers for '{project.name}'...[/green]")
    run_command(
        ["docker", "compose", "--project-name", compose_project, "up", "-d"],
        cwd=worktree_path,
        env=env,
    )

    if is_json_mode():
        json_success({"project": project.name, "compose_project_name": compose_project, "action": "up"})


@docker_app.command("down")
def docker_down(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name"),
    ] = None,
) -> None:
    """Stop Docker containers."""
    project, _ = require_project(project_name)
    worktree_path = Path(project.worktree_path)

    if not is_json_mode():
        console.print(f"[yellow]Stopping containers for '{project.name}'...[/yellow]")
    stop_project_containers(str(worktree_path))

    if is_json_mode():
        json_success({"project": project.name, "action": "down"})


pr_app = typer.Typer(help="GitHub PR commands", no_args_is_help=True)
app.add_typer(pr_app, name="pr", rich_help_panel="Core Commands")


@pr_app.command("link")
def pr_link(
    pr_number: Annotated[int, typer.Argument(help="PR number")],
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name"),
    ] = None,
) -> None:
    """Link a GitHub PR to a project."""
    project, project_dir = require_project(project_name)
    project.pr_number = pr_number
    project.save(project_dir)

    if is_json_mode():
        json_success(project.to_rich_dict())

    console.print(f"Linked PR #{pr_number} to '{project.name}'")


@pr_app.command("open")
def pr_open(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name"),
    ] = None,
) -> None:
    """Open the linked PR in browser."""
    project, _ = require_project(project_name)

    if not project.pr_number:
        if is_json_mode():
            json_error(f"No PR linked to '{project.name}'", INVALID_INPUT)
        console.print(f"[yellow]No PR linked to '{project.name}'[/yellow]")
        raise typer.Exit(1)

    url = f"https://github.com/apache/airflow/pull/{project.pr_number}"

    if is_json_mode():
        # Return URL instead of opening browser
        json_success({"project": project.name, "pr_number": project.pr_number, "url": url})

    webbrowser.open(url)
    console.print(f"Opening PR #{project.pr_number}")


@pr_app.command("clear")
def pr_clear(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name"),
    ] = None,
) -> None:
    """Clear PR association."""
    project, project_dir = require_project(project_name)
    project.pr_number = None
    project.save(project_dir)

    if is_json_mode():
        json_success(project.to_rich_dict())

    console.print(f"Cleared PR association from '{project.name}'")


@app.command(rich_help_panel="Maintenance")
def freeze(
    project_name: Annotated[str, typer.Argument(help="Project name")],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation"),
    ] = False,
) -> None:
    """Freeze a project to save disk space."""
    project, project_dir = require_project(project_name)

    if project.frozen:
        if is_json_mode():
            json_success({"project": project.to_rich_dict(), "already_frozen": True})
        console.print(f"[yellow]Project '{project_name}' is already frozen[/yellow]")
        return

    if not force:
        confirm = safe_confirm(f"Freeze project '{project_name}'? This will remove node_modules and .venv")
        if not confirm:
            raise typer.Abort()

    worktree_path = Path(project.worktree_path)

    # Remove node_modules
    node_modules = worktree_path / "airflow-core" / "src" / "airflow" / "ui" / "node_modules"
    if node_modules.exists():
        if not is_json_mode():
            console.print("Removing node_modules...")
        shutil.rmtree(node_modules)

    # Mark as frozen
    project.frozen = True
    project.save(project_dir)

    if is_json_mode():
        json_success(project.to_rich_dict())

    console.print(f"[green]Project '{project_name}' frozen[/green]")


@app.command(rich_help_panel="Maintenance")
def thaw(
    project_name: Annotated[str, typer.Argument(help="Project name")],
) -> None:
    """Thaw a frozen project."""
    project, project_dir = require_project(project_name)

    if not project.frozen:
        if is_json_mode():
            json_success({"project": project.to_rich_dict(), "already_thawed": True})
        console.print(f"[yellow]Project '{project_name}' is not frozen[/yellow]")
        return

    worktree_path = Path(project.worktree_path)

    # Reinstall node modules
    ui_path = worktree_path / "airflow-core" / "src" / "airflow" / "ui"
    if (ui_path / "package.json").exists():
        if not is_json_mode():
            console.print("Reinstalling node_modules...")
        run_command(["npm", "ci"], cwd=ui_path)

    # Mark as thawed
    project.frozen = False
    project.save(project_dir)

    if is_json_mode():
        json_success(project.to_rich_dict())

    console.print(f"[green]Project '{project_name}' thawed[/green]")


@app.command(rich_help_panel="Maintenance")
def cleanup() -> None:
    """Clean up orphaned breeze containers."""
    if not is_json_mode():
        console.print("[cyan]Cleaning up breeze containers...[/cyan]")

    count = cleanup_breeze_containers()

    if is_json_mode():
        json_success({"containers_cleaned": count})

    console.print("\n[dim]Tip: Run this if you get 'port already allocated' errors[/dim]")


def _get_project_names() -> builtins.list[str]:
    """Get list of all project names for autocompletion."""
    try:
        projects = get_all_projects()
        return [p.name for p in projects]
    except Exception:
        return []


@app.command(rich_help_panel="Maintenance")
def setup_autocomplete(
    shell: Annotated[
        str | None,
        typer.Argument(help="Shell type (bash, zsh, fish). Auto-detected if not provided."),
    ] = None,
) -> None:
    """Set up shell autocompletion for abm commands.

    This enables tab completion for project names, commands, and options.

    Example:
        abm setup-autocomplete           # Auto-detect shell
        abm setup-autocomplete zsh       # Explicit shell
    """
    # Auto-detect shell if not provided
    if not shell:
        shell_path = os.environ.get("SHELL", "")
        if "zsh" in shell_path:
            shell = "zsh"
        elif "bash" in shell_path:
            shell = "bash"
        elif "fish" in shell_path:
            shell = "fish"
        else:
            console.print(f"[yellow]Could not detect shell from: {shell_path}[/yellow]")
            console.print("Please specify explicitly: abm setup-autocomplete [bash|zsh|fish]")
            raise typer.Exit(1)

    # Validate shell
    if shell not in ["bash", "zsh", "fish"]:
        console.print(f"[red]Unsupported shell: {shell}[/red]")
        console.print("Supported shells: bash, zsh, fish")
        raise typer.Exit(1)

    # Determine completion file location
    if shell == "zsh":
        # Check if Oh-My-Zsh is installed
        omz_custom = Path.home() / ".oh-my-zsh" / "custom" / "completions"
        if omz_custom.parent.exists():
            # Use Oh-My-Zsh custom completion directory
            completion_file = omz_custom / "_abm"
            omz_custom.mkdir(exist_ok=True)
            rc_file = Path.home() / ".zshrc"
            use_separate_file = True
        else:
            # Use regular zshrc
            rc_file = Path.home() / ".zshrc"
            completion_file = None
            use_separate_file = False
    elif shell == "bash":
        # Bash completion directory
        bash_completion_dir = Path.home() / ".local" / "share" / "bash-completion" / "completions"
        bash_completion_dir.mkdir(parents=True, exist_ok=True)
        completion_file = bash_completion_dir / "abm"
        rc_file = Path.home() / ".bashrc"
        use_separate_file = True
    else:  # fish
        fish_completions = Path.home() / ".config" / "fish" / "completions"
        fish_completions.mkdir(parents=True, exist_ok=True)
        completion_file = fish_completions / "abm.fish"
        rc_file = Path.home() / ".config" / "fish" / "config.fish"
        use_separate_file = True

    console.print(f"[cyan]Setting up autocompletion for {shell}...[/cyan]")

    # Check if already installed
    completion_marker = "# ABM shell completion"

    if use_separate_file and completion_file:
        # Using separate completion file
        if completion_file.exists():
            console.print(f"[yellow]Autocompletion already installed in {completion_file}[/yellow]")
            if not safe_confirm("Reinstall anyway?"):
                raise typer.Exit(0)
    else:
        # Check in rc file
        if rc_file.exists():
            content = rc_file.read_text()
            if completion_marker in content:
                console.print(f"[yellow]Autocompletion already installed in {rc_file}[/yellow]")
                if not safe_confirm("Reinstall anyway?"):
                    raise typer.Exit(0)
                # Remove old completion from rc file
                lines = [
                    line
                    for line in content.split("\n")
                    if completion_marker not in line
                    and "_abm_completion" not in line
                    and "compdef _abm_completion abm" not in line
                ]
                rc_file.write_text("\n".join(lines))

    # Install completion
    if use_separate_file and completion_file:
        # Write to separate completion file
        with open(completion_file, "w") as f:
            f.write(f"{completion_marker}\n")
            if shell == "zsh":
                # Custom zsh completion function - proper autoload format
                completion_script = """#compdef abm

_abm() {
    local line state

    _arguments -C \\
        "1: :->cmds" \\
        "*::arg:->args"

    case "$state" in
        cmds)
            _values "abm command" \\
                "init[Initialize ABM]" \\
                "add[Create new project]" \\
                "adopt[Adopt existing worktree]" \\
                "list[List all projects]" \\
                "status[Show project status]" \\
                "shell[Enter breeze shell]" \\
                "exec[Join running container]" \\
                "run[Run breeze command]" \\
                "start-airflow[Start full Airflow]" \\
                "remove[Remove project]" \\
                "disown[Remove ABM management]" \\
                "freeze[Freeze project]" \\
                "thaw[Thaw project]" \\
                "cleanup[Clean up containers]" \\
                "setup-autocomplete[Setup shell completion]" \\
                "docker[Docker commands]" \\
                "pr[GitHub PR commands]"
            ;;
        args)
            case $line[1] in
                add)
                    _arguments \\
                        '--create-branch[Create new branch]' \\
                        '--branch=[Existing branch name]' \\
                        '--description=[Project description]' \\
                        '--pr=[GitHub PR number]' \\
                        '--backend=[Database backend]:(sqlite postgres mysql)' \\
                        '--python-version=[Python version]:(3.9 3.10 3.11 3.12 3.13)' \\
                        '--help[Show help]'
                    ;;
                shell|status|start-airflow|run|exec|freeze|thaw|disown)
                    _arguments \\
                        '--help[Show help]' \\
                        '1:project:compadd ${(f)"$(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"}'
                    ;;
                start-airflow)
                    _arguments \\
                        '--dev-mode[Enable dev mode]' \\
                        '--skip-assets-compilation[Skip assets compilation]' \\
                        '--executor=[Executor type]:(LocalExecutor CeleryExecutor EdgeExecutor)' \\
                        '--load-example-dags[Load example DAGs]' \\
                        '--create-all-roles[Create all roles]' \\
                        '--mount-ui-dist[Mount UI dist]' \\
                        '--terminal-multiplexer=[Terminal multiplexer]:(mprocs tmux)' \\
                        '--debug-components=[Debug components]' \\
                        '--help[Show help]' \\
                        '1:project:compadd ${(f)"$(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"}'
                    ;;
                remove)
                    _arguments \\
                        '--keep-docs[Keep PROJECT.md]' \\
                        '--delete-branch[Delete the git branch]' \\
                        '(-f --force)'{-f,--force}'[Skip confirmation]' \\
                        '--help[Show help]' \\
                        '1:project:compadd ${(f)"$(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"}'
                    ;;
                init)
                    _arguments \\
                        '--airflow-repo=[Path to Airflow repository]:directory:_directories' \\
                        '--worktree-base=[Path to worktree base]:directory:_directories' \\
                        '--help[Show help]'
                    ;;
                setup-autocomplete)
                    _arguments \\
                        '--help[Show help]' \\
                        '1:shell:(bash zsh fish)'
                    ;;
            esac
            ;;
    esac
}

# Don't call the function - let zsh autoload it via #compdef
_abm "$@"
"""
                f.write(completion_script)
            elif shell == "bash":
                # Custom bash completion function
                f.write("""_abm_completion() {
    local cur prev commands projects
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    commands="init add adopt list status shell exec run start-airflow remove disown freeze thaw cleanup setup-autocomplete docker pr"

    if [ $COMP_CWORD -eq 1 ]; then
        COMPREPLY=($(compgen -W "$commands" -- "$cur"))
    elif [ $COMP_CWORD -eq 2 ]; then
        case "$prev" in
            shell|remove|status|start-airflow|run|exec|freeze|thaw|disown)
                projects=$(ls ~/.airflow-breeze-manager/projects 2>/dev/null)
                COMPREPLY=($(compgen -W "$projects" -- "$cur"))
                ;;
        esac
    fi
}
complete -F _abm_completion abm
""")
            else:  # fish
                # Fish completion
                f.write("""complete -c abm -f
complete -c abm -n "__fish_use_subcommand" -a "init" -d "Initialize ABM"
complete -c abm -n "__fish_use_subcommand" -a "add" -d "Create new project"
complete -c abm -n "__fish_use_subcommand" -a "adopt" -d "Adopt existing worktree"
complete -c abm -n "__fish_use_subcommand" -a "list" -d "List all projects"
complete -c abm -n "__fish_use_subcommand" -a "status" -d "Show project status"
complete -c abm -n "__fish_use_subcommand" -a "shell" -d "Enter breeze shell"
complete -c abm -n "__fish_use_subcommand" -a "exec" -d "Join running container"
complete -c abm -n "__fish_use_subcommand" -a "run" -d "Run breeze command"
complete -c abm -n "__fish_use_subcommand" -a "start-airflow" -d "Start full Airflow"
complete -c abm -n "__fish_use_subcommand" -a "remove" -d "Remove project"
complete -c abm -n "__fish_use_subcommand" -a "disown" -d "Remove ABM management"
complete -c abm -n "__fish_use_subcommand" -a "freeze" -d "Freeze project"
complete -c abm -n "__fish_use_subcommand" -a "thaw" -d "Thaw project"
complete -c abm -n "__fish_use_subcommand" -a "cleanup" -d "Clean up containers"
complete -c abm -n "__fish_use_subcommand" -a "setup-autocomplete" -d "Setup completion"

# Project name completions
set -l project_commands shell remove status start-airflow run exec freeze thaw disown
for cmd in $project_commands
    complete -c abm -n "__fish_seen_subcommand_from $cmd" -a "(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"
end
""")

        console.print(f"[green]Autocompletion installed to {completion_file}[/green]")

        # Add note about Oh-My-Zsh auto-loading
        if shell == "zsh" and "oh-my-zsh" in str(completion_file):
            # Clear Oh-My-Zsh completion cache to force reload
            import glob

            for cache_file in glob.glob(str(Path.home() / ".zcompdump*")):
                try:
                    Path(cache_file).unlink()
                except Exception:
                    pass

            # Also clear the cache completions directory
            cache_comp = Path.home() / ".oh-my-zsh" / "cache" / "completions" / "_abm"
            if cache_comp.exists():
                try:
                    cache_comp.unlink()
                except Exception:
                    pass

            #  Add loader to .zshrc (following uv plugin pattern)
            loader_marker = "# ABM completion loader"
            loader_code = f"""
{loader_marker}
if [[ ! -f "${{ZSH_CACHE_DIR:-$HOME/.oh-my-zsh/cache}}/completions/_abm" ]]; then
  typeset -g -A _comps
  autoload -Uz _abm
  _comps[abm]=_abm
fi
"""
            if rc_file.exists():
                rc_content = rc_file.read_text()
                if loader_marker not in rc_content:
                    # Find where to insert (after Oh-My-Zsh is sourced)
                    lines = rc_content.split("\n")
                    insert_pos = -1
                    for i, line in enumerate(lines):
                        if "source $ZSH/oh-my-zsh.sh" in line or "source ${ZSH}/oh-my-zsh.sh" in line:
                            insert_pos = i + 1
                            break

                    if insert_pos > 0:
                        lines.insert(insert_pos, loader_code)
                        rc_file.write_text("\n".join(lines))
                        console.print(f"[green]Added completion loader to {rc_file}[/green]")

            console.print("\n[green]Cleared completion cache[/green]")
            console.print("\n[cyan]To activate:[/cyan]")
            console.print("  exec zsh")
            console.print("\n[dim]Or run: omz reload[/dim]")
        else:
            console.print("\n[cyan]To activate:[/cyan]")
            console.print(f"  source {rc_file}")
            console.print("\n[dim]Or restart your terminal[/dim]")
    else:
        # Inline in rc file (fallback for zsh without Oh-My-Zsh)
        with open(rc_file, "a") as f:
            f.write(f"\n{completion_marker}\n")
            completion_script = """_abm() {
    local line state

    _arguments -C \\
        "1: :->cmds" \\
        "*::arg:->args"

    case "$state" in
        cmds)
            _values "abm command" \\
                "init[Initialize ABM]" \\
                "add[Create new project]" \\
                "adopt[Adopt existing worktree]" \\
                "list[List all projects]" \\
                "status[Show project status]" \\
                "shell[Enter breeze shell]" \\
                "exec[Join running container]" \\
                "run[Run breeze command]" \\
                "start-airflow[Start full Airflow]" \\
                "remove[Remove project]" \\
                "disown[Remove ABM management]" \\
                "freeze[Freeze project]" \\
                "thaw[Thaw project]" \\
                "cleanup[Clean up containers]" \\
                "setup-autocomplete[Setup shell completion]" \\
                "docker[Docker commands]" \\
                "pr[GitHub PR commands]"
            ;;
        args)
            case $line[1] in
                add)
                    _arguments \\
                        '--create-branch[Create new branch]' \\
                        '--branch=[Existing branch name]' \\
                        '--description=[Project description]' \\
                        '--pr=[GitHub PR number]' \\
                        '--backend=[Database backend]:(sqlite postgres mysql)' \\
                        '--python-version=[Python version]:(3.9 3.10 3.11 3.12 3.13)' \\
                        '--help[Show help]'
                    ;;
                shell|status|start-airflow|run|exec|freeze|thaw|disown)
                    _arguments \\
                        '--help[Show help]' \\
                        '1:project:compadd ${(f)"$(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"}'
                    ;;
                start-airflow)
                    _arguments \\
                        '--dev-mode[Enable dev mode]' \\
                        '--skip-assets-compilation[Skip assets compilation]' \\
                        '--executor=[Executor type]:(LocalExecutor CeleryExecutor EdgeExecutor)' \\
                        '--load-example-dags[Load example DAGs]' \\
                        '--create-all-roles[Create all roles]' \\
                        '--mount-ui-dist[Mount UI dist]' \\
                        '--terminal-multiplexer=[Terminal multiplexer]:(mprocs tmux)' \\
                        '--debug-components=[Debug components]' \\
                        '--help[Show help]' \\
                        '1:project:compadd ${(f)"$(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"}'
                    ;;
                remove)
                    _arguments \\
                        '--keep-docs[Keep PROJECT.md]' \\
                        '--delete-branch[Delete the git branch]' \\
                        '(-f --force)'{-f,--force}'[Skip confirmation]' \\
                        '--help[Show help]' \\
                        '1:project:compadd ${(f)"$(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"}'
                    ;;
                init)
                    _arguments \\
                        '--airflow-repo=[Path to Airflow repository]:directory:_directories' \\
                        '--worktree-base=[Path to worktree base]:directory:_directories' \\
                        '--help[Show help]'
                    ;;
                setup-autocomplete)
                    _arguments \\
                        '--help[Show help]' \\
                        '1:shell:(bash zsh fish)'
                    ;;
            esac
            ;;
    esac
}
compdef _abm abm
"""
            f.write(completion_script)

        console.print(f"[green]Autocompletion installed to {rc_file}[/green]")
        console.print("\n[cyan]To activate:[/cyan]")
        console.print(f"  source {rc_file}")
        console.print("\n[dim]Or restart your terminal[/dim]")

    console.print("\n[bold]Usage examples:[/bold]")
    console.print("  abm <TAB>            # Shows all commands")
    console.print("  abm shell <TAB>      # Shows project names")
    console.print("  abm remove <TAB>     # Shows project names")


@app.command("start-airflow", rich_help_panel="Environment Commands")
def start_airflow(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
    dev_mode: Annotated[
        bool,
        typer.Option("--dev-mode", help="Enable dev mode for UI development"),
    ] = False,
    skip_assets_compilation: Annotated[
        bool,
        typer.Option("--skip-assets-compilation", help="Skip assets compilation for faster startup"),
    ] = False,
    executor: Annotated[
        str | None,
        typer.Option("--executor", help="Executor type (e.g., LocalExecutor, CeleryExecutor, EdgeExecutor)"),
    ] = None,
    load_example_dags: Annotated[
        bool,
        typer.Option("--load-example-dags", "-e", help="Load example DAGs"),
    ] = False,
    create_all_roles: Annotated[
        bool,
        typer.Option("--create-all-roles", help="Create all roles (for FabAuthManager testing)"),
    ] = False,
    mount_ui_dist: Annotated[
        bool,
        typer.Option("--mount-ui-dist", help="Mount UI dist for UI development"),
    ] = False,
    terminal_multiplexer: Annotated[
        str | None,
        typer.Option("--terminal-multiplexer", "-t", help="Terminal multiplexer (mprocs or tmux)"),
    ] = None,
    debug_components: Annotated[
        builtins.list[str] | None,
        typer.Option("--debug-components", help="Components to enable remote debugging for"),
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless",
            help="Run Airflow without a terminal multiplexer (no TTY needed). "
            "Uses 'breeze shell' + 'airflow standalone' to start all Airflow processes. "
            "Automatically enabled when no TTY is available or in --json mode.",
        ),
    ] = False,
    extra_args: Annotated[
        builtins.list[str] | None,
        typer.Argument(help="Extra arguments passed to breeze start-airflow"),
    ] = None,
) -> None:
    """Start Airflow in breeze (equivalent to 'breeze start-airflow').

    By default, launches mprocs (terminal multiplexer) which requires a TTY.
    Use --headless to run without a TTY via 'airflow standalone' inside
    'breeze shell'. Headless mode is auto-enabled in --json mode or when
    no TTY is detected (e.g. piped input, AI agents).

    Use 'abm stop-airflow' to stop a headless instance.
    """
    project, project_dir = require_project(project_name)

    if project.frozen:
        if is_json_mode():
            json_error(
                f"Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'",
                PROJECT_FROZEN,
            )
        console.print(
            f"[yellow]Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'[/yellow]"
        )
        raise typer.Exit(1)

    # Check for port conflicts BEFORE starting
    conflicts = get_conflicting_ports(project.ports)
    _resolve_port_conflicts(project, project_dir, conflicts)

    worktree_path = Path(project.worktree_path)

    # Set environment variables for port isolation
    env = os.environ.copy()

    # Update with project-specific ports and instance name
    port_env = project.ports.to_env_dict(project_name=project.name)
    env.update(port_env)

    # Set compose project name for container isolation
    compose_project = get_docker_compose_project_name(project.name)
    env["COMPOSE_PROJECT_NAME"] = compose_project

    # Headless mode: auto-detect when no TTY or in JSON mode
    use_headless = headless or is_json_mode() or not sys.stdin.isatty()

    if use_headless:
        # Build extra flags for breeze shell (subset of start-airflow flags that apply)
        extra_breeze_args: builtins.list[str] = []
        if mount_ui_dist:
            extra_breeze_args.append("--mount-ui-dist")
        if skip_assets_compilation:
            extra_breeze_args.append("--skip-assets-compilation")

        # Executor is passed as env var for 'airflow standalone'
        if executor:
            env["AIRFLOW__CORE__EXECUTOR"] = executor
        if load_example_dags:
            env["AIRFLOW__CORE__LOAD_EXAMPLES"] = "True"

        _start_airflow_headless(project, project_dir, worktree_path, env, compose_project, extra_breeze_args)
        return

    breeze_cmd = [
        "breeze",
        "start-airflow",
        "--python",
        project.python_version,
        "--backend",
        project.backend,
    ]
    if dev_mode:
        breeze_cmd.append("--dev-mode")
    if skip_assets_compilation:
        breeze_cmd.append("--skip-assets-compilation")
    if executor:
        breeze_cmd.extend(["--executor", executor])
    if load_example_dags:
        breeze_cmd.append("--load-example-dags")
    if create_all_roles:
        breeze_cmd.append("--create-all-roles")
    if mount_ui_dist:
        breeze_cmd.append("--mount-ui-dist")
    if terminal_multiplexer:
        breeze_cmd.extend(["--terminal-multiplexer", terminal_multiplexer])
    if debug_components:
        for component in debug_components:
            breeze_cmd.extend(["--debug-components", component])
    if extra_args:
        breeze_cmd.extend(extra_args)

    if is_json_mode():
        # Launch detached via subprocess.Popen, return PID + URLs
        process = subprocess.Popen(
            breeze_cmd,
            cwd=worktree_path,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        json_success(
            {
                "project": project.to_rich_dict(),
                "pid": process.pid,
                "urls": {
                    "webserver": f"http://localhost:{project.ports.webserver}",
                    "flower": f"http://localhost:{project.ports.flower}",
                },
                "compose_project_name": compose_project,
                "breeze_command": breeze_cmd,
            }
        )

    console.print(f"[green]Starting Airflow for '{project.name}'...[/green]")
    console.print("[cyan]Services:[/cyan]")
    console.print(f"  API/Web: http://localhost:{project.ports.webserver}")
    console.print(f"  Flower: http://localhost:{project.ports.flower}")
    console.print(f"  Postgres: localhost:{project.ports.postgres}")
    console.print(f"  Python: {project.python_version}")
    console.print(f"  Backend: {project.backend}")
    console.print(f"  Compose project: {compose_project}")
    console.print("\n[dim]Press Ctrl+C to stop all services[/dim]\n")

    # Run breeze start-airflow with project-specific python and backend
    os.chdir(worktree_path)
    os.execvpe(
        "breeze",
        breeze_cmd,
        env,
    )


def _wait_for_ready(port: int, timeout: int = HEADLESS_READY_TIMEOUT) -> bool:
    """Poll Airflow API until ready.

    Tries /api/v2/version first (Airflow 3), then /api/v1/version (Airflow 2).
    Treats 200, 401, and 403 as "ready" (server is up, auth may be required).
    """
    import time
    import urllib.error
    import urllib.request

    urls = [
        f"http://localhost:{port}/api/v2/version",
        f"http://localhost:{port}/api/v1/version",
    ]
    start = time.time()
    while time.time() - start < timeout:
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    if resp.status in (200, 401, 403):
                        return True
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    return True
            except Exception:
                pass
        time.sleep(HEADLESS_POLL_INTERVAL)
    return False


def _start_airflow_headless(
    project: ProjectMetadata,
    project_dir: Path,
    worktree_path: Path,
    env: dict[str, str],
    compose_project: str,
    extra_breeze_args: builtins.list[str] | None = None,
) -> None:
    """Start Airflow in headless mode using 'breeze shell' + 'airflow standalone'.

    Uses 'breeze shell' (which already forwards ports) to run 'airflow standalone',
    which starts scheduler, dag-processor, api-server, and triggerer in a single
    process. No mprocs/tmux needed, no TTY needed.
    """
    breeze_cmd = [
        "breeze",
        "shell",
        "--python",
        project.python_version,
        "--backend",
        project.backend,
        "--quiet",
        "--tty",
        "disabled",
    ]
    if extra_breeze_args:
        breeze_cmd.extend(extra_breeze_args)
    breeze_cmd.extend(["airflow", "standalone"])

    log_file = project_dir / "headless.log"

    # Run in background, capturing output to log file
    with open(log_file, "w") as log_fh:
        process = subprocess.Popen(
            breeze_cmd,
            cwd=worktree_path,
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )

    # Poll for readiness
    ready = _wait_for_ready(project.ports.webserver)

    result = {
        "project": project.to_rich_dict(),
        "pid": process.pid,
        "ready": ready,
        "urls": {
            "webserver": f"http://localhost:{project.ports.webserver}",
        },
        "log_file": str(log_file),
        "compose_project_name": compose_project,
    }

    if is_json_mode():
        json_success(result)

    if ready:
        console.print(f"[green]Airflow is ready for '{project.name}'![/green]")
        console.print(f"  API: http://localhost:{project.ports.webserver}")
        console.print(f"  PID: {process.pid}")
        console.print(f"  Logs: {log_file}")
    else:
        console.print("[yellow]Airflow did not become ready within timeout[/yellow]")
        console.print(f"  Check logs: {log_file}")


@app.command("stop-airflow", rich_help_panel="Environment Commands")
def stop_airflow(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
) -> None:
    """Stop a running Airflow instance for a project."""
    project, _project_dir = require_project(project_name)
    stop_project_containers(project.worktree_path)
    if is_json_mode():
        json_success({"project": project.name, "stopped": True})
    console.print(f"[green]Stopped Airflow for '{project.name}'[/green]")


@app.command(rich_help_panel="Environment Commands")
def api(
    endpoint: Annotated[
        str | None,
        typer.Argument(help="API endpoint (e.g. 'dags', 'dags/my_dag') or 'ls' to list endpoints"),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option("--project", "-p", help="Project name (auto-detected if in project directory)"),
    ] = None,
    method: Annotated[
        str,
        typer.Option("--method", "-X", help="HTTP method"),
    ] = "GET",
    field: Annotated[
        builtins.list[str] | None,
        typer.Option("--field", "-F", help="Typed field (key=value, auto-coerces types)"),
    ] = None,
    raw_field: Annotated[
        builtins.list[str] | None,
        typer.Option("--raw-field", "-f", help="Raw string field (key=value, always string)"),
    ] = None,
    header: Annotated[
        builtins.list[str] | None,
        typer.Option("--header", "-H", help="Additional header (key:value)"),
    ] = None,
    body: Annotated[
        str | None,
        typer.Option("--body", help="Raw JSON body"),
    ] = None,
    include: Annotated[
        bool,
        typer.Option("--include", "-i", help="Include HTTP status and headers in output"),
    ] = False,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Use endpoint as-is without /api/vX prefix"),
    ] = False,
    username: Annotated[
        str,
        typer.Option("--username", "-U", help="Airflow username"),
    ] = "airflow",
    password: Annotated[
        str,
        typer.Option("--password", "-P", help="Airflow password"),
    ] = "airflow",
    filter_pattern: Annotated[
        str | None,
        typer.Option("--filter", help="Filter pattern for 'ls' subcommand"),
    ] = None,
) -> None:
    """Make direct requests to an ABM project's Airflow REST API.

    Similar to `gh api` for GitHub. The API version prefix (/api/v1 or /api/v2) is
    auto-detected based on the running Airflow version.

    \b
    Examples:
      abm api dags                              # GET /api/v1/dags
      abm api dags/my_dag                       # GET /api/v1/dags/my_dag
      abm api health --raw                      # GET /health (no version prefix)
      abm api dags -F limit=10                  # GET with query params
      abm api dags/my_dag -X PATCH -F is_paused=true
      abm api variables -X POST -f key=test -f value=hello
      abm api dags -i                           # Include HTTP headers
      abm api ls                                # List available endpoints
      abm api ls --filter variable              # Filter endpoints by pattern
    """
    import json as json_mod

    from airflow_breeze_manager.api import (
        detect_api_version,
        format_endpoint_list,
        get_openapi_spec,
        make_request,
        parse_fields,
    )

    if endpoint is None:
        if is_json_mode():
            json_error("Endpoint is required. Use 'abm api <endpoint>' or 'abm api ls'.", INVALID_INPUT)
        console.print("[red]Endpoint is required. Use 'abm api <endpoint>' or 'abm api ls'.[/red]")
        raise typer.Exit(1)

    project, _project_dir = require_project(project_name)

    base_url = f"http://localhost:{project.ports.webserver}"

    # Detect API version (unless --raw, which skips the prefix entirely)
    api_version = "v1"
    if not raw:
        api_version = detect_api_version(base_url, username=username, password=password)

    # Handle 'ls' subcommand — list available endpoints from OpenAPI spec
    if endpoint == "ls":
        spec = get_openapi_spec(base_url, api_version, username=username, password=password)
        if spec is None:
            if is_json_mode():
                json_error("Could not fetch OpenAPI spec. Is Airflow running?", API_ERROR)
            console.print("[red]Could not fetch OpenAPI spec. Is Airflow running?[/red]")
            raise typer.Exit(1)

        endpoints = format_endpoint_list(spec, filter_pattern=filter_pattern)

        if is_json_mode():
            json_success({"endpoints": endpoints, "api_version": api_version})

        if not endpoints:
            console.print("[yellow]No endpoints found matching filter.[/yellow]")
            raise typer.Exit(0)

        table = Table(title=f"Airflow API Endpoints ({api_version})")
        table.add_column("Method", style="bold cyan", width=8)
        table.add_column("Path", style="green")
        table.add_column("Summary", style="dim")
        for ep in endpoints:
            table.add_row(ep["method"], ep["path"], ep["summary"])
        console.print(table)
        raise typer.Exit(0)

    # Parse fields
    try:
        parsed_fields = parse_fields(field, raw_field)
    except ValueError as e:
        if is_json_mode():
            json_error(str(e), INVALID_INPUT)
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Parse additional headers
    extra_headers: dict[str, str] = {}
    if header:
        for h in header:
            if ":" not in h:
                msg = f"Invalid header format (expected key:value): {h}"
                if is_json_mode():
                    json_error(msg, INVALID_INPUT)
                console.print(f"[red]{msg}[/red]")
                raise typer.Exit(1)
            key, value = h.split(":", 1)
            extra_headers[key.strip()] = value.strip()

    # Determine params vs json_data based on method
    params = None
    json_data = None

    if body:
        # Explicit --body takes precedence
        try:
            json_data = json_mod.loads(body)
        except json_mod.JSONDecodeError as e:
            msg = f"Invalid JSON body: {e}"
            if is_json_mode():
                json_error(msg, INVALID_INPUT)
            console.print(f"[red]{msg}[/red]")
            raise typer.Exit(1)
        # Fields merge into body if present
        if parsed_fields:
            if isinstance(json_data, dict):
                json_data.update(parsed_fields)
    elif parsed_fields:
        if method.upper() in ("GET", "DELETE", "HEAD", "OPTIONS"):
            params = parsed_fields
        else:
            json_data = parsed_fields

    # Make the request
    result = make_request(
        base_url=base_url,
        endpoint=endpoint,
        method=method.upper(),
        params=params,
        json_data=json_data,
        headers=extra_headers if extra_headers else None,
        username=username,
        password=password,
        raw_endpoint=raw,
        api_version=api_version,
    )

    # Handle connection errors (status_code 0)
    if result["status_code"] == 0:
        error_body = result["body"]
        error_msg = error_body.get("error", str(error_body)) if isinstance(error_body, dict) else str(error_body)
        msg = f"Connection failed: {error_msg}"
        if is_json_mode():
            json_error(msg, API_ERROR)
        console.print(f"[red]{msg}[/red]")
        raise typer.Exit(1)

    # JSON mode: structured output
    if is_json_mode():
        json_success(
            {
                "status_code": result["status_code"],
                "headers": result["headers"],
                "body": result["body"],
                "api_version": api_version,
            }
        )

    # Rich mode: format output
    if include:
        console.print(f"[bold]HTTP {result['status_code']}[/bold]")
        for key, value in result["headers"].items():
            console.print(f"[dim]{key}: {value}[/dim]")
        console.print()

    # Pretty-print body
    response_body = result["body"]
    if isinstance(response_body, (dict, builtins.list)):
        console.print_json(json_mod.dumps(response_body, indent=2))
    else:
        console.print(str(response_body))

    # Exit with non-zero for HTTP errors
    if result["status_code"] >= 400:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
