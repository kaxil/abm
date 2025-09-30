from __future__ import annotations

import builtins
import os
import shutil
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from airflow_breeze_manager.cli_helpers import cleanup_breeze_containers
from airflow_breeze_manager.constants import (
    ABM_CONFIG_FILE,
    ABM_DIR,
    DEFAULT_AIRFLOW_REPO,
    DEFAULT_WORKTREE_BASE,
    PROJECTS_DIR,
    SCHEMA_VERSION,
    SYMLINKED_FILES,
)
from airflow_breeze_manager.models import GlobalConfig, ProjectMetadata
from airflow_breeze_manager.utils import (
    allocate_ports,
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
    run_command,
    stop_project_containers,
)

app = typer.Typer(
    help="Manage multiple Airflow development environments with isolated breeze instances",
    no_args_is_help=True,
)


def get_config() -> GlobalConfig:
    """Get or create global configuration."""
    if not ABM_CONFIG_FILE.exists():
        console.print("[red]ABM not initialized. Run 'abm init' first.[/red]")
        raise typer.Exit(1)
    config = GlobalConfig.load(ABM_CONFIG_FILE)
    if not config:
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
            console.print("[red]Not in a project directory. Specify project name.[/red]")
            raise typer.Exit(1)

    project_or_none = get_project(name)
    if project_or_none is None:
        console.print(f"[red]Project '{name}' not found.[/red]")
        raise typer.Exit(1)

    # At this point, project_or_none is guaranteed to be ProjectMetadata (not None)
    project_dir = PROJECTS_DIR / name
    return project_or_none, project_dir


@app.command()
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
        console.print("[yellow]ABM already initialized.[/yellow]")
        config = GlobalConfig.load(ABM_CONFIG_FILE)
        if config:
            console.print(f"   Airflow repo: {config.airflow_repo}")
            console.print(f"   Worktree base: {config.worktree_base}")
        return

    # Try to detect Airflow repo
    if airflow_repo is None:
        # 1. Try current directory
        cwd = Path.cwd()
        if (cwd / ".git").exists() and (cwd / "airflow-core").exists():
            console.print(f"[cyan]Detected Airflow repository in current directory: {cwd}[/cyan]")
            if typer.confirm("Use this as the Airflow repository?", default=True):
                airflow_repo = str(cwd)

        # 2. Fall back to default
        if airflow_repo is None:
            default_path = Path(DEFAULT_AIRFLOW_REPO).expanduser()
            if default_path.exists() and (default_path / ".git").exists():
                console.print(f"[cyan]Found Airflow repository at default location: {default_path}[/cyan]")
                if typer.confirm("Use this as the Airflow repository?", default=True):
                    airflow_repo = str(default_path)

        # 3. Ask user
        if airflow_repo is None:
            console.print("[yellow]Could not auto-detect Airflow repository.[/yellow]")
            airflow_repo = typer.prompt("Enter path to Airflow repository", default=DEFAULT_AIRFLOW_REPO)

    if worktree_base is None:
        default_worktree = Path(DEFAULT_WORKTREE_BASE).expanduser()
        console.print(f"[cyan]Worktrees will be created in: {default_worktree}[/cyan]")
        if not typer.confirm("Use this location?", default=True):
            worktree_base = typer.prompt("Enter base directory for worktrees", default=DEFAULT_WORKTREE_BASE)
        else:
            worktree_base = str(default_worktree)

    # Validate Airflow repo
    repo_path = Path(airflow_repo).expanduser().resolve()
    if not repo_path.exists():
        console.print(f"[red]Directory does not exist: {repo_path}[/red]")
        raise typer.Exit(1)

    if not (repo_path / ".git").exists():
        console.print(f"[red]Not a git repository: {repo_path}[/red]")
        console.print("Expected to find .git directory")
        raise typer.Exit(1)

    # Check if it looks like Airflow
    if not (repo_path / "airflow-core").exists() and not (repo_path / "airflow").exists():
        console.print("[yellow]Warning: This doesn't look like an Airflow repository[/yellow]")
        console.print(f"Expected to find 'airflow-core' or 'airflow' directory in {repo_path}")
        if not typer.confirm("Continue anyway?", default=False):
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

    console.print("✅ [green]Airflow Breeze Manager initialized![/green]")
    console.print(f"   Airflow repo: {repo_path}")
    console.print(f"   Worktree base: {worktree_base}")


