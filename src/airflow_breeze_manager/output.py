"""Structured output helpers for agent-friendly CLI mode.

Provides JSON output, non-interactive prompts, and error code constants
so AI agents (Claude Code, Cursor, etc.) can use ABM programmatically.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

import typer

# ---------------------------------------------------------------------------
# Error codes — stable identifiers that agents can match on
# ---------------------------------------------------------------------------
NOT_INITIALIZED = "NOT_INITIALIZED"
PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
PROJECT_EXISTS = "PROJECT_EXISTS"
BRANCH_NOT_FOUND = "BRANCH_NOT_FOUND"
WORKTREE_EXISTS = "WORKTREE_EXISTS"
PORT_CONFLICT = "PORT_CONFLICT"
PROJECT_FROZEN = "PROJECT_FROZEN"
DOCKER_ERROR = "DOCKER_ERROR"
INVALID_WORKTREE = "INVALID_WORKTREE"
INVALID_INPUT = "INVALID_INPUT"
COMMAND_FAILED = "COMMAND_FAILED"

# ---------------------------------------------------------------------------
# Module-level state — set by the Typer app callback in cli.py
# ---------------------------------------------------------------------------
_json_mode: bool = False
_yes_mode: bool = False


def set_agent_mode(*, json_mode: bool, yes_mode: bool) -> None:
    """Configure the global agent mode flags (called from Typer callback)."""
    global _json_mode, _yes_mode  # noqa: PLW0603
    _json_mode = json_mode
    _yes_mode = yes_mode


def is_json_mode() -> bool:
    return _json_mode


def is_yes_mode() -> bool:
    return _yes_mode or _json_mode  # --json implies --yes


# ---------------------------------------------------------------------------
# AgentResponse — the single envelope for all JSON output
# ---------------------------------------------------------------------------
@dataclass
class AgentResponse:
    """Structured response envelope for JSON mode."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"success": self.success}
        if self.success:
            d["data"] = self.data
        else:
            d["error"] = self.error
            if self.error_code:
                d["error_code"] = self.error_code
        return d


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def output_json(response: AgentResponse, exit_code: int = 0) -> None:
    """Print JSON to stdout (no Rich markup) and raise typer.Exit."""
    # Write to stdout so agents can capture it; bypass Rich console entirely
    json.dump(response.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()
    raise typer.Exit(exit_code)


def json_success(data: dict[str, Any]) -> None:
    """Shorthand: emit a success response and exit 0."""
    output_json(AgentResponse(success=True, data=data), exit_code=0)


def json_error(error: str, error_code: str, exit_code: int = 1) -> None:
    """Shorthand: emit an error response and exit with given code."""
    output_json(
        AgentResponse(success=False, error=error, error_code=error_code),
        exit_code=exit_code,
    )


# ---------------------------------------------------------------------------
# Safe interactive helpers — auto-accept / return defaults in agent mode
# ---------------------------------------------------------------------------


def safe_confirm(message: str, *, default: bool = False) -> bool:
    """Like typer.confirm(), but auto-accepts in --json or --yes mode."""
    if is_yes_mode():
        return True
    return typer.confirm(message, default=default)


def safe_prompt(message: str, *, default: str = "") -> str:
    """Like typer.prompt(), but returns *default* in --json or --yes mode."""
    if is_yes_mode():
        return default
    return str(typer.prompt(message, default=default))
