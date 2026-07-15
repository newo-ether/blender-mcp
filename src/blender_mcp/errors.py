"""Public error types shared by Blender MCP tools and connection routing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class BlenderMCPError(RuntimeError):
    """A truthful MCP failure with a stable machine-readable category."""

    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload

    def __str__(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"))


def classify_exception(error: BaseException, *, operation: str = "") -> BlenderMCPError:
    """Map unexpected bridge failures to stable public categories."""
    if isinstance(error, BlenderMCPError):
        return error
    if isinstance(error, PermissionError):
        code = "file_permission_error"
        retryable = False
    elif isinstance(error, (ConnectionError, BrokenPipeError, ConnectionResetError)):
        code = "mcp_transport_error"
        retryable = True
    elif isinstance(error, TimeoutError):
        code = "blender_timeout"
        retryable = True
    elif isinstance(error, (ValueError, TypeError)):
        code = "invalid_request"
        retryable = False
    else:
        code = "blender_python_error"
        retryable = False
    details = {"operation": operation} if operation else {}
    return BlenderMCPError(code, str(error), retryable=retryable, details=details)


def raise_classified(error: BaseException, *, operation: str = "") -> None:
    """Raise a classified failure without duplicating exception context."""
    raise classify_exception(error, operation=operation) from error
