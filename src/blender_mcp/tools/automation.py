from __future__ import annotations

import json
import logging
import os
import tempfile

from mcp.server.fastmcp import Context, Image

from ..host import get_blender_connection, mcp
from ..observability.decorators import rich_telemetry_tool
from ..observability.telemetry import EventType, get_telemetry

logger = logging.getLogger("BlenderMCPServer")


@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 1000, user_prompt: str = "") -> Image:
    """
    Capture a screenshot of the current Blender 3D viewport.

    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 1000)
    - user_prompt: The original user prompt that led to this tool call (for telemetry)

    Returns the screenshot as an Image.
    """
    start_time = __import__('time').time()
    screenshot_url = None
    success = False
    error_msg = None

    try:
        blender = get_blender_connection()

        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")

        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "filepath": temp_path,
            "format": "png"
        })

        if "error" in result:
            raise Exception(result["error"])

        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")

        # Read the file
        with open(temp_path, 'rb') as f:
            image_bytes = f.read()

        # Delete the temp file
        os.remove(temp_path)

        # Upload to storage for telemetry
        try:
            telemetry = get_telemetry()
            if telemetry._check_user_consent():
                screenshot_url = telemetry.upload_screenshot(image_bytes, "screenshot")
        except Exception:
            pass  # Silently fail - don't break screenshot for telemetry issues

        success = True
        return Image(data=image_bytes, format="png")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")
    finally:
        # Record telemetry with screenshot URL in metadata
        try:
            telemetry = get_telemetry()
            duration_ms = (__import__('time').time() - start_time) * 1000

            metadata = None
            if screenshot_url:
                metadata = {"screenshot_url": screenshot_url}

            telemetry.record_event(
                event_type=EventType.TOOL_EXECUTION,
                tool_name="get_viewport_screenshot",
                prompt_text=user_prompt,
                success=success,
                duration_ms=duration_ms,
                error_message=error_msg,
                metadata=metadata,
            )
        except Exception:
            pass

@mcp.tool()
@rich_telemetry_tool("execute_blender_code", capture_code=True)
def execute_blender_code(
    ctx: Context,
    code: str,
    transaction: bool = False,
    rollback_on_error: bool = True,
    user_prompt: str = "",
    timeout_seconds: float | None = None,
) -> str:
    """
    Execute arbitrary Python code in Blender. Make sure to do it step-by-step by breaking it into smaller chunks.

    Parameters:
    - code: The Python code to execute
    - user_prompt: The original user prompt that led to this tool call (for telemetry)
    - timeout_seconds: Optional per-call bound on how long to wait for Blender to
      respond. Use a small value (e.g. 10) when the code could hang Blender —
      an unbounded loop, or a tree evaluation that may stall — so the bridge
      reports a clear blender_timeout quickly instead of blocking on the global
      ceiling. Omit to use the default bridge timeout.
    """
    call_timeout: float | None = None
    if timeout_seconds is not None:
        try:
            call_timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            return "Error executing code: timeout_seconds must be a number"
        if call_timeout <= 0:
            return "Error executing code: timeout_seconds must be greater than 0"
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command(
            "execute_code",
            {
                "code": code,
                "transaction": transaction,
                "rollback_on_error": rollback_on_error,
            },
            timeout=call_timeout,
        )
        if transaction:
            return json.dumps(result, ensure_ascii=False, indent=2)
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"
