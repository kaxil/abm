"""Tests for --json and --yes agent mode flags."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from airflow_breeze_manager.cli import app
from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts
from airflow_breeze_manager.output import AgentResponse

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_project(projects_dir: Path, name: str = "my-project", **kwargs) -> ProjectMetadata:
    """Create a project on disk and return its metadata."""
    project_dir = projects_dir / name
    project_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "name": name,
        "branch": "feature/test",
        "worktree_path": f"/tmp/{name}",
        "ports": ProjectPorts.default(),
        "description": "Test project",
    }
    defaults.update(kwargs)
    metadata = ProjectMetadata(**defaults)
    metadata.save(project_dir)
    return metadata


@contextmanager
def _patch_projects_dir(projects_dir: Path) -> Iterator[None]:
    """Patch PROJECTS_DIR in both utils and cli modules."""
    with (
        patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
        patch("airflow_breeze_manager.cli.PROJECTS_DIR", projects_dir),
    ):
        yield


# ---------------------------------------------------------------------------
# AgentResponse unit tests
# ---------------------------------------------------------------------------


class TestAgentResponse:
    def test_success_response_shape(self) -> None:
        r = AgentResponse(success=True, data={"key": "val"})
        d = r.to_dict()
        assert d == {"success": True, "data": {"key": "val"}}
        assert "error" not in d

    def test_error_response_shape(self) -> None:
        r = AgentResponse(success=False, error="boom", error_code="ERR")
        d = r.to_dict()
        assert d == {"success": False, "error": "boom", "error_code": "ERR"}
        assert "data" not in d

    def test_error_response_without_code_omits_it(self) -> None:
        r = AgentResponse(success=False, error="boom")
        d = r.to_dict()
        assert "error_code" not in d


# ---------------------------------------------------------------------------
# list --json
# ---------------------------------------------------------------------------


class TestListJson:
    def test_list_json_empty(self) -> None:
        """list --json returns empty projects array."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.get_running_containers", return_value={}),
            ):
                result = runner.invoke(app, ["--json", "list"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["projects"] == []
                assert data["data"]["current_project"] is None

    def test_list_json_with_projects(self) -> None:
        """list --json returns enriched project data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.get_running_containers", return_value={}),
            ):
                result = runner.invoke(app, ["--json", "list"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                projects = data["data"]["projects"]
                assert len(projects) == 1
                p = projects[0]
                assert p["name"] == "my-project"
                assert "urls" in p
                assert "webserver" in p["urls"]
                assert p["running"] is None
                assert p["health"] == "not_running"

    def test_list_json_health_airflow_running(self) -> None:
        """list --json returns health field when airflow is running."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            running = {"my-project": {"services": ["airflow"], "is_start_airflow": True}}

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.get_running_containers", return_value=running),
                patch("airflow_breeze_manager.cli.check_webserver_health", return_value="healthy"),
            ):
                result = runner.invoke(app, ["--json", "list"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                p = data["data"]["projects"][0]
                assert p["running"] == "airflow"
                assert p["health"] == "healthy"


# ---------------------------------------------------------------------------
# status --json
# ---------------------------------------------------------------------------


class TestStatusJson:
    def test_status_json_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.get_running_containers", return_value={}),
            ):
                result = runner.invoke(app, ["--json", "status", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["name"] == "my-project"
                assert data["data"]["branch"] == "feature/test"
                assert "urls" in data["data"]
                assert data["data"]["running"] is None
                assert data["data"]["health"] == "not_running"

    def test_status_json_health_airflow_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            running = {"my-project": {"services": ["airflow"], "is_start_airflow": True}}

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.get_running_containers", return_value=running),
                patch("airflow_breeze_manager.cli.check_webserver_health", return_value="unhealthy"),
            ):
                result = runner.invoke(app, ["--json", "status", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["data"]["running"] == "airflow"
                assert data["data"]["health"] == "unhealthy"

    def test_status_json_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "status", "nonexistent"])
                assert result.exit_code == 1
                data = json.loads(result.output)
                assert data["success"] is False
                assert data["error_code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# pr commands --json
# ---------------------------------------------------------------------------


class TestPrJson:
    def test_pr_link_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "pr", "link", "42", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["pr_number"] == 42

    def test_pr_open_json_returns_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir, pr_number=123)

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.webbrowser") as mock_wb,
            ):
                result = runner.invoke(app, ["--json", "pr", "open", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["pr_number"] == 123
                assert "url" in data["data"]
                # Should NOT open browser in json mode
                mock_wb.open.assert_not_called()

    def test_pr_clear_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir, pr_number=123)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "pr", "clear", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["pr_number"] is None

    def test_pr_open_json_no_pr_linked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "pr", "open", "my-project"])
                assert result.exit_code == 1
                data = json.loads(result.output)
                assert data["success"] is False
                assert data["error_code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# freeze / thaw --json
# ---------------------------------------------------------------------------


class TestFreezeThawJson:
    def test_freeze_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            worktree = Path(tmpdir) / "wt"
            worktree.mkdir()
            _setup_project(projects_dir, worktree_path=str(worktree))

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "freeze", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["frozen"] is True

    def test_freeze_already_frozen_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir, frozen=True)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "freeze", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["already_frozen"] is True

    def test_thaw_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            worktree = Path(tmpdir) / "wt"
            worktree.mkdir()
            _setup_project(projects_dir, worktree_path=str(worktree), frozen=True)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "thaw", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["frozen"] is False


# ---------------------------------------------------------------------------
# shell --json (returns env without launching)
# ---------------------------------------------------------------------------


class TestShellJson:
    def test_shell_json_returns_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            worktree = Path(tmpdir) / "wt"
            worktree.mkdir()
            _setup_project(projects_dir, worktree_path=str(worktree))

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.get_conflicting_ports", return_value={}),
            ):
                result = runner.invoke(app, ["--json", "shell", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert "env" in data["data"]
                assert "breeze_command" in data["data"]
                assert "compose_project_name" in data["data"]
                assert data["data"]["breeze_command"][0] == "breeze"

    def test_shell_json_frozen_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir, frozen=True)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "shell", "my-project"])
                assert result.exit_code == 1
                data = json.loads(result.output)
                assert data["success"] is False
                assert data["error_code"] == "PROJECT_FROZEN"