@app.command()
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
    ] = "3.11",
    create_branch: Annotated[
        bool,
        typer.Option("--create-branch", help="Create new branch if it doesn't exist"),
    ] = False,
) -> None:
    """Add a new project with isolated environment."""
    config = get_config()

    # Check if project already exists
    if get_project(name):
        console.print(f"[red]Project '{name}' already exists.[/red]")
        raise typer.Exit(1)

    branch = branch or name
    repo_path = Path(config.airflow_repo)
    worktree_path = Path(config.worktree_base) / name

    # Check if worktree already exists
    if worktree_path.exists():
        console.print(f"[red]Worktree path already exists: {worktree_path}[/red]")
        raise typer.Exit(1)

    # Check if branch exists
    if not git_branch_exists(repo_path, branch):
        if create_branch:
            console.print(f"Creating new branch: {branch}")
            run_command(["git", "branch", branch], cwd=repo_path)
        else:
            console.print(f"[red]Branch '{branch}' does not exist. Use --create-branch to create it.[/red]")
            raise typer.Exit(1)

    # Check if worktree for branch already exists
    if git_worktree_exists(repo_path, branch):
        console.print(f"[yellow]Worktree for branch '{branch}' already exists.[/yellow]")
        console.print("Remove existing worktree first: git worktree remove <path>")
        raise typer.Exit(1)

    # Allocate ports
    ports = allocate_ports()

    # Create worktree
    console.print(f"Creating worktree at {worktree_path}...")
    run_command(["git", "worktree", "add", str(worktree_path), branch], cwd=repo_path)

    # Create project directory
    project_dir = PROJECTS_DIR / name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create project metadata
    project = ProjectMetadata(
        name=name,
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
        project_md.write_text(f"""# {name}

## Description
{project.description}

## Branch
`{branch}`

## Ports
- Webserver: {ports.webserver}
- Flower: {ports.flower}
- Postgres: {ports.postgres}
- MySQL: {ports.mysql}
- Redis: {ports.redis}
- SSH: {ports.ssh}

## Notes
Add your notes here...
""")

    # Create CLAUDE.md template for AI assistant context
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(f"""# Project Context for AI Assistants

## Project: {name}

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
    env_vars.append(f'AIRFLOW__API__INSTANCE_NAME="ABM: {name}"')

    # Use project-specific database name for Postgres/MySQL isolation
    if backend in ("postgres", "mysql"):
        # Sanitize project name for database naming (replace hyphens with underscores)
        db_name = f"airflow_{name.replace('-', '_')}"
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
            console.print("[yellow]Warning: .cursor exists as a directory, not creating symlink[/yellow]")
        elif worktree_cursor_link.is_symlink():
            # Already a symlink, update it
            worktree_cursor_link.unlink()
            worktree_cursor_link.symlink_to(airflow_cursor_dir)
        else:
            # Create new symlink
            worktree_cursor_link.symlink_to(airflow_cursor_dir)
            console.print(f"[dim]→ Created .cursor symlink to {airflow_cursor_dir}[/dim]")
    else:
        console.print("[dim]Note: .cursor not found in main Airflow repo (it's gitignored)[/dim]")

    console.print(f"✅ [green]Project '{name}' created successfully![/green]")
    console.print(f"   Branch: {branch}")
    console.print(f"   Worktree: {worktree_path}")
    console.print(f"   Webserver: http://localhost:{ports.webserver}")


@app.command()
def list() -> None:
    """List all projects."""
    projects = get_all_projects()

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
            flags.append("🧊")

        # PR link (clickable if linked)
        if project.pr_number:
            pr_url = f"https://github.com/apache/airflow/pull/{project.pr_number}"
            pr_str = f"[link={pr_url}]#{project.pr_number}[/link]"
        else:
            pr_str = "-"

        flags_str = " ".join(flags) if flags else "-"

        # Active indicator
        active = "→" if project.name == current_project_name else ""

        # Running status & API URL
        container_info = running_containers.get(project.name, {})
        services = container_info.get("services", [])
        is_start_airflow = container_info.get("is_start_airflow", False)

        if services:
            if is_start_airflow:
                # Full Airflow environment (start-airflow with tmux)
                running = "🟢 airflow"
                # Make API port clickable when running
                api_url = f"http://localhost:{project.ports.webserver}"
                api_display = f"[link={api_url}]:{project.ports.webserver}[/link]"
            else:
                # Just shell or other services
                running = "🟡 shell"
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
        footer_parts.append(f"→ Currently in: {current_project_name}")

    # Count running projects by type
    airflow_count = sum(1 for info in running_containers.values() if info.get("is_start_airflow"))
    shell_count = sum(
        1 for info in running_containers.values() if info.get("services") and not info.get("is_start_airflow")
    )

    status_parts = []
    if airflow_count > 0:
        status_parts.append(f"🟢 {airflow_count} airflow")
    if shell_count > 0:
        status_parts.append(f"🟡 {shell_count} shell")

    if status_parts:
        footer_parts.append(" | ".join(status_parts))

    if footer_parts:
        console.print(f"\n[dim]{' | '.join(footer_parts)}[/dim]")


@app.command()
def status(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
) -> None:
    """Show project status."""
    project, project_dir = require_project(project_name)

    console.print(f"[bold cyan]{project.name}[/bold cyan]")
    console.print(f"  Branch: {project.branch}")
    console.print(f"  Worktree: {project.worktree_path}")
    console.print(f"  Backend: {project.backend}")
    console.print(f"  Python: {project.python_version}")
    console.print(f"  Created: {project.created_at}")
    console.print(f"  Frozen: {'Yes 🧊' if project.frozen else 'No'}")
    if project.pr_number:
        console.print(f"  PR: #{project.pr_number}")

    console.print("\n[bold]Ports:[/bold]")
    console.print(f"  API/Webserver: {project.ports.webserver}")
    console.print(f"  Flower: {project.ports.flower}")
    console.print(f"  Postgres: {project.ports.postgres}")
    console.print(f"  MySQL: {project.ports.mysql}")
    console.print(f"  Redis: {project.ports.redis}")
    console.print(f"  SSH: {project.ports.ssh}")

    console.print("\n[bold]URLs:[/bold]")
    console.print(f"  API/Web: http://localhost:{project.ports.webserver}")
    console.print(f"  Flower: http://localhost:{project.ports.flower}")
    console.print(f"  SSH: ssh -p {project.ports.ssh} airflow@localhost")


@app.command()
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

    if not force:
        msg = f"Remove project '{project_name}'"
        if delete_branch:
            msg += f" and DELETE branch '{project.branch}'"
        msg += "?"
        confirm = typer.confirm(msg)
        if not confirm:
            raise typer.Abort()

    worktree_path = Path(project.worktree_path)

    # Stop Docker containers by targeting this specific worktree
    console.print("Stopping Docker containers...")
    stop_project_containers(str(worktree_path))

    # Remove symlinks (including .cursor)
    if worktree_path.exists():
        remove_symlinks(worktree_path, SYMLINKED_FILES)

        # Also remove .cursor symlink if it exists
        cursor_link = worktree_path / ".cursor"
        if cursor_link.is_symlink():
            cursor_link.unlink()
            console.print("[dim]→ Removed .cursor symlink[/dim]")

    # Remove worktree
    console.print("Removing worktree...")
    run_command(
        ["git", "worktree", "remove", str(worktree_path), "--force"],
        cwd=Path(config.airflow_repo),
        check=False,
    )

    # Delete branch if requested
    if delete_branch:
        console.print(f"[yellow]Deleting branch '{project.branch}'...[/yellow]")
        result = run_command(
            ["git", "branch", "-D", project.branch],
            cwd=Path(config.airflow_repo),
            check=False,
        )
        if result and result.returncode == 0:
            console.print(f"[green]✓ Branch '{project.branch}' deleted[/green]")
        else:
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
        console.print(f"✅ Project removed (kept PROJECT.md in {project_dir})")
    else:
        shutil.rmtree(project_dir)
        console.print(f"✅ Project '{project_name}' removed completely")


@app.command()
def shell(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
) -> None:
    """Enter breeze shell for a project."""
    project, project_dir = require_project(project_name)

    if project.frozen:
        console.print(
            f"[yellow]Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'[/yellow]"
        )
        raise typer.Exit(1)

    # Check for port conflicts BEFORE starting breeze
    conflicts = get_conflicting_ports(project.ports)
    if conflicts:
        console.print("[red]⚠️  Port conflict detected![/red]\n")
        console.print("The following ports are already in use:")
        for service, port in conflicts.items():
            console.print(f"  • {service}: {port}")

        console.print("\n[yellow]This usually means:[/yellow]")
        console.print("  1. Another breeze instance is running (run 'abm cleanup')")
        console.print("  2. Another ABM project is running (check 'abm list')")
        console.print("  3. Some other service is using these ports")

        console.print("\n[cyan]Quick fixes:[/cyan]")
        console.print("  • Run: abm cleanup")
        console.print("  • Or: abm docker down <other-project>")
        console.print("  • Or: lsof -i :<port> to see what's using it")

        if typer.confirm("\nTry to automatically find alternative ports?", default=True):
            # Try to find alternative ports
            from airflow_breeze_manager.constants import PORT_RANGES

            new_ports = {}
            all_existing = get_all_projects()
            used_ports = {
                service: {getattr(p.ports, service) for p in all_existing if p.name != project.name}
                for service in ["webserver", "flower", "postgres", "mysql", "redis", "ssh"]
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
                console.print(f"\n[red]Could not find alternative ports for: {', '.join(failed)}[/red]")
                console.print("Port ranges exhausted. Please clean up containers or adjust port ranges.")
                raise typer.Exit(1)

            # Update project ports
            for service, port in new_ports.items():
                setattr(project.ports, service, port)

            project.save(project_dir)

            console.print("\n[green]✅ Updated ports:[/green]")
            for service, port in new_ports.items():
                console.print(f"  • {service}: {port}")
        else:
            raise typer.Exit(1)

    worktree_path = Path(project.worktree_path)

    # Set environment variables for port isolation
    env = os.environ.copy()

    # Update with project-specific ports and instance name
    port_env = project.ports.to_env_dict(project_name=project.name)
    env.update(port_env)

    # Set compose project name for container isolation
    compose_project = get_docker_compose_project_name(project.name)
    env["COMPOSE_PROJECT_NAME"] = compose_project

    console.print(f"[green]Entering breeze shell for '{project.name}'...[/green]")
    console.print("[cyan]Configuration:[/cyan]")
    console.print(f"  API: http://localhost:{project.ports.webserver}")
    console.print(f"  SSH: localhost:{project.ports.ssh}")
    console.print(f"  Python: {project.python_version}")
    console.print(f"  Backend: {project.backend}")
    console.print(f"  Compose project: {compose_project}")

    # Run breeze shell with project-specific python and backend
    os.chdir(worktree_path)
    os.execvpe(
        "breeze",
        [
            "breeze",
            "shell",
            "--python",
            project.python_version,
            "--backend",
            project.backend,
        ],
        env,
    )


@app.command()
def run(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
    command: Annotated[
        builtins.list[str] | None,
        typer.Argument(help="Command to run (e.g., pytest tests/, python -m mypy)"),
    ] = None,
) -> None:
    """Run an ad-hoc command in the breeze environment (equivalent to 'breeze run').

    Examples:
        abm run my-project pytest tests/
        abm run my-project python -m mypy providers/amazon/
        abm run my-project python -c "import airflow; print(airflow.__version__)"

    This uses 'breeze run' under the hood to execute commands without entering an interactive shell.
    """
    project, _ = require_project(project_name)

    if project.frozen:
        console.print(f"[yellow]Project '{project.name}' is frozen. Thaw it first.[/yellow]")
        raise typer.Exit(1)

    if not command:
        console.print("[red]Specify a command to run[/red]")
        raise typer.Exit(1)

    worktree_path = Path(project.worktree_path)

    # Set environment variables
    env = os.environ.copy()
    env.update(project.ports.to_env_dict(project_name=project.name))
    compose_project = get_docker_compose_project_name(project.name)
    env["COMPOSE_PROJECT_NAME"] = compose_project

    # Run breeze run with project-specific python and backend
    os.chdir(worktree_path)
    os.execvpe(
        "breeze",
        [
            "breeze",
            "run",
            "--python",
            project.python_version,
            "--backend",
            project.backend,
        ]
        + command,
        env,
    )


docker_app = typer.Typer(help="Docker commands", no_args_is_help=True)
app.add_typer(docker_app, name="docker")


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

    console.print(f"[green]Starting containers for '{project.name}'...[/green]")
    run_command(
        ["docker", "compose", "--project-name", compose_project, "up", "-d"],
        cwd=worktree_path,
        env=env,
    )


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

    console.print(f"[yellow]Stopping containers for '{project.name}'...[/yellow]")
    stop_project_containers(str(worktree_path))


pr_app = typer.Typer(help="GitHub PR commands", no_args_is_help=True)
app.add_typer(pr_app, name="pr")


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
    console.print(f"✅ Linked PR #{pr_number} to '{project.name}'")


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
        console.print(f"[yellow]No PR linked to '{project.name}'[/yellow]")
        raise typer.Exit(1)

    url = f"https://github.com/apache/airflow/pull/{project.pr_number}"
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
    console.print(f"✅ Cleared PR association from '{project.name}'")


@app.command()
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
        console.print(f"[yellow]Project '{project_name}' is already frozen[/yellow]")
        return

    if not force:
        confirm = typer.confirm(f"Freeze project '{project_name}'? This will remove node_modules and .venv")
        if not confirm:
            raise typer.Abort()

    worktree_path = Path(project.worktree_path)

    # Remove node_modules
    node_modules = worktree_path / "airflow-core" / "src" / "airflow" / "ui" / "node_modules"
    if node_modules.exists():
        console.print("Removing node_modules...")
        shutil.rmtree(node_modules)

    # Mark as frozen
    project.frozen = True
    project.save(project_dir)
    console.print(f"✅ [green]Project '{project_name}' frozen[/green]")


@app.command()
def thaw(
    project_name: Annotated[str, typer.Argument(help="Project name")],
) -> None:
    """Thaw a frozen project."""
    project, project_dir = require_project(project_name)

    if not project.frozen:
        console.print(f"[yellow]Project '{project_name}' is not frozen[/yellow]")
        return

    worktree_path = Path(project.worktree_path)

    # Reinstall node modules
    ui_path = worktree_path / "airflow-core" / "src" / "airflow" / "ui"
    if (ui_path / "package.json").exists():
        console.print("Reinstalling node_modules...")
        run_command(["npm", "ci"], cwd=ui_path)

    # Mark as thawed
    project.frozen = False
    project.save(project_dir)
    console.print(f"✅ [green]Project '{project_name}' thawed[/green]")


@app.command()
def cleanup() -> None:
    """Clean up orphaned breeze containers."""
    console.print("[cyan]Cleaning up breeze containers...[/cyan]")
    cleanup_breeze_containers()
    console.print("\n[dim]Tip: Run this if you get 'port already allocated' errors[/dim]")


def _get_project_names() -> builtins.list[str]:
    """Get list of all project names for autocompletion."""
    try:
        projects = get_all_projects()
        return [p.name for p in projects]
    except Exception:
        return []


@app.command()
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
            if not typer.confirm("Reinstall anyway?"):
                raise typer.Exit(0)
    else:
        # Check in rc file
        if rc_file.exists():
            content = rc_file.read_text()
            if completion_marker in content:
                console.print(f"[yellow]Autocompletion already installed in {rc_file}[/yellow]")
                if not typer.confirm("Reinstall anyway?"):
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
                "list[List all projects]" \\
                "status[Show project status]" \\
                "shell[Enter breeze shell]" \\
                "run[Run breeze command]" \\
                "start-airflow[Start full Airflow]" \\
                "remove[Remove project]" \\
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
                shell|status|start-airflow|run|freeze|thaw)
                    _arguments \\
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
    commands="init add list status shell run start-airflow remove freeze thaw cleanup setup-autocomplete docker pr"

    if [ $COMP_CWORD -eq 1 ]; then
        COMPREPLY=($(compgen -W "$commands" -- "$cur"))
    elif [ $COMP_CWORD -eq 2 ]; then
        case "$prev" in
            shell|remove|status|start-airflow|run|freeze|thaw)
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
complete -c abm -n "__fish_use_subcommand" -a "list" -d "List all projects"
complete -c abm -n "__fish_use_subcommand" -a "status" -d "Show project status"
complete -c abm -n "__fish_use_subcommand" -a "shell" -d "Enter breeze shell"
complete -c abm -n "__fish_use_subcommand" -a "run" -d "Run breeze command"
complete -c abm -n "__fish_use_subcommand" -a "start-airflow" -d "Start full Airflow"
complete -c abm -n "__fish_use_subcommand" -a "remove" -d "Remove project"
complete -c abm -n "__fish_use_subcommand" -a "freeze" -d "Freeze project"
complete -c abm -n "__fish_use_subcommand" -a "thaw" -d "Thaw project"
complete -c abm -n "__fish_use_subcommand" -a "cleanup" -d "Clean up containers"
complete -c abm -n "__fish_use_subcommand" -a "setup-autocomplete" -d "Setup completion"

# Project name completions
set -l project_commands shell remove status start-airflow run freeze thaw
for cmd in $project_commands
    complete -c abm -n "__fish_seen_subcommand_from $cmd" -a "(ls ~/.airflow-breeze-manager/projects 2>/dev/null)"
end
""")

        console.print(f"[green]✓ Autocompletion installed to {completion_file}[/green]")

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
                        console.print(f"[green]✓ Added completion loader to {rc_file}[/green]")

            console.print("\n[green]✓ Cleared completion cache[/green]")
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
                "list[List all projects]" \\
                "status[Show project status]" \\
                "shell[Enter breeze shell]" \\
                "run[Run breeze command]" \\
                "start-airflow[Start full Airflow]" \\
                "remove[Remove project]" \\
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
                shell|status|start-airflow|run|freeze|thaw)
                    _arguments \\
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

        console.print(f"[green]✓ Autocompletion installed to {rc_file}[/green]")
        console.print("\n[cyan]To activate:[/cyan]")
        console.print(f"  source {rc_file}")
        console.print("\n[dim]Or restart your terminal[/dim]")

    console.print("\n[bold]Usage examples:[/bold]")
    console.print("  abm <TAB>            # Shows all commands")
    console.print("  abm shell <TAB>      # Shows project names")
    console.print("  abm remove <TAB>     # Shows project names")


