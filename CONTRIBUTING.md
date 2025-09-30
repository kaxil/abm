# Contributing to Airflow Breeze Manager

Thank you for your interest in contributing to ABM!

## Development Setup

### 1. Clone the Repository

```bash
git clone https://github.com/kaxil/abm.git
cd abm
```

### 2. Install in Development Mode

```bash
# Using uv (recommended)
uv pip install -e ".[dev]"

# Or using uv tool
uv tool install -e . --force
```

### 3. Install Prek Hooks

```bash
prek install
```

## Running Tests

```bash
# Run all tests with uv
uv run pytest

# Run with coverage
uv run pytest --cov=airflow_breeze_manager --cov-report=html

# Run specific test file
uv run pytest tests/test_models.py

# Run with verbose output
uv run pytest -v
```

## Code Quality

### Type Checking

```bash
uv run mypy src/airflow_breeze_manager
```

### Linting

```bash
# Check code
uv run ruff check src/

# Auto-fix issues
uv run ruff check --fix src/

# Format code
uv run ruff format src/
```

### Prek

```bash
# Run all prek hooks
prek run --all-files
```

## Project Structure

```
abm/
├── src/
│   └── airflow_breeze_manager/
│       ├── __init__.py
│       ├── cli.py           # Main CLI implementation
│       ├── constants.py     # Constants and defaults
│       ├── models.py        # Data models
│       └── utils.py         # Utility functions
├── tests/
│   ├── __init__.py
│   └── test_models.py
├── pyproject.toml           # Project configuration
├── README.md                # User documentation
└── CONTRIBUTING.md          # This file
```

## Making Changes

1. **Create a branch** for your changes
2. **Make your changes** with clear, focused commits
3. **Add tests** for new functionality
4. **Update documentation** if needed
5. **Run tests and linting** to ensure quality
6. **Submit a pull request**

## Commit Messages

Follow conventional commits format:

```
type(scope): description

feat(cli): add new command for syncing project
fix(utils): handle missing worktree correctly
docs(readme): update installation instructions
test(models): add tests for port allocation
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Test additions or changes
- `refactor`: Code refactoring
- `style`: Code style changes
- `chore`: Build process or auxiliary tool changes

## Adding New Features

### Adding a New Command

1. Add command function to `cli.py`:

```python
@app.command()
def my_command(
    project_name: Annotated[str, typer.Argument(help="Project name")],
) -> None:
    """Description of command."""
    project, project_dir = require_project(project_name)
    # Implementation
```

2. Add tests in `tests/test_cli.py`
3. Update README with command documentation

### Adding New Configuration

1. Add constant to `constants.py`
2. Update `GlobalConfig` or `ProjectMetadata` in `models.py` if persisted
3. Add migration logic if changing schema

## Testing Locally

Test the CLI as an end user would:

```bash
# Test installation with uv
uv pip install -e .

# Or install as a tool
uv tool install -e .

# Test commands
abm --help
abm init --airflow-repo ~/code/airflow
abm add test-project --create-branch
abm list
abm status test-project
abm remove test-project --force

# Or run without installing
uvx --from . abm --help
```

## Release Process

(For maintainers)

1. Update version in `pyproject.toml` and `src/airflow_breeze_manager/__init__.py`
2. Update CHANGELOG
3. Create git tag: `git tag v0.1.0`
4. Push tag: `git push origin v0.1.0`
5. Build and publish:

```bash
# Build with uv
uv build

# Publish to PyPI
uv publish

# Or use twine if preferred
python -m twine upload dist/*
```

## Questions?

Open an issue or reach out to the maintainers!