# ---------------------------------------------------------------------------
# run --json (captures stdout/stderr)
# ---------------------------------------------------------------------------


class TestRunJson:
    def test_run_json_captures_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            worktree = Path(tmpdir) / "wt"
            worktree.mkdir()
            _setup_project(projects_dir, worktree_path=str(worktree))

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "pytest output here\n"
            mock_proc.stderr = ""

            with (
                _patch_projects_dir(projects_dir),
                patch("subprocess.run", return_value=mock_proc),
            ):
                result = runner.invoke(app, ["--json", "run", "my-project", "pytest", "tests/"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["exit_code"] == 0
                assert "stdout" in data["data"]
                assert "command" in data["data"]

    def test_run_json_no_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--json", "run", "my-project"])
                assert result.exit_code == 1
                data = json.loads(result.output)
                assert data["success"] is False
                assert data["error_code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# cleanup --json
# ---------------------------------------------------------------------------


class TestCleanupJson:
    def test_cleanup_json(self) -> None:
        with patch("airflow_breeze_manager.cli.cleanup_breeze_containers", return_value=3):
            result = runner.invoke(app, ["--json", "cleanup"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["success"] is True
            assert data["data"]["containers_cleaned"] == 3


# ---------------------------------------------------------------------------
# init --json
# ---------------------------------------------------------------------------


class TestInitJson:
    def test_init_json_already_initialized(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            abm_dir = Path(tmpdir) / ".abm"
            abm_dir.mkdir()
            config_file = abm_dir / "config.json"

            from airflow_breeze_manager.models import GlobalConfig

            config = GlobalConfig(schema_version=1, airflow_repo="/tmp/airflow", worktree_base="/tmp/wt")
            config.save(config_file)

            with patch("airflow_breeze_manager.cli.ABM_CONFIG_FILE", config_file):
                result = runner.invoke(app, ["--json", "init"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["already_initialized"] is True


# ---------------------------------------------------------------------------
# --yes flag bypasses prompts
# ---------------------------------------------------------------------------


class TestYesFlag:
    def test_yes_skips_freeze_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            worktree = Path(tmpdir) / "wt"
            worktree.mkdir()
            _setup_project(projects_dir, worktree_path=str(worktree))

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["--yes", "freeze", "my-project"])
                assert result.exit_code == 0
                assert "frozen" in result.output.lower()

    def test_yes_skips_disown_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            worktree = Path(tmpdir) / "wt"
            worktree.mkdir()
            _setup_project(projects_dir, worktree_path=str(worktree))

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.stop_project_containers"),
            ):
                result = runner.invoke(app, ["--yes", "disown", "my-project"])
                assert result.exit_code == 0
                assert "disowned" in result.output.lower()


# ---------------------------------------------------------------------------
# Human output unchanged (regression)
# ---------------------------------------------------------------------------


class TestHumanOutputUnchanged:
    def test_list_human_output_uses_table(self) -> None:
        """Ensure regular list (no --json) still uses Rich table."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.get_running_containers", return_value={}),
            ):
                result = runner.invoke(app, ["list"])
                assert result.exit_code == 0
                # Rich table output should contain project name but NOT be JSON
                assert "my-project" in result.output
                try:
                    json.loads(result.output)
                    raise AssertionError("Human output should not be JSON")
                except json.JSONDecodeError:
                    pass  # Expected

    def test_status_human_output_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with _patch_projects_dir(projects_dir):
                result = runner.invoke(app, ["status", "my-project"])
                assert result.exit_code == 0
                assert "my-project" in result.output
                try:
                    json.loads(result.output)
                    raise AssertionError("Human output should not be JSON")
                except json.JSONDecodeError:
                    pass


# ---------------------------------------------------------------------------
# Docker commands --json
# ---------------------------------------------------------------------------


class TestDockerJson:
    def test_docker_down_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / ".abm" / "projects"
            projects_dir.mkdir(parents=True)
            _setup_project(projects_dir)

            with (
                _patch_projects_dir(projects_dir),
                patch("airflow_breeze_manager.cli.stop_project_containers"),
            ):
                result = runner.invoke(app, ["--json", "docker", "down", "my-project"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert data["data"]["action"] == "down"
