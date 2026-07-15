from __future__ import annotations

import json
import os
import os.path as osp
import socket
import sys
import threading
import time
import traceback
import uuid
from contextlib import suppress

import bpy

from .. import state
from ..errors import BlenderMCPAddonError
from ..preferences import get_blendermcp_addon_preferences
from ..runtime import runtime_directory, tag_redraw
from ..version import BLENDER_MCP_ADDON_VERSION
from .constants import (
    BLENDER_MCP_ACCEPT_POLL_SECONDS,
    BLENDER_MCP_BRIDGE_PROTOCOL,
    BLENDER_MCP_DEFAULT_HOST,
    BLENDER_MCP_DEFAULT_LEASE_SECONDS,
    BLENDER_MCP_DEFAULT_PORT,
    BLENDER_MCP_HEARTBEAT_SECONDS,
    BLENDER_MCP_INSTANCE_SCHEMA,
    BLENDER_MCP_MAX_LEASE_SECONDS,
    BLENDER_MCP_MIN_LEASE_SECONDS,
    RODIN_FREE_TRIAL_KEY,
)


class BridgeLifecycleMixin:
    def __init__(self):
        self.host = BLENDER_MCP_DEFAULT_HOST
        self.port = 0
        self.running = False
        self.socket = None
        self.server_thread = None
        self.busy = False
        self.claim = None
        self.last_claim_end_reason = ""
        self.registry_path = osp.join(
            runtime_directory(),
            f"{state.instance_id}.json",
        )

    def _claim_is_live(self):
        return bool(self.claim and float(self.claim.get("expires_at", 0.0)) > time.time())

    def has_live_claim(self):
        if self.claim and not self._claim_is_live():
            self.revoke_claim("claim_expired")
        return self._claim_is_live()

    def _public_claim(self):
        if not self._claim_is_live():
            return None
        return {
            "client_id": self.claim["client_id"],
            "owner_label": self.claim["owner_label"],
            "expires_at": self.claim["expires_at"],
        }

    def _registry_record(self):
        preferences = get_blendermcp_addon_preferences()
        allow_ai = bool(preferences is None or preferences.allow_ai_control)
        return {
            "schema": BLENDER_MCP_INSTANCE_SCHEMA,
            "protocol_version": BLENDER_MCP_BRIDGE_PROTOCOL,
            "instance_id": state.instance_id,
            "file_session_id": state.file_session_id,
            "pid": os.getpid(),
            "host": self.host,
            "port": int(self.port),
            "heartbeat_at": time.time(),
            "blender_version": bpy.app.version_string,
            "binary_path": bpy.app.binary_path or sys.executable,
            "blend_file": bpy.data.filepath or "",
            "dirty": bool(getattr(bpy.data, "is_dirty", False)),
            "active_scene": getattr(getattr(bpy.context, "scene", None), "name", ""),
            "addon_version": ".".join(str(value) for value in BLENDER_MCP_ADDON_VERSION),
            "allow_ai_control": allow_ai,
            "busy": bool(self.busy),
            "claim": self._public_claim(),
        }

    def _write_registry_record(self):
        if not self.running or not self.socket:
            return
        directory = osp.dirname(self.registry_path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        temp_path = self.registry_path + f".{os.getpid()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self._registry_record(), handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            with suppress(OSError):
                os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.registry_path)
        finally:
            with suppress(FileNotFoundError, OSError):
                os.remove(temp_path)

    def _remove_registry_record(self):
        with suppress(FileNotFoundError, OSError):
            os.remove(self.registry_path)

    def _heartbeat_tick(self):
        if not self.running:
            return None
        if self.claim and not self._claim_is_live():
            self.revoke_claim("claim_expired")
        try:
            self._write_registry_record()
        except Exception as error:
            print(f"BlenderMCP registry heartbeat failed: {error}")
        return BLENDER_MCP_HEARTBEAT_SECONDS

    def rotate_file_session(self):
        self.revoke_claim("file_session_changed")
        self._write_registry_record()

    def revoke_claim(self, reason="released"):
        changed = self.claim is not None
        self.claim = None
        self.last_claim_end_reason = reason
        if changed:
            tag_redraw()
        if self.running:
            with suppress(Exception):
                self._write_registry_record()

    def blender_mcp_handshake(self):
        record = self._registry_record()
        return {
            "protocol_version": record["protocol_version"],
            "instance_id": record["instance_id"],
            "file_session_id": record["file_session_id"],
            "addon_version": record["addon_version"],
            "allow_ai_control": record["allow_ai_control"],
            "busy": record["busy"],
            "claim": record["claim"],
        }

    def claim_blender_instance(
        self,
        instance_id,
        file_session_id,
        client_id,
        owner_label="MCP client",
        lease_seconds=BLENDER_MCP_DEFAULT_LEASE_SECONDS,
    ):
        if instance_id != state.instance_id:
            raise BlenderMCPAddonError("instance_changed", "Blender instance identity changed")
        if file_session_id != state.file_session_id:
            raise BlenderMCPAddonError("file_session_changed", "The open Blender file session changed")
        preferences = get_blendermcp_addon_preferences()
        if preferences and not preferences.allow_ai_control:
            raise BlenderMCPAddonError("instance_manual", "Allow AI control is disabled")
        lease_seconds = float(lease_seconds)
        if not BLENDER_MCP_MIN_LEASE_SECONDS <= lease_seconds <= BLENDER_MCP_MAX_LEASE_SECONDS:
            raise BlenderMCPAddonError("invalid_request", "Requested claim lease is outside supported bounds")
        if self._claim_is_live() and self.claim["client_id"] != client_id:
            raise BlenderMCPAddonError(
                "instance_claimed_by_other_client",
                f"This Blender instance is occupied by {self.claim['owner_label']}",
                retryable=True,
            )
        token = self.claim["token"] if self._claim_is_live() else uuid.uuid4().hex + uuid.uuid4().hex
        self.claim = {
            "client_id": str(client_id),
            "owner_label": str(owner_label or "MCP client")[:80],
            "token": token,
            "expires_at": time.time() + lease_seconds,
            "lease_seconds": lease_seconds,
        }
        self._write_registry_record()
        tag_redraw()
        return {
            "instance_id": state.instance_id,
            "file_session_id": state.file_session_id,
            "owner_label": self.claim["owner_label"],
            "expires_at": self.claim["expires_at"],
            "claim_token": token,
        }

    def release_blender_instance(self, client_id, claim_token):
        if not self._claim_is_live():
            self.revoke_claim("claim_expired")
            return {"released": False, "reason": "claim_expired"}
        if self.claim["client_id"] != client_id or self.claim["token"] != claim_token:
            raise BlenderMCPAddonError("claim_revoked_by_user", "The supplied claim is not current")
        self.revoke_claim("released")
        return {"released": True, "instance_id": state.instance_id}

    def renew_blender_instance(self, client_id, claim_token, lease_seconds=BLENDER_MCP_DEFAULT_LEASE_SECONDS):
        if not self._claim_is_live():
            raise BlenderMCPAddonError("claim_expired", "The Blender claim expired")
        if self.claim["client_id"] != client_id or self.claim["token"] != claim_token:
            raise BlenderMCPAddonError("claim_expired", "The Blender claim token is invalid")
        lease_seconds = float(lease_seconds)
        if not BLENDER_MCP_MIN_LEASE_SECONDS <= lease_seconds <= BLENDER_MCP_MAX_LEASE_SECONDS:
            raise BlenderMCPAddonError("invalid_request", "Requested claim lease is outside supported bounds")
        self.claim["expires_at"] = time.time() + lease_seconds
        self.claim["lease_seconds"] = lease_seconds
        self._write_registry_record()
        return {"renewed": True, "expires_at": self.claim["expires_at"]}

    def _authorize_command(self, command_type, params):
        read_only = {
            "blender_mcp_handshake", "get_scene_info", "get_object_info",
            "get_blender_version_context", "get_runtime_automation_context",
            "list_node_trees", "export_node_tree", "get_node_tree_index",
            "get_node_type_schema", "validate_node_tree_patch",
            "list_geometry_node_trees", "export_geometry_node_tree",
            "get_geometry_node_type_schema", "search_geometry_node_types",
            "search_blender_node_assets", "export_blender_node_asset",
            "get_geometry_node_tree_index", "validate_geometry_node_patch",
            "get_viewport_screenshot", "get_telemetry_consent",
            "get_polyhaven_status", "get_hyper3d_status", "get_sketchfab_status",
            "get_hunyuan3d_status", "get_polyhaven_categories",
            "search_polyhaven_assets", "search_sketchfab_models",
            "get_sketchfab_model_preview", "audit_external_dependencies",
            "plan_external_dependency_relinks", "inspect_evaluated_mesh",
            "get_simulation_status", "query_node_graph",
        }
        claim_commands = {
            "claim_blender_instance", "renew_blender_instance", "release_blender_instance"
        }
        if command_type in read_only or command_type in claim_commands:
            return
        if command_type == "ensure_scene_compositor_tree" and not params.get("create_if_missing", False):
            return
        if not self._claim_is_live():
            code = self.last_claim_end_reason or "claim_expired"
            if code not in {"claim_expired", "claim_revoked_by_user", "file_session_changed"}:
                code = "claim_expired"
            raise BlenderMCPAddonError(code, "A live AI claim is required before modifying Blender")
        if params.get("_instance_id") != state.instance_id:
            raise BlenderMCPAddonError("instance_changed", "Command instance identity does not match")
        if params.get("_file_session_id") != state.file_session_id:
            raise BlenderMCPAddonError("file_session_changed", "Command file session does not match")
        if params.get("_client_id") != self.claim["client_id"] or params.get("_claim_token") != self.claim["token"]:
            raise BlenderMCPAddonError("claim_expired", "Command claim token is invalid or expired")
        self.claim["expires_at"] = time.time() + self.claim.get("lease_seconds", BLENDER_MCP_DEFAULT_LEASE_SECONDS)

    def _get_config_value(self, scene_attr, pref_attr=None, env_var=None):
        """Read config in order: addon preferences -> scene -> env var."""
        prefs = get_blendermcp_addon_preferences()
        if prefs and pref_attr:
            pref_value = getattr(prefs, pref_attr, "")
            if pref_value:
                return pref_value

        scene_value = getattr(bpy.context.scene, scene_attr, "")
        if scene_value:
            return scene_value

        if env_var:
            env_value = os.getenv(env_var, "")
            if env_value:
                return env_value
        return ""

    def _get_hyper3d_api_key(self):
        # Let the free-trial button temporarily override persistent keys
        # without overwriting user-saved private keys.
        scene_value = getattr(bpy.context.scene, "blendermcp_hyper3d_api_key", "")
        if scene_value == RODIN_FREE_TRIAL_KEY:
            return scene_value
        return self._get_config_value(
            "blendermcp_hyper3d_api_key",
            "hyper3d_api_key",
            "BLENDERMCP_HYPER3D_API_KEY",
        )

    def _get_sketchfab_api_key(self):
        return self._get_config_value(
            "blendermcp_sketchfab_api_key",
            "sketchfab_api_key",
            "BLENDERMCP_SKETCHFAB_API_KEY",
        )

    def _get_hunyuan3d_secret_id(self):
        return self._get_config_value(
            "blendermcp_hunyuan3d_secret_id",
            "hunyuan3d_secret_id",
            "BLENDERMCP_HUNYUAN3D_SECRET_ID",
        )

    def _get_hunyuan3d_secret_key(self):
        return self._get_config_value(
            "blendermcp_hunyuan3d_secret_key",
            "hunyuan3d_secret_key",
            "BLENDERMCP_HUNYUAN3D_SECRET_KEY",
        )

    def _get_hunyuan3d_api_url(self):
        return self._get_config_value(
            "blendermcp_hunyuan3d_api_url",
            "hunyuan3d_api_url",
            "BLENDERMCP_HUNYUAN3D_API_URL",
        ) or "http://localhost:8081"

    def start(self):
        if bpy.app.background:
            print("BlenderMCP: cannot start server in background mode (blender -b) - commands would never execute\n"
                  "BlenderMCP: run Blender with a GUI, or use a virtual display: xvfb-run -a blender")
            return

        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                # Windows SO_REUSEADDR permits two live listeners to share the
                # same endpoint, which destroys instance routing guarantees.
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.socket.bind((BLENDER_MCP_DEFAULT_HOST, BLENDER_MCP_DEFAULT_PORT))
            except OSError as bind_error:
                if getattr(bind_error, "errno", None) not in {48, 98, 10048}:
                    raise
                self.socket.bind((BLENDER_MCP_DEFAULT_HOST, 0))
            self.host = BLENDER_MCP_DEFAULT_HOST
            self.port = int(self.socket.getsockname()[1])
            self.socket.listen(1)

            # Publish a complete registration before accepting clients. This
            # also keeps initialization failures out of the server thread.
            self._write_registry_record()
            if not bpy.app.timers.is_registered(self._heartbeat_tick):
                bpy.app.timers.register(self._heartbeat_tick, first_interval=BLENDER_MCP_HEARTBEAT_SECONDS)

            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False
        self.revoke_claim("addon_stopped")
        self._remove_registry_record()

        if bpy.app.timers.is_registered(self._heartbeat_tick):
            with suppress(Exception):
                bpy.app.timers.unregister(self._heartbeat_tick)

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("BlenderMCP server stopped")

    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(BLENDER_MCP_ACCEPT_POLL_SECONDS)

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    if not self.running or self.socket is None:
                        break
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b''

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''

                        # Execute command in Blender's main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    print("Failed to send response - client disconnected")
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None

                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        # Incomplete data, wait for more
                        pass
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            return self._execute_command_internal(command)

        except BlenderMCPAddonError as e:
            print(f"BlenderMCP command rejected [{e.code}]: {str(e)}")
            return {
                "status": "error",
                "message": str(e),
                "error": {
                    "code": e.code,
                    "retryable": e.retryable,
                    "details": e.details,
                },
            }
        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e),
                "error": {"code": "blender_python_error", "retryable": False},
            }

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        if not isinstance(cmd_type, str) or not isinstance(params, dict):
            raise BlenderMCPAddonError("invalid_request", "Command type and params are required")
        self._authorize_command(cmd_type, params)
        handler_params = {
            key: value for key, value in params.items()
            if not key.startswith("_")
        }

        # Base handlers that are always available
        handlers = {
            "blender_mcp_handshake": self.blender_mcp_handshake,
            "claim_blender_instance": self.claim_blender_instance,
            "renew_blender_instance": self.renew_blender_instance,
            "release_blender_instance": self.release_blender_instance,
            "get_scene_info": self.get_scene_info,
            "get_object_info": self.get_object_info,
            "audit_external_dependencies": self.audit_external_dependencies,
            "plan_external_dependency_relinks": self.plan_external_dependency_relinks,
            "apply_external_dependency_relinks": self.apply_external_dependency_relinks,
            "inspect_evaluated_mesh": self.inspect_evaluated_mesh,
            "get_simulation_status": self.get_simulation_status,
            "clear_simulation_cache": self.clear_simulation_cache,
            "reset_simulation": self.reset_simulation,
            "bake_simulation": self.bake_simulation,
            "get_blender_version_context": self.get_blender_version_context,
            "get_runtime_automation_context": self.get_runtime_automation_context,
            "list_node_trees": self.list_node_trees,
            "ensure_scene_compositor_tree": self.ensure_scene_compositor_tree,
            "export_node_tree": self.export_node_tree,
            "get_node_tree_index": self.get_node_tree_index,
            "query_node_graph": self.query_node_graph,
            "get_node_type_schema": self.get_node_type_schema,
            "validate_node_tree_patch": self.validate_node_tree_patch,
            "apply_node_tree_patch": self.apply_node_tree_patch,
            "list_geometry_node_trees": self.list_geometry_node_trees,
            "export_geometry_node_tree": self.export_geometry_node_tree,
            "get_geometry_node_type_schema": self.get_geometry_node_type_schema,
            "search_geometry_node_types": self.search_geometry_node_types,
            "search_blender_node_assets": self.search_blender_node_assets,
            "export_blender_node_asset": self.export_blender_node_asset,
            "import_blender_node_asset": self.import_blender_node_asset,
            "get_geometry_node_tree_index": self.get_geometry_node_tree_index,
            "validate_geometry_node_patch": self.validate_geometry_node_patch,
            "apply_geometry_node_patch": self.apply_geometry_node_patch,
            "modify_verify_save": self.modify_verify_save,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "get_telemetry_consent": self.get_telemetry_consent,
            "get_polyhaven_status": self.get_polyhaven_status,
            "get_hyper3d_status": self.get_hyper3d_status,
            "get_sketchfab_status": self.get_sketchfab_status,
            "get_hunyuan3d_status": self.get_hunyuan3d_status,
        }

        # Add Polyhaven handlers only if enabled
        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "download_polyhaven_asset": self.download_polyhaven_asset,
                "set_texture": self.set_texture,
            }
            handlers.update(polyhaven_handlers)

        # Add Hyper3d handlers only if enabled
        if bpy.context.scene.blendermcp_use_hyper3d:
            polyhaven_handlers = {
                "create_rodin_job": self.create_rodin_job,
                "poll_rodin_job_status": self.poll_rodin_job_status,
                "import_generated_asset": self.import_generated_asset,
            }
            handlers.update(polyhaven_handlers)

        # Add Sketchfab handlers only if enabled
        if bpy.context.scene.blendermcp_use_sketchfab:
            sketchfab_handlers = {
                "search_sketchfab_models": self.search_sketchfab_models,
                "get_sketchfab_model_preview": self.get_sketchfab_model_preview,
                "download_sketchfab_model": self.download_sketchfab_model,
            }
            handlers.update(sketchfab_handlers)

        # Add Hunyuan3d handlers only if enabled
        if bpy.context.scene.blendermcp_use_hunyuan3d:
            hunyuan_handlers = {
                "create_hunyuan_job": self.create_hunyuan_job,
                "poll_hunyuan_job_status": self.poll_hunyuan_job_status,
                "import_generated_asset_hunyuan": self.import_generated_asset_hunyuan
            }
            handlers.update(hunyuan_handlers)

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                self.busy = True
                self._write_registry_record()
                result = handler(**handler_params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except BlenderMCPAddonError:
                raise
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                raise BlenderMCPAddonError("blender_python_error", str(e)) from e
            finally:
                self.busy = False
                with suppress(Exception):
                    self._write_registry_record()
        else:
            raise BlenderMCPAddonError("unknown_command", f"Unknown command type: {cmd_type}")
