#Requires -Version 5.1

<#
.SYNOPSIS
    One-command Windows installer for Blender MCP.

.DESCRIPTION
    When executed directly from GitHub Raw, this script downloads the latest
    checksummed release assets, installs the Python MCP server into a stable
    per-user virtual environment, and installs the Blender Extension.

    When executed from a repository checkout, it builds and installs the local
    source instead. Use -UseRelease to test the published release path locally.

    The installer configures Codex CLI/Desktop and Claude Code when their CLIs
    are available. Claude Desktop uses the official MCPB package and keeps its
    required user confirmation.

    It is safe to run repeatedly. Matching Codex entries are retained, managed
    user entries named blender_mcp are updated by default, and Blender treats
    a repeated Extension install as a reinstall.

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
    # Optional Blender 4.2+ executable. The newest Program Files installation is used by default.
    [string[]]$BlenderPath = @(),

    # Optional Python 3.10+ executable used only when .venv does not exist.
    [string]$PythonPath = "",

    # Directory allowed for Geometry Nodes snapshot and patch JSON files.
    [string]$WorkspacePath = "",

    # GitHub repository used for release discovery.
    [string]$Repository = "newo-ether/blender-mcp",

    # Optional exact GitHub Release tag. Empty means the latest stable release.
    [string]$ReleaseTag = "",

    # Stable per-user install directory for release mode.
    [string]$InstallRoot = "",

    # Use GitHub Release assets even when running from a source checkout.
    [switch]$UseRelease,

    # Install only the Python MCP server; do not build or install the Blender Extension.
    [switch]$SkipBlenderExtension,

    # Do not add the MCP server to the Codex global user configuration.
    [switch]$SkipCodexRegistration,

    # Keep an existing, non-matching blender_mcp entry instead of updating it.
    [switch]$PreserveExistingMcpEntries,

    # Deprecated compatibility switch. Replacement is now the default behavior.
    [switch]$ForceCodexRegistration,

    # Do not add the MCP server to Claude Code's user scope.
    [switch]$SkipClaudeCodeRegistration,

    # Do not download or open the Claude Desktop MCPB package.
    [switch]$SkipClaudeDesktop,

    # Show detected paths and commands without changing the machine.
    [switch]$DryRun,

    # Use the graphical checkbox selector instead of the default terminal UI.
    [switch]$Gui,

    # Skip the interactive target selector and use detected/default targets.
    [switch]$NonInteractive
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$script:StepNumber = 0
$script:DryRunEnabled = [bool]$DryRun
$script:CodexStatus = "not requested"
$script:ClaudeCodeStatus = "not requested"
$script:ClaudeDesktopStatus = "not requested"
$script:BlenderStatus = "not requested"
$script:SelectedCodexCli = $false
$script:SelectedCodexDesktop = $false
$script:SelectedClaudeCode = $false
$script:SelectedClaudeDesktop = $false

if ($PreserveExistingMcpEntries -and $ForceCodexRegistration) {
    throw "-PreserveExistingMcpEntries and -ForceCodexRegistration cannot be used together."
}

