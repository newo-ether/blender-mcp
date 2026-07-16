#Requires -Version 5.1

param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

# The production entry point owns these parameters. Contract tests import the
# implementation directly so they can exercise bounded functions without
# starting a full machine installation.
$script:DryRun = $false
$script:Language = "en-US"
$script:PreserveExistingMcpEntries = $false
$script:ForceCodexRegistration = $false
$script:InstallerEntryRoot = [System.IO.Path]::GetFullPath($Root)

$moduleRoot = Join-Path $script:InstallerEntryRoot "scripts\installer"
foreach ($moduleFile in @(
    "common.ps1",
    "release.ps1",
    "skills.ps1",
    "discovery.ps1",
    "targets.ps1",
    "clients.ps1",
    "codex-config.ps1",
    "install-main.ps1"
)) {
    $modulePath = Join-Path $moduleRoot $moduleFile
    if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
        throw "Installer test module is missing: $modulePath"
    }
    . $modulePath
}
