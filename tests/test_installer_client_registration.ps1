[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("blender-mcp-installer-clients-" + [guid]::NewGuid().ToString("N"))
$clientLog = Join-Path $tempRoot "client.log"
$previousClientLog = $env:FAKE_MCP_LOG
$previousCodexGetMode = $env:FAKE_CODEX_GET_MODE
$previousCodexJson = $env:FAKE_CODEX_JSON
$previousCodexListJson = $env:FAKE_CODEX_LIST_JSON
$previousClaudeGetMode = $env:FAKE_CLAUDE_GET_MODE
$previousClaudeListFile = $env:FAKE_CLAUDE_LIST_FILE

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
if /I "%1"=="mcp" if /I "%2"=="list" (
  if defined FAKE_CODEX_LIST_JSON (
    type "%FAKE_CODEX_LIST_JSON%"
    exit /b 0
  )
  echo []
  exit /b 0
)
if /I "%1"=="mcp" if /I "%2"=="remove" exit /b 0
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
if /I "%1"=="mcp" if /I "%2"=="list" (
  if defined FAKE_CLAUDE_LIST_FILE type "%FAKE_CLAUDE_LIST_FILE%"
  exit /b 0
)
if /I "%1"=="mcp" if /I "%2"=="remove" (
  if /I not "%3"=="blender_mcp" exit /b 0
  >&2 echo MCP server exists outside user scope.
  exit /b 1
)
if /I "%1"=="mcp" if /I "%2"=="add" exit /b 0
>&2 echo Unexpected Claude arguments: %*
exit /b 2
'@

    . (Join-Path $PSScriptRoot "import_installer.ps1") -Root $root

    $workspace = Join-Path $tempRoot "workspace"
    $serverExecutable = Join-Path $tempRoot "blender-mcp.exe"

    Assert-True -Condition (Test-LegacyBlenderMcpCommand -Command "uvx" -Arguments @("blender-mcp")) -Message "uvx blender-mcp was not recognized."
    Assert-True -Condition (Test-LegacyBlenderMcpCommand -Command "C:\tools\uv.exe" -Arguments @("tool", "uvx", "blender-mcp")) -Message "uv tool uvx blender-mcp was not recognized."
    Assert-True -Condition (Test-LegacyBlenderMcpCommandLine -CommandLine '"C:\tools\uvx.exe" blender-mcp') -Message "Quoted uvx command line was not recognized."
    Assert-True -Condition (-not (Test-LegacyBlenderMcpCommand -Command "uvx" -Arguments @("another-package"))) -Message "An unrelated uvx package was misidentified."
    Assert-True -Condition (-not (Test-LegacyBlenderMcpCommand -Command $serverExecutable -Arguments @())) -Message "The direct server executable was misidentified as legacy."

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

    $codexListJson = Join-Path $tempRoot "codex-list.json"
    @(
        [PSCustomObject]@{
            name = "blender"
            transport = [PSCustomObject]@{ command = "C:\tools\uvx.exe"; args = @("blender-mcp") }
        },
        [PSCustomObject]@{
            name = "other"
            transport = [PSCustomObject]@{ command = "uvx"; args = @("another-package") }
        }
    ) | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $codexListJson -Encoding UTF8
    $env:FAKE_CODEX_LIST_JSON = $codexListJson
    Remove-Item -LiteralPath $clientLog -Force

    $legacyCodexProbe = Invoke-CapturedCommand -FilePath $codex -ArgumentList @("mcp", "list", "--json") -IncludeErrorOutput
    $legacyCodexProbeText = $legacyCodexProbe.Output -join "`n"
    Assert-True `
        -Condition ($legacyCodexProbe.ExitCode -eq 0 -and $legacyCodexProbeText -match '"blender"') `
        -Message ("Codex list probe returned exit=" + $legacyCodexProbe.ExitCode + " output=" + $legacyCodexProbeText)

    $legacyCodexEntries = @(Get-CodexLegacyBlenderMcpEntries -CodexExecutable $codex)
    Assert-True `
        -Condition ($legacyCodexEntries.Count -eq 1 -and $legacyCodexEntries[0].Name -eq "blender") `
        -Message ("Codex legacy discovery returned: " + ($legacyCodexEntries | ConvertTo-Json -Depth 4 -Compress))

    Remove-CodexLegacyBlenderMcpEntries -CodexExecutable $codex -PreserveExisting $false
    $calls = @(Get-Content -LiteralPath $clientLog)
    Assert-True -Condition ([bool]($calls -match '^codex mcp remove blender$')) -Message "Codex legacy Blender entry was not removed."
    Assert-True -Condition (-not [bool]($calls -match '^codex mcp remove other$')) -Message "Unrelated Codex uvx entry was removed."

    $claudeListFile = Join-Path $tempRoot "claude-list.txt"
    @(
        'blender: uvx blender-mcp - connected',
        'other: uvx another-package - connected',
        ('blender_mcp: ' + $serverExecutable + ' - connected')
    ) | Set-Content -LiteralPath $claudeListFile -Encoding UTF8
    $env:FAKE_CLAUDE_LIST_FILE = $claudeListFile
    Remove-Item -LiteralPath $clientLog -Force

    Remove-ClaudeLegacyBlenderMcpEntries -ClaudeExecutable $claude -PreserveExisting $false
    $calls = @(Get-Content -LiteralPath $clientLog)
    Assert-True -Condition ([bool]($calls -match '^claude mcp remove blender --scope user$')) -Message "Claude legacy Blender entry was not removed."
    Assert-True -Condition (-not [bool]($calls -match '^claude mcp remove other --scope user$')) -Message "Unrelated Claude uvx entry was removed."

    Remove-Item -LiteralPath $clientLog -Force
    Remove-CodexLegacyBlenderMcpEntries -CodexExecutable $codex -PreserveExisting $true
    $calls = @(Get-Content -LiteralPath $clientLog)
    Assert-True -Condition (-not [bool]($calls -match '^codex mcp remove blender$')) -Message "Preserve mode removed a legacy Codex entry."

    $python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    Assert-True -Condition ($null -ne $python) -Message "Python is required for the Desktop-only TOML contract test."
    $codexHome = Join-Path $tempRoot "desktop-only-codex"
    $codexConfig = Join-Path $codexHome "config.toml"
    New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
    @'
model = "gpt-test"

[mcp_servers.keep_me]
command = "keep.exe"
'@ | Set-Content -LiteralPath $codexConfig -Encoding UTF8

    $desktopServer = Join-Path $tempRoot "desktop-only\blender-mcp.exe"
    $desktopWorkspace = Join-Path $tempRoot "desktop-workspace"
    Register-CodexConfigMcp `
        -ConfigPath $codexConfig `
        -PythonExecutable $python.Source `
        -ServerExecutable $desktopServer `
        -Workspace $desktopWorkspace `
        -Port 9876 `
        -PreserveExisting $false

    Assert-True -Condition ($script:CodexStatus -eq "configured (config file)") -Message "Desktop-only Codex registration did not add the missing entry."
    $desktopState = Get-CodexConfigStateWithPython -PythonExecutable $python.Source -ConfigPath $codexConfig
    Assert-True -Condition $desktopState.exists -Message "Desktop-only config lacks blender_mcp."
    Assert-True -Condition (Test-SamePath -Left $desktopState.command -Right $desktopServer) -Message "Desktop-only config has the wrong command."
    Assert-True -Condition (Test-SamePath -Left $desktopState.workspace -Right $desktopWorkspace) -Message "Desktop-only config has the wrong workspace."
    $desktopConfigText = Get-Content -LiteralPath $codexConfig -Raw -Encoding UTF8
    Assert-True -Condition ($desktopConfigText -match 'model = "gpt-test"') -Message "Desktop-only registration removed an unrelated top-level setting."
    Assert-True -Condition ($desktopConfigText -match '\[mcp_servers\.keep_me\]') -Message "Desktop-only registration removed another MCP server."
    Assert-True -Condition (@(Get-ChildItem -LiteralPath $codexHome -Filter "config.toml.blender-mcp-*.bak").Count -eq 1) -Message "Desktop-only registration did not back up the existing config."

    Register-CodexConfigMcp `
        -ConfigPath $codexConfig `
        -PythonExecutable $python.Source `
        -ServerExecutable $desktopServer `
        -Workspace $desktopWorkspace `
        -Port 9876 `
        -PreserveExisting $false
    Assert-True -Condition ($script:CodexStatus -eq "already configured") -Message "Matching Desktop-only config was not retained."
    Assert-True -Condition (@(Get-ChildItem -LiteralPath $codexHome -Filter "config.toml.blender-mcp-*.bak").Count -eq 1) -Message "Matching Desktop-only config created an unnecessary backup."

    $replacementServer = Join-Path $tempRoot "replacement\blender-mcp.exe"
    Register-CodexConfigMcp `
        -ConfigPath $codexConfig `
        -PythonExecutable $python.Source `
        -ServerExecutable $replacementServer `
        -Workspace $desktopWorkspace `
        -Port 9876 `
        -PreserveExisting $true
    Assert-True -Condition ($script:CodexStatus -eq "existing different entry preserved") -Message "PreserveExistingMcpEntries did not retain the Desktop-only entry."

    Register-CodexConfigMcp `
        -ConfigPath $codexConfig `
        -PythonExecutable $python.Source `
        -ServerExecutable $replacementServer `
        -Workspace $desktopWorkspace `
        -Port 9876 `
        -PreserveExisting $false
    Assert-True -Condition ($script:CodexStatus -eq "updated (config file)") -Message "Default Desktop-only behavior did not replace the different entry."
    $replacementState = Get-CodexConfigStateWithPython -PythonExecutable $python.Source -ConfigPath $codexConfig
    Assert-True -Condition (Test-SamePath -Left $replacementState.command -Right $replacementServer) -Message "Desktop-only replacement kept the old command."

    $inlineConfig = Join-Path $codexHome "inline.toml"
    $inlineOriginal = 'mcp_servers = { blender_mcp = { command = "custom.exe", env = { BLENDER_PORT = "9876" } } }'
    Set-Content -LiteralPath $inlineConfig -Value $inlineOriginal -Encoding UTF8
    $inlineRejected = $false
    try {
        Register-CodexConfigMcp `
            -ConfigPath $inlineConfig `
            -PythonExecutable $python.Source `
            -ServerExecutable $desktopServer `
            -Workspace $desktopWorkspace `
            -Port 9876 `
            -PreserveExisting $false
    }
    catch { $inlineRejected = $true }
    Assert-True -Condition $inlineRejected -Message "An inline TOML layout was rewritten without a safely located table."
    Assert-True -Condition ((Get-Content -LiteralPath $inlineConfig -Raw).Trim() -eq $inlineOriginal) -Message "Rejected inline TOML was modified."

    $invalidConfig = Join-Path $codexHome "invalid.toml"
    $invalidOriginal = '[mcp_servers.blender_mcp'
    Set-Content -LiteralPath $invalidConfig -Value $invalidOriginal -Encoding UTF8
    $global:LASTEXITCODE = 0
    $invalidRejected = $false
    try {
        Register-CodexConfigMcp `
            -ConfigPath $invalidConfig `
            -PythonExecutable $python.Source `
            -ServerExecutable $desktopServer `
            -Workspace $desktopWorkspace `
            -Port 9876 `
            -PreserveExisting $false
    }
    catch { $invalidRejected = $true }
    Assert-True -Condition $invalidRejected -Message "Invalid Codex TOML was not rejected."
    Assert-True -Condition ((Get-Content -LiteralPath $invalidConfig -Raw).Trim() -eq $invalidOriginal) -Message "Invalid Codex TOML was modified."
    Assert-True -Condition ($global:LASTEXITCODE -eq 0) -Message "A handled TOML probe failure leaked into the caller's native exit code."

    Write-Host "Installer client-registration tests passed." -ForegroundColor Green
}
finally {
    $env:FAKE_MCP_LOG = $previousClientLog
    $env:FAKE_CODEX_GET_MODE = $previousCodexGetMode
    $env:FAKE_CODEX_JSON = $previousCodexJson
    $env:FAKE_CODEX_LIST_JSON = $previousCodexListJson
    $env:FAKE_CLAUDE_GET_MODE = $previousClaudeGetMode
    $env:FAKE_CLAUDE_LIST_FILE = $previousClaudeListFile
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
