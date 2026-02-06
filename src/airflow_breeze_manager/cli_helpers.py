from __future__ import annotations

import subprocess

from rich.console import Console

console = Console()


def find_airflow_container(worktree_path: str) -> str | None:
    """Find the running Airflow container for a specific ABM project.

    Args:
        worktree_path: Path to the project worktree

    Returns:
        Container ID if found, None otherwise
    """
    try:
        # Find running containers with the airflow service
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "name=airflow",
                "--filter",
                "status=running",
                "--format",
                "{{.ID}}\t{{.Labels}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            container_id, labels = parts

            # Check if this container belongs to the specified worktree
            # Docker compose labels include the working directory
            if f"com.docker.compose.project.working_dir={worktree_path}" in labels:
                return container_id

        return None
    except subprocess.CalledProcessError:
        return None
    except FileNotFoundError:
        console.print("[red]Docker not found. Is Docker installed?[/red]")
        return None


def cleanup_breeze_containers() -> int:
    """Clean up any orphaned breeze containers."""
    try:
        # Find all breeze containers
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=breeze", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            check=True,
        )

        container_ids = [cid.strip() for cid in result.stdout.splitlines() if cid.strip()]

        if not container_ids:
            console.print("[green]No breeze containers to clean up[/green]")
            return 0

        console.print(f"[yellow]Found {len(container_ids)} breeze container(s)[/yellow]")

        # Stop and remove each container
        for container_id in container_ids:
            console.print(f"  Stopping {container_id}...")
            subprocess.run(["docker", "stop", container_id], capture_output=True, check=False)
            subprocess.run(["docker", "rm", container_id], capture_output=True, check=False)

        console.print(f"[green]✅ Cleaned up {len(container_ids)} container(s)[/green]")
        return len(container_ids)

    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error cleaning up containers: {e}[/red]")
        return -1
    except FileNotFoundError:
        console.print("[red]Docker not found. Is Docker installed?[/red]")
        return -1
