"""Tests for the ABM API module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from airflow_breeze_manager.api import (
    _coerce_value,
    _get_bearer_token,
    clear_token_cache,
    detect_api_version,
    format_endpoint_list,
    make_request,
    parse_fields,
)
from airflow_breeze_manager.cli import app
from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """Clear bearer token cache between tests."""
    clear_token_cache()
    yield
    clear_token_cache()


# ---------------------------------------------------------------------------
# Tests for _coerce_value
# ---------------------------------------------------------------------------


class TestCoerceValue:
    def test_bool_true(self) -> None:
        assert _coerce_value("true") is True
        assert _coerce_value("True") is True
        assert _coerce_value("TRUE") is True

    def test_bool_false(self) -> None:
        assert _coerce_value("false") is False
        assert _coerce_value("False") is False

    def test_null(self) -> None:
        assert _coerce_value("null") is None
        assert _coerce_value("none") is None
        assert _coerce_value("None") is None

    def test_int(self) -> None:
        assert _coerce_value("10") == 10
        assert _coerce_value("0") == 0
        assert _coerce_value("-5") == -5

    def test_float(self) -> None:
        assert _coerce_value("3.14") == 3.14
        assert _coerce_value("-0.5") == -0.5

    def test_string(self) -> None:
        assert _coerce_value("hello") == "hello"
        assert _coerce_value("") == ""
        assert _coerce_value("foo bar") == "foo bar"


# ---------------------------------------------------------------------------
# Tests for parse_fields
# ---------------------------------------------------------------------------


class TestParseFields:
    def test_typed_fields(self) -> None:
        result = parse_fields(["limit=10", "only_active=true"], None)
        assert result == {"limit": 10, "only_active": True}

    def test_raw_fields(self) -> None:
        result = parse_fields(None, ["key=my_var", "value=hello"])
        assert result == {"key": "my_var", "value": "hello"}

    def test_raw_fields_preserve_numeric_strings(self) -> None:
        result = parse_fields(None, ["port=8080"])
        assert result == {"port": "8080"}
        assert isinstance(result["port"], str)

    def test_mixed_fields(self) -> None:
        result = parse_fields(["limit=10"], ["key=my_var"])
        assert result == {"limit": 10, "key": "my_var"}

    def test_empty_fields(self) -> None:
        result = parse_fields(None, None)
        assert result == {}

    def test_value_with_equals(self) -> None:
        result = parse_fields(None, ["query=a=b"])
        assert result == {"query": "a=b"}

    def test_invalid_field_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid field format"):
            parse_fields(["no_equals_sign"], None)


# ---------------------------------------------------------------------------
# Tests for make_request
# ---------------------------------------------------------------------------


class TestMakeRequest:
    def test_get_request(self) -> None:
        """Test basic GET request construction."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.read.return_value = b'{"dags": []}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = make_request("http://localhost:28180", "dags")

        assert result["status_code"] == 200
        assert result["body"] == {"dags": []}

        # Verify the URL was constructed correctly
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:28180/api/v1/dags"
        assert req.method == "GET"

    def test_get_with_params(self) -> None:
        """Test GET request with query parameters."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.read.return_value = b'{"dags": []}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            make_request("http://localhost:28180", "dags", params={"limit": 10, "only_active": "true"})

        req = mock_urlopen.call_args[0][0]
        assert "limit=10" in req.full_url
        assert "only_active=true" in req.full_url

    def test_post_with_json_body(self) -> None:
        """Test POST request with JSON body."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.read.return_value = b'{"key": "test"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            make_request(
                "http://localhost:28180",
                "variables",
                method="POST",
                json_data={"key": "test", "value": "hello"},
            )

        req = mock_urlopen.call_args[0][0]
        assert req.method == "POST"
        assert req.data == b'{"key": "test", "value": "hello"}'
        assert req.get_header("Content-type") == "application/json"

    def test_raw_endpoint(self) -> None:
        """Test raw endpoint (no /api/vX prefix)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = b'{"status": "healthy"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            make_request("http://localhost:28180", "health", raw_endpoint=True)

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:28180/health"

    def test_api_v2(self) -> None:
        """Test API v2 URL construction (with Bearer token)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = b'{"dags": []}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("airflow_breeze_manager.api._get_bearer_token", return_value="fake-token"),
            patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        ):
            make_request("http://localhost:28180", "dags", api_version="v2")

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:28180/api/v2/dags"

    def test_http_error(self) -> None:
        """Test handling of HTTP errors."""
        import urllib.error

        error = urllib.error.HTTPError(
            "http://localhost:28180/api/v1/dags",
            404,
            "Not Found",
            {"Content-Type": "application/json"},
            None,
        )
        error.read = MagicMock(return_value=b'{"detail": "not found"}')

        with patch("urllib.request.urlopen", side_effect=error):
            result = make_request("http://localhost:28180", "dags")

        assert result["status_code"] == 404
        assert result["body"] == {"detail": "not found"}

    def test_connection_error(self) -> None:
        """Test handling of connection errors."""
        import urllib.error

        error = urllib.error.URLError("Connection refused")

        with patch("urllib.request.urlopen", side_effect=error):
            result = make_request("http://localhost:28180", "dags")

        assert result["status_code"] == 0
        assert "Connection refused" in result["body"]["error"]

    def test_auth_header_v1_basic(self) -> None:
        """Test that Basic Auth header is set correctly for v1 API."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            make_request("http://localhost:28180", "dags", username="admin", password="secret")

        req = mock_urlopen.call_args[0][0]
        auth_header = req.get_header("Authorization")
        assert auth_header is not None
        assert auth_header.startswith("Basic ")

        # Decode and verify credentials
        import base64

        decoded = base64.b64decode(auth_header.split(" ")[1]).decode()
        assert decoded == "admin:secret"

    def test_auth_header_v2_bearer(self) -> None:
        """Test that Bearer token is used for v2 API when available."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("airflow_breeze_manager.api._get_bearer_token", return_value="my-jwt-token"),
            patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        ):
            make_request("http://localhost:28180", "dags", api_version="v2")

        req = mock_urlopen.call_args[0][0]
        auth_header = req.get_header("Authorization")
        assert auth_header == "Bearer my-jwt-token"

    def test_auth_header_v2_fallback_to_basic(self) -> None:
        """Test that v2 falls back to Basic auth when Bearer token is unavailable."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("airflow_breeze_manager.api._get_bearer_token", return_value=None),
            patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        ):
            make_request("http://localhost:28180", "dags", api_version="v2")

        req = mock_urlopen.call_args[0][0]
        auth_header = req.get_header("Authorization")
        assert auth_header is not None
        assert auth_header.startswith("Basic ")

    def test_auth_v2_retries_on_401_with_fresh_token(self) -> None:
        """Test that v2 request retries with a fresh token on 401 (expired token)."""
        import urllib.error

        # First request: 401 (expired token)
        error_401 = urllib.error.HTTPError(
            "http://localhost:28180/api/v2/dags",
            401,
            "Unauthorized",
            {"Content-Type": "application/json"},
            None,
        )
        error_401.read = MagicMock(return_value=b'{"detail": "Token expired"}')

        # Retry request: 200 (fresh token works)
        mock_success = MagicMock()
        mock_success.status = 200
        mock_success.headers = {}
        mock_success.read.return_value = b'{"dags": []}'
        mock_success.__enter__ = lambda s: s
        mock_success.__exit__ = MagicMock(return_value=False)

        call_count = 0

        def side_effect_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise error_401
            return mock_success

        with (
            patch("airflow_breeze_manager.api._get_bearer_token") as mock_get_token,
            patch("urllib.request.urlopen", side_effect=side_effect_fn),
        ):
            # First call returns stale token, force_refresh call returns new token
            mock_get_token.side_effect = lambda *a, **kw: "fresh-token" if kw.get("_force_refresh") else "stale-token"
            result = make_request("http://localhost:28180", "dags", api_version="v2")

        assert result["status_code"] == 200
        assert result["body"] == {"dags": []}
        # Should have called _get_bearer_token twice (initial + force_refresh)
        assert mock_get_token.call_count == 2

    def test_non_json_response(self) -> None:
        """Test handling of non-JSON response body."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.read.return_value = b"OK"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = make_request("http://localhost:28180", "health", raw_endpoint=True)

        assert result["status_code"] == 200
        assert result["body"] == "OK"


