# Airflow Breeze Manager (ABM)

[![PyPI version](https://badge.fury.io/py/airflow-breeze-manager.svg)](https://badge.fury.io/py/airflow-breeze-manager)
[![Python](https://img.shields.io/pypi/pyversions/airflow-breeze-manager.svg)](https://pypi.org/project/airflow-breeze-manager/)
[![CI](https://github.com/kaxil/abm/actions/workflows/ci.yml/badge.svg)](https://github.com/kaxil/abm/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Manage multiple Airflow development environments with isolated breeze instances for Apache Airflow development.

## Why ABM?

In today's AI-assisted development world, where Claude Code and other AI tools can generate code rapidly, **environment bottlenecks have become the new limiting factor**. You're no longer waiting for code to be written—you're waiting for environments to spin up.

When working on multiple Airflow features or PRs simultaneously, you face these challenges:

- **Port conflicts**: Multiple breeze instances can't run on the same ports
- **Branch switching**: Switching branches disrupts your development environment (rebuilding containers, reinstalling dependencies)
- **Container conflicts**: Docker containers collide when running multiple instances
- **Lost context**: Hard to track which branch/PR you're working on
- **Environment hell**: Waiting for containers to restart every time you switch branches

ABM solves this by giving each feature branch its own **complete isolated environment**:

- ✅ Separate git worktrees for each branch
- ✅ Unique port assignments (webserver, flower, postgres, mysql, redis)
- ✅ Isolated Docker containers (via COMPOSE_PROJECT_NAME)
- ✅ Branch-specific documentation (PROJECT.md)
- ✅ GitHub PR tracking
- ✅ Disk space management (freeze/thaw)

## Installation

### Using uvx (Recommended)

```bash
# Quick install with provided script
curl -sSL https://raw.githubusercontent.com/kaxil/abm/main/install.sh | bash

# Or manually with uv
uv tool install airflow-breeze-manager

# Or run directly without installing
uvx airflow-breeze-manager --help
```

### Using uv for Development

```bash
git clone https://github.com/kaxil/abm.git
cd abm
uv pip install -e .
```


## Quick Start

### 1. Initialize ABM

```bash
# Run from your Airflow repository (recommended - auto-detects location)
cd ~/path/to/airflow
abm init

# Set up shell autocompletion (optional but recommended)
abm setup-autocomplete

# Or specify custom locations explicitly
abm init --airflow-repo ~/projects/airflow --worktree-base ~/airflow-worktrees
```

**Auto-detection:** ABM will:
1. Try to detect Airflow repo in current directory
2. Check default location (`~/code/airflow`)
3. Prompt you to enter the path if not found

Default worktree base: `~/code/airflow-worktree`

**Autocompletion:** Enables tab completion for project names in all commands. After setup, you can type `abm shell <TAB>` to see all your projects.

### 2. Create a Project

```bash
# Create a new project for feature development
abm add my-feature --create-branch

# Enter breeze shell
abm shell my-feature

# Or start full Airflow environment
abm start-airflow my-feature
```

**What happens:**
- Creates a git worktree for the branch
- Allocates unique ports for all services
- Creates a PROJECT.md file for branch-specific notes
- Sets up isolated environment configuration

### 3. Work on Your Project

```bash
# Option A: Interactive shell
abm shell my-feature

# Option B: Start full Airflow (webserver, scheduler, triggerer, etc.)
abm start-airflow my-feature
# Visit http://localhost:28180 (shows "ABM: my-feature" in navbar)

# Option C: Run specific commands
abm run my-feature pytest tests/unit_tests/
```

### 4. Manage Projects

```bash
# List all projects (shows → for current project)
abm list

# Check project status
abm status my-feature

# Link a GitHub PR
abm pr link 12345 my-feature
abm pr open my-feature  # Opens PR in browser

# Freeze project to save disk space (~3GB per project)
abm freeze old-feature

# Thaw when ready to work again
abm thaw old-feature

# Remove project
abm remove my-feature
abm remove my-feature --keep-docs  # Keep PROJECT.md for later
```

## How It Works

### Port Isolation

Each project gets unique ports to avoid conflicts:

| Service    | ABM Default | Breeze Default | Range        |
|------------|-------------|----------------|--------------|
| API/Web    | 28180       | 28080          | 28180-28999  |
| Flower     | 25655       | 25555          | 25655-25999  |
| Postgres   | 25533       | 25433          | 25533-25999  |
| MySQL      | 23406       | 23306          | 23406-23999  |
| Redis      | 26479       | 26379          | 26479-26999  |
| SSH        | 12422       | 12322          | 12422-12999  |

**Notes:**
- In Airflow 3.0+, the API server and webserver are unified, so the "API/Web" port serves both the REST API and the web UI
- ABM defaults start at +100 from Breeze defaults to avoid conflicts when running vanilla `breeze` alongside ABM projects
- Each project sets `AIRFLOW__API__INSTANCE_NAME` to show "ABM: project-name" in the UI navbar ([see docs](https://airflow.apache.org/docs/apache-airflow/stable/configurations-ref.html#instance-name))

**Smart Port Management:**
- Automatically allocates available ports when creating projects
- Detects conflicts before starting breeze
- Offers to auto-reassign ports if conflicts are found
- Works even if you run `breeze` directly or have other services using ports

### Docker & Database Isolation

**Container Isolation:**
Each project uses a unique `COMPOSE_PROJECT_NAME` (e.g., `abm-my-feature`) to ensure:
- Container names don't conflict
- Networks are isolated
- Volumes are separate

**Database Isolation:**

**SQLite (default - recommended):**
- ✅ **Perfect isolation** - each project has its own `airflow.db` file in the worktree
- ✅ **No shared state** - completely independent databases
- ✅ **Portable** - database moves with the worktree

**Postgres/MySQL:**
- ✅ Each project gets its **own isolated database** (e.g., `airflow_my_feature`, `airflow_bug_fix`)
- ✅ **Schema isolation** - tables and data are completely separate per project
- ✅ **Automatic creation** - ABM creates the database on first run
- ✅ **Identified in UI** - Instance name shows which project you're viewing
- 💡 **How it works**: Projects use the same database server but separate databases within it (similar to having separate schemas)

ABM automatically configures each project with:
1. Project-specific database connection strings
2. `files/airflow-breeze-config/init.sh` to create database if needed
3. `files/airflow-breeze-config/environment_variables.env` with all settings

### UI Identification

Each project sets `AIRFLOW__API__INSTANCE_NAME` to display **"ABM: project-name"** in the Airflow UI navbar, making it easy to identify which project you're viewing ([Airflow docs](https://airflow.apache.org/docs/apache-airflow/stable/configurations-ref.html#instance-name)).

### Git Worktrees

ABM uses [git worktrees](https://git-scm.com/docs/git-worktree) to:
- Check out multiple branches simultaneously
- Avoid switching branches in your main repo
- Keep each project's state independent

### Project-Specific Documentation

Each project has two documentation files that persist across worktree removal/recreation:

#### `PROJECT.md`
- Lives in `~/.airflow-breeze-manager/projects/{project}/PROJECT.md`
- Symlinked into the worktree for easy editing
- Human-readable notes about the project
- Includes ports, branch info, and your notes

#### `CLAUDE.md`
- Lives in `~/.airflow-breeze-manager/projects/{project}/CLAUDE.md`
- Symlinked into the worktree as **project-specific AI context**
- Templates for "What I'm Working On", "Key Files", "Testing Strategy", etc.
- Helps Claude/Cursor maintain context across sessions

**Note about AI Editor Configurations:**
- If your Airflow repo has a `.cursor` directory with rules, **ABM automatically creates a symlink** from each worktree to it
- This means Cursor/AI editor rules work immediately in all worktrees without manual setup
- Combined with ABM's project-specific `CLAUDE.md`, you get both global and per-feature context for AI assistants

## Commands Reference

### Core Commands

#### `abm init`

Initialize ABM and set up directory structure.

```bash
# Auto-detect (run from Airflow directory)
cd /path/to/airflow
abm init

# Or specify explicitly
abm init --airflow-repo PATH --worktree-base PATH
```

**Interactive prompts:** ABM will ask for confirmation when it detects your Airflow repository and suggest default locations for worktrees.

#### `abm add <name>`

Create a new project with isolated environment.

```bash
abm add <name> [OPTIONS]

Options:
  -b, --branch TEXT          Git branch name (defaults to project name)
  -d, --description TEXT     Project description
  --backend TEXT             Database backend (default: sqlite)
  --python-version TEXT      Python version (default: 3.11)
  --create-branch            Create new branch if it doesn't exist
```

#### `abm adopt <worktree_path>`

Adopt an existing git worktree into ABM management.

```bash
abm adopt <worktree_path> [OPTIONS]

Options:
  -n, --name TEXT            Project name (defaults to branch name)
  -d, --description TEXT     Project description
  --backend TEXT             Database backend (default: sqlite)
  --python-version TEXT      Python version (default: 3.11)
```

**Use cases:**
- Import worktrees created manually or by other tools
- Manage existing development branches with ABM
- Migrate from manual worktree management to ABM

**Key features:**
- Validates worktree belongs to configured Airflow repository
- Idempotent - safe to run multiple times on the same worktree
- Automatically sanitizes branch names (e.g., `feature/foo` → `feature-foo`)
- Worktree is marked as "adopted" and protected from accidental removal

**Example:**
```bash
# You manually created a worktree
git worktree add ~/worktrees/my-feature feature-branch

# Now adopt it into ABM
abm adopt ~/worktrees/my-feature

# ABM will:
# - Detect the branch name (feature-branch)
# - Create project metadata
# - Allocate ports
# - Set up PROJECT.md and CLAUDE.md
# - Mark it as adopted (protected from removal)
```

#### `abm disown [project]`

Remove ABM management but keep the worktree.

```bash
abm disown [project] [OPTIONS]

Options:
  -f, --force              Skip confirmation
```

**Use cases:**
- Stop managing a worktree with ABM but keep it for manual use
- Clean up ABM metadata while preserving your work
- Prepare a worktree for management by another tool

**What gets removed:**
- ABM project metadata
- Symlinks (PROJECT.md, CLAUDE.md)
- Breeze configuration
- Docker containers

**What gets preserved:**
- The git worktree itself
- All your code changes
- Git branch

**Example:**
```bash
# Disown a project
abm disown my-feature --force

# The worktree at ~/worktrees/my-feature still exists
# You can now manage it manually or re-adopt it later
```

#### `abm list`

List all projects with their status.

```bash
abm list
```

**Shows:**
- Active indicator (→) for current project
- Project name
- Git branch
- Python version
- Database backend
- API port (Airflow 3.0+ unified API/Webserver)
- Running status (🟢 = full Airflow, 🟡 = shell/services, - = stopped)
- Associated PR number
- Flags (🧊 = frozen)

**Example:**
```
┏━━┳━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━┳━━━━━━━┓
┃  ┃ Name    ┃ Branch       ┃ Py   ┃ Backend ┃ API  ┃ Running ┃ PR ┃ Flags ┃
┡━━╇━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━╇━━━━━━━┩
│→ │ feature │ my-feature   │ 3.11 │ sqlite  │ :281 │ 🟢      │ -  │ -     │
│  │         │              │      │         │      │ airflow │    │       │
│  │ bugfix  │ fix-bug-123  │ 3.12 │ sqlite  │ :281 │ 🟡 shell│ #5 │ -     │
│  │ oldwork │ old-feature  │ 3.10 │ sqlite  │ :282 │ -       │ -  │ 🧊    │
└──┴─────────┴──────────────┴──────┴─────────┴──────┴─────────┴────┴───────┘

→ Currently in: feature | 🟢 1 airflow | 🟡 1 shell
```

**Column meanings:**
- **Py** - Python version (e.g., 3.10, 3.11, 3.12)
- **API** - API server port (clickable link when Airflow is running)
  - Click to open in browser (terminal must support hyperlinks)
- **Running** - Container status:
  - `🟢 airflow` - Full Airflow running (`start-airflow`)
  - `🟡 shell` - Breeze shell session active
  - `-` - Nothing running
- **PR** - GitHub PR number (clickable link if associated)
  - Click to open PR in browser
- **Flags** - Project state:
  - `🧊` - Frozen (node_modules removed to save disk space)
  - `-` - Active (ready to use)

**Clickable Links:**
ABM uses terminal hyperlinks (supported by iTerm2, VS Code terminal, Windows Terminal, GNOME Terminal, etc.). When a project is running or has a PR linked, you can Cmd+Click (Mac) or Ctrl+Click (Windows/Linux) on the links to open them in your browser.

#### `abm status [project]`

Show detailed project status.

```bash
abm status [project]
```

If no project specified, auto-detects from current directory.

#### `abm shell [project]`

Enter breeze shell for a project.

```bash
abm shell [project]
```

This sets up the environment with isolated ports and runs `breeze shell`.

#### `abm run [project] <command...>`

Run a breeze command for a project.

```bash
abm run my-feature pytest tests/unit_tests/
abm run my-feature mypy providers/amazon/
```

#### `abm remove <project>`

Remove a project and clean up resources (stops containers, removes worktree and metadata).

```bash
abm remove <project> [--keep-docs] [--force] [--delete-branch]

Options:
  --keep-docs       Keep PROJECT.md file
  --delete-branch   Delete the git branch (WARNING: destructive)
  -f, --force       Skip confirmation & allow removing adopted projects
```

**What it does:**
1. Stops and removes all Docker containers for this project
2. Removes the git worktree
3. Deletes project metadata (or keeps only PROJECT.md if `--keep-docs`)

**Protection for adopted projects:**
- Adopted projects (created with `abm adopt`) require `--force` to remove
- This prevents accidental deletion of worktrees you didn't create with ABM
- Use `abm disown` if you want to keep the worktree but remove ABM management

**Example:**
```bash
# Remove a managed project (created with abm add)
abm remove my-feature

# Try to remove an adopted project (will fail without --force)
abm remove adopted-feature
# Error: Cannot remove adopted project 'adopted-feature' without --force
# Hint: Use 'abm disown' to keep the worktree

# Force remove an adopted project
abm remove adopted-feature --force
```

### Docker Commands

#### `abm docker up [project]`

Start Docker containers for a project.

```bash
abm docker up [project]
```

#### `abm docker down [project]`

Stop Docker containers for a specific project.

```bash
abm docker down [project]
```

**How it works:** Directly stops and removes containers by matching their working directory to the project's worktree path. This ensures only the specified project's containers are affected, leaving other ABM projects running.

### GitHub PR Commands

#### `abm pr link <pr_number> [project]`

Associate a GitHub PR with a project.

```bash
abm pr link 12345 my-feature
```

#### `abm pr open [project]`

Open the associated PR in browser.

```bash
abm pr open my-feature
```

#### `abm pr clear [project]`

Remove PR association.

```bash
abm pr clear my-feature
```

### Disk Space Management

#### `abm freeze <project>`

Freeze a project to save disk space.

```bash
abm freeze old-project [--force]
```

This removes:
- `node_modules` directory (~2-3GB)

Projects must be thawed before use.

#### `abm thaw <project>`

Thaw a frozen project.

```bash
abm thaw old-project
```

This reinstalls:
- npm packages with `npm ci`

#### `abm setup-autocomplete`

Set up shell autocompletion for tab completion of project names and commands.

```bash
# Auto-detect shell (bash, zsh, or fish)
abm setup-autocomplete

# Or specify explicitly
abm setup-autocomplete zsh
```

**Smart Installation:**
- **Oh-My-Zsh users:** Installs to `~/.oh-my-zsh/custom/completions/_abm` (auto-loaded)
- **Standard Zsh:** Adds completion to `~/.zshrc`
- **Bash:** Installs to `~/.local/share/bash-completion/completions/abm`
- **Fish:** Installs to `~/.config/fish/completions/abm.fish`

After installation, restart your terminal or run `exec zsh` (for Oh-My-Zsh) / `source ~/.zshrc`.

**What you get:**
- `abm <TAB>` → Shows all commands with descriptions
- `abm shell <TAB>` → Shows all project names
- `abm start-airflow <TAB>` → Shows all project names
- `abm remove <TAB>` → Shows all project names
- Smart completion for all commands and subcommands

#### `abm start-airflow [project]`

Start full Airflow environment (equivalent to `breeze start-airflow`).

```bash
# Start Airflow for a project
abm start-airflow my-feature

# Or auto-detect from current directory
cd ~/code/airflow-worktree/my-feature
abm start-airflow
```

**What it does:**
- Starts webserver, scheduler, triggerer, and all dependencies
- Uses isolated ports for the project
- Automatically detects and resolves port conflicts
- Runs in foreground (Ctrl+C to stop)

**Access services:**
- Webserver: http://localhost:28080 (or your project's port)
- Flower: http://localhost:25555 (or your project's port)

## Configuration

### Environment Variables

- `ABM_AIRFLOW_REPO` - Default Airflow repository path (default: `~/code/airflow`)
- `ABM_WORKTREE_BASE` - Default worktree base directory (default: `~/code/airflow-worktree`)

Set these before running `abm init` to use custom locations:
```bash
export ABM_AIRFLOW_REPO=~/projects/airflow
export ABM_WORKTREE_BASE=~/airflow-dev
abm init  # Uses environment variables
```

### Files & Directories

```
~/.airflow-breeze-manager/
├── .abm.json                    # Global configuration
└── projects/
    ├── my-feature/
    │   ├── .abm                 # Project metadata (JSON)
    │   ├── PROJECT.md           # Human notes (symlinked to worktree)
    │   └── CLAUDE.md            # AI context (symlinked to worktree)
    └── another-feature/
        ├── .abm
        ├── PROJECT.md
        └── CLAUDE.md
```

### Documentation Files

Each project has two documentation files for branch-specific context:

**`PROJECT.md`** - Human-readable notes:
```markdown
# my-feature

## Description
Implement awesome feature

## Branch
`feature/awesome-improvement`

## Ports
- Webserver: 28081
- Flower: 25556
- Postgres: 25434
- MySQL: 23307
- Redis: 26380

## Notes
Add your notes here...
```

**`CLAUDE.md`** - AI assistant context:
```markdown
# Project Context for AI Assistants

## Project: my-feature

### What I'm Working On
Adding a new execution model for deferred tasks that improves
performance by 40% in high-throughput scenarios.

### Key Files/Areas
- `airflow/models/taskinstance.py` - Main task execution logic
- `airflow/executors/celery_executor.py` - Celery integration
- `tests/models/test_taskinstance.py` - Test coverage

### Testing Strategy
1. Unit tests for new defer() method
2. Integration tests with Celery backend
3. Performance benchmarks (see dev/benchmark_defer.py)

### Notes & Decisions
- Decided to use Redis for state tracking (not DB) for lower latency
- Need to handle edge case where worker dies mid-defer
```

**Both files:**
- Live in the project folder (survive worktree removal)
- Are symlinked into the worktree for easy editing
- Are available to AI assistants for project-specific context

## Development Workflow

### Typical Multi-Branch Workflow

```bash
# Working on feature A
abm add feature-a --create-branch
abm start-airflow feature-a
# ... test at http://localhost:28080 ...
# Press Ctrl+C to stop

# Switch to bug fix (without affecting feature-a!)
abm add bugfix-123 --create-branch -d "Fix critical bug"
abm start-airflow bugfix-123
# ... test at http://localhost:28081 (different port!)
# Press Ctrl+C to stop

# Back to feature A
abm shell feature-a
# ... continue working ...

# Clean up when done
abm remove bugfix-123
abm freeze feature-a  # Save space while waiting for review
```

### Working with Claude Code

```bash
# Create project
abm add my-feature --create-branch

# Add context to CLAUDE.md for AI assistants
cd ~/code/airflow-worktree/my-feature
cat >> CLAUDE.md << 'EOF'
### What I'm Working On
Adding support for asset-based scheduling with dynamic dependencies.

### Key Files
- airflow/models/asset.py
- airflow/dag_processing/dag_processor.py
EOF

# Both CLAUDE.md and PROJECT.md are available to AI assistants
# Airflow's main CLAUDE.md provides architecture context
# Your project's CLAUDE.md provides feature-specific context
abm shell my-feature
```

### Testing Multiple Branches

```bash
# Set up parallel testing
abm add test-postgres --create-branch
abm add test-mysql --create-branch

# Start both environments (in separate terminals)
# Terminal 1:
abm start-airflow test-postgres

# Terminal 2:
abm start-airflow test-mysql

# Each runs on different ports automatically!
# test-postgres: http://localhost:28080
# test-mysql: http://localhost:28081
```

## Troubleshooting

### Port Conflicts (Automatically Handled!)

**ABM automatically detects and fixes port conflicts!** When you run `abm shell`, it checks for conflicts first.

**Example interaction:**
```bash
❯ abm shell my-feature
⚠️  Port conflict detected!

The following ports are already in use:
  • ssh: 12322

This usually means:
  1. Another breeze instance is running (run 'abm cleanup')
  2. Another ABM project is running (check 'abm list')
  3. Some other service is using these ports

Try to automatically find alternative ports? [Y/n]: y

✅ Updated ports:
  • ssh: 12323

Entering breeze shell for 'my-feature'...
```

**Manual fixes:**
```bash
abm cleanup          # Clean up orphaned breeze containers
abm docker down      # Stop other ABM projects
lsof -i :12322       # See what's using a port
```

### Worktree Already Exists

If you get a "worktree already exists" error:

```bash
# List existing worktrees
cd ~/code/airflow
git worktree list

# Remove old worktree
git worktree remove path/to/worktree

# Then recreate project
abm add my-feature
```

### Docker Containers Won't Start

```bash
# Clean up all breeze containers (recommended)
abm cleanup

# Or stop specific project
abm docker down my-feature

# Clean up Docker system
docker system prune

# Restart
abm docker up my-feature
```

### Project is Frozen

If you try to use a frozen project:

```bash
# Thaw it first
abm thaw my-feature

# Then use normally
abm shell my-feature
```

Contributions welcome! This is a tool for Airflow developers by Airflow developers.

### Development Setup

```bash
git clone https://github.com/kaxil/abm.git
cd abm
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Code quality
uv run mypy src/
uv run ruff check src/
```

## License

Apache License 2.0

## Credits

- Inspired by [claudette-cli](https://github.com/mistercrunch/claudette-cli)
- Built for the [Apache Airflow](https://github.com/apache/airflow) community
