"""Lightweight Airflow REST API client for ABM projects."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from airflow_breeze_manager.constants import TOKEN_MAX_AGE

# Sentinel: /auth/token returned 404 → Airflow 2, no point retrying
_NOT_AVAILABLE = "NOT_AVAILABLE"


def _build_auth_header(username: str, password: str) -> str:
    """Build Basic Auth header value."""
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {credentials}"


# Bearer token cache: keyed by (base_url, username, password)
# Values are (token_string, timestamp) or (_NOT_AVAILABLE, timestamp).
_token_cache: dict[tuple[str, str, str], tuple[str, float]] = {}


def clear_token_cache() -> None:
    """Clear the bearer token cache (for testing)."""
    _token_cache.clear()


def _get_bearer_token(
    base_url: str,
    username: str,
    password: str,
    timeout: float = 10.0,
    _force_refresh: bool = False,
) -> str | None:
    """Get a JWT bearer token from Airflow 3's /auth/token endpoint.

    Returns:
        The access_token string on success.
        None if the endpoint returned 404 (Airflow 2 — no JWT support)
            or on connection/request error.

    Caches results with a TTL of TOKEN_MAX_AGE seconds. A 404 is also cached
    (as _NOT_AVAILABLE) so we don't re-probe Airflow 2 instances every call.
    """
    cache_key = (base_url, username, password)

    if not _force_refresh and cache_key in _token_cache:
        cached_value, cached_at = _token_cache[cache_key]
        if time.monotonic() - cached_at < TOKEN_MAX_AGE:
            return None if cached_value == _NOT_AVAILABLE else cached_value
        # Expired — fall through to refresh
        del _token_cache[cache_key]

    url = f"{base_url}/auth/token"
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read())
            token: str | None = data.get("access_token")
            if token:
                _token_cache[cache_key] = (token, time.monotonic())
            return token
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Airflow 2 — cache so we don't retry
            _token_cache[cache_key] = (_NOT_AVAILABLE, time.monotonic())
        return None
    except (urllib.error.URLError, OSError):
        return None


def make_request(
    base_url: str,
    endpoint: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    username: str = "airflow",
    password: str = "airflow",
    raw_endpoint: bool = False,
    api_version: str = "v1",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Make an HTTP request to Airflow REST API.

    Args:
        base_url: Base URL like http://localhost:28180
        endpoint: API endpoint like "dags" or "dags/my_dag"
        method: HTTP method (GET, POST, PATCH, PUT, DELETE)
        params: Query parameters
        json_data: JSON body for POST/PATCH/PUT
        headers: Additional headers
        username: Basic auth username
        password: Basic auth password
        raw_endpoint: If True, use endpoint as-is without /api/vX prefix
        api_version: API version prefix (v1 or v2)
        timeout: Request timeout in seconds

    Returns:
        Dict with 'status_code', 'headers', 'body' keys.
    """
    # Build the URL
    endpoint = endpoint.lstrip("/")
    if raw_endpoint:
        url = f"{base_url}/{endpoint}"
    else:
        url = f"{base_url}/api/{api_version}/{endpoint}"

    # Add query parameters
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    # Prepare request body
    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)

    # Set headers — use Bearer token for v2 (Airflow 3), Basic auth for v1 (Airflow 2)
    use_bearer = api_version == "v2" and not raw_endpoint
    if use_bearer:
        token = _get_bearer_token(base_url, username, password, timeout=timeout)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        else:
            # Fallback to Basic auth if token retrieval fails
            req.add_header("Authorization", _build_auth_header(username, password))
    else:
        req.add_header("Authorization", _build_auth_header(username, password))
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)

    result = _do_request(req, timeout)

    # Retry once with a fresh token if we got 401 on a Bearer-auth request
    # (handles expired tokens without requiring the caller to know about it)
    if result["status_code"] == 401 and use_bearer and token:
        new_token = _get_bearer_token(base_url, username, password, timeout=timeout, _force_refresh=True)
        if new_token and new_token != token:
            retry_req = urllib.request.Request(req.full_url, data=req.data, method=req.get_method())
            retry_req.add_header("Authorization", f"Bearer {new_token}")
            retry_req.add_header("Accept", "application/json")
            if data is not None:
                retry_req.add_header("Content-Type", "application/json")
            if headers:
                for key, value in headers.items():
                    retry_req.add_header(key, value)
            result = _do_request(retry_req, timeout)

    return result


