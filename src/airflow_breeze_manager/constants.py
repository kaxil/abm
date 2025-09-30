from __future__ import annotations

import os
from pathlib import Path

# Version for schema migrations
SCHEMA_VERSION = 1

# Base directories
HOME = Path.home()
ABM_DIR = HOME / ".airflow-breeze-manager"
PROJECTS_DIR = ABM_DIR / "projects"
ABM_CONFIG_FILE = ABM_DIR / ".abm.json"

# Default Airflow repository location
DEFAULT_AIRFLOW_REPO = os.environ.get("ABM_AIRFLOW_REPO", str(HOME / "code" / "airflow"))

# Worktree base directory
DEFAULT_WORKTREE_BASE = os.environ.get("ABM_WORKTREE_BASE", str(HOME / "code" / "airflow-worktree"))

# Port ranges for services
# Breeze defaults are: 28080, 25555, 25433, 23306, 26379, 12322
# ABM starts at +100 to avoid conflicts with vanilla breeze
PORT_RANGES = {
    "webserver": (28180, 28999),  # Airflow API/webserver (breeze default: 28080)
    "flower": (25655, 25999),  # Celery Flower (breeze default: 25555)
    "postgres": (25533, 25999),  # PostgreSQL (breeze default: 25433)
    "mysql": (23406, 23999),  # MySQL (breeze default: 23306)
    "redis": (26479, 26999),  # Redis (breeze default: 26379)
    "ssh": (12422, 12999),  # SSH server (breeze default: 12322)
}

# Default ports for first ABM project (offset +100 from breeze defaults)
DEFAULT_PORTS = {
    "webserver": 28180,
    "flower": 25655,
    "postgres": 25533,
    "mysql": 23406,
    "redis": 26479,
    "ssh": 12422,
}

# Project files managed by ABM
PROJECT_FILES = {
    "metadata": ".abm",
    "project_doc": "PROJECT.md",
}

# Symlinked files from project folder to worktree
# These files are stored in the project folder but symlinked to the worktree
# so they persist across worktree removal/recreation
SYMLINKED_FILES = [
    "PROJECT.md",  # Project-specific notes and context
    "CLAUDE.md",  # AI assistant context (separate from Airflow's CLAUDE.md)
]

# Note: .cursor/ is gitignored in Airflow, so it won't be in worktrees via git.
# ABM automatically creates a symlink from each worktree to the main repo's .cursor
# directory during `abm add`, so Cursor rules work immediately in all projects.

# Breeze environment variable prefixes that should be isolated per project
BREEZE_ENV_PREFIXES = [
    "WEB_HOST_PORT",
    "FLOWER_HOST_PORT",
    "POSTGRES_HOST_PORT",
    "MYSQL_HOST_PORT",
    "REDIS_HOST_PORT",
]