# ---------------------------------------------------------------------------
# Tests for detect_api_version
# ---------------------------------------------------------------------------


class TestDetectApiVersion:
    def test_detects_v2(self) -> None:
        """Test detection of Airflow 3.x (v2 API responds 200)."""
        with patch("airflow_breeze_manager.api.make_request") as mock_req:
            mock_req.return_value = {"status_code": 200, "headers": {}, "body": {"version": "3.0.0"}}
            version = detect_api_version("http://localhost:28180")

        assert version == "v2"
        assert mock_req.call_args_list[0][1]["api_version"] == "v2"

    def test_detects_v2_via_401(self) -> None:
        """Test detection of Airflow 3.x when v2 returns 401 (needs JWT auth)."""
        with patch("airflow_breeze_manager.api.make_request") as mock_req:
            mock_req.return_value = {"status_code": 401, "headers": {}, "body": {"detail": "Unauthorized"}}
            version = detect_api_version("http://localhost:28180")

        assert version == "v2"

    def test_detects_v2_via_403(self) -> None:
        """Test detection of Airflow 3.x when v2 returns 403 (forbidden)."""
        with patch("airflow_breeze_manager.api.make_request") as mock_req:
            mock_req.return_value = {"status_code": 403, "headers": {}, "body": {"detail": "Forbidden"}}
            version = detect_api_version("http://localhost:28180")

        assert version == "v2"

    def test_detects_v1(self) -> None:
        """Test detection of Airflow 2.x (v1 API)."""
        with patch("airflow_breeze_manager.api.make_request") as mock_req:
            # v2 fails (404), v1 succeeds (200)
            mock_req.side_effect = [
                {"status_code": 404, "headers": {}, "body": {}},
                {"status_code": 200, "headers": {}, "body": {"version": "2.10.0"}},
            ]
            version = detect_api_version("http://localhost:28180")

        assert version == "v1"

    def test_defaults_to_v1(self) -> None:
        """Test fallback to v1 when neither version responds."""
        with patch("airflow_breeze_manager.api.make_request") as mock_req:
            mock_req.return_value = {"status_code": 0, "headers": {}, "body": {}}
            version = detect_api_version("http://localhost:28180")

        assert version == "v1"


