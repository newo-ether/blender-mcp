#Requires -Version 5.1

<#
.SYNOPSIS
    One-command Windows installer for Blender MCP.

.DESCRIPTION
    This script installs the Python MCP server into a repository-local virtual
    environment, builds and installs the Blender Extension ZIP, and registers
    the server with Codex when the Codex CLI is available.

    It is safe to run repeatedly. Existing matching Codex configuration is
    preserved, and Blender treats a repeated Extension install as a reinstall.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    .\install.ps1 -DryRun

.EXAMPLE
    .\install.ps1 -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"

.EXAMPLE
    .\install.ps1 -SkipCodexRegistration
#>

[CmdletBinding()]
param(
    # Optional Blender 4.2+ executable. The newest Program Files installation is used by default.
    [string]$BlenderPath = "",

    # Optional Python 3.10+ executable used only when .venv does not exist.
    [string]$PythonPath = "",

    # Directory allowed for Geometry Nodes snapshot and patch JSON files.
    [string]$WorkspacePath = "",

    # Install only the Python MCP server; do not build or install the Blender Extension.
    [switch]$SkipBlenderExtension,

    # Do not add the MCP server to the Codex global user configuration.
    [switch]$SkipCodexRegistration,

    # Replace an existing, non-matching Codex entry named blender_mcp.
    [switch]$ForceCodexRegistration,

    # Show detected paths and commands without changing the machine.
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$script:StepNumber = 0
$script:DryRunEnabled = [bool]$DryRun
$script:CodexStatus = "not requested"
$script:BlenderStatus = "not requested"

function Write-Banner {
    Write-Host ""
    Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkCyan
    Write-Host "  |                    Blender MCP Installer                 |" -ForegroundColor Cyan
    Write-Host "  |          Server + Blender Extension + Codex setup        |" -ForegroundColor DarkCyan
    Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkCyan
    Write-Host ""
}

function Write-Step {
    param([string]$Title)
    $script:StepNumber += 1
    Write-Host ""
    Write-Host ("  [{0}] {1}" -f $script:StepNumber, $Title) -ForegroundColor Cyan
    Write-Host ("  " + ("-" * 58)) -ForegroundColor DarkGray
}

function Write-Info {
    param([string]$Message)
    Write-Host "      $Message" -ForegroundColor Gray
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-WarningLine {
    param([string]$Message)
    Write-Host "  [!]  $Message" -ForegroundColor Yellow
}

function Get-AbsolutePath {
    param(
        [string]$Path,
        [string]$BasePath
    )
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Format-CommandLine {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList
    )
    $parts = @($FilePath) + $ArgumentList
    return (($parts | ForEach-Object {
        $item = [string]$_
        if ($item -match '[\s"]') {
            '"' + ($item -replace '"', '\"') + '"'
        }
        else {
            $item
        }
    }) -join " ")
}

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$Description
    )
    $display = Format-CommandLine -FilePath $FilePath -ArgumentList $ArgumentList
    if ($script:DryRunEnabled) {
        Write-Info "Would run: $display"
        return
    }

    Write-Info $Description
    & $FilePath @ArgumentList
    $exitCode = $LASTEXITCODE
    if ($null -ne $exitCode -and $exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $display"
    }
}

function Get-BlenderExecutable {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolved = Get-AbsolutePath -Path $RequestedPath -BasePath (Get-Location).Path
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Blender executable was not found: $resolved"
        }
        return $resolved
    }

    $candidates = @()
    $command = Get-Command blender -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        $candidates += [PSCustomObject]@{
            Path = $command.Source
            Version = [version]"0.0"
        }
    }

    $programRoots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
        Select-Object -Unique

    foreach ($programRoot in $programRoots) {
        $pattern = Join-Path $programRoot "Blender Foundation\Blender *\blender.exe"
        foreach ($item in Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue) {
            $version = [version]"0.0"
            if ($item.Directory.Name -match '^Blender\s+([0-9]+(?:\.[0-9]+){0,2})') {
                $version = [version]$Matches[1]
            }
            $candidates += [PSCustomObject]@{
                Path = $item.FullName
                Version = $version
            }
        }
    }

    $selected = $candidates |
        Sort-Object -Property Version -Descending |
        Select-Object -First 1
    if ($null -eq $selected) {
        throw "Blender 4.2 or newer was not found. Install Blender or pass -BlenderPath."
    }
    return $selected.Path
}

function Get-BlenderVersion {
    param([string]$Executable)
    $output = & $Executable --factory-startup --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read Blender version from: $Executable"
    }
    $firstLine = [string]($output | Select-Object -First 1)
    if ($firstLine -notmatch 'Blender\s+([0-9]+(?:\.[0-9]+){1,2})') {
        throw "Could not parse Blender version from: $firstLine"
    }
    return [version]$Matches[1]
}

function Get-PythonLauncher {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolved = Get-AbsolutePath -Path $RequestedPath -BasePath (Get-Location).Path
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Python executable was not found: $resolved"
        }
        return [PSCustomObject]@{ Command = $resolved; Prefix = @() }
    }

    $py = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        return [PSCustomObject]@{ Command = $py.Source; Prefix = @("-3") }
    }

    $python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        return [PSCustomObject]@{ Command = $python.Source; Prefix = @() }
    }

    throw "Python 3.10 or newer was not found. Install Python and run this script again."
}

