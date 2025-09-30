# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**Airflow Breeze Manager (ABM)** is a CLI tool for managing multiple Apache Airflow development environments with isolated breeze instances, specifically designed for Airflow development.

## Key Features

- **Git Worktrees**: Manage multiple branches simultaneously
- **Port Isolation**: Each project gets unique ports for all services (webserver, flower, postgres, mysql, redis)
- **Docker Isolation**: Uses `COMPOSE_PROJECT_NAME` to isolate containers
- **PROJECT.md**: Branch-specific documentation that survives worktree removal
- **PR Tracking**: Link GitHub PRs to projects
- **Freeze/Thaw**: Save disk space by removing/restoring dependencies

## Architecture

### Components

1. **CLI** (`cli.py`): Main entry point using Typer
2. **Models** (`models.py`): Data models for projects and configuration
3. **Utils** (`utils.py`): Helper functions for git, docker, and file operations
4. **Constants** (`constants.py`): Configuration defaults and port ranges

### Data Flow

1. User runs `abm init` → Creates `~/.airflow-breeze-manager/` structure
2. User runs `abm add project` → Creates:
   - Git worktree in `worktree_base/project`
   - Project folder in `~/.airflow-breeze-manager/projects/project/`
   - Allocates unique ports
   - Creates PROJECT.md and symlinks to worktree
3. User runs `abm shell project` → Sets environment variables and runs `breeze shell`

### Port Allocation

Ports are allocated from ranges to avoid conflicts:
- Webserver: 28080-28999 (breeze default: 28080)
- Flower: 25555-25999 (breeze default: 25555)
- Postgres: 25433-25999 (breeze default: 25433)
- MySQL: 23306-23999 (breeze default: 23306)
- Redis: 26379-26999 (breeze default: 26379)

### Docker Isolation

Each project uses a unique `COMPOSE_PROJECT_NAME`:
- Format: `abm-{project_name}`
- Based on breeze pattern: `airflow-test-{name}`
- Ensures container names don't conflict

## Development Guidelines

### Code Style

- Use type hints everywhere (Python 3.10+ syntax)
- Follow PEP 8
- Use Rich for console output
- Use Typer Annotated syntax for CLI arguments
- Keep functions focused and testable

### File Structure

```
src/airflow_breeze_manager/
├── __init__.py          # Package info
├── cli.py              # 500+ lines - main CLI commands
├── constants.py        # 80 lines - configuration
├── models.py           # 150 lines - data models
└── utils.py            # 150 lines - utilities
```

### Testing

- Use pytest
- Test models and utilities thoroughly
- CLI testing can be light (integration-focused)
- Mock subprocess calls where appropriate

### Dependencies

- **typer**: CLI framework with rich formatting
- **rich**: Terminal formatting and tables
- Standard library only for core functionality

### Build System

- Uses **uv** for package management (modern, fast Python package installer)
- `uv tool install` for global CLI installation
- `uvx` for running without installation
- Compatible with pip for legacy workflows

### Key Patterns

1. **Project Detection**: Commands can auto-detect project from current directory
2. **Require Project**: Use `require_project()` helper for validation
3. **Port Environment**: Set `WEB_HOST_PORT`, etc. before running breeze
4. **Symlinks**: PROJECT.md lives in project folder, symlinked to worktree

## Common Tasks

### Adding a New Command

1. Add to `cli.py` with proper type hints
2. Use `require_project()` for project commands
3. Update README command reference
4. Add tests if complex logic

### Changing Configuration

1. Update `constants.py`
2. Bump `SCHEMA_VERSION` if persisted
3. Add migration logic to handle old configs
4. Update `models.py` if affects data structures

### Improving Port Allocation

Logic in `utils.py:allocate_ports()`:
- Gathers used ports from all projects
- Finds first available in each range
- Raises error if range exhausted

## Integration with Breeze

ABM wraps breeze commands and sets environment variables:

```python
env = os.environ.copy()
env.update({
    "WEB_HOST_PORT": "28081",
    "FLOWER_HOST_PORT": "25556",
    # ... other ports
    "COMPOSE_PROJECT_NAME": "abm-my-feature"
})
os.execvpe("breeze", ["breeze", "shell"], env)
```

Breeze respects these environment variables for:
- Port bindings in docker-compose
- Container naming via COMPOSE_PROJECT_NAME
- Network isolation

## Troubleshooting Common Issues

### Worktree Conflicts

If worktree already exists:
- Check with `git worktree list`
- Remove with `git worktree remove <path> --force`
- Then recreate project

### Port Conflicts

If port already in use:
- ABM allocates next available
- Can manually check with `lsof -i :<port>`
- Remove and recreate project to get new ports

### Docker Issues

If containers won't start:
- Check `docker ps` for conflicts
- Use `abm docker down` to clean up
- Run `docker system prune` if needed

## Future Enhancements

Potential improvements:
- [ ] Custom port ranges via config
- [ ] Support for multiple Airflow repos
- [ ] Database snapshot/restore
- [ ] Integration with GitHub CLI for PR management
- [ ] Shell completion scripts
- [ ] Project templates
- [ ] Bulk operations (freeze/thaw all)

## References

- [claudette-cli](https://github.com/mistercrunch/claudette-cli): Original inspiration
- [Apache Airflow](https://github.com/apache/airflow): Target development environment
- [Git Worktrees](https://git-scm.com/docs/git-worktree): Core technology