# ---------------------------------------------------------------------------
# Tests for _get_bearer_token
# ---------------------------------------------------------------------------


class TestBearerToken:
    def test_get_bearer_token_success(self) -> None:
        """Test successful token retrieval from /auth/token."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"access_token": "jwt-123", "token_type": "bearer"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            token = _get_bearer_token("http://localhost:28180", "airflow", "airflow")

        assert token == "jwt-123"

        # Verify POST to /auth/token with correct Content-Type
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:28180/auth/token"
        assert req.method == "POST"
        assert req.get_header("Content-type") == "application/x-www-form-urlencoded"
        assert b"username=airflow" in req.data
        assert b"password=airflow" in req.data

    def test_get_bearer_token_cached(self) -> None:
        """Test that second call returns cached token without HTTP request."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"access_token": "jwt-cached", "token_type": "bearer"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            token1 = _get_bearer_token("http://localhost:28180", "airflow", "airflow")
            token2 = _get_bearer_token("http://localhost:28180", "airflow", "airflow")

        assert token1 == "jwt-cached"
        assert token2 == "jwt-cached"
        # Only one HTTP call should have been made
        assert mock_urlopen.call_count == 1

    def test_get_bearer_token_cache_expires(self) -> None:
        """Test that expired cached tokens are refreshed."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"access_token": "jwt-fresh", "token_type": "bearer"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        # Seed cache with an old token (timestamp guaranteed to be expired)
        import time

        from airflow_breeze_manager.api import _token_cache
        from airflow_breeze_manager.constants import TOKEN_MAX_AGE

        cache_key = ("http://localhost:28180", "airflow", "airflow")
        expired_ts = time.monotonic() - TOKEN_MAX_AGE - 1
        _token_cache[cache_key] = ("jwt-stale", expired_ts)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            token = _get_bearer_token("http://localhost:28180", "airflow", "airflow")

        assert token == "jwt-fresh"
        assert mock_urlopen.call_count == 1

    def test_get_bearer_token_force_refresh(self) -> None:
        """Test that _force_refresh bypasses the cache."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"access_token": "jwt-new", "token_type": "bearer"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        # Seed cache with a valid, non-expired token
        import time

        from airflow_breeze_manager.api import _token_cache

        cache_key = ("http://localhost:28180", "airflow", "airflow")
        _token_cache[cache_key] = ("jwt-old", time.monotonic())

        with patch("urllib.request.urlopen", return_value=mock_response):
            token = _get_bearer_token("http://localhost:28180", "airflow", "airflow", _force_refresh=True)

        assert token == "jwt-new"

    def test_get_bearer_token_404(self) -> None:
        """Test that 404 (Airflow 2) returns None and caches the negative result."""
        import urllib.error

        error = urllib.error.HTTPError(
            "http://localhost:28180/auth/token",
            404,
            "Not Found",
            {},
            None,
        )

        with patch("urllib.request.urlopen", side_effect=error) as mock_urlopen:
            token1 = _get_bearer_token("http://localhost:28180", "airflow", "airflow")
            token2 = _get_bearer_token("http://localhost:28180", "airflow", "airflow")

        assert token1 is None
        assert token2 is None
        # Second call should use cached NOT_AVAILABLE — only 1 HTTP call
        assert mock_urlopen.call_count == 1

    def test_get_bearer_token_connection_error(self) -> None:
        """Test that connection errors return None (not cached as NOT_AVAILABLE)."""
        import urllib.error

        error = urllib.error.URLError("Connection refused")

        with patch("urllib.request.urlopen", side_effect=error) as mock_urlopen:
            token1 = _get_bearer_token("http://localhost:28180", "airflow", "airflow")
            token2 = _get_bearer_token("http://localhost:28180", "airflow", "airflow")

        assert token1 is None
        assert token2 is None
        # Connection errors are NOT cached — both calls hit the network
        assert mock_urlopen.call_count == 2


