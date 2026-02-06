"""Lightweight Airflow REST API client for ABM projects."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _build_auth_header(username: str, password: str) -> str:
    """Build Basic Auth header value."""
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {credentials}"


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

    # Set headers
    req.add_header("Authorization", _build_auth_header(username, password))
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)

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
    """Detect Airflow API version by probing endpoints.

    Tries /api/v2/version first (Airflow 3.x), falls back to /api/v1/version (Airflow 2.x).

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
        if result["status_code"] == 200:
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
