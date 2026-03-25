# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-03-25

### Fixed
- Fix `abm start-airflow --headless` failing to start Airflow (standalone command not passed correctly to breeze)
- Fix `abm status` reporting false "running" state for stopped projects
- Fix `abm api` JWT authentication for Airflow 3

### Added
- Progress output during headless startup (port, log file path, polling status, elapsed time)
- `abm api` command for direct Airflow REST API calls
- `--forward-ports` flag for `abm run`

### Changed
- Improved help text for `shell`, `run`, and `start-airflow` commands

## [0.3.0] - 2026-02-06

### Added
- `abm exec` command to join a running Airflow container
- `--json` and `--yes` flags for agent-friendly, non-interactive CLI usage
- Headless mode for `start-airflow` (auto-enabled when no TTY or `--json`)

### Fixed
- `run`/`exec` commands now pass through flags like `-c` to the underlying command

### Changed
- Updated minimum dependency versions: `typer>=0.20.0`, `rich>=14.0.0`, `docker>=7.0.0`

## [0.2.0] - 2025-11-02

### Added
- New `adopt` command to import existing git worktrees into ABM management
  - Validates worktree belongs to configured Airflow repository
  - Sanitizes branch names (e.g., feature/foo → feature-foo)
  - Marks worktrees as adopted (protected from accidental removal)
- New `disown` command to remove ABM management while preserving worktrees
  - Stops containers and removes project metadata
  - Keeps worktree directory intact for manual use
  - Allows re-adoption later if needed
- Protection for adopted projects - require `--force` flag to remove

### Fixed
- Project names with slashes (e.g., "feature/version-indicator") now automatically sanitized to dashes
- Added warning message when project name is sanitized

### Changed
- Enhanced error messages to guide users to use `disown` instead of `remove --force` for adopted projects

## [0.1.0] - 2025-10-31

### Added
- Core project management commands (`init`, `add`, `list`, `status`, `remove`)
- Breeze integration commands (`shell`, `run`, `start-airflow`)
- Docker management commands (`docker up`, `docker down`, `cleanup`)
- GitHub PR integration (`pr link`, `pr open`, `pr clear`)
- Disk space management (`freeze`, `thaw`)
- Shell autocompletion support (`setup-autocomplete`)
- Port isolation for multiple projects (6 services)
- Docker isolation via `COMPOSE_PROJECT_NAME`
- Git worktree management
- Project-specific documentation (`PROJECT.md`, `CLAUDE.md`)
- Database isolation support (SQLite, Postgres, MySQL)
- Port conflict detection and auto-resolution
- Automatic `.cursor` directory symlinking for AI context
- Running status detection in project list

### Documentation
- Comprehensive README with all features
- AI assistant context (CLAUDE.md)

[Unreleased]: https://github.com/kaxil/abm/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/kaxil/abm/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/kaxil/abm/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kaxil/abm/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kaxil/abm/releases/tag/v0.1.0
