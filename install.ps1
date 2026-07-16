#Requires -Version 5.1

<#
.SYNOPSIS
    Stable Windows installer entry point for Blender MCP.

.DESCRIPTION
    This entry point preserves the public install.ps1 interface while loading
    the installer implementation from scripts/installer. In a source checkout,
    those files are loaded locally. When executed from GitHub Raw, the matching
    source archive is downloaded first so the modular installer can run from
    disk under Windows PowerShell 5.1.

    The installer supports Codex Desktop without requiring Codex CLI. When the
    CLI is unavailable, it safely adds or updates the shared Codex config.toml.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    .\install.ps1 -DryRun

.EXAMPLE
    .\install.ps1 -Gui

.EXAMPLE
    .\install.ps1 -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"

.EXAMPLE
    .\install.ps1 -SkipCodexRegistration
#>

[CmdletBinding()]
param(
    # Optional Blender 4.2+ executables. Every detected supported installation is used by default.
    [string[]]$BlenderPath = @(),

    # Optional Python 3.10+ executable used only when the target venv does not exist.
    [string]$PythonPath = "",

    # Directory allowed for structured node snapshot and patch JSON files.
    [string]$WorkspacePath = "",

    # GitHub repository used for installer-source and release discovery.
    [string]$Repository = "newo-ether/blender-mcp",

    # Optional exact GitHub Release tag. Empty means the latest stable release.
    [string]$ReleaseTag = "",

    # Stable per-user root containing versioned environments in release mode.
    [string]$InstallRoot = "",

    # Use GitHub Release assets even when running from a source checkout.
    [switch]$UseRelease,

    # Install only the Python MCP server; do not build or install the Blender Extension.
    [switch]$SkipBlenderExtension,

    # Do not add the MCP server to the shared Codex/ChatGPT user configuration.
    [switch]$SkipCodexRegistration,

    # Keep an existing, non-matching blender_mcp entry instead of updating it.
    [switch]$PreserveExistingMcpEntries,

    # Deprecated compatibility switch. Replacement is now the default behavior.
    [switch]$ForceCodexRegistration,

    # Do not add the MCP server to Claude Code's user scope.
    [switch]$SkipClaudeCodeRegistration,

    # Do not configure Claude Desktop or download its fallback MCPB package.
    [switch]$SkipClaudeDesktop,

    # Do not install the portable Blender MCP Agent Skill for selected clients.
    [switch]$SkipSkillInstallation,

    # Install filesystem Skills for the current user or an explicit project.
    [ValidateSet("User", "Project")]
    [string]$SkillScope = "User",

    # Project root used when -SkillScope Project; defaults to the current directory.
    [string]$SkillProjectPath = "",

    # Replace an existing Skill even when local modifications are detected.
    [switch]$ForceSkillUpdate,

    # Show detected paths and commands without changing the machine.
    [switch]$DryRun,

    # Use the graphical checkbox selector instead of the default terminal UI.
    [switch]$Gui,

    # Skip the interactive target selector and use detected/default targets.
    [switch]$NonInteractive,

    # Installer display language. Auto uses the Windows UI language.
    [ValidateSet("Auto", "en-US", "zh-CN")]
    [string]$Language = "Auto"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$script:InstallerEntryRoot = if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    $null
}
else {
    [System.IO.Path]::GetFullPath($PSScriptRoot)
}

$script:InstallerModuleFiles = @(
    "common.ps1",
    "release.ps1",
    "skills.ps1",
    "discovery.ps1",
    "targets.ps1",
    "clients.ps1",
    "codex-config.ps1",
    "install-main.ps1"
)

function Test-InstallerModuleRoot {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }
    foreach ($moduleFile in $script:InstallerModuleFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $Path $moduleFile) -PathType Leaf)) {
            return $false
        }
    }
    return $true
}

function Resolve-InstallerModuleRoot {
    if ($script:InstallerEntryRoot) {
        $localModuleRoot = Join-Path $script:InstallerEntryRoot "scripts\installer"
        if (Test-InstallerModuleRoot -Path $localModuleRoot) {
            return [PSCustomObject]@{ Path = $localModuleRoot; CleanupRoot = $null }
        }
    }

    if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        throw "GitHub repository must use owner/name syntax: $Repository"
    }
    $sourceRef = if ($ReleaseTag) { $ReleaseTag } else { "main" }
    if ($sourceRef -notmatch '^[A-Za-z0-9._-]+$') {
        throw "Installer source ref contains unsupported characters: $sourceRef"
    }
    $refKind = if ($ReleaseTag) { "tags" } else { "heads" }
    $archiveUrl = "https://github.com/$Repository/archive/refs/$refKind/$sourceRef.zip"
    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
        "blender-mcp-installer-" + [guid]::NewGuid().ToString("N")
    )
    $archivePath = Join-Path $tempRoot "source.zip"
    $expandRoot = Join-Path $tempRoot "source"

    try {
        New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
        Write-Host "Loading modular installer implementation from $Repository@$sourceRef..." -ForegroundColor DarkGray
        Invoke-WebRequest -Uri $archiveUrl -UseBasicParsing -OutFile $archivePath
        Expand-Archive -LiteralPath $archivePath -DestinationPath $expandRoot -Force

        $moduleRoots = @(
            Get-ChildItem -LiteralPath $expandRoot -Directory |
                ForEach-Object { Join-Path $_.FullName "scripts\installer" } |
                Where-Object { Test-InstallerModuleRoot -Path $_ }
        )
        if ($moduleRoots.Count -ne 1) {
            throw "The downloaded source archive does not contain one complete scripts/installer implementation."
        }
        return [PSCustomObject]@{ Path = $moduleRoots[0]; CleanupRoot = $tempRoot }
    }
    catch {
        if (Test-Path -LiteralPath $tempRoot -PathType Container) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

$implementation = $null
try {
    $implementation = Resolve-InstallerModuleRoot
    foreach ($moduleFile in $script:InstallerModuleFiles) {
        . (Join-Path $implementation.Path $moduleFile)
    }
}
catch {
    Write-Host ""
    Write-Host "  Installer startup failed" -ForegroundColor Red
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    exit 1
}
finally {
    if ($null -ne $implementation -and $implementation.CleanupRoot) {
        Remove-Item -LiteralPath $implementation.CleanupRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Invoke-BlenderMcpInstall