def _do_request(req: urllib.request.Request, timeout: float) -> dict[str, Any]:
    """Execute an HTTP request and return a normalized result dict."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body_bytes = response.read()
            try:
                body = json.loads(body_bytes)
            except (json.JSONDecodeError, ValueError):
                body = body_bytes.decode("utf-8", errors="replace")
            return {
                "status_code": response.status,
                "headers": dict(response.headers),
                "body": body,
            }
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            body = body_bytes.decode("utf-8", errors="replace")
        return {
            "status_code": e.code,
            "headers": dict(e.headers) if e.headers else {},
            "body": body,
        }
    except urllib.error.URLError as e:
        return {
            "status_code": 0,
            "headers": {},
            "body": {"error": str(e.reason)},
        }


def detect_api_version(
    base_url: str,
    username: str = "airflow",
    password: str = "airflow",
    timeout: float = 5.0,
) -> str:
    """Detect Airflow API version by probing version endpoints.

    Tries /api/v2/version first (Airflow 3.x), falls back to /api/v1/version (Airflow 2.x).
    Treats 200, 401, and 403 as "this version exists" (server is responding).

    Returns:
        "v2" or "v1"
    """
    for version in ("v2", "v1"):
        result = make_request(
            base_url,
            "version",
            api_version=version,
            username=username,
            password=password,
            timeout=timeout,
        )
        # 200 = success, 401/403 = server is alive but needs auth (Airflow 3 with JWT)
        if result["status_code"] in (200, 401, 403):
            return version

    # Default to v1 if neither responds
    return "v1"


def get_openapi_spec(
    base_url: str,
    api_version: str,
    username: str = "airflow",
    password: str = "airflow",
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    """Fetch the OpenAPI spec for endpoint listing.

    Returns:
        Parsed OpenAPI spec dict, or None if unavailable.
    """
    # Airflow serves OpenAPI spec at /api/v1/openapi.json (v1) or /api/v2/openapi.json (v2)
    # Also try the alternative path /api/vX/openapi.yaml (returns JSON sometimes)
    for path in (f"api/{api_version}/openapi.json", f"api/{api_version}/openapi.yaml"):
        result = make_request(
            base_url,
            path,
            raw_endpoint=True,
            username=username,
            password=password,
            timeout=timeout,
        )
        if result["status_code"] == 200 and isinstance(result["body"], dict):
            return result["body"]
    return None


def parse_fields(
    fields: list[str] | None,
    raw_fields: list[str] | None,
) -> dict[str, Any]:
    """Parse -F (typed) and -f (raw string) fields into a dict.

    -F fields auto-detect type: int, float, bool, null, or string.
    -f fields are always strings.

    Args:
        fields: Typed fields from -F, e.g. ["limit=10", "only_active=true"]
        raw_fields: Raw string fields from -f, e.g. ["key=my_var", "value=x"]

    Returns:
        Merged dict of field values.
    """
    result: dict[str, Any] = {}

    for field_list, typed in ((fields, True), (raw_fields, False)):
        if not field_list:
            continue
        for item in field_list:
            if "=" not in item:
                raise ValueError(f"Invalid field format (expected key=value): {item}")
            key, value = item.split("=", 1)
            if typed:
                result[key] = _coerce_value(value)
            else:
                result[key] = value

    return result


def _coerce_value(value: str) -> Any:
    """Coerce a string value to its most specific Python type.

    Handles: int, float, bool (true/false), null/none, or string.
    """
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in ("null", "none"):
        return None

    # Try int
    try:
        return int(value)
    except ValueError:
        pass

    # Try float
    try:
        return float(value)
    except ValueError:
        pass

    return value


def format_endpoint_list(
    spec: dict[str, Any],
    filter_pattern: str | None = None,
) -> list[dict[str, str]]:
    """Extract endpoint list from OpenAPI spec.

    Args:
        spec: Parsed OpenAPI spec dict.
        filter_pattern: Optional substring filter for paths.

    Returns:
        List of dicts with 'method', 'path', 'summary' keys.
    """
    endpoints: list[dict[str, str]] = []
    paths = spec.get("paths", {})

    for path, methods in sorted(paths.items()):
        if filter_pattern and filter_pattern.lower() not in path.lower():
            continue
        if not isinstance(methods, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            if method in methods:
                operation = methods[method]
                summary = ""
                if isinstance(operation, dict):
                    summary = operation.get("summary", "")
                endpoints.append(
                    {
                        "method": method.upper(),
                        "path": path,
                        "summary": summary,
                    }
                )

    return endpoints
