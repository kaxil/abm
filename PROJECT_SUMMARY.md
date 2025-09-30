# Airflow Breeze Manager - Project Summary

## Overview

**Airflow Breeze Manager (ABM)** is a CLI tool that enables Airflow developers to work on multiple branches simultaneously without port conflicts or container collisions, specifically designed for Apache Airflow's breeze development environment.

## Problem Solved

When working on multiple Airflow PRs/features simultaneously, developers face:

1. **Port conflicts**: Can't run multiple breeze instances on the same ports
2. **Branch switching overhead**: Switching branches disrupts development state
3. **Container conflicts**: Docker containers collide when running multiple instances
4. **Lost context**: Hard to track which branch is for which PR/feature

## Solution

ABM creates isolated development environments for each branch/feature:

- ✅ **Git worktrees**: Multiple branches checked out simultaneously
- ✅ **Unique ports**: Auto-allocated for each project (5 services)
- ✅ **Docker isolation**: Via `COMPOSE_PROJECT_NAME`
- ✅ **Persistent context**: PROJECT.md survives worktree removal
- ✅ **PR tracking**: Link and open GitHub PRs
- ✅ **Disk management**: Freeze/thaw to save space

## Key Features

### 1. Port Isolation

Each project gets unique ports across 5 services:
- Webserver/API: 28080-28999
- Flower: 25555-25999
- Postgres: 25433-25999
- MySQL: 23306-23999
- Redis: 26379-26999

### 2. Docker Isolation

Uses `COMPOSE_PROJECT_NAME=abm-{project}` to ensure:
- Unique container names
- Isolated networks
- Separate volumes

### 3. Git Worktrees

Leverages git's worktree feature to:
- Check out multiple branches simultaneously
- Avoid branch switching in main repo
- Keep independent working states

### 4. PROJECT.md

Branch-specific documentation that:
- Lives in `~/.airflow-breeze-manager/projects/{project}/`
- Symlinked to worktree for easy editing
- Persists across worktree removal/recreation
- Available to Claude Code for context

### 5. GitHub Integration

Track PRs associated with projects:
- Link PR numbers to projects
- Open PR in browser with one command
- See PR status in project list

### 6. Disk Management

Freeze/thaw projects to manage disk space:
- Freeze removes node_modules (~2-3GB)
- Thaw reinstalls dependencies
- Multiple projects can coexist without eating disk

## Architecture

### Directory Structure

```
~/.airflow-breeze-manager/
├── .abm.json                      # Global config
└── projects/
    ├── feature-a/
    │   ├── .abm                   # Project metadata
    │   └── PROJECT.md             # Symlinked to worktree
    └── feature-b/
        ├── .abm
        └── PROJECT.md

~/code/airflow-worktree/           # Worktrees
├── feature-a/                     # Git worktree
│   └── PROJECT.md -> ~/.abm/projects/feature-a/PROJECT.md
└── feature-b/
    └── PROJECT.md -> ~/.abm/projects/feature-b/PROJECT.md
```

### Data Models

1. **GlobalConfig**: ABM configuration (repo paths, schema version)
2. **ProjectMetadata**: Project details (name, branch, ports, PR, etc.)
3. **ProjectPorts**: Port allocation for 5 services

### Command Flow

```
abm add my-feature
  ├─> Check if project exists
  ├─> Create/verify git branch
  ├─> Allocate unique ports
  ├─> Create git worktree
  ├─> Create project folder
  ├─> Save metadata
  ├─> Create PROJECT.md
  └─> Symlink PROJECT.md to worktree

abm shell my-feature
  ├─> Load project metadata
  ├─> Set environment variables (ports, COMPOSE_PROJECT_NAME)
  ├─> Change to worktree directory
  └─> Execute breeze shell
```

## CLI Commands

### Core Commands
- `abm init` - Initialize ABM
- `abm add` - Create new project
- `abm list` - List all projects
- `abm status` - Show project details
- `abm shell` - Enter breeze shell
- `abm run` - Run breeze command
- `abm remove` - Delete project

### Docker Commands
- `abm docker up` - Start containers
- `abm docker down` - Stop containers

### PR Commands
- `abm pr link` - Link GitHub PR
- `abm pr open` - Open PR in browser
- `abm pr clear` - Clear PR link

### Disk Commands
- `abm freeze` - Save disk space
- `abm thaw` - Restore dependencies

