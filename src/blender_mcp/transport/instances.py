"""Local Blender instance discovery and single-target claim management."""

from __future__ import annotations

import json
import os
import platform
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..protocol.errors import BlenderMCPError

REGISTRY_SCHEMA = "blender-mcp-instance/1"
INSTANCE_PROTOCOL = "blender-mcp-bridge/2"
MAX_RECORD_BYTES = 64 * 1024
DEFAULT_HEARTBEAT_TTL = 15.0
DEFAULT_LEASE_SECONDS = 120.0
MIN_LEASE_SECONDS = 30.0
MAX_LEASE_SECONDS = 600.0
LEASE_RENEWAL_WINDOW_SECONDS = 60.0


def runtime_directory(env: dict[str, str] | None = None) -> Path:
    """Resolve the private per-user instance registry directory."""
    values = os.environ if env is None else env
    override = values.get("BLENDER_MCP_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    if system == "Windows":
        base = values.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "BlenderMCP" / "instances"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "BlenderMCP" / "instances"
    runtime = values.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "blender-mcp" / "instances"
    state = values.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "blender-mcp" / "instances"


def _is_loopback(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"}


def _read_record(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError("registry record exceeds size limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("registry record must be an object")
    return payload


def _validate_record(payload: dict[str, Any]) -> None:
    if payload.get("schema") != REGISTRY_SCHEMA:
        raise BlenderMCPError(
            "instance_protocol_mismatch",
            "Unsupported Blender instance registry schema",
            details={"schema": payload.get("schema")},
        )
    for key in ("instance_id", "file_session_id", "host"):
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError(f"invalid registry field: {key}")
    port = payload.get("port")
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("invalid registry field: port")
    if not _is_loopback(payload["host"]):
        raise ValueError("non-loopback registry endpoint rejected")


def _round_age(seconds: float) -> float:
    return round(max(0.0, seconds), 3)


@dataclass
class DiscoveredInstance:
    record: dict[str, Any]
    status: str
    heartbeat_age: float
    reason: str = ""

    def public_dict(self, *, client_id: str = "") -> dict[str, Any]:
        record = self.record
        claim = record.get("claim") or {}
        owner_id = claim.get("client_id", "")
        public = {
            "instance_id": record.get("instance_id", ""),
            "file_session_id": record.get("file_session_id", ""),
            "pid": record.get("pid"),
            "blender_version": record.get("blender_version", ""),
            "binary_path": record.get("binary_path", ""),
            "blend_file": record.get("blend_file") or "Untitled",
            "dirty": bool(record.get("dirty", False)),
            "active_scene": record.get("active_scene", ""),
            "addon_version": record.get("addon_version", ""),
            "protocol_version": record.get("protocol_version", ""),
            "allow_ai_control": bool(record.get("allow_ai_control", True)),
            "claim_owner": claim.get("owner_label", ""),
            "claim_expires_at": claim.get("expires_at"),
            "busy": bool(record.get("busy", False)),
            "heartbeat_age": self.heartbeat_age,
            "status": self.status,
            "may_auto_claim": self.status in {"ready", "claimed_by_this_client"},
        }
        if self.reason:
            public["reason"] = self.reason
        if owner_id and owner_id == client_id:
            public["status"] = "claimed_by_this_client"
        public["next_action"] = _next_action(public["status"])
        return public


def _next_action(status: str) -> str:
    return {
        "ready": "claim_blender_instance",
        "claimed_by_this_client": "continue_with_active_instance",
        "claimed_by_other_client": "use_another_instance_or_wait",
        "manual": "enable_allow_ai_control_in_blender",
        "busy": "wait_for_current_operation",
        "stale": "restart_or_reconnect_blender_addon",
        "unreachable": "check_blender_addon",
        "protocol_mismatch": "update_blender_mcp_addon",
    }.get(status, "inspect_instance")


def discover_registry_records(
    *,
    directory: Path | None = None,
    now: float | None = None,
    heartbeat_ttl: float = DEFAULT_HEARTBEAT_TTL,
) -> list[DiscoveredInstance]:
    """Read bounded registry records without opening a Blender connection."""
    root = directory or runtime_directory()
    timestamp = time.time() if now is None else now
    if not root.exists():
        return []
    found: list[DiscoveredInstance] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name)[:128]:
        try:
            payload = _read_record(path)
            _validate_record(payload)
            heartbeat_age = _round_age(timestamp - float(payload.get("heartbeat_at", 0.0)))
            claim = payload.get("claim") or {}
            claim_alive = float(claim.get("expires_at", 0.0) or 0.0) > timestamp
            if heartbeat_age > heartbeat_ttl and not payload.get("busy"):
                status = "stale"
            elif not payload.get("allow_ai_control", True):
                status = "manual"
            elif claim_alive:
                status = "claimed_by_other_client"
            elif payload.get("busy"):
                status = "busy"
            else:
                status = "ready"
            found.append(DiscoveredInstance(payload, status, heartbeat_age))
        except BlenderMCPError as error:
            found.append(DiscoveredInstance(
                {"instance_id": path.stem, "file_session_id": ""},
                "protocol_mismatch",
                0.0,
                str(error),
            ))
        except Exception as error:
            found.append(DiscoveredInstance(
                {"instance_id": path.stem, "file_session_id": ""},
                "stale",
                0.0,
                str(error),
            ))
    return found


@dataclass
class InstanceConnectionManager:
    """Discovers many local instances while routing to at most one."""

    connection_factory: Callable[[str, int], Any]
    directory: Path | None = None
    client_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner_label: str = "MCP client"
    active: Any = None
    active_record: dict[str, Any] | None = None
    claim_token: str = ""
    claim_expires_at: float = 0.0
    last_command: str = ""

    def list_instances(self, *, validate_live: bool = True) -> list[dict[str, Any]]:
        discovered = discover_registry_records(directory=self.directory)
        results: list[dict[str, Any]] = []
        for item in discovered:
            public = item.public_dict(client_id=self.client_id)
            if validate_live and item.status not in {"stale", "protocol_mismatch"}:
                try:
                    connection = self.connection_factory(item.record["host"], item.record["port"])
                    if not connection.connect():
                        raise ConnectionError("connection refused")
                    handshake = connection.send_command("blender_mcp_handshake")
                    connection.disconnect()
                    for key in ("instance_id", "file_session_id", "protocol_version"):
                        if handshake.get(key) != item.record.get(key):
                            raise BlenderMCPError(
                                "instance_protocol_mismatch",
                                f"Live Blender identity differs for {key}",
                            )
                    if public["status"] == "claimed_by_other_client" and (
                        (item.record.get("claim") or {}).get("client_id") == self.client_id
                    ):
                        public["status"] = "claimed_by_this_client"
                except BlenderMCPError as error:
                    public["status"] = (
                        "protocol_mismatch"
                        if error.code == "instance_protocol_mismatch"
                        else error.code
                    )
                    public["reason"] = error.message
                except Exception as error:
                    public["status"] = "unreachable"
                    public["reason"] = str(error)
                public["next_action"] = _next_action(public["status"])
            results.append(public)
        return results

    def claim(
        self,
        instance_id: str,
        *,
        expected_file_session_id: str = "",
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        if not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS:
            raise BlenderMCPError("invalid_request", "lease_seconds is outside the supported range")
        records = discover_registry_records(directory=self.directory)
        matches = [item for item in records if item.record.get("instance_id") == instance_id]
        if not matches:
            raise BlenderMCPError("instance_not_found", f"Blender instance {instance_id} was not found")
        item = matches[0]
        if item.status == "manual":
            raise BlenderMCPError("instance_manual", "Allow AI control is disabled in Blender")
        if item.status == "stale":
            raise BlenderMCPError("instance_stale", "Blender instance registration is stale", retryable=True)
        record = item.record
        if expected_file_session_id and record["file_session_id"] != expected_file_session_id:
            raise BlenderMCPError("file_session_changed", "The Blender file session changed before claim")
        connection = self.connection_factory(record["host"], record["port"])
        if not connection.connect():
            raise BlenderMCPError("instance_unreachable", "Could not connect to the selected Blender instance", retryable=True)
        result = connection.send_command("claim_blender_instance", {
            "instance_id": instance_id,
            "file_session_id": record["file_session_id"],
            "client_id": self.client_id,
            "owner_label": self.owner_label,
            "lease_seconds": lease_seconds,
        })
        self.release(ignore_errors=True)
        self.active = connection
        self.active_record = record
        self.claim_token = result["claim_token"]
        self.claim_expires_at = float(result["expires_at"])
        return self.active_summary()

    def auto_select(self) -> Any:
        if self.active is not None:
            return self.active
        records = discover_registry_records(directory=self.directory)
        ready = [item for item in records if item.status == "ready"]
        if not records:
            raise BlenderMCPError("no_registered_instances", "No registered Blender instances were found")
        if len(ready) != 1:
            if len(ready) > 1:
                raise BlenderMCPError(
                    "multiple_instances_require_selection",
                    "More than one Blender instance is available; claim one explicitly",
                    details={"instance_ids": [item.record["instance_id"] for item in ready]},
                )
            raise BlenderMCPError("no_registered_instances", "No Blender instance is currently claimable")
        self.claim(ready[0].record["instance_id"])
        return self.active

    def prepare_params(self, command: str, params: dict[str, Any] | None) -> dict[str, Any]:
        prepared = dict(params or {})
        if self.active_record:
            prepared["_instance_id"] = self.active_record["instance_id"]
            prepared["_file_session_id"] = self.active_record["file_session_id"]
            prepared["_client_id"] = self.client_id
            prepared["_claim_token"] = self.claim_token
        self.last_command = command
        return prepared

    def active_summary(self) -> dict[str, Any]:
        if self.active is None or self.active_record is None:
            return {"active": False, "client_id": self.client_id}
        record = self.active_record
        return {
            "active": True,
            "instance_id": record["instance_id"],
            "file_session_id": record["file_session_id"],
            "blend_file": record.get("blend_file") or "Untitled",
            "blender_version": record.get("blender_version", ""),
            "lease_expires_at": self.claim_expires_at,
            "last_command": self.last_command,
        }

    def ensure_lease(self) -> None:
        if self.active is None or not self.claim_token:
            return
        if self.claim_expires_at - time.time() > LEASE_RENEWAL_WINDOW_SECONDS:
            return
        result = self.active.send_command("renew_blender_instance", {
            "client_id": self.client_id,
            "claim_token": self.claim_token,
            "lease_seconds": DEFAULT_LEASE_SECONDS,
        })
        self.claim_expires_at = float(result["expires_at"])

    def invalidate(self) -> None:
        if self.active is not None:
            try:
                self.active.disconnect()
            except Exception:
                pass
        self.active = None
        self.active_record = None
        self.claim_token = ""
        self.claim_expires_at = 0.0

    def release(self, *, ignore_errors: bool = False) -> dict[str, Any]:
        if self.active is None:
            return {"released": False, "reason": "no_active_instance"}
        try:
            result = self.active.send_command("release_blender_instance", {
                "client_id": self.client_id,
                "claim_token": self.claim_token,
            })
        except Exception:
            if not ignore_errors:
                raise
            result = {"released": False}
        finally:
            self.invalidate()
        return result