function Write-Banner {
    Write-Host ""
    Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkCyan
    Write-Host "  |                    Blender MCP Installer                 |" -ForegroundColor Cyan
    Write-Host "  |       Server + Blender Extension + MCP client setup      |" -ForegroundColor DarkCyan
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
        [string]$Description,
        [switch]$Quiet
    )
    $display = Format-CommandLine -FilePath $FilePath -ArgumentList $ArgumentList
    if ($script:DryRunEnabled) {
        Write-Info "Would run: $display"
        return
    }

    Write-Info $Description
    $capturedOutput = @()
    if ($Quiet) {
        # Blender and enabled third-party extensions may print directly to both
        # stdout and stderr even for a successful command. Keep the installer
        # readable, but retain that output so failures remain diagnosable.
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $capturedOutput = @(& $FilePath @ArgumentList 2>&1)
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
    }
    else {
        & $FilePath @ArgumentList
        $exitCode = $LASTEXITCODE
    }
    if ($null -ne $exitCode -and $exitCode -ne 0) {
        $message = "Command failed with exit code ${exitCode}: $display"
        if ($Quiet -and $capturedOutput.Count -gt 0) {
            $outputLimit = 80
            $outputLines = @($capturedOutput | ForEach-Object { [string]$_ })
            if ($outputLines.Count -gt $outputLimit) {
                $omitted = $outputLines.Count - $outputLimit
                $outputLines = @("... $omitted earlier output line(s) omitted ...") + @(
                    $outputLines | Select-Object -Last $outputLimit
                )
            }
            $message += "`nCaptured command output:`n$($outputLines -join "`n")"
        }
        throw $message
    }
}

function Get-GitHubRelease {
    param(
        [string]$Repo,
        [string]$Tag
    )
    if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        throw "GitHub repository must use the owner/name form: $Repo"
    }
    $headers = @{
        Accept = "application/vnd.github+json"
        "User-Agent" = "blender-mcp-installer"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    if ($env:GITHUB_TOKEN) {
        $headers.Authorization = "Bearer $($env:GITHUB_TOKEN)"
    }
    if ($Tag) {
        $encodedTag = [System.Uri]::EscapeDataString($Tag)
        $url = "https://api.github.com/repos/$Repo/releases/tags/$encodedTag"
    }
    else {
        $url = "https://api.github.com/repos/$Repo/releases/latest"
    }
    Write-Info "Querying GitHub Release: $url"
    try {
        $release = Invoke-RestMethod -Uri $url -Headers $headers -UseBasicParsing
    }
    catch {
        throw "GitHub Release discovery failed for $Repo. Check the repository, release tag, network, or API rate limit. $($_.Exception.Message)"
    }
    $tagNameProperty = $release.PSObject.Properties["tag_name"]
    $assetsProperty = $release.PSObject.Properties["assets"]
    if ($null -eq $tagNameProperty -or -not $tagNameProperty.Value -or $null -eq $assetsProperty) {
        throw "GitHub returned an incomplete Release response for $Repo."
    }
    return $release
}

function Get-ReleaseAsset {
    param(
        $Release,
        [string]$Pattern,
        [string]$Purpose
    )
    $matches = @($Release.assets | Where-Object { $_.name -like $Pattern })
    if ($matches.Count -ne 1) {
        throw "Expected one $Purpose asset matching '$Pattern'; found $($matches.Count)."
    }
    return $matches[0]
}

function Save-ReleaseAsset {
    param(
        $Asset,
        [string]$Directory
    )
    $destination = Join-Path $Directory ([string]$Asset.name)
    if ($script:DryRunEnabled) {
        Write-Info "Would download: $($Asset.browser_download_url)"
        return $destination
    }
    $temporaryPath = "$destination.download"
    for ($attempt = 1; $attempt -le 3; $attempt += 1) {
        try {
            Write-Info "Downloading $($Asset.name) (attempt $attempt/3)..."
            Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $temporaryPath -UseBasicParsing -Headers @{
                "User-Agent" = "blender-mcp-installer"
            }
            Move-Item -LiteralPath $temporaryPath -Destination $destination -Force
            return $destination
        }
        catch {
            if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
                Remove-Item -LiteralPath $temporaryPath -Force
            }
            if ($attempt -eq 3) {
                throw "Could not download $($Asset.name) after 3 attempts: $($_.Exception.Message)"
            }
            Write-WarningLine "Download attempt $attempt failed; retrying."
            Start-Sleep -Seconds $attempt
        }
    }
}

function Test-ReleaseChecksums {
    param(
        [string]$ChecksumPath,
        [string[]]$AssetPaths
    )
    if ($script:DryRunEnabled) {
        Write-Info "Would verify SHA-256 for all release assets."
        return
    }

    $expected = @{}
    foreach ($rawLine in Get-Content -LiteralPath $ChecksumPath) {
        $line = ([string]$rawLine).TrimStart([char]0xFEFF)
        if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+)$') {
            $expected[$Matches[2].Trim()] = $Matches[1].ToLowerInvariant()
        }
    }
    foreach ($assetPath in $AssetPaths) {
        $name = Split-Path -Leaf $assetPath
        if (-not $expected.ContainsKey($name)) {
            throw "SHA256SUMS.txt does not contain $name."
        }
        $actual = (Get-FileHash -LiteralPath $assetPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected[$name]) {
            throw "SHA-256 verification failed for $name."
        }
        Write-Ok "Verified $name"
    }
}

function Get-BlenderInstallations {
    param([string[]]$RequestedPaths)

    $candidatePaths = @()
    foreach ($requested in @($RequestedPaths)) {
        if (-not $requested) { continue }
        try {
            $resolved = Get-AbsolutePath -Path $requested -BasePath (Get-Location).Path
        }
        catch {
            throw "Invalid Blender executable path '$requested': $($_.Exception.Message)"
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Blender executable was not found: $resolved"
        }
        $candidatePaths += $resolved
    }

    if ($candidatePaths.Count -eq 0) {
        $command = Get-Command blender -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            $candidatePaths += $command.Source
        }

        $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
        $programRoots = @($env:ProgramFiles, $programFilesX86) |
            Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
            Select-Object -Unique
        foreach ($programRoot in $programRoots) {
            $pattern = Join-Path $programRoot "Blender Foundation\Blender *\blender.exe"
            $candidatePaths += @(
                Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue |
                    ForEach-Object { $_.FullName }
            )
        }

        $appPathKeys = @(
            "HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\blender.exe",
            "HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\blender.exe"
        )
        foreach ($registryKey in $appPathKeys) {
            $item = Get-Item -LiteralPath $registryKey -ErrorAction SilentlyContinue
            if ($null -ne $item) {
                $candidatePaths += [string]$item.GetValue("")
            }
        }

        $uninstallRoots = @(
            "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
        )
        foreach ($entry in Get-ItemProperty $uninstallRoots -ErrorAction SilentlyContinue) {
            $displayNameProperty = $entry.PSObject.Properties["DisplayName"]
            if ($null -eq $displayNameProperty -or [string]$displayNameProperty.Value -notmatch '^Blender(?:\s|$)') { continue }
            $installLocationProperty = $entry.PSObject.Properties["InstallLocation"]
            if ($null -ne $installLocationProperty -and $installLocationProperty.Value) {
                $candidatePaths += Join-Path ([string]$installLocationProperty.Value) "blender.exe"
            }
            $displayIconProperty = $entry.PSObject.Properties["DisplayIcon"]
            if ($null -ne $displayIconProperty -and $displayIconProperty.Value) {
                $candidatePaths += ([string]$displayIconProperty.Value -replace ',\d+$', '').Trim('"')
            }
        }

        if ($programFilesX86) {
            $candidatePaths += Join-Path $programFilesX86 "Steam\steamapps\common\Blender\blender.exe"
        }
    }

    $seen = @{}
    $installations = @()
    foreach ($candidate in $candidatePaths) {
        if (-not $candidate -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        $key = $resolved.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        try {
            $version = Get-BlenderVersion -Executable $resolved
            $installations += [PSCustomObject]@{
                Name = "Blender $version"
                Path = $resolved
                Version = $version
                Supported = ($version -ge [version]"4.2")
            }
        }
        catch {
            Write-WarningLine (
                "Ignoring an unreadable Blender candidate: {0} ({1})" -f
                $resolved, $_.Exception.Message
            )
        }
    }
    return @($installations | Sort-Object -Property Version -Descending)
}

function Get-BlenderVersion {
    param([string]$Executable)

    # Capture native stdout/stderr independently. PowerShell 7 can promote any
    # native stderr line to a terminating ErrorRecord under ErrorAction=Stop,
    # even when Blender exits successfully and prints a valid version to
    # stdout (for example a benign TBB allocator warning in portable 4.2).
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Executable
    $startInfo.Arguments = "--factory-startup --version"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "Could not start Blender: $Executable"
        }
        $standardOutput = $process.StandardOutput.ReadToEnd()
        $standardError = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw "Could not read Blender version from: $Executable (exit $($process.ExitCode))"
        }
    }
    finally {
        $process.Dispose()
    }
    $output = @($standardOutput -split "`r?`n") + @($standardError -split "`r?`n")
    $versionLine = @($output | ForEach-Object { [string]$_ }) |
        Where-Object { $_ -match 'Blender\s+[0-9]+(?:\.[0-9]+){1,2}' } |
        Select-Object -First 1
    if (-not $versionLine -or $versionLine -notmatch 'Blender\s+([0-9]+(?:\.[0-9]+){1,2})') {
        $summary = (@($output | Select-Object -First 3) -join ' | ')
        throw "Could not parse Blender version from: $summary"
    }
    return [version]$Matches[1]
}

function Find-DesktopApplication {
    param(
        [string]$NamePattern,
        [string[]]$KnownPaths
    )

    $evidence = @()
    foreach ($path in @($KnownPaths)) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            $evidence += $path
        }
    }

    $getStartApps = Get-Command Get-StartApps -ErrorAction SilentlyContinue
    if ($null -ne $getStartApps) {
        foreach ($app in Get-StartApps -ErrorAction SilentlyContinue | Where-Object { $_.Name -match $NamePattern }) {
            $evidence += "Start menu: $($app.Name)"
        }
    }

    $getAppx = Get-Command Get-AppxPackage -ErrorAction SilentlyContinue
    if ($null -ne $getAppx) {
        foreach ($package in Get-AppxPackage -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match $NamePattern -or $_.PackageFullName -match $NamePattern
        }) {
            $evidence += "AppX: $($package.Name)"
        }
    }

    $uninstallRoots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($entry in Get-ItemProperty $uninstallRoots -ErrorAction SilentlyContinue) {
        $displayNameProperty = $entry.PSObject.Properties["DisplayName"]
        if ($null -ne $displayNameProperty -and [string]$displayNameProperty.Value -match $NamePattern) {
            $evidence += "Installed app: $($displayNameProperty.Value)"
        }
    }

    $unique = @($evidence | Select-Object -Unique)
    return [PSCustomObject]@{
        Found = ($unique.Count -gt 0)
        Evidence = if ($unique.Count) { $unique[0] } else { "not detected" }
    }
}

function Get-ClientDetection {
    $codex = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $codexCommand = if ($null -ne $codex) { [string]$codex.Source } else { $null }
    if (-not $codexCommand) {
        $bundledCodexCandidates = @(
            (Join-Path $env:LOCALAPPDATA "Programs\OpenAI\Codex\bin\codex.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\OpenAI\ChatGPT\bin\codex.exe"),
            (Join-Path $env:LOCALAPPDATA "OpenAI\ChatGPT\bin\codex.exe")
        )
        foreach ($bundledCodex in $bundledCodexCandidates) {
            if (Test-Path -LiteralPath $bundledCodex -PathType Leaf) {
                $codexCommand = $bundledCodex
                break
            }
        }
    }
    $claude = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $claudeCommand = if ($null -ne $claude) { [string]$claude.Source } else { $null }

    $codexDesktop = Find-DesktopApplication -NamePattern '^ChatGPT$|^Codex$|OpenAI\.ChatGPT|OpenAI\.Codex|ChatGPT Desktop|Codex Desktop' -KnownPaths @(
        (Join-Path $env:LOCALAPPDATA "Programs\OpenAI\ChatGPT\ChatGPT.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\OpenAI\Codex\Codex.exe")
    )
    $claudeDesktop = Find-DesktopApplication -NamePattern '^Claude$|^Claude Desktop$|AnthropicClaude|com\.anthropic\.claude' -KnownPaths @(
        (Join-Path $env:LOCALAPPDATA "Programs\Claude\Claude.exe"),
        (Join-Path $env:LOCALAPPDATA "AnthropicClaude\Claude.exe")
    )

    return [PSCustomObject]@{
        CodexCommand = $codexCommand
        CodexCliFound = [bool]$codexCommand
        CodexDesktopFound = [bool]$codexDesktop.Found
        CodexDesktopEvidence = [string]$codexDesktop.Evidence
        ClaudeCommand = $claudeCommand
        ClaudeCodeFound = [bool]$claudeCommand
        ClaudeDesktopFound = [bool]$claudeDesktop.Found
        ClaudeDesktopEvidence = [string]$claudeDesktop.Evidence
    }
}

function Select-InstallTargetsTui {
    param(
        $Detection,
        [object[]]$BlenderInstallations,
        [bool]$DisableBlender,
        [bool]$DisableCodex,
        [bool]$DisableClaudeCode,
        [bool]$DisableClaudeDesktop
    )

    $entries = @()
    $entries += [PSCustomObject]@{
        Group = "MCP clients"
        Label = if ($Detection.CodexCliFound) { "Codex CLI - $($Detection.CodexCommand)" } else { "Codex CLI - not detected" }
        Enabled = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        Selected = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        Kind = "CodexCli"
        Value = $null
    }
    $entries += [PSCustomObject]@{
        Group = "MCP clients"
        Label = "Codex Desktop (ChatGPT) - $($Detection.CodexDesktopEvidence) - shares Codex MCP config"
        Enabled = [bool]($Detection.CodexDesktopFound -and $Detection.CodexCliFound -and -not $DisableCodex)
        Selected = [bool]($Detection.CodexDesktopFound -and $Detection.CodexCliFound -and -not $DisableCodex)
        Kind = "CodexDesktop"
        Value = $null
    }
    $entries += [PSCustomObject]@{
        Group = "MCP clients"
        Label = if ($Detection.ClaudeCodeFound) { "Claude Code CLI - $($Detection.ClaudeCommand)" } else { "Claude Code CLI - not detected" }
        Enabled = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        Selected = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        Kind = "ClaudeCode"
        Value = $null
    }
    $entries += [PSCustomObject]@{
        Group = "MCP clients"
        Label = "Claude Desktop - $($Detection.ClaudeDesktopEvidence) - opens an MCPB for confirmation"
        Enabled = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        Selected = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        Kind = "ClaudeDesktop"
        Value = $null
    }

    $supportedIndex = 0
    foreach ($blender in $BlenderInstallations) {
        $supportText = if ($blender.Supported) { "supported" } else { "requires Blender 4.2+" }
        $entries += [PSCustomObject]@{
            Group = "Blender installations"
            Label = "$($blender.Name) - $supportText - $($blender.Path)"
            Enabled = [bool]($blender.Supported -and -not $DisableBlender)
            Selected = [bool]($blender.Supported -and $supportedIndex -eq 0 -and -not $DisableBlender)
            Kind = "Blender"
            Value = [string]$blender.Path
        }
        if ($blender.Supported) { $supportedIndex += 1 }
    }
    if ($BlenderInstallations.Count -eq 0) {
        $entries += [PSCustomObject]@{
            Group = "Blender installations"
            Label = "No Blender detected - install Blender 4.2+ or use -BlenderPath"
            Enabled = $false
            Selected = $false
            Kind = "BlenderMissing"
            Value = $null
        }
    }

    $cursor = 0
    for ($index = 0; $index -lt $entries.Count; $index += 1) {
        if ($entries[$index].Enabled) {
            $cursor = $index
            break
        }
    }

    try {
        [Console]::CursorVisible = $false
        while ($true) {
            [Console]::Clear()
            Write-Host "Blender MCP - Select installation targets" -ForegroundColor Cyan
            Write-Host "Use Up/Down to move, Space to toggle, A to toggle all, Enter to install, Esc to cancel." -ForegroundColor DarkGray
            Write-Host "The Python MCP server is always installed." -ForegroundColor DarkGray

            $currentGroup = ""
            for ($index = 0; $index -lt $entries.Count; $index += 1) {
                $entry = $entries[$index]
                if ($entry.Group -ne $currentGroup) {
                    $currentGroup = $entry.Group
                    Write-Host ""
                    Write-Host $currentGroup -ForegroundColor Yellow
                }
                $pointer = if ($index -eq $cursor) { ">" } else { " " }
                $box = if ($entry.Selected) { "[x]" } else { "[ ]" }
                if ($entry.Enabled) {
                    $color = if ($index -eq $cursor) { "White" } else { "Gray" }
                }
                else {
                    $color = "DarkGray"
                }
                Write-Host ("  {0} {1} {2}" -f $pointer, $box, $entry.Label) -ForegroundColor $color
            }

            $key = [Console]::ReadKey($true)
            if ($key.Key -eq [ConsoleKey]::Enter) {
                return [PSCustomObject]@{
                    Cancelled = $false
                    CodexCli = [bool](@($entries | Where-Object { $_.Kind -eq "CodexCli" -and $_.Selected }).Count)
                    CodexDesktop = [bool](@($entries | Where-Object { $_.Kind -eq "CodexDesktop" -and $_.Selected }).Count)
                    ClaudeCode = [bool](@($entries | Where-Object { $_.Kind -eq "ClaudeCode" -and $_.Selected }).Count)
                    ClaudeDesktop = [bool](@($entries | Where-Object { $_.Kind -eq "ClaudeDesktop" -and $_.Selected }).Count)
                    BlenderPaths = @($entries | Where-Object { $_.Kind -eq "Blender" -and $_.Selected } | ForEach-Object { $_.Value })
                }
            }
            if ($key.Key -eq [ConsoleKey]::Escape -or $key.Key -eq [ConsoleKey]::Q) {
                return [PSCustomObject]@{ Cancelled = $true }
            }
            if ($key.Key -eq [ConsoleKey]::Spacebar -and $entries[$cursor].Enabled) {
                $entries[$cursor].Selected = -not $entries[$cursor].Selected
                continue
            }
            if ($key.Key -eq [ConsoleKey]::A) {
                $enabledEntries = @($entries | Where-Object { $_.Enabled })
                $selectAll = [bool](@($enabledEntries | Where-Object { -not $_.Selected }).Count)
                foreach ($entry in $enabledEntries) { $entry.Selected = $selectAll }
                continue
            }
            $direction = 0
            if ($key.Key -eq [ConsoleKey]::UpArrow) { $direction = -1 }
            if ($key.Key -eq [ConsoleKey]::DownArrow) { $direction = 1 }
            if ($direction -ne 0) {
                for ($offset = 1; $offset -le $entries.Count; $offset += 1) {
                    $candidate = ($cursor + ($direction * $offset) + ($entries.Count * 2)) % $entries.Count
                    if ($entries[$candidate].Enabled) {
                        $cursor = $candidate
                        break
                    }
                }
            }
        }
    }
    finally {
        try { [Console]::CursorVisible = $true } catch {}
        try { [Console]::Clear() } catch {}
    }
}

function Select-InstallTargets {
    param(
        $Detection,
        [object[]]$BlenderInstallations,
        [bool]$NoGui,
        [bool]$UseGui = $false,
        [bool]$DisableBlender = $false,
        [bool]$DisableCodex = $false,
        [bool]$DisableClaudeCode = $false,
        [bool]$DisableClaudeDesktop = $false
    )

    $supportedBlenders = @($BlenderInstallations | Where-Object { $_.Supported })
    if ($NoGui) {
        return [PSCustomObject]@{
            Cancelled = $false
            CodexCli = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
            CodexDesktop = [bool]($Detection.CodexDesktopFound -and -not $DisableCodex)
            ClaudeCode = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
            ClaudeDesktop = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
            BlenderPaths = if ($supportedBlenders.Count -and -not $DisableBlender) { @($supportedBlenders[0].Path) } else { @() }
        }
    }

    if (-not $UseGui) {
        try {
            return Select-InstallTargetsTui -Detection $Detection -BlenderInstallations $BlenderInstallations -DisableBlender $DisableBlender -DisableCodex $DisableCodex -DisableClaudeCode $DisableClaudeCode -DisableClaudeDesktop $DisableClaudeDesktop
        }
        catch {
            Write-WarningLine "Terminal selector unavailable; trying the graphical selector."
            Write-Info $_.Exception.Message
            return Select-InstallTargets -Detection $Detection -BlenderInstallations $BlenderInstallations -NoGui $false -UseGui $true -DisableBlender $DisableBlender -DisableCodex $DisableCodex -DisableClaudeCode $DisableClaudeCode -DisableClaudeDesktop $DisableClaudeDesktop
        }
    }

    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing

        $form = New-Object System.Windows.Forms.Form
        $form.Text = "Blender MCP - Select installation targets"
        $form.StartPosition = "CenterScreen"
        $form.Size = New-Object System.Drawing.Size(780, 650)
        $form.MinimumSize = New-Object System.Drawing.Size(700, 560)
        $form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

        $title = New-Object System.Windows.Forms.Label
        $title.Text = "Choose where Blender MCP should be installed"
        $title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 15)
        $title.AutoSize = $true
        $title.Location = New-Object System.Drawing.Point(22, 18)
        $form.Controls.Add($title)

        $subtitle = New-Object System.Windows.Forms.Label
        $subtitle.Text = "Detected targets are selected by default. Codex CLI and ChatGPT share one MCP configuration."
        $subtitle.AutoSize = $true
        $subtitle.ForeColor = [System.Drawing.Color]::DimGray
        $subtitle.Location = New-Object System.Drawing.Point(25, 54)
        $form.Controls.Add($subtitle)

        $clientGroup = New-Object System.Windows.Forms.GroupBox
        $clientGroup.Text = "MCP clients"
        $clientGroup.Location = New-Object System.Drawing.Point(22, 84)
        $clientGroup.Size = New-Object System.Drawing.Size(720, 205)
        $clientGroup.Anchor = "Top,Left,Right"
        $form.Controls.Add($clientGroup)

        $codexCliCheck = New-Object System.Windows.Forms.CheckBox
        $codexCliCheck.Text = if ($Detection.CodexCliFound) {
            "Codex CLI - detected: $($Detection.CodexCommand)"
        } else { "Codex CLI - not detected" }
        $codexCliCheck.Checked = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        $codexCliCheck.Enabled = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        $codexCliCheck.AutoSize = $true
        $codexCliCheck.Location = New-Object System.Drawing.Point(18, 30)
        $clientGroup.Controls.Add($codexCliCheck)

        $codexDesktopCheck = New-Object System.Windows.Forms.CheckBox
        $codexDesktopCheck.Text = "Codex Desktop (ChatGPT) - $($Detection.CodexDesktopEvidence) - shares Codex MCP config"
        $codexDesktopCheck.Checked = [bool]($Detection.CodexDesktopFound -and $Detection.CodexCliFound -and -not $DisableCodex)
        $codexDesktopCheck.Enabled = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        $codexDesktopCheck.AutoSize = $true
        $codexDesktopCheck.Location = New-Object System.Drawing.Point(18, 70)
        $clientGroup.Controls.Add($codexDesktopCheck)

        $claudeCodeCheck = New-Object System.Windows.Forms.CheckBox
        $claudeCodeCheck.Text = if ($Detection.ClaudeCodeFound) {
            "Claude Code CLI - detected: $($Detection.ClaudeCommand)"
        } else { "Claude Code CLI - not detected" }
        $claudeCodeCheck.Checked = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        $claudeCodeCheck.Enabled = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        $claudeCodeCheck.AutoSize = $true
        $claudeCodeCheck.Location = New-Object System.Drawing.Point(18, 110)
        $clientGroup.Controls.Add($claudeCodeCheck)

        $claudeDesktopCheck = New-Object System.Windows.Forms.CheckBox
        $claudeDesktopCheck.Text = "Claude Desktop - $($Detection.ClaudeDesktopEvidence) - opens an MCPB for confirmation"
        $claudeDesktopCheck.Checked = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        $claudeDesktopCheck.Enabled = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        $claudeDesktopCheck.AutoSize = $true
        $claudeDesktopCheck.Location = New-Object System.Drawing.Point(18, 150)
        $clientGroup.Controls.Add($claudeDesktopCheck)

        $blenderGroup = New-Object System.Windows.Forms.GroupBox
        $blenderGroup.Text = "Blender installations"
        $blenderGroup.Location = New-Object System.Drawing.Point(22, 304)
        $blenderGroup.Size = New-Object System.Drawing.Size(720, 220)
        $blenderGroup.Anchor = "Top,Bottom,Left,Right"
        $form.Controls.Add($blenderGroup)

        $blenderPanel = New-Object System.Windows.Forms.Panel
        $blenderPanel.AutoScroll = $true
        $blenderPanel.Dock = "Fill"
        $blenderGroup.Controls.Add($blenderPanel)

        $blenderChecks = @()
        $row = 14
        $supportedIndex = 0
        foreach ($blender in $BlenderInstallations) {
            $check = New-Object System.Windows.Forms.CheckBox
            $supportText = if ($blender.Supported) { "supported" } else { "requires Blender 4.2+" }
            $check.Text = "$($blender.Name) - $supportText - $($blender.Path)"
            $check.Enabled = [bool]($blender.Supported -and -not $DisableBlender)
            $check.Checked = [bool]($blender.Supported -and $supportedIndex -eq 0 -and -not $DisableBlender)
            if ($blender.Supported) { $supportedIndex += 1 }
            $check.AutoSize = $true
            $check.Location = New-Object System.Drawing.Point(14, $row)
            $check.Tag = $blender.Path
            $blenderPanel.Controls.Add($check)
            $blenderChecks += $check
            $row += 34
        }
        if ($BlenderInstallations.Count -eq 0) {
            $none = New-Object System.Windows.Forms.Label
            $none.Text = "No Blender installation was detected. Install Blender 4.2+ or use -BlenderPath."
            $none.AutoSize = $true
            $none.ForeColor = [System.Drawing.Color]::DarkOrange
            $none.Location = New-Object System.Drawing.Point(14, 18)
            $blenderPanel.Controls.Add($none)
        }

        $installButton = New-Object System.Windows.Forms.Button
        $installButton.Text = "Install selected"
        $installButton.Size = New-Object System.Drawing.Size(135, 36)
        $installButton.Location = New-Object System.Drawing.Point(607, 545)
        $installButton.Anchor = "Bottom,Right"
        $installButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.AcceptButton = $installButton
        $form.Controls.Add($installButton)

        $cancelButton = New-Object System.Windows.Forms.Button
        $cancelButton.Text = "Cancel"
        $cancelButton.Size = New-Object System.Drawing.Size(100, 36)
        $cancelButton.Location = New-Object System.Drawing.Point(495, 545)
        $cancelButton.Anchor = "Bottom,Right"
        $cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $form.CancelButton = $cancelButton
        $form.Controls.Add($cancelButton)

        $result = $form.ShowDialog()
        if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
            return [PSCustomObject]@{ Cancelled = $true }
        }
        return [PSCustomObject]@{
            Cancelled = $false
            CodexCli = [bool]$codexCliCheck.Checked
            CodexDesktop = [bool]$codexDesktopCheck.Checked
            ClaudeCode = [bool]$claudeCodeCheck.Checked
            ClaudeDesktop = [bool]$claudeDesktopCheck.Checked
            BlenderPaths = @($blenderChecks | Where-Object { $_.Checked } | ForEach-Object { $_.Tag })
        }
    }
    catch {
        Write-WarningLine "Graphical selector unavailable; using detected defaults."
        Write-Info $_.Exception.Message
        return Select-InstallTargets -Detection $Detection -BlenderInstallations $BlenderInstallations -NoGui $true -DisableBlender $DisableBlender -DisableCodex $DisableCodex -DisableClaudeCode $DisableClaudeCode -DisableClaudeDesktop $DisableClaudeDesktop
    }
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
        [bool]$PreserveExisting
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

    if ($null -ne $existing -and $PreserveExisting) {
        Write-WarningLine "Codex already has a different blender_mcp entry; it was preserved."
        Write-Info "Re-run without -PreserveExistingMcpEntries to update that entry."
        $script:CodexStatus = "existing different entry preserved"
        return
    }

    $wasExisting = $null -ne $existing
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
        $script:CodexStatus = if ($wasExisting) { "would be updated" } else { "would be configured" }
    }
    else {
        if ($wasExisting) {
            Write-Ok "Codex global MCP entry blender_mcp was updated."
            $script:CodexStatus = "updated"
        }
        else {
            Write-Ok "Codex global MCP entry blender_mcp is configured."
            $script:CodexStatus = "configured"
        }
    }
}

function Register-ClaudeCodeMcp {
    param(
        [string]$ClaudeExecutable,
        [string]$ServerExecutable,
        [string]$Workspace,
        [int]$Port,
        [bool]$PreserveExisting
    )

    & $ClaudeExecutable mcp get blender_mcp *> $null
    $wasExisting = $LASTEXITCODE -eq 0
    $removedUserEntry = $false
    if ($wasExisting -and $PreserveExisting) {
        Write-Ok "Claude Code already has a blender_mcp entry; it was preserved."
        $script:ClaudeCodeStatus = "existing entry preserved"
        return
    }

    if ($wasExisting) {
        if ($script:DryRunEnabled) {
            Write-Info "Would remove the previous Claude Code user-scope blender_mcp entry."
        }
        else {
            $removeOutput = & $ClaudeExecutable mcp remove blender_mcp --scope user 2>&1
            $removeExitCode = $LASTEXITCODE
            if ($removeExitCode -eq 0) {
                $removedUserEntry = $true
                Write-Info "Removed the previous Claude Code user-scope blender_mcp entry."
            }
            else {
                Write-WarningLine "Claude Code found blender_mcp outside user scope; that entry was not modified."
                if ($removeOutput) {
                    Write-Info ([string]($removeOutput | Select-Object -Last 1))
                }
            }
        }
    }

    Invoke-CheckedCommand -FilePath $ClaudeExecutable -ArgumentList @(
        "mcp", "add", "--scope", "user", "blender_mcp",
        "--env", "BLENDER_MCP_WORKSPACE=$Workspace",
        "--env", "BLENDER_HOST=localhost",
        "--env", "BLENDER_PORT=$Port",
        "--", $ServerExecutable
    ) -Description "Registering blender_mcp in Claude Code user scope..."

    if ($script:DryRunEnabled) {
        $script:ClaudeCodeStatus = if ($wasExisting) { "would configure/update user scope" } else { "would be configured" }
    }
    else {
        if ($removedUserEntry) {
            Write-Ok "Claude Code user-scope MCP entry was updated."
            $script:ClaudeCodeStatus = "updated"
        }
        else {
            Write-Ok "Claude Code user-scope MCP entry is configured."
            $script:ClaudeCodeStatus = if ($wasExisting) {
                "configured; higher-priority entry retained"
            }
            else {
                "configured"
            }
        }
    }
}

function Open-ClaudeDesktopBundle {
    param([string]$BundlePath)

    if (-not $BundlePath) {
        Write-WarningLine "Claude Desktop MCPB asset is unavailable."
        $script:ClaudeDesktopStatus = "bundle unavailable"
        return
    }
    if ($script:DryRunEnabled) {
        Write-Info "Would open Claude Desktop bundle: $BundlePath"
        $script:ClaudeDesktopStatus = "would request confirmation"
        return
    }
    if (-not (Test-Path -LiteralPath $BundlePath -PathType Leaf)) {
        throw "Claude Desktop MCPB was not downloaded: $BundlePath"
    }

    try {
        Start-Process -FilePath $BundlePath
        Write-Ok "Opened the Claude Desktop MCPB installer."
        Write-Info "Confirm installation in Claude Desktop to complete this target."
        $script:ClaudeDesktopStatus = "confirmation requested"
    }
    catch {
        Write-WarningLine "Could not open the MCPB automatically."
        Write-Info "Install it from Claude Desktop > Settings > Extensions: $BundlePath"
        $script:ClaudeDesktopStatus = "manual confirmation required"
    }
}

try {
    Write-Banner

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $hasSourceCheckout = $false
    $repoRoot = $null
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $candidateRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
        $hasSourceCheckout = Test-Path -LiteralPath (Join-Path $candidateRoot "pyproject.toml") -PathType Leaf
        if ($hasSourceCheckout) { $repoRoot = $candidateRoot }
    }
    $releaseMode = ([bool]$UseRelease -or -not $hasSourceCheckout)

    if ($releaseMode) {
        if (-not $InstallRoot) {
            $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
            if (-not $localAppData) { $localAppData = $env:LOCALAPPDATA }
            if (-not $localAppData) { throw "Could not locate the current user's LocalAppData directory." }
            $InstallRoot = Join-Path $localAppData "BlenderMCP"
        }
        $installBase = Get-AbsolutePath -Path $InstallRoot -BasePath (Get-Location).Path
        $venvRoot = Join-Path $installBase "venv"
        $downloadDirectory = Join-Path $installBase "downloads"
        if (-not $WorkspacePath) {
            $documents = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)
            if (-not $documents) { $documents = $env:USERPROFILE }
            if (-not $documents) { throw "Could not choose a default Blender MCP workspace." }
            $WorkspacePath = Join-Path $documents "BlenderMCP"
        }
        $baseForWorkspace = $installBase
    }
    else {
        $installBase = $repoRoot
        $venvRoot = Join-Path $repoRoot ".venv"
        $downloadDirectory = Join-Path $repoRoot "dist"
        if (-not $WorkspacePath) { $WorkspacePath = $repoRoot }
        $baseForWorkspace = $repoRoot
    }

    $workspace = Get-AbsolutePath -Path $WorkspacePath -BasePath $baseForWorkspace
    $blenderPort = 9876
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    $serverExecutable = Join-Path $venvRoot "Scripts\blender-mcp.exe"

    Write-Step "Detection and target selection"
    $clientDetection = Get-ClientDetection
    $blenderInstallations = @(Get-BlenderInstallations -RequestedPaths $BlenderPath)
    Write-Info "Codex CLI            : $($clientDetection.CodexCliFound)"
    Write-Info "Codex Desktop (ChatGPT): $($clientDetection.CodexDesktopFound)"
    Write-Info "Claude Code CLI       : $($clientDetection.ClaudeCodeFound)"
    Write-Info "Claude Desktop        : $($clientDetection.ClaudeDesktopFound)"
    if ($blenderInstallations.Count -eq 0) {
        Write-WarningLine "No Blender installation was detected. The server can still be installed."
    }
    else {
        foreach ($blender in $blenderInstallations) {
            $support = if ($blender.Supported) { "supported" } else { "unsupported; requires 4.2+" }
            Write-Info "$($blender.Name): $support - $($blender.Path)"
        }
    }

    $noGui = (
        [bool]$NonInteractive -or
        $script:DryRunEnabled -or
        -not [Environment]::UserInteractive -or
        ($env:CI -and $env:CI -notmatch '^(0|false|no)$') -or
        [bool]$env:SSH_CONNECTION -or
        [Console]::IsInputRedirected -or
        [Console]::IsOutputRedirected
    )
    $selection = Select-InstallTargets -Detection $clientDetection -BlenderInstallations $blenderInstallations -NoGui $noGui -UseGui ([bool]$Gui) -DisableBlender ([bool]$SkipBlenderExtension) -DisableCodex ([bool]$SkipCodexRegistration) -DisableClaudeCode ([bool]$SkipClaudeCodeRegistration) -DisableClaudeDesktop ([bool]$SkipClaudeDesktop)
    if ($selection.Cancelled) {
        Write-WarningLine "Installation cancelled; no changes were made."
        return
    }
    if ($noGui -and @($BlenderPath).Count -gt 0 -and -not $SkipBlenderExtension) {
        $selection.BlenderPaths = @($blenderInstallations | Where-Object { $_.Supported } | ForEach-Object { $_.Path })
    }

    $script:SelectedCodexCli = [bool]$selection.CodexCli
    $script:SelectedCodexDesktop = [bool]$selection.CodexDesktop
    $script:SelectedClaudeCode = [bool]$selection.ClaudeCode
    $script:SelectedClaudeDesktop = [bool]$selection.ClaudeDesktop
    $selectedBlenderPaths = @($selection.BlenderPaths)
    if ($SkipCodexRegistration) {
        $script:SelectedCodexCli = $false
        $script:SelectedCodexDesktop = $false
        $script:CodexStatus = "skipped"
    }
    if ($SkipClaudeCodeRegistration) {
        $script:SelectedClaudeCode = $false
        $script:ClaudeCodeStatus = "skipped"
    }
    if ($SkipClaudeDesktop) {
        $script:SelectedClaudeDesktop = $false
        $script:ClaudeDesktopStatus = "skipped"
    }
    if ($SkipBlenderExtension) {
        $selectedBlenderPaths = @()
        $script:BlenderStatus = "skipped"
    }

    Write-Step "Installation plan"
    Write-Info "Mode      : $(if ($releaseMode) { 'GitHub Release' } else { 'local source checkout' })"
    Write-Info "Install   : $installBase"
    Write-Info "Workspace : $workspace"
    Write-Info "MCP port  : $blenderPort"
    Write-Info "Blender targets: $($selectedBlenderPaths.Count)"
    Write-Info "Codex targets : CLI=$($script:SelectedCodexCli), Desktop (ChatGPT)=$($script:SelectedCodexDesktop)"
    Write-Info "Claude targets: Code=$($script:SelectedClaudeCode), Desktop=$($script:SelectedClaudeDesktop)"
    if ($script:DryRunEnabled) {
        Write-WarningLine "Dry-run mode is active; no machine state will be changed."
    }

    foreach ($directory in @($installBase, $workspace, $downloadDirectory)) {
        if (Test-Path -LiteralPath $directory -PathType Container) { continue }
        if ($script:DryRunEnabled) {
            Write-Info "Would create directory: $directory"
        }
        else {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            Write-Ok "Created $directory"
        }
    }

    $archivePath = $null
    $wheelPath = $null
    $mcpbPath = $null
    $release = $null
    if ($releaseMode) {
        Write-Step "Verified GitHub Release assets"
        $release = Get-GitHubRelease -Repo $Repository -Tag $ReleaseTag
        if ($release.draft -or $release.prerelease) {
            Write-WarningLine "The explicitly selected release is marked draft or prerelease."
        }
        Write-Ok "Selected release $($release.tag_name)"

        $checksumAsset = Get-ReleaseAsset -Release $release -Pattern "SHA256SUMS.txt" -Purpose "checksum"
        $wheelAsset = Get-ReleaseAsset -Release $release -Pattern "blender_mcp-*.whl" -Purpose "Python wheel"
        $assetsToDownload = @($checksumAsset, $wheelAsset)
        if ($selectedBlenderPaths.Count -gt 0) {
            $assetsToDownload += Get-ReleaseAsset -Release $release -Pattern "blender_mcp-*.zip" -Purpose "Blender Extension ZIP"
        }
        if ($script:SelectedClaudeDesktop) {
            $assetsToDownload += Get-ReleaseAsset -Release $release -Pattern "blender_mcp-*.mcpb" -Purpose "Claude Desktop MCPB"
        }

        $downloaded = @()
        foreach ($asset in $assetsToDownload) {
            $downloaded += Save-ReleaseAsset -Asset $asset -Directory $downloadDirectory
        }
        $checksumPath = @($downloaded | Where-Object { (Split-Path -Leaf $_) -eq "SHA256SUMS.txt" })[0]
        $verifiedAssets = @($downloaded | Where-Object { (Split-Path -Leaf $_) -ne "SHA256SUMS.txt" })
        Test-ReleaseChecksums -ChecksumPath $checksumPath -AssetPaths $verifiedAssets
        $wheelPath = @($verifiedAssets | Where-Object { $_ -like "*.whl" })[0]
        $archivePath = $verifiedAssets | Where-Object { $_ -like "*.zip" } | Select-Object -First 1
        $mcpbPath = $verifiedAssets | Where-Object { $_ -like "*.mcpb" } | Select-Object -First 1
    }

    Write-Step "Python MCP server"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $pythonVersion = & $venvPython -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'; print(sys.version.split()[0])" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "The existing environment must contain a working Python 3.10 or newer: $venvPython"
        }
        Write-Ok "Reusing Python $pythonVersion from $venvRoot"
    }
    else {
        $launcher = Get-PythonLauncher -RequestedPath $PythonPath
        $pythonVersion = Test-PythonLauncher -Launcher $launcher
        Write-Ok "Found Python $pythonVersion"
        $venvArguments = @($launcher.Prefix) + @("-m", "venv", $venvRoot)
        Invoke-CheckedCommand -FilePath $launcher.Command -ArgumentList $venvArguments -Description "Creating the Blender MCP virtual environment..."
    }

    if ($releaseMode) {
        $pipArguments = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", $wheelPath)
    }
    else {
        $pipArguments = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--editable", $repoRoot)
    }
    Invoke-CheckedCommand -FilePath $venvPython -ArgumentList $pipArguments -Description "Installing Blender MCP and Python dependencies..."
    Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
        "-c",
        "import asyncio; from blender_mcp.server import mcp; tools = asyncio.run(mcp.list_tools()); names = {tool.name for tool in tools}; required = {'get_blender_documentation_context', 'search_blender_docs', 'get_blender_doc_page', 'search_geometry_node_types', 'search_blender_node_assets'}; missing = sorted(required - names); print(f'Registered MCP tools: {len(tools)}'); print(f'Missing required tools: {missing}' if missing else 'Knowledge tools: ready'); assert len(tools) >= 33 and not missing"
    ) -Description "Verifying MCP imports and tool registration..."
    if (-not $script:DryRunEnabled) {
        if (-not (Test-Path -LiteralPath $serverExecutable -PathType Leaf)) {
            throw "MCP console executable was not installed: $serverExecutable"
        }
        Write-Ok "Python MCP server is ready."
    }

    Write-Step "Blender Extension"
    if ($selectedBlenderPaths.Count -eq 0) {
        if ($SkipBlenderExtension) {
            Write-WarningLine "Skipped by -SkipBlenderExtension."
        }
        else {
            Write-WarningLine "No supported Blender target was selected."
            $script:BlenderStatus = "no target selected"
        }
    }
    else {
        if (-not $releaseMode) {
            $manifest = Get-Content -LiteralPath (Join-Path $repoRoot "packaging\blender_extension\blender_manifest.toml") -Raw
            if ($manifest -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
                throw "Could not read the Extension version from blender_manifest.toml."
            }
            $archivePath = Join-Path $repoRoot ("dist\blender_mcp-{0}.zip" -f $Matches[1])
            Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
                (Join-Path $repoRoot "scripts\build_blender_extension.py"),
                "--blender", $selectedBlenderPaths[0]
            ) -Description "Building and validating the installable Blender Extension ZIP..." -Quiet
        }
        if (-not $script:DryRunEnabled -and -not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
            throw "The Blender Extension archive is unavailable: $archivePath"
        }

        $installedVersions = @()
        foreach ($blenderExecutable in $selectedBlenderPaths) {
            $blenderVersion = Get-BlenderVersion -Executable $blenderExecutable
            if ($blenderVersion -lt [version]"4.2") {
                throw "Blender $blenderVersion is too old; Blender 4.2+ is required: $blenderExecutable"
            }
            # Loading factory preferences here would make `-e` persist Blender's
            # defaults over the user's settings. Load the real preferences while
            # disabling startup-file script execution, then change only the
            # Extension installation/enabled state.
            Invoke-CheckedCommand -FilePath $blenderExecutable -ArgumentList @(
                "--quiet", "--disable-autoexec", "--command", "extension", "install-file",
                "-r", "user_default", "-e", $archivePath
            ) -Description "Installing and enabling Blender MCP in Blender $blenderVersion..." -Quiet
            $installedVersions += [string]$blenderVersion
        }
        if ($script:DryRunEnabled) {
            $script:BlenderStatus = "would install for $($installedVersions -join ', ')"
        }
        else {
            Write-Ok "Blender Extension installed and enabled in $($installedVersions.Count) installation(s)."
            $script:BlenderStatus = "installed for $($installedVersions -join ', ')"
        }
    }

    Write-Step "MCP client registration"
    if ($script:SelectedCodexCli -or $script:SelectedCodexDesktop) {
        if (-not $clientDetection.CodexCommand) {
            Write-WarningLine "Codex command was not found; Codex configuration was not changed."
            $script:CodexStatus = "command unavailable"
        }
        else {
            Register-CodexMcp -CodexExecutable $clientDetection.CodexCommand -ServerExecutable $serverExecutable -Workspace $workspace -Port $blenderPort -PreserveExisting ([bool]$PreserveExistingMcpEntries)
        }
    }
    elseif (-not $SkipCodexRegistration) {
        $script:CodexStatus = "not selected"
    }

    if ($script:SelectedClaudeCode) {
        if (-not $clientDetection.ClaudeCommand) {
            Write-WarningLine "Claude Code command was not found; its configuration was not changed."
            $script:ClaudeCodeStatus = "command unavailable"
        }
        else {
            Register-ClaudeCodeMcp -ClaudeExecutable $clientDetection.ClaudeCommand -ServerExecutable $serverExecutable -Workspace $workspace -Port $blenderPort -PreserveExisting ([bool]$PreserveExistingMcpEntries)
        }
    }
    elseif (-not $SkipClaudeCodeRegistration) {
        $script:ClaudeCodeStatus = "not selected"
    }

    if ($script:SelectedClaudeDesktop) {
        if ($releaseMode) {
            Open-ClaudeDesktopBundle -BundlePath $mcpbPath
        }
        else {
            Write-WarningLine "Claude Desktop MCPB installation requires the stable release layout."
            Write-Info "Re-run with -UseRelease, or use the GitHub Raw one-line command."
            $script:ClaudeDesktopStatus = "requires release mode"
        }
    }
    elseif (-not $SkipClaudeDesktop) {
        $script:ClaudeDesktopStatus = "not selected"
    }

    Write-Step "Finished"
    Write-Host ""
    if ($script:DryRunEnabled) {
        Write-Host "  Dry run completed successfully." -ForegroundColor Green
    }
    else {
        Write-Host "  Blender MCP installation completed successfully." -ForegroundColor Green
    }
    Write-Info "Server        : $serverExecutable"
    Write-Info "Blender       : $script:BlenderStatus"
    Write-Info "Codex         : $script:CodexStatus"
    Write-Info "Claude Code   : $script:ClaudeCodeStatus"
    Write-Info "Claude Desktop: $script:ClaudeDesktopStatus"
    if ($archivePath) { Write-Info "ZIP           : $archivePath" }
    if ($release) { Write-Info "Release       : $($release.html_url)" }
    Write-Host ""
    Write-Host "  Next steps" -ForegroundColor Cyan
    if ($selectedBlenderPaths.Count -gt 0) {
        Write-Info "1. Open a selected Blender version and find BlenderMCP in the 3D View sidebar (N)."
        Write-Info "2. The local bridge starts automatically on port $blenderPort by default."
    }
    else {
        Write-Info "1. Install the Blender Extension later by running this installer again."
    }
    $anyClientSelected = (
        $script:SelectedCodexCli -or
        $script:SelectedCodexDesktop -or
        $script:SelectedClaudeCode -or
        $script:SelectedClaudeDesktop
    )
    if ($anyClientSelected) {
        Write-Info "Restart the selected MCP clients so they load the blender_mcp server."
    }
    else {
        Write-Info "Run the installer again after installing an MCP client to configure it."
    }
    if ($script:SelectedClaudeDesktop) {
        Write-Info "Complete the MCPB confirmation inside Claude Desktop."
    }
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
