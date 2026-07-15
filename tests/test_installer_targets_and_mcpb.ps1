[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$installer = Join-Path $root "install.ps1"
$manifestPath = Join-Path $root "packaging\claude_desktop\manifest.json"
$launcherPath = Join-Path $root "packaging\claude_desktop\server\run.cmd"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$caseRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $tempRoot ("blender-mcp-installer-targets-" + [guid]::NewGuid().ToString("N")))
)

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

try {
    $source = (Get-Content -LiteralPath $installer -Raw -Encoding UTF8) -replace "`r`n", "`n"
    $mainMarker = "`ntry {`n    Write-Banner"
    $mainIndex = $source.LastIndexOf($mainMarker, [System.StringComparison]::Ordinal)
    Assert-True -Condition ($mainIndex -ge 0) -Message "Could not isolate installer function definitions."
    . ([scriptblock]::Create($source.Substring(0, $mainIndex)))

    Assert-True -Condition ((Resolve-InstallerLanguage -RequestedLanguage "Auto" -UiCultureName "zh-CN") -eq "zh-CN") -Message "zh-CN UI culture did not select Chinese."
    Assert-True -Condition ((Resolve-InstallerLanguage -RequestedLanguage "Auto" -UiCultureName "zh-Hans-CN") -eq "zh-CN") -Message "zh-Hans UI culture did not select Chinese."
    Assert-True -Condition ((Resolve-InstallerLanguage -RequestedLanguage "Auto" -UiCultureName "zh-TW") -eq "en-US") -Message "A non-zh-CN UI culture did not fall back to English."
    Assert-True -Condition ((Resolve-InstallerLanguage -RequestedLanguage "zh-CN" -UiCultureName "en-US") -eq "zh-CN") -Message "Explicit Chinese language override was ignored."
    $script:UseChinese = $true
    Assert-True -Condition ((L "English" "中文") -eq "中文") -Message "Chinese localizer output is incorrect."
    $script:UseChinese = $false

    $installations = @(
        [PSCustomObject]@{ Name = "Blender 5.2"; Path = "C:\Blender52\blender.exe"; Supported = $true },
        [PSCustomObject]@{ Name = "Blender 5.1"; Path = "C:\Blender51\blender.exe"; Supported = $true },
        [PSCustomObject]@{ Name = "Blender 4.1"; Path = "C:\Blender41\blender.exe"; Supported = $false }
    )
    $defaults = @(Get-DefaultBlenderPaths -BlenderInstallations $installations -DisableBlender $false)
    Assert-True -Condition ($defaults.Count -eq 2) -Message "All supported Blender versions were not selected by default."
    Assert-True -Condition ($defaults -contains "C:\Blender52\blender.exe") -Message "Blender 5.2 was not selected."
    Assert-True -Condition ($defaults -contains "C:\Blender51\blender.exe") -Message "Blender 5.1 was not selected."
    Assert-True -Condition (-not ($defaults -contains "C:\Blender41\blender.exe")) -Message "Unsupported Blender was selected."

    $detection = [PSCustomObject]@{
        CodexCliFound = $false
        CodexDesktopFound = $false
        ClaudeCodeFound = $false
        ClaudeDesktopFound = $false
    }
    $selection = Select-InstallTargets `
        -Detection $detection `
        -BlenderInstallations $installations `
        -NoGui $true
    Assert-True -Condition (@($selection.BlenderPaths).Count -eq 2) -Message "Non-interactive defaults did not select every supported Blender."

    $codexDetection = [PSCustomObject]@{
        CodexCliFound = $true
        CodexDesktopFound = $true
        ClaudeCodeFound = $false
        ClaudeDesktopFound = $false
    }
    $codexSelection = Select-InstallTargets `
        -Detection $codexDetection `
        -BlenderInstallations @() `
        -NoGui $true
    Assert-True -Condition ($codexSelection.CodexCli -and $codexSelection.CodexDesktop) -Message "The combined Codex/ChatGPT target did not enable the shared configuration."
    Assert-True -Condition ($source -match 'Kind = "Codex"') -Message "The selector lacks the combined Codex target."
    Assert-True -Condition ($source -notmatch 'Kind = "CodexCli"|Kind = "CodexDesktop"') -Message "The selector still exposes separate Codex and ChatGPT targets."

    $disabled = @(Get-DefaultBlenderPaths -BlenderInstallations $installations -DisableBlender $true)
    Assert-True -Condition ($disabled.Count -eq 0) -Message "-SkipBlenderExtension did not clear default Blender targets."

    $manifestText = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
    $manifest = $manifestText | ConvertFrom-Json
    Assert-True -Condition ($manifestText -notmatch '\$\{HOME\}') -Message "MCPB manifest still contains a HOME placeholder."
    Assert-True -Condition ($null -eq $manifest.PSObject.Properties["user_config"]) -Message "MCPB still asks Claude to resolve bootstrap paths."
    Assert-True -Condition ($null -eq $manifest.server.mcp_config.env.PSObject.Properties["BLENDER_MCP_INSTALL_ROOT"]) -Message "MCPB manifest still overrides the launcher install root."
    Assert-True -Condition ($null -eq $manifest.server.mcp_config.env.PSObject.Properties["BLENDER_MCP_WORKSPACE"]) -Message "MCPB manifest still overrides the launcher workspace."

    $launcher = Get-Content -LiteralPath $launcherPath -Raw -Encoding UTF8
    Assert-True -Condition ($launcher -match '%LOCALAPPDATA%\\BlenderMCP') -Message "MCPB launcher lacks the per-user install default."
    Assert-True -Condition ($launcher -match 'claude-server\.txt') -Message "MCPB launcher lacks the absolute server fallback pointer."
    Assert-True -Condition ($launcher -match 'claude-workspace\.txt') -Message "MCPB launcher lacks the absolute workspace fallback pointer."
    Assert-True -Condition ($launcher -match 'current-workspace\.txt') -Message "MCPB launcher does not honor the installer workspace pointer."
    Assert-True -Condition ($launcher -match '%USERPROFILE%\\Documents\\BlenderMCP') -Message "MCPB launcher lacks the per-user workspace default."

    New-Item -ItemType Directory -Path $caseRoot -Force | Out-Null
    $workspace = Join-Path $caseRoot "工作区"
    New-Item -ItemType Directory -Path $workspace -Force | Out-Null
    $script:DryRunEnabled = $false
    $workspacePointer = Set-CurrentWorkspacePointer -InstallBase $caseRoot -Workspace $workspace
    $recordedWorkspace = (Get-Content -LiteralPath $workspacePointer -Raw -Encoding UTF8).Trim()
    Assert-True -Condition ($recordedWorkspace -eq [System.IO.Path]::GetFullPath($workspace)) -Message "The MCPB workspace pointer did not preserve the selected Unicode path."

    $serverExecutable = Join-Path $caseRoot "自定义安装\venv-1.9.2\Scripts\blender-mcp.exe"
    $bridgeRoot = Join-Path $caseRoot "bridge"
    Set-ClaudeDesktopFallbackPointers `
        -ServerExecutable $serverExecutable `
        -Workspace $workspace `
        -BridgeRoot $bridgeRoot
    $fallbackServer = (Get-Content -LiteralPath (Join-Path $bridgeRoot "claude-server.txt") -Raw -Encoding UTF8).Trim()
    $fallbackWorkspace = (Get-Content -LiteralPath (Join-Path $bridgeRoot "claude-workspace.txt") -Raw -Encoding UTF8).Trim()
    Assert-True -Condition ($fallbackServer -eq [System.IO.Path]::GetFullPath($serverExecutable)) -Message "MCPB fallback did not preserve a custom Unicode install root."
    Assert-True -Condition ($fallbackWorkspace -eq [System.IO.Path]::GetFullPath($workspace)) -Message "MCPB fallback workspace pointer is incorrect."

    $claudeConfig = Join-Path $caseRoot "Claude\claude_desktop_config.json"
    New-Item -ItemType Directory -Path (Split-Path -Parent $claudeConfig) -Force | Out-Null
    $claudeFixture = @'
{
  "theme": "dark",
  "mcpServers": {
    "keep_me": {
      "command": "keep.exe"
    },
    "blender_mcp": {
      "command": "old.exe"
    }
  }
}
'@
    Set-Content -LiteralPath $claudeConfig -Value $claudeFixture -Encoding UTF8
    $serverExecutable = Join-Path $caseRoot "venv-1.9.2\Scripts\blender-mcp.exe"
    $registered = Register-ClaudeDesktopMcp `
        -ConfigPath $claudeConfig `
        -ServerExecutable $serverExecutable `
        -Workspace $workspace `
        -Port 9876 `
        -PreserveExisting $false
    Assert-True -Condition $registered -Message "Claude Desktop JSON registration failed."
    $claudeJson = Get-Content -LiteralPath $claudeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True -Condition ($claudeJson.theme -eq "dark") -Message "Claude Desktop registration removed an unrelated top-level field."
    Assert-True -Condition ($claudeJson.mcpServers.keep_me.command -eq "keep.exe") -Message "Claude Desktop registration removed another MCP server."
    Assert-True -Condition ($claudeJson.mcpServers.blender_mcp.command -eq [System.IO.Path]::GetFullPath($serverExecutable)) -Message "Claude Desktop command path is incorrect."
    Assert-True -Condition ($claudeJson.mcpServers.blender_mcp.env.BLENDER_MCP_WORKSPACE -eq [System.IO.Path]::GetFullPath($workspace)) -Message "Claude Desktop workspace path is incorrect."
    Assert-True -Condition (@(Get-ChildItem -LiteralPath (Split-Path -Parent $claudeConfig) -Filter "claude_desktop_config.json.blender-mcp-*.bak").Count -eq 1) -Message "Claude Desktop config backup was not created."
    Assert-True -Condition ($script:ClaudeDesktopStatus -eq "updated") -Message "Claude Desktop JSON update status is incorrect."

    $matching = Register-ClaudeDesktopMcp `
        -ConfigPath $claudeConfig `
        -ServerExecutable $serverExecutable `
        -Workspace $workspace `
        -Port 9876 `
        -PreserveExisting $false
    Assert-True -Condition $matching -Message "Matching Claude Desktop config was not accepted."
    Assert-True -Condition ($script:ClaudeDesktopStatus -eq "already configured") -Message "Matching Claude Desktop config was needlessly rewritten."
    Assert-True -Condition (@(Get-ChildItem -LiteralPath (Split-Path -Parent $claudeConfig) -Filter "claude_desktop_config.json.blender-mcp-*.bak").Count -eq 1) -Message "Matching Claude Desktop config created an unnecessary backup."

    $preservedCommand = $claudeJson.mcpServers.blender_mcp.command
    $preserved = Register-ClaudeDesktopMcp `
        -ConfigPath $claudeConfig `
        -ServerExecutable (Join-Path $caseRoot "replacement.exe") `
        -Workspace $workspace `
        -Port 9876 `
        -PreserveExisting $true
    Assert-True -Condition $preserved -Message "Preserving a Claude Desktop entry should not trigger MCPB fallback."
    $preservedJson = Get-Content -LiteralPath $claudeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True -Condition ($preservedJson.mcpServers.blender_mcp.command -eq $preservedCommand) -Message "PreserveExistingMcpEntries changed the Claude Desktop entry."

    $invalidClaudeConfig = Join-Path $caseRoot "invalid-claude.json"
    Set-Content -LiteralPath $invalidClaudeConfig -Value '{ invalid json' -Encoding ASCII
    $invalidOriginal = Get-Content -LiteralPath $invalidClaudeConfig -Raw
    $invalidResult = Register-ClaudeDesktopMcp `
        -ConfigPath $invalidClaudeConfig `
        -ServerExecutable $serverExecutable `
        -Workspace $workspace `
        -Port 9876 `
        -PreserveExisting $false
    Assert-True -Condition (-not $invalidResult) -Message "Invalid Claude Desktop JSON did not request MCPB fallback."
    Assert-True -Condition ((Get-Content -LiteralPath $invalidClaudeConfig -Raw) -eq $invalidOriginal) -Message "Invalid Claude Desktop JSON was modified."

    $bundle = Join-Path $caseRoot "blender_mcp-test.mcpb"
    Set-Content -LiteralPath $bundle -Value "fixture" -Encoding ASCII
    $script:ProcessCalls = @()
    function Start-Process {
        param([string]$FilePath, [object[]]$ArgumentList)
        $script:ProcessCalls += [PSCustomObject]@{
            FilePath = $FilePath
            ArgumentList = @($ArgumentList)
        }
        return [PSCustomObject]@{ Id = 1 }
    }

    Open-ClaudeDesktopBundle `
        -BundlePath $bundle `
        -LaunchKind "Executable" `
        -LaunchTarget "C:\Claude\Claude.exe"
    Assert-True -Condition ($script:ProcessCalls.Count -eq 1) -Message "Claude handoff unexpectedly invoked another Windows opener."
    Assert-True -Condition ($script:ProcessCalls[0].FilePath -eq "C:\Claude\Claude.exe") -Message "The MCPB was not opened with the detected Claude executable."
    $handedOffBundle = ([string]$script:ProcessCalls[0].ArgumentList[0]).Trim('"')
    Assert-True -Condition ($handedOffBundle -eq $bundle) -Message "The MCPB path was not passed to Claude Desktop."
    Assert-True -Condition ($script:ClaudeDesktopStatus -eq "confirmation requested") -Message "Claude Desktop handoff status is incorrect."

    Write-Host "Installer target-selection and MCPB tests passed." -ForegroundColor Green
}
finally {
    if (
        (Test-Path -LiteralPath $caseRoot -PathType Container) -and
        $caseRoot.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        Remove-Item -LiteralPath $caseRoot -Recurse -Force
    }
}
