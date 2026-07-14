[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$installer = Join-Path $root "install.ps1"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("blender-mcp-installer-clients-" + [guid]::NewGuid().ToString("N"))
$clientLog = Join-Path $tempRoot "client.log"
$previousClientLog = $env:FAKE_MCP_LOG
$previousCodexGetMode = $env:FAKE_CODEX_GET_MODE
$previousCodexJson = $env:FAKE_CODEX_JSON
$previousClaudeGetMode = $env:FAKE_CLAUDE_GET_MODE

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    $env:FAKE_MCP_LOG = $clientLog

    $codex = Join-Path $tempRoot "codex.cmd"
    $claude = Join-Path $tempRoot "claude.cmd"
    Set-Content -LiteralPath $codex -Encoding ASCII -Value @'
@echo off
>>"%FAKE_MCP_LOG%" echo codex %*
if /I "%1"=="mcp" if /I "%2"=="get" (
  if /I "%FAKE_CODEX_GET_MODE%"=="matching" (
    type "%FAKE_CODEX_JSON%"
    exit /b 0
  )
  >&2 echo Error: No MCP server named 'blender_mcp' found.
  exit /b 1
)
if /I "%1"=="mcp" if /I "%2"=="add" exit /b 0
>&2 echo Unexpected Codex arguments: %*
exit /b 2
'@
    Set-Content -LiteralPath $claude -Encoding ASCII -Value @'
@echo off
>>"%FAKE_MCP_LOG%" echo claude %*
if /I "%1"=="mcp" if /I "%2"=="get" (
  if /I "%FAKE_CLAUDE_GET_MODE%"=="existing" exit /b 0
  >&2 echo No MCP server found with name: "blender_mcp".
  exit /b 1
)
if /I "%1"=="mcp" if /I "%2"=="remove" (
  >&2 echo MCP server exists outside user scope.
  exit /b 1
)
if /I "%1"=="mcp" if /I "%2"=="add" exit /b 0
>&2 echo Unexpected Claude arguments: %*
exit /b 2
'@

    $source = (Get-Content -LiteralPath $installer -Raw -Encoding UTF8) -replace "`r`n", "`n"
    $mainMarker = "`ntry {`n    Write-Banner"
    $mainIndex = $source.LastIndexOf($mainMarker, [System.StringComparison]::Ordinal)
    Assert-True -Condition ($mainIndex -ge 0) -Message "Could not isolate installer function definitions."
    . ([scriptblock]::Create($source.Substring(0, $mainIndex)))

    $workspace = Join-Path $tempRoot "workspace"
    $serverExecutable = Join-Path $tempRoot "blender-mcp.exe"
    Register-CodexMcp -CodexExecutable $codex -ServerExecutable $serverExecutable -Workspace $workspace -Port 9876 -PreserveExisting $false
    Register-ClaudeCodeMcp -ClaudeExecutable $claude -ServerExecutable $serverExecutable -Workspace $workspace -Port 9876 -PreserveExisting $false

    Assert-True -Condition ($script:CodexStatus -eq "configured") -Message "Codex first-time registration did not complete."
    Assert-True -Condition ($script:ClaudeCodeStatus -eq "configured") -Message "Claude Code first-time registration did not complete."

    $calls = @(Get-Content -LiteralPath $clientLog)
    Assert-True -Condition ([bool]($calls -match '^codex mcp get blender_mcp --json$')) -Message "Codex existing-entry probe was not called."
    Assert-True -Condition ([bool]($calls -match '^codex mcp add blender_mcp ')) -Message "Codex add was not called after the missing-entry probe."
    Assert-True -Condition ([bool]($calls -match '^claude mcp get blender_mcp$')) -Message "Claude Code existing-entry probe was not called."
    Assert-True -Condition ([bool]($calls -match '^claude mcp add --scope user blender_mcp ')) -Message "Claude Code add was not called after the missing-entry probe."
    Assert-True -Condition (-not [bool]($calls -match ' mcp remove ')) -Message "A first-time registration unexpectedly removed an entry."

    $codexJson = Join-Path $tempRoot "codex.json"
    [PSCustomObject]@{
        transport = [PSCustomObject]@{
            command = $serverExecutable
            env = [PSCustomObject]@{
                BLENDER_MCP_WORKSPACE = $workspace
                BLENDER_HOST = "localhost"
                BLENDER_PORT = "9876"
            }
        }
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $codexJson -Encoding UTF8
    $env:FAKE_CODEX_GET_MODE = "matching"
    $env:FAKE_CODEX_JSON = $codexJson
    Remove-Item -LiteralPath $clientLog -Force

    Register-CodexMcp -CodexExecutable $codex -ServerExecutable $serverExecutable -Workspace $workspace -Port 9876 -PreserveExisting $false

    Assert-True -Condition ($script:CodexStatus -eq "already configured") -Message "A matching Codex registration was not retained."
    $calls = @(Get-Content -LiteralPath $clientLog)
    Assert-True -Condition ([bool]($calls -match '^codex mcp get blender_mcp --json$')) -Message "Matching Codex configuration was not queried."
    Assert-True -Condition (-not [bool]($calls -match '^codex mcp (add|remove) ')) -Message "Matching Codex configuration was unexpectedly changed."

    $env:FAKE_CLAUDE_GET_MODE = "existing"
    Remove-Item -LiteralPath $clientLog -Force

    Register-ClaudeCodeMcp -ClaudeExecutable $claude -ServerExecutable $serverExecutable -Workspace $workspace -Port 9876 -PreserveExisting $false

    Assert-True -Condition ($script:ClaudeCodeStatus -eq "configured; higher-priority entry retained") -Message "Claude Code did not tolerate a non-user entry removal failure."
    $calls = @(Get-Content -LiteralPath $clientLog)
    Assert-True -Condition ([bool]($calls -match '^claude mcp remove blender_mcp --scope user$')) -Message "Claude Code user-scope removal was not attempted."
    Assert-True -Condition ([bool]($calls -match '^claude mcp add --scope user blender_mcp ')) -Message "Claude Code user-scope registration did not continue after removal failed."

    Write-Host "Installer client-registration tests passed." -ForegroundColor Green
}
finally {
    $env:FAKE_MCP_LOG = $previousClientLog
    $env:FAKE_CODEX_GET_MODE = $previousCodexGetMode
    $env:FAKE_CODEX_JSON = $previousCodexJson
    $env:FAKE_CLAUDE_GET_MODE = $previousClaudeGetMode
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