# ---------------------------------------------------------------------------
# Tests for format_endpoint_list
# ---------------------------------------------------------------------------


class TestFormatEndpointList:
    @pytest.fixture
    def sample_spec(self) -> dict:
        return {
            "paths": {
                "/api/v1/dags": {
                    "get": {"summary": "List DAGs"},
                },
                "/api/v1/dags/{dag_id}": {
                    "get": {"summary": "Get a DAG"},
                    "patch": {"summary": "Update a DAG"},
                },
                "/api/v1/variables": {
                    "get": {"summary": "List variables"},
                    "post": {"summary": "Create a variable"},
                },
                "/api/v1/variables/{variable_key}": {
                    "get": {"summary": "Get a variable"},
                    "patch": {"summary": "Update a variable"},
                    "delete": {"summary": "Delete a variable"},
                },
            }
        }

    def test_list_all_endpoints(self, sample_spec: dict) -> None:
        endpoints = format_endpoint_list(sample_spec)
        assert len(endpoints) == 8
        # Should be sorted by path
        assert endpoints[0]["path"] == "/api/v1/dags"

    def test_filter_endpoints(self, sample_spec: dict) -> None:
        endpoints = format_endpoint_list(sample_spec, filter_pattern="variable")
        assert len(endpoints) == 5
        assert all("variable" in ep["path"].lower() for ep in endpoints)

    def test_filter_no_match(self, sample_spec: dict) -> None:
        endpoints = format_endpoint_list(sample_spec, filter_pattern="nonexistent")
        assert endpoints == []

    def test_endpoint_structure(self, sample_spec: dict) -> None:
        endpoints = format_endpoint_list(sample_spec)
        for ep in endpoints:
            assert "method" in ep
            assert "path" in ep
            assert "summary" in ep
            assert ep["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE")

    def test_empty_spec(self) -> None:
        endpoints = format_endpoint_list({})
        assert endpoints == []

    def test_spec_with_no_paths(self) -> None:
        endpoints = format_endpoint_list({"paths": {}})
        assert endpoints == []


# ---------------------------------------------------------------------------
# Tests for CLI api command
# ---------------------------------------------------------------------------


