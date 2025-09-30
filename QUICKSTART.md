# Quick Start Guide

Get up and running with Airflow Breeze Manager in 5 minutes.

## Installation

```bash
# Install using uv (recommended)
uv tool install airflow-breeze-manager

# Or run directly without installing
uvx airflow-breeze-manager --help

# Or install from source
git clone https://github.com/kaxil/abm.git
cd abm
uv pip install -e .
```

## Initial Setup

```bash
# 1. Initialize ABM (one-time setup)
# Run from your Airflow repository directory - it will auto-detect!
cd /path/to/airflow
abm init

# Or specify paths explicitly
abm init --airflow-repo /path/to/airflow --worktree-base /path/to/worktrees
```

**Tip:** ABM auto-detects Airflow repositories, so running `abm init` from your Airflow directory is the easiest way!

## Your First Project

```bash
# 2. Create a project for your feature
abm add my-feature --create-branch

# This creates:
# - Git worktree at ~/code/airflow-worktree/my-feature
# - Isolated ports (webserver: 28080, etc.)
# - PROJECT.md for notes

# 3. Start Airflow (webserver, scheduler, etc.)
abm start-airflow my-feature
# Visit http://localhost:28080

# Or just enter breeze shell
abm shell my-feature
# Inside breeze:
breeze@container$ pytest tests/unit_tests/
breeze@container$ exit

# 5. Check status
abm status my-feature
abm list

# 6. Link to GitHub PR
abm pr link 12345 my-feature
abm pr open my-feature  # Opens in browser
```

## Working with Multiple Projects

```bash
# Create another project (gets unique ports automatically)
abm add bugfix-456 --create-branch

# Both can run simultaneously!
abm docker up my-feature      # webserver on 28080
abm docker up bugfix-456      # webserver on 28081

# Switch between them
abm shell my-feature
abm shell bugfix-456

# List all projects
abm list
```

## Cleaning Up

```bash
# Remove project when done
abm remove my-feature

# Or freeze to save disk space (~3GB per project)
abm freeze my-feature        # Removes node_modules
abm thaw my-feature          # Restores when needed
```

## Common Commands

```bash
# Core
abm init                      # Initialize ABM
abm add <name>               # Create project
abm list                     # List all projects
abm status [project]         # Show project details
abm shell [project]          # Enter breeze shell
abm remove <project>         # Delete project

# Docker
abm docker up [project]      # Start containers
abm docker down [project]    # Stop containers

# GitHub
abm pr link <num> [project]  # Link PR
abm pr open [project]        # Open PR in browser
abm pr clear [project]       # Clear PR link

# Disk space
abm freeze <project>         # Save space
abm thaw <project>           # Restore dependencies
```

## Tips

1. **Auto-detection**: Most commands detect project from current directory:
   ```bash
   cd ~/code/airflow-worktree/my-feature
   abm status        # No project name needed
   abm shell         # Enters shell for current project
   ```

2. **PROJECT.md**: Edit branch-specific notes:
   ```bash
   cd ~/code/airflow-worktree/my-feature
   vim PROJECT.md    # Notes persist even after worktree removal
   ```

3. **Parallel testing**: Run multiple environments simultaneously:
   ```bash
   abm add test-postgres --backend postgres --create-branch
   abm add test-mysql --backend mysql --create-branch
   abm docker up test-postgres
   abm docker up test-mysql
   ```

4. **Breeze commands**: Run any breeze command:
   ```bash
   abm run my-feature pytest tests/unit_tests/
   abm run my-feature mypy providers/amazon/
   ```

## Environment Variables

- `ABM_AIRFLOW_REPO`: Default Airflow repository path
- `ABM_WORKTREE_BASE`: Default worktree directory

```bash
export ABM_AIRFLOW_REPO=~/projects/airflow
export ABM_WORKTREE_BASE=~/airflow-dev
abm init  # Uses environment variables as defaults
```

## Troubleshooting

**Port conflict**: ABM auto-assigns next available port. If issues persist:
```bash
abm remove my-feature
abm add my-feature  # Gets new ports
```

**Worktree exists**: Remove old worktree first:
```bash
cd ~/code/airflow
git worktree list
git worktree remove path/to/old/worktree --force
```

**Docker won't start**: Clean up containers:
```bash
abm docker down my-feature
docker system prune
abm docker up my-feature
```

## Next Steps

- Read the [full README](README.md) for detailed documentation
- Check [CONTRIBUTING.md](CONTRIBUTING.md) to contribute
- Star the repo if you find it useful!

## Help

```bash
# Get help for any command
abm --help
abm add --help
abm docker --help
```
