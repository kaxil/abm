from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from airflow_breeze_manager.constants import DEFAULT_PORTS


@dataclass
class ProjectPorts:
    """Port configuration for a project."""

    webserver: int
    flower: int
    postgres: int
    mysql: int
    redis: int
    ssh: int

    @classmethod
    def default(cls) -> ProjectPorts:
        """Create default ports configuration."""
        return cls(**DEFAULT_PORTS)

    def to_env_dict(self, project_name: str | None = None) -> dict[str, str]:
        """Convert to environment variable dictionary.

        Args:
            project_name: Optional project name to set as instance name in Airflow UI
        """
        env = {
            "WEB_HOST_PORT": str(self.webserver),
            "FLOWER_HOST_PORT": str(self.flower),
            "POSTGRES_HOST_PORT": str(self.postgres),
            "MYSQL_HOST_PORT": str(self.mysql),
            "REDIS_HOST_PORT": str(self.redis),
            "SSH_PORT": str(self.ssh),
        }

        # Set Airflow instance name in UI (shows in navbar)
        if project_name:
            env["AIRFLOW__API__INSTANCE_NAME"] = f"ABM: {project_name}"

        return env


@dataclass
class ProjectMetadata:
    """Metadata for an Airflow development project."""

    name: str
    branch: str
    worktree_path: str
    ports: ProjectPorts
    description: str = ""
    pr_number: int | None = None
    backend: str = "sqlite"
    python_version: str = "3.11"
    created_at: str = ""
    frozen: bool = False
    managed_worktree: bool = True  # True if ABM created the worktree, False if adopted

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        data["ports"] = asdict(self.ports)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectMetadata:
        """Create from dictionary."""
        ports_data = data.pop("ports")

        # Migration: Add managed_worktree if missing (for old projects created before adopt/disown feature)
        if "managed_worktree" not in data:
            data["managed_worktree"] = True  # Existing projects were all created by ABM

        # Migration: Add SSH port if missing (for old projects created before v0.1.1)
        if "ssh" not in ports_data:
            # Calculate SSH port based on webserver port offset from OLD defaults
            ports_data["ssh"] = 12322 + (ports_data.get("webserver", 28080) - 28080)

        # Migration: Update ports from old defaults (28080, etc.) to new defaults (28180, etc.)
        # This avoids conflicts with vanilla breeze
        old_defaults = {
            "webserver": 28080,
            "flower": 25555,
            "postgres": 25433,
            "mysql": 23306,
            "redis": 26379,
            "ssh": 12322,
        }
        new_defaults = {
            "webserver": 28180,
            "flower": 25655,
            "postgres": 25533,
            "mysql": 23406,
            "redis": 26479,
            "ssh": 12422,
        }

        # If all ports match old defaults exactly, migrate to new defaults
        if all(ports_data.get(k) == v for k, v in old_defaults.items()):
            for k, v in new_defaults.items():
                ports_data[k] = v
        # If ports were offset from old defaults, apply same offset to new defaults
        elif ports_data.get("webserver", 0) < 28180:  # Old range
            offset = ports_data.get("webserver", 28080) - 28080
            for k in ["webserver", "flower", "postgres", "mysql", "redis", "ssh"]:
                ports_data[k] = new_defaults[k] + offset

        ports = ProjectPorts(**ports_data)
        return cls(ports=ports, **data)

    def save(self, project_dir: Path) -> None:
        """Save metadata to project directory."""
        metadata_file = project_dir / ".abm"
        with open(metadata_file, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, project_dir: Path) -> ProjectMetadata:
        """Load metadata from project directory."""
        metadata_file = project_dir / ".abm"
        with open(metadata_file) as f:
            data = json.load(f)
        return cls.from_dict(data)


@dataclass
class GlobalConfig:
    """Global ABM configuration."""

    schema_version: int
    airflow_repo: str
    worktree_base: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalConfig:
        """Create from dictionary."""
        return cls(**data)

    def save(self, config_file: Path) -> None:
        """Save configuration to file."""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, config_file: Path) -> GlobalConfig | None:
        """Load configuration from file."""
        if not config_file.exists():
            return None
        try:
            with open(config_file) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            # Corrupted config file
            return None
