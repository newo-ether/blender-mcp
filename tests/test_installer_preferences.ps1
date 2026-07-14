#Requires -Version 5.1

<#
.SYNOPSIS
    Verify that Extension installation preserves existing Blender preferences.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BlenderPath
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$blender = [System.IO.Path]::GetFullPath($BlenderPath)
if (-not (Test-Path -LiteralPath $blender -PathType Leaf)) {
    throw "Blender executable not found: $blender"
}

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$caseRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $tempRoot ("blender-mcp-installer-preferences-" + [guid]::NewGuid().ToString("N")))
)
if (-not $caseRoot.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Temporary test directory escaped the system temp root: $caseRoot"
}

$fixture = Join-Path $PSScriptRoot "blender_installer_preferences.py"
$installer = Join-Path $root "install.ps1"
$workspace = Join-Path $caseRoot "workspace"
$oldConfig = $env:BLENDER_USER_CONFIG
$oldScripts = $env:BLENDER_USER_SCRIPTS
$oldExtensions = $env:BLENDER_USER_EXTENSIONS
$oldDataFiles = $env:BLENDER_USER_DATAFILES

try {
    $env:BLENDER_USER_CONFIG = Join-Path $caseRoot "config"
    $env:BLENDER_USER_SCRIPTS = Join-Path $caseRoot "scripts"
    $env:BLENDER_USER_EXTENSIONS = Join-Path $caseRoot "extensions"
    $env:BLENDER_USER_DATAFILES = Join-Path $caseRoot "datafiles"
    New-Item -ItemType Directory -Force -Path @(
        $env:BLENDER_USER_CONFIG,
        $env:BLENDER_USER_SCRIPTS,
        $env:BLENDER_USER_EXTENSIONS,
        $env:BLENDER_USER_DATAFILES
    ) | Out-Null

    & $blender --factory-startup --background --python $fixture -- seed
    if ($LASTEXITCODE -ne 0) { throw "Could not seed isolated Blender preferences." }

    foreach ($attempt in 1..2) {
        $installerOutput = @(& powershell -NoProfile -ExecutionPolicy Bypass -File $installer `
            -NonInteractive `
            -BlenderPath $blender `
            -WorkspacePath $workspace `
            -SkipCodexRegistration `
            -SkipClaudeCodeRegistration `
            -SkipClaudeDesktop 2>&1)
        $installerExitCode = $LASTEXITCODE
        if ($installerExitCode -ne 0) {
            throw "The Blender MCP installer failed on attempt $attempt.`n$($installerOutput -join "`n")"
        }

        $rawBlenderOutput = @($installerOutput | Where-Object {
            [string]$_ -match '^(?:Blender \d|Blender quit|BlenderMCP(?: addon| server|:)|Read prefs:)'
        })
        if ($rawBlenderOutput.Count -gt 0) {
            throw "The installer leaked Blender console output on attempt $attempt.`n$($rawBlenderOutput -join "`n")"
        }
    }

    & $blender --background --python $fixture -- verify
    if ($LASTEXITCODE -ne 0) { throw "Blender preferences changed during installation." }
}
finally {
    $env:BLENDER_USER_CONFIG = $oldConfig
    $env:BLENDER_USER_SCRIPTS = $oldScripts
    $env:BLENDER_USER_EXTENSIONS = $oldExtensions
    $env:BLENDER_USER_DATAFILES = $oldDataFiles
    if (
        (Test-Path -LiteralPath $caseRoot -PathType Container) -and
        $caseRoot.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        Remove-Item -LiteralPath $caseRoot -Recurse -Force
    }
}
