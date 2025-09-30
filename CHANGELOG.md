# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Quick start guide (QUICKSTART.md)
- Real-world examples (EXAMPLES.md)
- Contributing guide (CONTRIBUTING.md)
- Project summary (PROJECT_SUMMARY.md)
- AI assistant context (CLAUDE.md)

[Unreleased]: https://github.com/kaxil/abm/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kaxil/abm/releases/tag/v0.1.0