class TestApiCommand:
    def _create_project(self, projects_dir: Path) -> None:
        """Helper to create a test project in the given directory."""
        project_dir = projects_dir / "test-project"
        project_dir.mkdir()
        metadata = ProjectMetadata(
            name="test-project",
            branch="main",
            worktree_path="/tmp/test-project",
            ports=ProjectPorts.default(),
            description="Test project",
        )
        with open(project_dir / ".abm", "w") as f:
            json.dump(metadata.to_dict(), f)

    def test_api_no_endpoint(self) -> None:
        """Test api command with no endpoint shows error."""
        result = runner.invoke(app, ["api"])
        assert result.exit_code == 1

    def test_api_get_request(self) -> None:
        """Test basic GET request through CLI."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": {"dags": [], "total_entries": 0},
                }
                result = runner.invoke(app, ["api", "dags", "--project", "test-project"])

            assert result.exit_code == 0

    def test_api_connection_error(self) -> None:
        """Test api command when Airflow is not running."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 0,
                    "headers": {},
                    "body": {"error": "Connection refused"},
                }
                result = runner.invoke(app, ["api", "dags", "--project", "test-project"])

            assert result.exit_code == 1
            assert "Connection failed" in result.output

    def test_api_json_mode(self) -> None:
        """Test api command in JSON mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": {"dags": []},
                }
                result = runner.invoke(app, ["--json", "api", "dags", "--project", "test-project"])

            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output["success"] is True
            assert output["data"]["status_code"] == 200
            assert output["data"]["body"] == {"dags": []}

    def test_api_ls_subcommand(self) -> None:
        """Test api ls subcommand."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            sample_spec = {
                "paths": {
                    "/api/v1/dags": {"get": {"summary": "List DAGs"}},
                    "/api/v1/variables": {"get": {"summary": "List variables"}},
                }
            }

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.get_openapi_spec", return_value=sample_spec),
            ):
                result = runner.invoke(app, ["api", "ls", "--project", "test-project"])

            assert result.exit_code == 0

    def test_api_ls_json_mode(self) -> None:
        """Test api ls subcommand in JSON mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            sample_spec = {
                "paths": {
                    "/api/v1/dags": {"get": {"summary": "List DAGs"}},
                }
            }

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.get_openapi_spec", return_value=sample_spec),
            ):
                result = runner.invoke(app, ["--json", "api", "ls", "--project", "test-project"])

            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output["success"] is True
            assert "endpoints" in output["data"]
            assert len(output["data"]["endpoints"]) == 1

    def test_api_with_fields_get(self) -> None:
        """Test that -F fields become query params for GET requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {},
                    "body": {"dags": []},
                }
                runner.invoke(
                    app,
                    ["api", "dags", "--project", "test-project", "-F", "limit=10", "-F", "only_active=true"],
                )

            # Verify params were passed (not json_data)
            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["params"] == {"limit": 10, "only_active": True}
            assert call_kwargs["json_data"] is None

    def test_api_with_fields_post(self) -> None:
        """Test that -f fields become JSON body for POST requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {},
                    "body": {"key": "test"},
                }
                runner.invoke(
                    app,
                    [
                        "api",
                        "variables",
                        "--project",
                        "test-project",
                        "-X",
                        "POST",
                        "-f",
                        "key=test",
                        "-f",
                        "value=hello",
                    ],
                )

            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["json_data"] == {"key": "test", "value": "hello"}
            assert call_kwargs["params"] is None

    def test_api_with_body(self) -> None:
        """Test --body flag passes raw JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {},
                    "body": {},
                }
                runner.invoke(
                    app,
                    [
                        "api",
                        "dags/my_dag/dagRuns",
                        "--project",
                        "test-project",
                        "-X",
                        "POST",
                        "--body",
                        '{"conf": {}}',
                    ],
                )

            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["json_data"] == {"conf": {}}

    def test_api_raw_flag(self) -> None:
        """Test --raw flag skips /api/vX prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {},
                    "body": {"status": "healthy"},
                }
                runner.invoke(
                    app,
                    ["api", "health", "--project", "test-project", "--raw"],
                )

            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["raw_endpoint"] is True

    def test_api_include_headers(self) -> None:
        """Test -i flag includes headers in output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {"Content-Type": "application/json", "Server": "gunicorn"},
                    "body": {"dags": []},
                }
                result = runner.invoke(
                    app,
                    ["api", "dags", "--project", "test-project", "-i"],
                )

            assert result.exit_code == 0
            assert "HTTP 200" in result.output

    def test_api_invalid_body_json(self) -> None:
        """Test --body with invalid JSON shows error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
            ):
                result = runner.invoke(
                    app,
                    [
                        "api",
                        "dags",
                        "--project",
                        "test-project",
                        "-X",
                        "POST",
                        "--body",
                        "not-valid-json",
                    ],
                )

            assert result.exit_code == 1
            assert "Invalid JSON body" in result.output

    def test_api_get_request_v2(self) -> None:
        """Test GET request through CLI with v2 (Airflow 3) detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v2"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": {"dags": [], "total_entries": 0},
                }
                result = runner.invoke(app, ["api", "dags", "--project", "test-project"])

            assert result.exit_code == 0
            # Verify v2 was passed through
            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["api_version"] == "v2"

    def test_api_http_error_exit_code(self) -> None:
        """Test that HTTP 4xx/5xx responses result in exit code 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            projects_dir.mkdir(parents=True)
            self._create_project(projects_dir)

            with (
                patch("airflow_breeze_manager.utils.PROJECTS_DIR", projects_dir),
                patch("airflow_breeze_manager.api.detect_api_version", return_value="v1"),
                patch("airflow_breeze_manager.api.make_request") as mock_req,
            ):
                mock_req.return_value = {
                    "status_code": 404,
                    "headers": {},
                    "body": {"detail": "not found"},
                }
                result = runner.invoke(
                    app,
                    ["api", "dags/nonexistent", "--project", "test-project"],
                )

            assert result.exit_code == 1
