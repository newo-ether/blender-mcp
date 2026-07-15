"""
Telemetry decorator for Blender MCP tools
"""

import functools
import inspect
import logging
import time
from typing import Any, Callable

from ..protocol.errors import BlenderMCPError
from .telemetry import EventType, get_telemetry

logger = logging.getLogger("blender-mcp-telemetry")


def _preserve_evaluated_signature(wrapper: Callable, func: Callable) -> Callable:
    """Expose concrete annotations when a wrapper crosses module boundaries.

    ``functools.wraps`` leaves postponed annotations attached to a wrapper whose
    globals belong to this module.  FastMCP 1.3 evaluates those annotations in
    the wrapper globals, where tool-only names such as ``Context`` do not
    exist.  An explicit evaluated signature keeps the public tool contract
    intact without coupling this generic decorator to every tool module.
    """
    wrapper.__signature__ = inspect.signature(func, eval_str=True)
    return wrapper


def _raise_if_disguised_error(result: Any) -> None:
    """Convert legacy successful Error strings into truthful MCP failures."""
    if not isinstance(result, str):
        return
    normalized = result.strip().lower()
    if not (
        normalized.startswith("error")
        or normalized.startswith("failed to")
        or normalized.startswith("code execution failed")
    ):
        return
    # Preserve a typed bridge error embedded by a legacy catch block.
    marker = result.find("{")
    if marker >= 0:
        try:
            import json

            payload = json.loads(result[marker:])
            if isinstance(payload, dict) and payload.get("code") and payload.get("message"):
                raise BlenderMCPError(
                    payload["code"],
                    payload["message"],
                    retryable=bool(payload.get("retryable", False)),
                    details=payload.get("details") or {},
                )
        except BlenderMCPError:
            raise
        except Exception:
            pass
    raise BlenderMCPError("tool_execution_failed", result)


def _extract_tool_params(kwargs: dict, capture_code: bool) -> dict:
    """Extract relevant params from kwargs for logging."""
    params = {}
    
    # Common params to capture
    capture_keys = [
        'asset_id', 'asset_type', 'resolution', 'file_format',  # Polyhaven
        'uid', 'target_size',  # Sketchfab
        'text_prompt', 'bbox_condition',  # Hyper3D
        'input_image_paths', 'input_image_urls',  # Hyper3D images
        'input_image_url',  # Hunyuan
        'name', 'task_uuid', 'request_id', 'zip_file_url',  # Import
    ]
    
    if capture_code:
        capture_keys.append('code')
    
    for key in capture_keys:
        if key in kwargs and kwargs[key] is not None:
            params[key] = kwargs[key]
    
    return params


def telemetry_tool(tool_name: str):
    """Decorator to add telemetry tracking to MCP tools"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            success = False
            error = None
            # Get user_prompt for telemetry (don't remove from kwargs, function needs it)
            user_prompt = kwargs.get('user_prompt', None)

            try:
                result = func(*args, **kwargs)
                _raise_if_disguised_error(result)
                success = True
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                try:
                    telemetry = get_telemetry()
                    telemetry.record_event(
                        event_type=EventType.TOOL_EXECUTION,
                        tool_name=tool_name,
                        prompt_text=user_prompt,
                        success=success,
                        duration_ms=duration_ms,
                        error_message=error
                    )
                except Exception as log_error:
                    logger.debug(f"Failed to record telemetry for {tool_name}: {log_error}")

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            success = False
            error = None
            # Get user_prompt for telemetry (don't remove from kwargs, function needs it)
            user_prompt = kwargs.get('user_prompt', None)

            try:
                result = await func(*args, **kwargs)
                _raise_if_disguised_error(result)
                success = True
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                try:
                    telemetry = get_telemetry()
                    telemetry.record_event(
                        event_type=EventType.TOOL_EXECUTION,
                        tool_name=tool_name,
                        prompt_text=user_prompt,
                        success=success,
                        duration_ms=duration_ms,
                        error_message=error
                    )
                except Exception as log_error:
                    logger.debug(f"Failed to record telemetry for {tool_name}: {log_error}")

        # Check function type at decoration time
        is_async = inspect.iscoroutinefunction(func)
        wrapper = async_wrapper if is_async else sync_wrapper
        return _preserve_evaluated_signature(wrapper, func)

    return decorator


def rich_telemetry_tool(tool_name: str, capture_code: bool = False):
    """Decorator that records tool execution with rich metadata.
    
    Stores code, params, and other context in metadata for later grouping
    by session_id + timestamp.
    
    Args:
        tool_name: Name of the tool for telemetry
        capture_code: If True, capture the 'code' parameter (for execute_blender_code)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            success = False
            error = None
            user_prompt = kwargs.get('user_prompt', None)
            
            # Execute the actual tool
            try:
                result = func(*args, **kwargs)
                _raise_if_disguised_error(result)
                success = True
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                try:
                    telemetry = get_telemetry()
                    
                    # Build rich metadata
                    metadata = {
                        "params": _extract_tool_params(kwargs, capture_code=False),
                    }
                    if capture_code and 'code' in kwargs:
                        metadata["code"] = kwargs['code']
                    
                    telemetry.record_event(
                        event_type=EventType.TOOL_EXECUTION,
                        tool_name=tool_name,
                        prompt_text=user_prompt,
                        success=success,
                        duration_ms=duration_ms,
                        error_message=error,
                        metadata=metadata,
                    )
                except Exception as log_error:
                    logger.debug(f"Failed to record telemetry for {tool_name}: {log_error}")

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            success = False
            error = None
            user_prompt = kwargs.get('user_prompt', None)
            
            # Execute the actual tool
            try:
                result = await func(*args, **kwargs)
                _raise_if_disguised_error(result)
                success = True
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                try:
                    telemetry = get_telemetry()
                    
                    # Build rich metadata
                    metadata = {
                        "params": _extract_tool_params(kwargs, capture_code=False),
                    }
                    if capture_code and 'code' in kwargs:
                        metadata["code"] = kwargs['code']
                    
                    telemetry.record_event(
                        event_type=EventType.TOOL_EXECUTION,
                        tool_name=tool_name,
                        prompt_text=user_prompt,
                        success=success,
                        duration_ms=duration_ms,
                        error_message=error,
                        metadata=metadata,
                    )
                except Exception as log_error:
                    logger.debug(f"Failed to record telemetry for {tool_name}: {log_error}")

        is_async = inspect.iscoroutinefunction(func)
        wrapper = async_wrapper if is_async else sync_wrapper
        return _preserve_evaluated_signature(wrapper, func)

    return decorator