function Test-PythonLauncher {
    param($Launcher)
    $arguments = @($Launcher.Prefix) + @(
        "-c",
        "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'; print(sys.version.split()[0])"
    )
    $version = & $Launcher.Command @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "The selected Python must be version 3.10 or newer."
    }
    return [string]($version | Select-Object -Last 1)
}

function Get-JsonProperty {
    param(
        $Object,
        [string]$Name
    )
    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Test-SamePath {
    param(
        [string]$Left,
        [string]$Right
    )
    if (-not $Left -or -not $Right) {
        return $false
    }
    try {
        $leftFull = [System.IO.Path]::GetFullPath($Left)
        $rightFull = [System.IO.Path]::GetFullPath($Right)
        return [string]::Equals(
            $leftFull,
            $rightFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    }
    catch {
        return $false
    }
}

function Register-CodexMcp {
    param(
        [string]$CodexExecutable,
        [string]$ServerExecutable,
        [string]$Workspace,
        [int]$Port,
        [bool]$Force
    )

    $existing = $null
    $existingJson = & $CodexExecutable mcp get blender_mcp --json 2>$null
    if ($LASTEXITCODE -eq 0 -and $existingJson) {
        try {
            $existing = $existingJson | ConvertFrom-Json
        }
        catch {
            Write-WarningLine "Codex returned an unreadable existing blender_mcp configuration."
        }
    }

    $matches = $false
    if ($null -ne $existing) {
        $transport = Get-JsonProperty -Object $existing -Name "transport"
        $envBlock = Get-JsonProperty -Object $transport -Name "env"
        $command = [string](Get-JsonProperty -Object $transport -Name "command")
        $configuredWorkspace = [string](Get-JsonProperty -Object $envBlock -Name "BLENDER_MCP_WORKSPACE")
        $configuredHost = [string](Get-JsonProperty -Object $envBlock -Name "BLENDER_HOST")
        $configuredPort = [string](Get-JsonProperty -Object $envBlock -Name "BLENDER_PORT")
        $matches = (
            (Test-SamePath -Left $command -Right $ServerExecutable) -and
            (Test-SamePath -Left $configuredWorkspace -Right $Workspace) -and
            $configuredHost -eq "localhost" -and
            $configuredPort -eq [string]$Port
        )
    }

    if ($matches) {
        Write-Ok "Codex already has the matching blender_mcp configuration."
        $script:CodexStatus = "already configured"
        return
    }

    if ($null -ne $existing -and -not $Force) {
        Write-WarningLine "Codex already has a different blender_mcp entry; it was preserved."
        Write-Info "Re-run with -ForceCodexRegistration to replace that entry."
        $script:CodexStatus = "existing different entry preserved"
        return
    }

    if ($null -ne $existing) {
        Invoke-CheckedCommand -FilePath $CodexExecutable -ArgumentList @(
            "mcp", "remove", "blender_mcp"
        ) -Description "Removing the previous Codex blender_mcp entry..."
    }

    Invoke-CheckedCommand -FilePath $CodexExecutable -ArgumentList @(
        "mcp", "add", "blender_mcp",
        "--env", "BLENDER_MCP_WORKSPACE=$Workspace",
        "--env", "BLENDER_HOST=localhost",
        "--env", "BLENDER_PORT=$Port",
        "--", $ServerExecutable
    ) -Description "Registering blender_mcp with Codex..."

    if ($script:DryRunEnabled) {
        $script:CodexStatus = "would be configured"
    }
    else {
        Write-Ok "Codex global MCP entry blender_mcp is configured."
        $script:CodexStatus = "configured"
    }
}

try {
    Write-Banner

    $repoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "pyproject.toml"))) {
        throw "Run the installer from a complete Blender MCP repository checkout."
    }

    if (-not $WorkspacePath) {
        $WorkspacePath = $repoRoot
    }
    $workspace = Get-AbsolutePath -Path $WorkspacePath -BasePath $repoRoot
    $blenderPort = 9876
    $venvRoot = Join-Path $repoRoot ".venv"
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    $serverExecutable = Join-Path $venvRoot "Scripts\blender-mcp.exe"

    Write-Step "Installation plan"
    Write-Info "Repository : $repoRoot"
    Write-Info "Workspace  : $workspace"
    Write-Info "MCP port   : $blenderPort"
    if ($script:DryRunEnabled) {
        Write-WarningLine "Dry-run mode is active; no machine state will be changed."
    }

    if (-not (Test-Path -LiteralPath $workspace -PathType Container)) {
        if ($script:DryRunEnabled) {
            Write-Info "Would create workspace directory: $workspace"
        }
        else {
            New-Item -ItemType Directory -Path $workspace -Force | Out-Null
            Write-Ok "Created workspace directory."
        }
    }

    Write-Step "Python environment"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $pythonVersion = & $venvPython -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'; print(sys.version.split()[0])" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "The existing .venv must contain a working Python 3.10 or newer: $venvPython"
        }
        Write-Ok "Reusing .venv with Python $pythonVersion"
    }
    else {
        $launcher = Get-PythonLauncher -RequestedPath $PythonPath
        $pythonVersion = Test-PythonLauncher -Launcher $launcher
        Write-Ok "Found Python $pythonVersion"
        $venvArguments = @($launcher.Prefix) + @("-m", "venv", $venvRoot)
        Invoke-CheckedCommand -FilePath $launcher.Command -ArgumentList $venvArguments `
            -Description "Creating repository-local .venv..."
    }

    Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
        "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
        "--editable", $repoRoot
    ) -Description "Installing Blender MCP and Python dependencies..."

    Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
        "-c",
        "import asyncio; from blender_mcp.server import mcp; tools = asyncio.run(mcp.list_tools()); print(f'Registered MCP tools: {len(tools)}'); assert len(tools) >= 28"
    ) -Description "Verifying MCP imports and tool registration..."
    if (-not $script:DryRunEnabled) {
        if (-not (Test-Path -LiteralPath $serverExecutable -PathType Leaf)) {
            throw "MCP console executable was not installed: $serverExecutable"
        }
        Write-Ok "Python MCP server is ready."
    }

    $archivePath = $null
    if (-not $SkipBlenderExtension) {
        Write-Step "Blender Extension"
        $blenderExecutable = Get-BlenderExecutable -RequestedPath $BlenderPath
        $blenderVersion = Get-BlenderVersion -Executable $blenderExecutable
        if ($blenderVersion -lt [version]"4.2") {
            throw "Blender $blenderVersion is too old for the Extension package; Blender 4.2+ is required."
        }
        Write-Ok "Found Blender $blenderVersion"
        Write-Info "Executable : $blenderExecutable"

        $manifest = Get-Content -LiteralPath (
            Join-Path $repoRoot "packaging\blender_extension\blender_manifest.toml"
        ) -Raw
        if ($manifest -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
            throw "Could not read the Extension version from blender_manifest.toml."
        }
        $archivePath = Join-Path $repoRoot ("dist\blender_mcp-{0}.zip" -f $Matches[1])

        Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
            (Join-Path $repoRoot "scripts\build_blender_extension.py"),
            "--blender", $blenderExecutable
        ) -Description "Building and validating the installable ZIP..."

        if (-not $script:DryRunEnabled -and -not (
            Test-Path -LiteralPath $archivePath -PathType Leaf
        )) {
            throw "The Extension archive was not created: $archivePath"
        }

        Invoke-CheckedCommand -FilePath $blenderExecutable -ArgumentList @(
            "--factory-startup", "--command", "extension", "install-file",
            "-r", "user_default", "-e", $archivePath
        ) -Description "Installing and enabling Blender MCP in Blender $blenderVersion..."

        if ($script:DryRunEnabled) {
            $script:BlenderStatus = "would build and install"
        }
        else {
            Write-Ok "Blender Extension installed and enabled."
            $script:BlenderStatus = "installed for Blender $blenderVersion"
        }
    }
    else {
        Write-Step "Blender Extension"
        Write-WarningLine "Skipped by -SkipBlenderExtension."
        $script:BlenderStatus = "skipped"
    }

    Write-Step "Codex MCP registration"
    if ($SkipCodexRegistration) {
        Write-WarningLine "Skipped by -SkipCodexRegistration."
        $script:CodexStatus = "skipped"
    }
    else {
        $codex = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue
        if ($null -eq $codex) {
            Write-WarningLine "Codex CLI was not found; server installation is still complete."
            Write-Info "Install Codex, then run this script again to register the MCP server."
            $script:CodexStatus = "Codex CLI not found"
        }
        else {
            Register-CodexMcp -CodexExecutable $codex.Source `
                -ServerExecutable $serverExecutable `
                -Workspace $workspace `
                -Port $blenderPort `
                -Force ([bool]$ForceCodexRegistration)
        }
    }

    Write-Step "Finished"
    Write-Host ""
    if ($script:DryRunEnabled) {
        Write-Host "  Dry run completed successfully." -ForegroundColor Green
    }
    else {
        Write-Host "  Blender MCP installation completed successfully." -ForegroundColor Green
    }
    Write-Info "Server  : $serverExecutable"
    Write-Info "Blender : $script:BlenderStatus"
    Write-Info "Codex   : $script:CodexStatus"
    if ($archivePath) {
        Write-Info "ZIP     : $archivePath"
    }
    Write-Host ""
    Write-Host "  Next steps" -ForegroundColor Cyan
    Write-Info "1. Open Blender and find BlenderMCP in the 3D View sidebar (N)."
    Write-Info "2. Click Connect to Claude to start the local bridge on port $blenderPort."
    Write-Info "3. Restart Codex or open a new Codex session to load the MCP tools."
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "  Installation failed" -ForegroundColor Red
    Write-Host "  -------------------" -ForegroundColor DarkRed
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    Write-Host "  Tip: run .\install.ps1 -DryRun to inspect detection and commands." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
