@echo off
setlocal
>nul chcp 65001
if not defined BLENDER_MCP_INSTALL_ROOT set "BLENDER_MCP_INSTALL_ROOT=%LOCALAPPDATA%\BlenderMCP"
if not defined BLENDER_MCP_WORKSPACE (
  set "BLENDER_MCP_WORKSPACE_POINTER=%BLENDER_MCP_INSTALL_ROOT%\claude-workspace.txt"
  if not exist "%BLENDER_MCP_WORKSPACE_POINTER%" set "BLENDER_MCP_WORKSPACE_POINTER=%BLENDER_MCP_INSTALL_ROOT%\current-workspace.txt"
  if exist "%BLENDER_MCP_WORKSPACE_POINTER%" (
    set /p BLENDER_MCP_WORKSPACE=<"%BLENDER_MCP_WORKSPACE_POINTER%"
  )
)
if not defined BLENDER_MCP_WORKSPACE set "BLENDER_MCP_WORKSPACE=%USERPROFILE%\Documents\BlenderMCP"
set "BLENDER_MCP_ABSOLUTE_POINTER=%BLENDER_MCP_INSTALL_ROOT%\claude-server.txt"
if exist "%BLENDER_MCP_ABSOLUTE_POINTER%" (
  set /p BLENDER_MCP_SERVER=<"%BLENDER_MCP_ABSOLUTE_POINTER%"
)
if not defined BLENDER_MCP_SERVER (
  set "BLENDER_MCP_POINTER=%BLENDER_MCP_INSTALL_ROOT%\current-server.txt"
  if not exist "%BLENDER_MCP_POINTER%" (
    >&2 echo Blender MCP server pointer is not installed at "%BLENDER_MCP_POINTER%".
    >&2 echo Run the PowerShell installer from https://github.com/newo-ether/blender-mcp first.
    exit /b 1
  )
  set /p BLENDER_MCP_SERVER_RELATIVE=<"%BLENDER_MCP_POINTER%"
  set "BLENDER_MCP_SERVER=%BLENDER_MCP_INSTALL_ROOT%\%BLENDER_MCP_SERVER_RELATIVE%"
)
if not exist "%BLENDER_MCP_SERVER%" (
  >&2 echo Blender MCP server is not installed at "%BLENDER_MCP_SERVER%".
  >&2 echo Run the PowerShell installer from https://github.com/newo-ether/blender-mcp first.
  exit /b 1
)
"%BLENDER_MCP_SERVER%"
