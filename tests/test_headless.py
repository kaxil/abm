"""Tests for headless Airflow startup: _wait_for_ready and _start_airflow_headless."""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import click
import pytest

from airflow_breeze_manager.cli import _start_airflow_headless, _wait_for_ready
from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts


@pytest.fixture
def sample_ports():
    return ProjectPorts(
        webserver=28180,
        flower=25655,
        postgres=25533,
        mysql=23406,
        redis=26479,
        ssh=12422,
        mssql=21533,
        rabbitmq=25772,
    )


@pytest.fixture
def sample_project(sample_ports, tmp_path):
    return ProjectMetadata(
        name="test-project",
        branch="main",
        worktree_path=str(tmp_path / "worktree"),
        ports=sample_ports,
        description="Test project",
        backend="sqlite",
        python_version="3.12",
        created_at="2026-01-01T00:00:00",
        frozen=False,
        managed_worktree=False,
    )


class TestWaitForReady:
    """Tests for _wait_for_ready polling logic."""

    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    def test_returns_true_on_200(self, _mock_json):
        """API returning 200 means ready."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _wait_for_ready(28180, timeout=5) is True

    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    def test_returns_true_on_401(self, _mock_json):
        """API returning 401 (auth required) means server is up."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(None, 401, "Unauthorized", {}, None),
        ):
            assert _wait_for_ready(28180, timeout=5) is True

    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    def test_returns_true_on_403(self, _mock_json):
        """API returning 403 (forbidden) means server is up."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(None, 403, "Forbidden", {}, None),
        ):
            assert _wait_for_ready(28180, timeout=5) is True

    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    @patch("airflow_breeze_manager.cli.HEADLESS_POLL_INTERVAL", 0.01)
    def test_returns_false_on_timeout(self, _mock_json):
        """Connection refused for the full timeout returns False."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            assert _wait_for_ready(28180, timeout=0.05) is False

    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    @patch("airflow_breeze_manager.cli.HEADLESS_POLL_INTERVAL", 0.01)
    def test_retries_until_ready(self, _mock_json):
        """Polls through failures then succeeds."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        # Fail twice (URLError for both URLs = 4 calls), then succeed
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                urllib.error.URLError("refused"),
                urllib.error.URLError("refused"),
                urllib.error.URLError("refused"),
                urllib.error.URLError("refused"),
                mock_resp,
            ],
        ):
            assert _wait_for_ready(28180, timeout=5) is True

    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=False)
    @patch("airflow_breeze_manager.cli.console")
    @patch("airflow_breeze_manager.cli.HEADLESS_POLL_INTERVAL", 0.01)
    def test_prints_progress_in_non_json_mode(self, mock_console, _mock_json):
        """Progress messages are printed when not in JSON mode."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        # Fail once, then succeed
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                urllib.error.URLError("refused"),
                urllib.error.URLError("refused"),
                mock_resp,
            ],
        ):
            assert _wait_for_ready(28180, timeout=5) is True

        # Should have printed at least one status line and the ready message
        print_calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("waiting" in c.lower() for c in print_calls)
        assert any("ready" in c.lower() for c in print_calls)

    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    @patch("airflow_breeze_manager.cli.console")
    @patch("airflow_breeze_manager.cli.HEADLESS_POLL_INTERVAL", 0.01)
    def test_no_progress_in_json_mode(self, mock_console, _mock_json):
        """No progress messages in JSON mode."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            _wait_for_ready(28180, timeout=0.05)

        mock_console.print.assert_not_called()


def _run_headless(sample_project, tmp_path, **kwargs):
    """Helper to call _start_airflow_headless, catching typer.Exit from json_success."""
    project_dir = tmp_path / "project_dir"
    project_dir.mkdir(exist_ok=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)

    mock_process = MagicMock()
    mock_process.pid = 12345

    with patch("subprocess.Popen", autospec=True) as mock_popen:
        mock_popen.return_value = mock_process
        try:
            _start_airflow_headless(
                sample_project,
                project_dir,
                worktree,
                env={"PATH": "/usr/bin"},
                compose_project="abm-test",
                **kwargs,
            )
        except (SystemExit, click.exceptions.Exit):
            pass  # json_success raises typer.Exit(0)

    return mock_popen


class TestStartAirflowHeadless:
    """Tests for _start_airflow_headless command construction."""

    @patch("airflow_breeze_manager.cli._wait_for_ready", return_value=True)
    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    def test_passes_standalone_as_single_arg(self, _mock_json, _mock_wait, sample_project, tmp_path):
        """'airflow standalone' must be a single string argument, not two."""
        mock_popen = _run_headless(sample_project, tmp_path)

        breeze_cmd = mock_popen.call_args[0][0]
        # "airflow standalone" should be ONE element, not two separate ones
        assert "airflow standalone" in breeze_cmd
        assert breeze_cmd[-1] == "airflow standalone"
        # Should NOT have "airflow" and "standalone" as separate args
        standalone_indices = [i for i, arg in enumerate(breeze_cmd) if arg == "standalone"]
        assert len(standalone_indices) == 0, "standalone should not be a separate argument"

    @patch("airflow_breeze_manager.cli._wait_for_ready", return_value=True)
    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    def test_breeze_cmd_structure(self, _mock_json, _mock_wait, sample_project, tmp_path):
        """Breeze command includes shell, python version, backend, quiet, tty disabled."""
        mock_popen = _run_headless(sample_project, tmp_path)

        breeze_cmd = mock_popen.call_args[0][0]
        assert breeze_cmd[0] == "breeze"
        assert "shell" in breeze_cmd
        assert "--quiet" in breeze_cmd
        assert "--tty" in breeze_cmd
        assert "disabled" in breeze_cmd
        assert "--python" in breeze_cmd
        assert "3.12" in breeze_cmd
        assert "--backend" in breeze_cmd
        assert "sqlite" in breeze_cmd

    @patch("airflow_breeze_manager.cli._wait_for_ready", return_value=True)
    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    def test_extra_breeze_args_appended(self, _mock_json, _mock_wait, sample_project, tmp_path):
        """Extra breeze args are inserted before 'airflow standalone'."""
        mock_popen = _run_headless(
            sample_project,
            tmp_path,
            extra_breeze_args=["--mount-ui-dist", "--skip-assets-compilation"],
        )

        breeze_cmd = mock_popen.call_args[0][0]
        # Extra args should come before "airflow standalone"
        standalone_idx = breeze_cmd.index("airflow standalone")
        assert "--mount-ui-dist" in breeze_cmd
        assert breeze_cmd.index("--mount-ui-dist") < standalone_idx

    @patch("airflow_breeze_manager.cli._wait_for_ready", return_value=True)
    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=False)
    @patch("airflow_breeze_manager.cli.console")
    def test_prints_startup_info(self, mock_console, _mock_json, _mock_wait, sample_project, tmp_path):
        """Prints project name, port, and log path on startup."""
        _run_headless(sample_project, tmp_path)

        print_calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("test-project" in c for c in print_calls)
        assert any("28180" in c for c in print_calls)
        assert any("headless.log" in c for c in print_calls)

    @patch("airflow_breeze_manager.cli._wait_for_ready", return_value=True)
    @patch("airflow_breeze_manager.cli.is_json_mode", return_value=True)
    @patch("airflow_breeze_manager.cli.console")
    def test_no_startup_info_in_json_mode(self, mock_console, _mock_json, _mock_wait, sample_project, tmp_path):
        """No startup messages in JSON mode."""
        _run_headless(sample_project, tmp_path)

        # In JSON mode, only json_success should print -- no startup messages
        startup_prints = [
            c for c in mock_console.print.call_args_list if "Starting" in str(c) or "Port" in str(c) or "Logs" in str(c)
        ]
        assert len(startup_prints) == 0
