@echo off
setlocal
set "BLENDER_MCP_SERVER=%LOCALAPPDATA%\BlenderMCP\venv\Scripts\blender-mcp.exe"
if not exist "%BLENDER_MCP_SERVER%" (
  >&2 echo Blender MCP server is not installed at "%BLENDER_MCP_SERVER%".
  >&2 echo Run the PowerShell installer from https://github.com/newo-ether/blender-mcp first.
  exit /b 1
)
"%BLENDER_MCP_SERVER%"
