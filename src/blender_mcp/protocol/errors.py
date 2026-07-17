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


UNRESOLVED_MUTATION = "unknown"

_UNDISPATCHED_HINT = (
    "The patch never reached Blender, so no datablock was touched. "
    "Fix the reported cause and resend the same patch."
)
_DISPATCHED_HINT = (
    "The patch was dispatched to Blender and this failure surfaced afterwards, so it "
    "cannot prove whether the transaction committed. Read the tree back (export or "
    "query_node_graph) and compare against the patch before resending: replaying an "
    "already-committed patch duplicates its additive operations."
)


def unresolved_application_result(
    schema: str,
    dispatched: bool,
    error: BaseException,
) -> dict[str, Any]:
    """Describe a patch application whose outcome the server cannot assert.

    A failure raised before dispatch proves nothing was mutated. A failure raised at or
    after dispatch proves nothing at all: the addon commits inside Blender, so the
    transaction may already have landed while the response was lost. Reporting
    ``mutated: false`` there is a claim the server cannot support, and callers who
    believe it retry and double-apply. ``unknown`` is deliberately truthy so that a
    caller testing ``if result["mutated"]`` degrades toward reading back rather than
    toward blind retry.
    """
    return {
        "schema": schema,
        "status": UNRESOLVED_MUTATION if dispatched else "failed",
        "applied": False,
        "mutated": UNRESOLVED_MUTATION if dispatched else False,
        "diagnostics": [{
            "severity": "error",
            "code": (
                "application_unresolved_after_dispatch"
                if dispatched
                else "application_transport_error"
            ),
            "path": "",
            "message": (
                f"{error}. {_DISPATCHED_HINT if dispatched else _UNDISPATCHED_HINT}"
            ),
        }],
        "plan": [],
    }