## Technical Implementation

### Port Allocation

```python
def allocate_ports() -> ProjectPorts:
    # Get used ports from existing projects
    used_ports = get_used_ports()

    # Find first available in each range
    for service, (min_port, max_port) in PORT_RANGES.items():
        for port in range(min_port, max_port + 1):
            if port not in used_ports[service]:
                allocate port
                break
```

### Docker Isolation

```python
# Set environment for breeze
env = {
    "WEB_HOST_PORT": "28081",
    "FLOWER_HOST_PORT": "25556",
    "COMPOSE_PROJECT_NAME": "abm-my-feature",
    ...
}

# Breeze respects these variables
os.execvpe("breeze", ["breeze", "shell"], env)
```

### Worktree Management

```python
# Create worktree
run_command([
    "git", "worktree", "add",
    str(worktree_path),
    branch
], cwd=repo_path)

# Remove worktree
run_command([
    "git", "worktree", "remove",
    str(worktree_path),
    "--force"
], cwd=repo_path)
```

## Dependencies

Minimal dependencies for broad compatibility:
- **typer**: Modern CLI framework
- **rich**: Beautiful terminal output
- Standard library for everything else

## Future Enhancements

Potential improvements:
- [ ] Custom port ranges via config file
- [ ] Multiple Airflow repo support
- [ ] Database snapshot/restore between projects
- [ ] GitHub CLI integration for automatic PR creation
- [ ] Shell completion (bash/zsh/fish)
- [ ] Project templates for common workflows
- [ ] Bulk operations (freeze/thaw all, start/stop all)
- [ ] Web UI for project management
- [ ] Integration with Airflow's CLAUDE.md for enhanced AI context

## Comparison to Claudette

| Feature | Claudette (Superset) | ABM (Airflow) |
|---------|---------------------|---------------|
| Purpose | Superset development | Airflow development |
| Worktrees | ✅ | ✅ |
| Port isolation | Frontend only | 5 services |
| Docker isolation | docker-compose | COMPOSE_PROJECT_NAME |
| Python venv | Manual | Via breeze |
| Shell | activate script | breeze shell |
| PROJECT.md | ✅ | ✅ |
| PR tracking | ✅ | ✅ |
| Freeze/thaw | ✅ | ✅ |

## Installation Methods

1. **uv tool** (recommended): `uv tool install airflow-breeze-manager`
2. **uvx** (run without install): `uvx airflow-breeze-manager`
3. **pip**: `pip install airflow-breeze-manager`
4. **Source**: `uv pip install -e .`

## Documentation

- **README.md**: Comprehensive user guide
- **QUICKSTART.md**: 5-minute getting started
- **CONTRIBUTING.md**: Developer guide
- **CLAUDE.md**: AI assistant context
- **PROJECT_SUMMARY.md**: This file (overview)

## Development

Built with:
- Python 3.10+ (using modern type hints)
- **uv** for package management (fast, modern)
- Typer for CLI framework
- Rich for terminal UI
- pytest for testing
- mypy for type checking
- ruff for linting/formatting

Clean architecture:
- `cli.py`: Command implementations (500 lines)
- `models.py`: Data structures (150 lines)
- `utils.py`: Helper functions (150 lines)
- `constants.py`: Configuration (80 lines)

Modern tooling:
- Uses `uv` instead of pip/pipx for speed
- `uvx` for running without installation
- Compatible with traditional pip workflows

## Target Users

Primary: Apache Airflow contributors and developers

Use cases:
1. Working on multiple PRs simultaneously
2. Testing different backends (sqlite, postgres, mysql)
3. Comparing implementations across branches
4. Long-running development with frequent context switching
5. Pair programming / code review setups

## Success Metrics

The tool succeeds if:
- ✅ Developers can work on 3+ branches simultaneously without conflicts
- ✅ Context switching takes < 5 seconds
- ✅ Zero port/container conflicts
- ✅ Disk space usage is manageable with freeze/thaw
- ✅ Setup time < 5 minutes for new users

## License

Apache License 2.0

## Credits

- Inspired by [claudette-cli](https://github.com/mistercrunch/claudette-cli)
- Built for [Apache Airflow](https://github.com/apache/airflow) community
- Leverages [git worktrees](https://git-scm.com/docs/git-worktree)
- Uses breeze development environment
