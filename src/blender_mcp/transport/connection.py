"""Blender bridge socket transport."""

from __future__ import annotations

import json
import logging
import socket
import threading
from dataclasses import dataclass, field
from typing import Any, Dict

from ..protocol.errors import BlenderMCPError, classify_exception
from .constants import BRIDGE_RECEIVE_BUFFER_BYTES, BRIDGE_RESPONSE_TIMEOUT_SECONDS

logger = logging.getLogger("BlenderMCPServer")

_LOG_REDACTED_KEYS = {
    "_claim_token", "claim_token", "code", "api_key", "secret_id",
    "secret_key", "password", "images", "input_image_urls",
}

def _redact_command_params(value: Any) -> Any:
    """Keep bridge logs useful without leaking claims, credentials, code, or media."""
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key).casefold() in _LOG_REDACTED_KEYS
            else _redact_command_params(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_command_params(item) for item in value[:20]]
    return value

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket | None = None
    params_enricher: Any = None
    _io_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
    )

    def _drop_socket(self) -> None:
        """Close and forget the current socket after any transport failure."""
        sock, self.sock = self.sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        with self._io_lock:
            if self.sock:
                return True

            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                candidate.connect((self.host, self.port))
                self.sock = candidate
                logger.info(f"Connected to Blender at {self.host}:{self.port}")
                return True
            except Exception as e:
                logger.error(f"Failed to connect to Blender: {str(e)}")
                try:
                    candidate.close()
                except OSError:
                    pass
                return False

    def disconnect(self):
        """Disconnect from the Blender addon"""
        with self._io_lock:
            self._drop_socket()

    def receive_full_response(self, sock, buffer_size=BRIDGE_RECEIVE_BUFFER_BYTES):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        sock.settimeout(BRIDGE_RESPONSE_TIMEOUT_SECONDS)

        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise ConnectionError("Connection closed before receiving any data")
                        break

                    chunks.append(chunk)

                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise

        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise ConnectionError("Incomplete JSON response received")
        else:
            raise ConnectionError("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        prepared_params = dict(params or {})
        if self.params_enricher is not None:
            prepared_params = self.params_enricher(command_type, prepared_params)
        command = {
            "type": command_type,
            "params": prepared_params
        }

        with self._io_lock:
            if not self.sock and not self.connect():
                raise BlenderMCPError(
                    "mcp_transport_error",
                    "Not connected to Blender",
                    retryable=True,
                    details={"operation": command_type},
                )
            try:
                logger.info(
                    "Sending command: %s with params: %s",
                    command_type,
                    _redact_command_params(prepared_params),
                )
                self.sock.sendall(json.dumps(command).encode('utf-8'))
                logger.info("Command sent, waiting for response...")
                self.sock.settimeout(BRIDGE_RESPONSE_TIMEOUT_SECONDS)
                response_data = self.receive_full_response(self.sock)
                logger.info(f"Received {len(response_data)} bytes of data")

                response = json.loads(response_data.decode('utf-8'))
                logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")

                if response.get("status") == "error":
                    logger.error(f"Blender error: {response.get('message')}")
                    error = response.get("error") or {}
                    raise BlenderMCPError(
                        error.get("code", "blender_python_error"),
                        response.get("message", "Unknown error from Blender"),
                        retryable=bool(error.get("retryable", False)),
                        details=error.get("details") or {},
                    )

                return response.get("result", {})
            except BlenderMCPError:
                raise
            except socket.timeout:
                logger.error("Socket timeout while waiting for response from Blender")
                self._drop_socket()
                raise BlenderMCPError(
                    "blender_timeout",
                    "Timeout waiting for Blender response; simplify the request and ensure Blender is running with a GUI",
                    retryable=True,
                    details={"operation": command_type},
                )
            except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                logger.error(f"Socket connection error: {str(e)}")
                self._drop_socket()
                raise BlenderMCPError(
                    "mcp_transport_error",
                    f"Connection to Blender lost: {str(e)}",
                    retryable=True,
                    details={"operation": command_type},
                )
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON response from Blender: {str(e)}")
                if 'response_data' in locals() and response_data:
                    logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
                self._drop_socket()
                raise BlenderMCPError(
                    "mcp_protocol_error",
                    f"Invalid response from Blender: {str(e)}",
                    details={"operation": command_type},
                )
            except Exception as e:
                logger.error(f"Error communicating with Blender: {str(e)}")
                self._drop_socket()
                raise classify_exception(e, operation=command_type)