@app.command("start-airflow")
def start_airflow(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project name (auto-detected if in project directory)"),
    ] = None,
) -> None:
    """Start Airflow in breeze (equivalent to 'breeze start-airflow')."""
    project, project_dir = require_project(project_name)

    if project.frozen:
        console.print(
            f"[yellow]Project '{project.name}' is frozen. Thaw it first with 'abm thaw {project.name}'[/yellow]"
        )
        raise typer.Exit(1)

    # Check for port conflicts BEFORE starting
    conflicts = get_conflicting_ports(project.ports)
    if conflicts:
        console.print("[red]⚠️  Port conflict detected![/red]\n")
        console.print("The following ports are already in use:")
        for service, port in conflicts.items():
            console.print(f"  • {service}: {port}")

        console.print("\n[yellow]This usually means:[/yellow]")
        console.print("  1. Another breeze instance is running (run 'abm cleanup')")
        console.print("  2. Another ABM project is running (check 'abm list')")
        console.print("  3. Some other service is using these ports")

        console.print("\n[cyan]Quick fixes:[/cyan]")
        console.print("  • Run: abm cleanup")
        console.print("  • Or: abm docker down <other-project>")
        console.print("  • Or: lsof -i :<port> to see what's using it")

        if typer.confirm("\nTry to automatically find alternative ports?", default=True):
            # Try to find alternative ports
            from airflow_breeze_manager.constants import PORT_RANGES

            new_ports = {}
            all_existing = get_all_projects()
            used_ports = {
                service: {getattr(p.ports, service) for p in all_existing if p.name != project.name}
                for service in ["webserver", "flower", "postgres", "mysql", "redis", "ssh"]
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
                console.print(f"\n[red]Could not find alternative ports for: {', '.join(failed)}[/red]")
                console.print("Port ranges exhausted. Please clean up containers or adjust port ranges.")
                raise typer.Exit(1)

            # Update project ports
            for service, port in new_ports.items():
                setattr(project.ports, service, port)

            project.save(project_dir)

            console.print("\n[green]✅ Updated ports:[/green]")
            for service, port in new_ports.items():
                console.print(f"  • {service}: {port}")
        else:
            raise typer.Exit(1)

    worktree_path = Path(project.worktree_path)

    # Set environment variables for port isolation
    env = os.environ.copy()

    # Update with project-specific ports and instance name
    port_env = project.ports.to_env_dict(project_name=project.name)
    env.update(port_env)

    # Set compose project name for container isolation
    compose_project = get_docker_compose_project_name(project.name)
    env["COMPOSE_PROJECT_NAME"] = compose_project

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
        [
            "breeze",
            "start-airflow",
            "--python",
            project.python_version,
            "--backend",
            project.backend,
        ],
        env,
    )


if __name__ == "__main__":
    app()
