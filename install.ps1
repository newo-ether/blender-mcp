#Requires -Version 5.1

<#
.SYNOPSIS
    One-command Windows installer for Blender MCP.

.DESCRIPTION
    When executed directly from GitHub Raw, this script downloads the latest
    checksummed release assets, installs the Python MCP server into a versioned
    per-user virtual environment, and installs the Blender Extension.

    When executed from a repository checkout, it builds and installs the local
    source instead. Use -UseRelease to test the published release path locally.

    The installer configures Codex CLI/Desktop and Claude Code when their CLIs
    are available. Claude Desktop is configured through its documented JSON
    file, with the official MCPB flow retained as a confirmation fallback.

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

    # Directory allowed for structured node snapshot and patch JSON files.
    [string]$WorkspacePath = "",

    # GitHub repository used for release discovery.
    [string]$Repository = "newo-ether/blender-mcp",

    # Optional exact GitHub Release tag. Empty means the latest stable release.
    [string]$ReleaseTag = "",

    # Stable per-user root containing versioned environments in release mode.
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

$script:StepNumber = 0
$script:DryRunEnabled = [bool]$DryRun
$script:CodexStatus = "not requested"
$script:ClaudeCodeStatus = "not requested"
$script:ClaudeDesktopStatus = "not requested"
$script:CodexSkillStatus = "not requested"
$script:ClaudeCodeSkillStatus = "not requested"
$script:ClaudeDesktopSkillStatus = "not requested"
$script:BlenderStatus = "not requested"
$script:SelectedCodexCli = $false
$script:SelectedCodexDesktop = $false
$script:SelectedClaudeCode = $false
$script:SelectedClaudeDesktop = $false
$script:ClaudeDesktopMcpbFallbackUsed = $false

function Resolve-InstallerLanguage {
    param(
        [string]$RequestedLanguage = "Auto",
        [string]$UiCultureName = ""
    )

    if ($RequestedLanguage -ne "Auto") {
        return $RequestedLanguage
    }
    if (-not $UiCultureName) {
        try {
            $override = Get-WinUILanguageOverride -ErrorAction SilentlyContinue
            if ($null -ne $override) { $UiCultureName = [string]$override.Name }
        }
        catch {}
    }
    if (-not $UiCultureName) {
        $UiCultureName = [System.Globalization.CultureInfo]::CurrentUICulture.Name
    }
    if ($UiCultureName -match '^(?i:zh-(?:CN|Hans)(?:-|$))') {
        return "zh-CN"
    }
    return "en-US"
}

$script:InstallerLanguage = Resolve-InstallerLanguage -RequestedLanguage $Language
$script:UseChinese = $script:InstallerLanguage -eq "zh-CN"

function L {
    param(
        [Parameter(Mandatory = $true)][string]$English,
        [Parameter(Mandatory = $true)][string]$Chinese
    )
    if ($script:UseChinese) { return $Chinese }
    return $English
}

if ($PreserveExistingMcpEntries -and $ForceCodexRegistration) {
    throw (L `
        "-PreserveExistingMcpEntries and -ForceCodexRegistration cannot be used together." `
        "-PreserveExistingMcpEntries 与 -ForceCodexRegistration 不能同时使用。")
}

function Write-Banner {
    Write-Host ""
    Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkCyan
    Write-Host (L `
        "  |                    Blender MCP Installer                 |" `
        "  |                    Blender MCP 安装器                    |") -ForegroundColor Cyan
    Write-Host (L `
        "  |       Server + Blender Extension + MCP client setup      |" `
        "  |          服务端 + Blender 扩展 + MCP 客户端配置          |") -ForegroundColor DarkCyan
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
        Write-Info (L "Would run: $display" "将运行：$display")
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
        $message = L `
            "Command failed with exit code ${exitCode}: $display" `
            "命令执行失败，退出代码 ${exitCode}：$display"
        if ($Quiet -and $capturedOutput.Count -gt 0) {
            $outputLimit = 80
            $outputLines = @($capturedOutput | ForEach-Object { [string]$_ })
            if ($outputLines.Count -gt $outputLimit) {
                $omitted = $outputLines.Count - $outputLimit
                $outputLines = @("... $omitted earlier output line(s) omitted ...") + @(
                    $outputLines | Select-Object -Last $outputLimit
                )
            }
            $message += L `
                "`nCaptured command output:`n$($outputLines -join "`n")" `
                "`n捕获的命令输出：`n$($outputLines -join "`n")"
        }
        throw $message
    }
}

function Invoke-CapturedCommand {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [switch]$IncludeErrorOutput
    )

    # Windows PowerShell 5.1 turns native stderr into error records. With the
    # installer's Stop preference, an expected non-zero probe would otherwise
    # terminate before the caller can inspect LASTEXITCODE.
    $capturedOutput = @()
    $exitCode = $null
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        if ($IncludeErrorOutput) {
            $capturedOutput = @(& $FilePath @ArgumentList 2>&1)
        }
        else {
            $capturedOutput = @(& $FilePath @ArgumentList 2>$null)
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [PSCustomObject]@{
        ExitCode = $exitCode
        Output = @($capturedOutput)
    }
}

function Get-GitHubRelease {
    param(
        [string]$Repo,
        [string]$Tag
    )
    if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        throw (L "GitHub repository must use the owner/name form: $Repo" "GitHub 仓库必须使用 owner/name 格式：$Repo")
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
    Write-Info (L "Querying GitHub Release: $url" "正在查询 GitHub Release：$url")
    try {
        $release = Invoke-RestMethod -Uri $url -Headers $headers -UseBasicParsing
    }
    catch {
        throw (L `
            "GitHub Release discovery failed for $Repo. Check the repository, release tag, network, or API rate limit. $($_.Exception.Message)" `
            "无法查询 $Repo 的 GitHub Release。请检查仓库、版本标签、网络或 API 速率限制。$($_.Exception.Message)")
    }
    $tagNameProperty = $release.PSObject.Properties["tag_name"]
    $assetsProperty = $release.PSObject.Properties["assets"]
    if ($null -eq $tagNameProperty -or -not $tagNameProperty.Value -or $null -eq $assetsProperty) {
        throw (L "GitHub returned an incomplete Release response for $Repo." "GitHub 为 $Repo 返回了不完整的 Release 响应。")
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
        throw (L `
            "Expected one $Purpose asset matching '$Pattern'; found $($matches.Count)." `
            "应当找到一个与 '$Pattern' 匹配的 $Purpose 资源，实际找到 $($matches.Count) 个。")
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
        Write-Info (L "Would download: $($Asset.browser_download_url)" "将下载：$($Asset.browser_download_url)")
        return $destination
    }
    $temporaryPath = "$destination.download"
    for ($attempt = 1; $attempt -le 3; $attempt += 1) {
        try {
            Write-Info (L "Downloading $($Asset.name) (attempt $attempt/3)..." "正在下载 $($Asset.name)（第 $attempt/3 次）……")
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
                throw (L `
                    "Could not download $($Asset.name) after 3 attempts: $($_.Exception.Message)" `
                    "尝试 3 次后仍无法下载 $($Asset.name)：$($_.Exception.Message)")
            }
            Write-WarningLine (L "Download attempt $attempt failed; retrying." "第 $attempt 次下载失败，正在重试。")
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
        Write-Info (L "Would verify SHA-256 for all release assets." "将校验所有 Release 资源的 SHA-256。")
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
            throw (L "SHA256SUMS.txt does not contain $name." "SHA256SUMS.txt 中没有 $name。")
        }
        $actual = (Get-FileHash -LiteralPath $assetPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected[$name]) {
            throw (L "SHA-256 verification failed for $name." "$name 的 SHA-256 校验失败。")
        }
        Write-Ok (L "Verified $name" "已校验 $name")
    }
}

function Set-CurrentServerPointer {
    param(
        [string]$InstallBase,
        [string]$ServerExecutable
    )

    $base = [System.IO.Path]::GetFullPath($InstallBase).TrimEnd('\')
    $server = [System.IO.Path]::GetFullPath($ServerExecutable)
    if (-not $server.StartsWith($base + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw (L "MCP server executable escaped the install root: $server" "MCP 服务端可执行文件位于安装根目录之外：$server")
    }
    $relative = $server.Substring($base.Length + 1)
    if ($relative -notmatch '^venv-[0-9]+\.[0-9]+\.[0-9]+\\Scripts\\blender-mcp\.exe$') {
        throw (L "Unexpected versioned MCP server path: $relative" "MCP 服务端版本路径不符合预期：$relative")
    }
    $pointer = Join-Path $base "current-server.txt"
    if ($script:DryRunEnabled) {
        Write-Info (L "Would point $pointer to $relative" "将把 $pointer 指向 $relative")
        return $pointer
    }
    $temporary = "$pointer.$([guid]::NewGuid().ToString('N')).tmp"
    $backup = "$pointer.$([guid]::NewGuid().ToString('N')).bak"
    try {
        $ascii = New-Object System.Text.ASCIIEncoding
        [System.IO.File]::WriteAllText($temporary, $relative + "`r`n", $ascii)
        if (Test-Path -LiteralPath $pointer -PathType Leaf) {
            [System.IO.File]::Replace($temporary, $pointer, $backup)
            Remove-Item -LiteralPath $backup -Force
        }
        else {
            [System.IO.File]::Move($temporary, $pointer)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Remove-Item -LiteralPath $backup -Force
        }
    }
    Write-Ok (L "Current server pointer targets $relative" "当前服务端指针已指向 $relative")
    return $pointer
}

function Get-SkillInstallRoot {
    param(
        [ValidateSet("Codex", "ClaudeCode")][string]$Client,
        [ValidateSet("User", "Project")][string]$Scope = "User",
        [string]$UserHome = "",
        [string]$ProjectPath = ""
    )

    if ($Scope -eq "User") {
        if (-not $UserHome) {
            $UserHome = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
        }
        if (-not $UserHome) { $UserHome = $env:USERPROFILE }
        if (-not $UserHome) { throw "Could not locate the current user's home directory." }
        $base = [System.IO.Path]::GetFullPath($UserHome)
    }
    else {
        if (-not $ProjectPath) { $ProjectPath = (Get-Location).Path }
        $base = [System.IO.Path]::GetFullPath($ProjectPath)
    }

    if ($Client -eq "Codex") {
        return Join-Path $base ".agents\skills"
    }
    return Join-Path $base ".claude\skills"
}

function Get-SkillFileHashes {
    param([string]$SkillPath)

    $root = [System.IO.Path]::GetFullPath($SkillPath).TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $root "SKILL.md") -PathType Leaf)) {
        throw "Skill folder does not contain SKILL.md: $root"
    }
    $hashes = [ordered]@{}
    foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName) {
        $relative = $file.FullName.Substring($root.Length).TrimStart('\').Replace('\', '/')
        $hashes[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $hashes
}

function Test-SkillHashMapsEqual {
    param($Left, $Right)

    if ($null -eq $Left -or $null -eq $Right -or $Left.Count -ne $Right.Count) {
        return $false
    }
    foreach ($key in $Left.Keys) {
        if (-not $Right.Contains($key) -or [string]$Left[$key] -ne [string]$Right[$key]) {
            return $false
        }
    }
    return $true
}

function Get-SkillManifestHashes {
    param([string]$ManifestPath)

    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { return $null }
    try {
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $files = $manifest.PSObject.Properties["files"]
        if ($null -eq $files -or $null -eq $files.Value) { return $null }
        $hashes = [ordered]@{}
        foreach ($property in $files.Value.PSObject.Properties) {
            $hashes[$property.Name] = [string]$property.Value
        }
        return $hashes
    }
    catch {
        return $null
    }
}

function Write-SkillOwnershipManifest {
    param(
        [string]$ManifestPath,
        [string]$Version,
        [string]$Source,
        $Hashes
    )

    $manifest = [ordered]@{
        schema = "blender-mcp-skill-install/1"
        name = "blender-mcp"
        version = $Version
        source = $Source
        files = $Hashes
    }
    $temporaryPath = "$ManifestPath.tmp"
    $json = $manifest | ConvertTo-Json -Depth 8
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporaryPath, $json + [Environment]::NewLine, $utf8WithoutBom)
    Move-Item -LiteralPath $temporaryPath -Destination $ManifestPath -Force
}

function Install-BlenderMcpSkill {
    param(
        [string]$SourcePath,
        [string]$DestinationRoot,
        [string]$Version,
        [string]$SourceLabel,
        [bool]$ForceUpdate = $false
    )

    $root = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd('\')
    $destination = Join-Path $root "blender-mcp"
    $manifestPath = Join-Path $root ".blender-mcp-managed.json"
    if ($script:DryRunEnabled -and -not (Test-Path -LiteralPath $SourcePath -PathType Container)) {
        Write-Info (L "Would install Blender MCP Skill into $destination." "将把 Blender MCP Skill 安装到 $destination。")
        return "would install"
    }

    $source = [System.IO.Path]::GetFullPath($SourcePath).TrimEnd('\')
    $sourceHashes = Get-SkillFileHashes -SkillPath $source
    $destinationExists = Test-Path -LiteralPath $destination -PathType Container
    if ($destinationExists) {
        $currentHashes = $null
        try {
            $currentHashes = Get-SkillFileHashes -SkillPath $destination
        }
        catch {
            if (-not $ForceUpdate) {
                Write-WarningLine (L "Preserved invalid or incomplete same-name Skill folder at $destination. Use -ForceSkillUpdate to replace it." "已保留无效或不完整的同名 Skill 文件夹：$destination。若要替换，请使用 -ForceSkillUpdate。")
                return "preserved invalid install"
            }
        }
        if (Test-SkillHashMapsEqual -Left $currentHashes -Right $sourceHashes) {
            if (-not $script:DryRunEnabled) {
                if (-not (Test-Path -LiteralPath $root -PathType Container)) {
                    New-Item -ItemType Directory -Path $root -Force | Out-Null
                }
                Write-SkillOwnershipManifest -ManifestPath $manifestPath -Version $Version -Source $SourceLabel -Hashes $sourceHashes
            }
            Write-Ok (L "Blender MCP Skill is already current at $destination." "Blender MCP Skill 已是最新版本：$destination。")
            return "already installed"
        }

        $ownedHashes = Get-SkillManifestHashes -ManifestPath $manifestPath
        $unmodifiedOwnedInstall = Test-SkillHashMapsEqual -Left $currentHashes -Right $ownedHashes
        if (-not $unmodifiedOwnedInstall -and -not $ForceUpdate) {
            Write-WarningLine (L "Preserved locally modified or unowned Skill at $destination. Use -ForceSkillUpdate to replace it." "已保留本地修改或非安装器管理的 Skill：$destination。若要替换，请使用 -ForceSkillUpdate。")
            return "preserved local changes"
        }
    }

    if ($script:DryRunEnabled) {
        $action = if ($destinationExists) { "update" } else { "install" }
        Write-Info (L "Would $action Blender MCP Skill at $destination." "将在 $destination $action Blender MCP Skill。")
        return "would $action"
    }

    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        New-Item -ItemType Directory -Path $root -Force | Out-Null
    }
    $stage = Join-Path $root (".blender-mcp.stage-" + [guid]::NewGuid().ToString("N"))
    $backup = Join-Path $root (".blender-mcp.backup-" + [guid]::NewGuid().ToString("N"))
    try {
        Copy-Item -LiteralPath $source -Destination $stage -Recurse -Force
        $stageHashes = Get-SkillFileHashes -SkillPath $stage
        if (-not (Test-SkillHashMapsEqual -Left $stageHashes -Right $sourceHashes)) {
            throw "Staged Skill content differs from its source."
        }
        if ($destinationExists) {
            Move-Item -LiteralPath $destination -Destination $backup
        }
        Move-Item -LiteralPath $stage -Destination $destination
        Write-SkillOwnershipManifest -ManifestPath $manifestPath -Version $Version -Source $SourceLabel -Hashes $sourceHashes
        if (Test-Path -LiteralPath $backup -PathType Container) {
            Remove-Item -LiteralPath $backup -Recurse -Force
        }
    }
    catch {
        if (Test-Path -LiteralPath $destination -PathType Container) {
            Remove-Item -LiteralPath $destination -Recurse -Force
        }
        if (Test-Path -LiteralPath $backup -PathType Container) {
            Move-Item -LiteralPath $backup -Destination $destination
        }
        throw
    }
    finally {
        if (Test-Path -LiteralPath $stage -PathType Container) {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
    }

    $status = if ($destinationExists) { "updated" } else { "installed" }
    Write-Ok (L "Blender MCP Skill $status at $destination." "Blender MCP Skill 已$status：$destination。")
    return $status
}

function Expand-BlenderMcpSkillArchive {
    param(
        [string]$ArchivePath,
        [string]$InstallBase,
        [string]$Version
    )

    $sourceParent = Join-Path ([System.IO.Path]::GetFullPath($InstallBase)) "skill-sources"
    $versionRoot = Join-Path $sourceParent $Version
    $skillSource = Join-Path $versionRoot "blender-mcp"
    if ($script:DryRunEnabled) {
        Write-Info (L "Would extract the verified Skill archive to $versionRoot." "将把已校验的 Skill 压缩包解压到 $versionRoot。")
        return $skillSource
    }
    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
        throw "Verified Skill archive is missing: $ArchivePath"
    }
    if (-not (Test-Path -LiteralPath $sourceParent -PathType Container)) {
        New-Item -ItemType Directory -Path $sourceParent -Force | Out-Null
    }
    $stage = Join-Path $sourceParent (".stage-" + [guid]::NewGuid().ToString("N"))
    try {
        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $stage -Force
        $stagedSkill = Join-Path $stage "blender-mcp"
        Get-SkillFileHashes -SkillPath $stagedSkill | Out-Null
        if (Test-Path -LiteralPath $versionRoot -PathType Container) {
            Remove-Item -LiteralPath $versionRoot -Recurse -Force
        }
        Move-Item -LiteralPath $stage -Destination $versionRoot
    }
    finally {
        if (Test-Path -LiteralPath $stage -PathType Container) {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
    }
    return $skillSource
}

function Set-CurrentWorkspacePointer {
    param(
        [string]$InstallBase,
        [string]$Workspace
    )

    $base = [System.IO.Path]::GetFullPath($InstallBase)
    $workspacePath = [System.IO.Path]::GetFullPath($Workspace)
    $pointer = Join-Path $base "current-workspace.txt"
    if ($script:DryRunEnabled) {
        Write-Info (L "Would point $pointer to $workspacePath" "将把 $pointer 指向 $workspacePath")
        return $pointer
    }
    $temporary = "$pointer.$([guid]::NewGuid().ToString('N')).tmp"
    $backup = "$pointer.$([guid]::NewGuid().ToString('N')).bak"
    try {
        $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporary, $workspacePath + "`r`n", $utf8WithoutBom)
        if (Test-Path -LiteralPath $pointer -PathType Leaf) {
            [System.IO.File]::Replace($temporary, $pointer, $backup)
            Remove-Item -LiteralPath $backup -Force
        }
        else {
            [System.IO.File]::Move($temporary, $pointer)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Remove-Item -LiteralPath $backup -Force
        }
    }
    Write-Ok (L "Current workspace pointer targets $workspacePath" "当前工作区指针已指向 $workspacePath")
    return $pointer
}

function Set-ClaudeDesktopFallbackPointers {
    param(
        [string]$ServerExecutable,
        [string]$Workspace,
        [string]$BridgeRoot = ""
    )

    if (-not $BridgeRoot) {
        $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
        if (-not $localAppData) { $localAppData = $env:LOCALAPPDATA }
        if (-not $localAppData) {
            throw (L "Could not locate LocalAppData for Claude Desktop fallback pointers." "无法找到用于 Claude Desktop 备用指针的 LocalAppData。")
        }
        $BridgeRoot = Join-Path $localAppData "BlenderMCP"
    }
    $bridgeRoot = [System.IO.Path]::GetFullPath($BridgeRoot)
    $values = [ordered]@{
        "claude-server.txt" = [System.IO.Path]::GetFullPath($ServerExecutable)
        "claude-workspace.txt" = [System.IO.Path]::GetFullPath($Workspace)
    }
    if ($script:DryRunEnabled) {
        foreach ($name in $values.Keys) {
            Write-Info (L "Would write $name in $bridgeRoot" "将在 $bridgeRoot 中写入 $name")
        }
        return
    }
    if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $bridgeRoot -Force | Out-Null
    }
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($name in $values.Keys) {
        $pointer = Join-Path $bridgeRoot $name
        $temporary = "$pointer.$([guid]::NewGuid().ToString('N')).tmp"
        $backup = "$pointer.$([guid]::NewGuid().ToString('N')).bak"
        try {
            [System.IO.File]::WriteAllText($temporary, [string]$values[$name] + "`r`n", $utf8WithoutBom)
            if (Test-Path -LiteralPath $pointer -PathType Leaf) {
                [System.IO.File]::Replace($temporary, $pointer, $backup)
                Remove-Item -LiteralPath $backup -Force
            }
            else {
                [System.IO.File]::Move($temporary, $pointer)
            }
        }
        finally {
            if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
            if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force }
        }
    }
    Write-Ok (L "Claude Desktop fallback pointers are ready." "Claude Desktop 备用指针已就绪。")
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
            throw (L "Invalid Blender executable path '$requested': $($_.Exception.Message)" "Blender 可执行文件路径无效 '$requested'：$($_.Exception.Message)")
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw (L "Blender executable was not found: $resolved" "未找到 Blender 可执行文件：$resolved")
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
                (L "Ignoring an unreadable Blender candidate: {0} ({1})" "已忽略无法读取的 Blender 候选项：{0}（{1}）") -f
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
            throw (L "Could not start Blender: $Executable" "无法启动 Blender：$Executable")
        }
        $standardOutput = $process.StandardOutput.ReadToEnd()
        $standardError = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw (L `
                "Could not read Blender version from: $Executable (exit $($process.ExitCode))" `
                "无法读取 Blender 版本：$Executable（退出代码 $($process.ExitCode)）")
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
        throw (L "Could not parse Blender version from: $summary" "无法从以下内容解析 Blender 版本：$summary")
    }
    return [version]$Matches[1]
}

function Find-DesktopApplication {
    param(
        [string]$NamePattern,
        [string[]]$KnownPaths
    )

    $evidence = @()
    $launchKind = ""
    $launchTarget = ""
    foreach ($path in @($KnownPaths)) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            $evidence += $path
            if (-not $launchTarget) {
                $launchKind = "Executable"
                $launchTarget = $path
            }
        }
    }

    $getStartApps = Get-Command Get-StartApps -ErrorAction SilentlyContinue
    if ($null -ne $getStartApps) {
        foreach ($app in Get-StartApps -ErrorAction SilentlyContinue | Where-Object { $_.Name -match $NamePattern }) {
            $evidence += "Start menu: $($app.Name)"
            if (-not $launchTarget -and $app.AppID) {
                $launchKind = "StartApp"
                $launchTarget = [string]$app.AppID
            }
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
            if (-not $launchTarget) {
                $launchCandidates = @()
                $displayIconProperty = $entry.PSObject.Properties["DisplayIcon"]
                if ($null -ne $displayIconProperty -and $displayIconProperty.Value) {
                    $iconPath = ([string]$displayIconProperty.Value -split ',', 2)[0].Trim().Trim('"')
                    if ($iconPath) { $launchCandidates += $iconPath }
                }
                $installLocationProperty = $entry.PSObject.Properties["InstallLocation"]
                if ($null -ne $installLocationProperty -and $installLocationProperty.Value) {
                    $launchCandidates += Join-Path ([string]$installLocationProperty.Value) "Claude.exe"
                }
                foreach ($candidate in $launchCandidates) {
                    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                        $launchKind = "Executable"
                        $launchTarget = $candidate
                        break
                    }
                }
            }
        }
    }

    $unique = @($evidence | Select-Object -Unique)
    return [PSCustomObject]@{
        Found = ($unique.Count -gt 0)
        Evidence = if ($unique.Count) { $unique[0] } else { L "not detected" "未检测到" }
        LaunchKind = $launchKind
        LaunchTarget = $launchTarget
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
        (Join-Path $env:LOCALAPPDATA "AnthropicClaude\Claude.exe"),
        (Join-Path $env:LOCALAPPDATA "Claude\Claude.exe"),
        (Join-Path $env:ProgramFiles "Claude\Claude.exe")
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
        ClaudeDesktopLaunchKind = [string]$claudeDesktop.LaunchKind
        ClaudeDesktopLaunchTarget = [string]$claudeDesktop.LaunchTarget
    }
}

function Get-DefaultBlenderPaths {
    param(
        [object[]]$BlenderInstallations,
        [bool]$DisableBlender
    )

    if ($DisableBlender) { return @() }
    return @(
        $BlenderInstallations |
            Where-Object { $_.Supported } |
            ForEach-Object { [string]$_.Path }
    )
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
        Group = L "MCP clients" "MCP 客户端"
        Label = if ($Detection.CodexCliFound) {
            L `
                "Codex / ChatGPT - shared MCP config - $($Detection.CodexCommand)" `
                "Codex / ChatGPT - 共用 MCP 配置 - $($Detection.CodexCommand)"
        }
        else { L "Codex / ChatGPT - configuration command not detected" "Codex / ChatGPT - 未检测到配置命令" }
        Enabled = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        Selected = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        Kind = "Codex"
        Value = $null
    }
    $entries += [PSCustomObject]@{
        Group = L "MCP clients" "MCP 客户端"
        Label = if ($Detection.ClaudeCodeFound) {
            "Claude Code CLI - $($Detection.ClaudeCommand)"
        }
        else { L "Claude Code CLI - not detected" "Claude Code CLI - 未检测到" }
        Enabled = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        Selected = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        Kind = "ClaudeCode"
        Value = $null
    }
    $entries += [PSCustomObject]@{
        Group = L "MCP clients" "MCP 客户端"
        Label = L `
            "Claude Desktop - $($Detection.ClaudeDesktopEvidence) - automatic JSON registration (MCPB fallback)" `
            "Claude Desktop - $($Detection.ClaudeDesktopEvidence) - 自动写入 JSON（MCPB 备用）"
        Enabled = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        Selected = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        Kind = "ClaudeDesktop"
        Value = $null
    }

    $defaultBlenderPaths = @(Get-DefaultBlenderPaths -BlenderInstallations $BlenderInstallations -DisableBlender $DisableBlender)
    foreach ($blender in $BlenderInstallations) {
        $supportText = if ($blender.Supported) {
            L "supported" "支持"
        }
        else { L "requires Blender 4.2+" "需要 Blender 4.2+" }
        $entries += [PSCustomObject]@{
            Group = L "Blender installations" "Blender 安装版本"
            Label = "$($blender.Name) - $supportText - $($blender.Path)"
            Enabled = [bool]($blender.Supported -and -not $DisableBlender)
            Selected = [bool]($defaultBlenderPaths -contains [string]$blender.Path)
            Kind = "Blender"
            Value = [string]$blender.Path
        }
    }
    if ($BlenderInstallations.Count -eq 0) {
        $entries += [PSCustomObject]@{
            Group = L "Blender installations" "Blender 安装版本"
            Label = L `
                "No Blender detected - install Blender 4.2+ or use -BlenderPath" `
                "未检测到 Blender - 请安装 Blender 4.2+，或使用 -BlenderPath"
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
            Write-Host (L "Blender MCP - Select installation targets" "Blender MCP - 选择安装目标") -ForegroundColor Cyan
            Write-Host (L `
                "Use Up/Down to move, Space to toggle, A to toggle all, Enter to install, Esc to cancel." `
                "方向键上下移动，空格切换，A 全选/全不选，Enter 安装，Esc 取消。") -ForegroundColor DarkGray
            Write-Host (L "The Python MCP server is always installed." "Python MCP 服务端始终会安装。") -ForegroundColor DarkGray

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
                $codexSelected = [bool](@($entries | Where-Object { $_.Kind -eq "Codex" -and $_.Selected }).Count)
                return [PSCustomObject]@{
                    Cancelled = $false
                    CodexCli = $codexSelected
                    CodexDesktop = $codexSelected
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

    $defaultBlenderPaths = @(Get-DefaultBlenderPaths -BlenderInstallations $BlenderInstallations -DisableBlender $DisableBlender)
    if ($NoGui) {
        $codexSelected = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        return [PSCustomObject]@{
            Cancelled = $false
            CodexCli = $codexSelected
            CodexDesktop = $codexSelected
            ClaudeCode = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
            ClaudeDesktop = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
            BlenderPaths = @($defaultBlenderPaths)
        }
    }

    if (-not $UseGui) {
        try {
            return Select-InstallTargetsTui -Detection $Detection -BlenderInstallations $BlenderInstallations -DisableBlender $DisableBlender -DisableCodex $DisableCodex -DisableClaudeCode $DisableClaudeCode -DisableClaudeDesktop $DisableClaudeDesktop
        }
        catch {
            Write-WarningLine (L "Terminal selector unavailable; trying the graphical selector." "终端选择器不可用，正在尝试图形选择器。")
            Write-Info $_.Exception.Message
            return Select-InstallTargets -Detection $Detection -BlenderInstallations $BlenderInstallations -NoGui $false -UseGui $true -DisableBlender $DisableBlender -DisableCodex $DisableCodex -DisableClaudeCode $DisableClaudeCode -DisableClaudeDesktop $DisableClaudeDesktop
        }
    }

    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing

        $form = New-Object System.Windows.Forms.Form
        $form.Text = L "Blender MCP - Select installation targets" "Blender MCP - 选择安装目标"
        $form.StartPosition = "CenterScreen"
        $form.Size = New-Object System.Drawing.Size(780, 650)
        $form.MinimumSize = New-Object System.Drawing.Size(700, 560)
        $form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

        $title = New-Object System.Windows.Forms.Label
        $title.Text = L "Choose where Blender MCP should be installed" "选择 Blender MCP 的安装目标"
        $title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 15)
        $title.AutoSize = $true
        $title.Location = New-Object System.Drawing.Point(22, 18)
        $form.Controls.Add($title)

        $subtitle = New-Object System.Windows.Forms.Label
        $subtitle.Text = L `
            "Detected targets are selected by default. Codex and ChatGPT are one shared configuration target." `
            "默认选中检测到的目标；Codex 与 ChatGPT 共用同一项配置。"
        $subtitle.AutoSize = $true
        $subtitle.ForeColor = [System.Drawing.Color]::DimGray
        $subtitle.Location = New-Object System.Drawing.Point(25, 54)
        $form.Controls.Add($subtitle)

        $clientGroup = New-Object System.Windows.Forms.GroupBox
        $clientGroup.Text = L "MCP clients" "MCP 客户端"
        $clientGroup.Location = New-Object System.Drawing.Point(22, 84)
        $clientGroup.Size = New-Object System.Drawing.Size(720, 205)
        $clientGroup.Anchor = "Top,Left,Right"
        $form.Controls.Add($clientGroup)

        $codexCheck = New-Object System.Windows.Forms.CheckBox
        $codexCheck.Text = if ($Detection.CodexCliFound) {
            L `
                "Codex / ChatGPT - shared MCP config: $($Detection.CodexCommand)" `
                "Codex / ChatGPT - 共用 MCP 配置：$($Detection.CodexCommand)"
        } else { L "Codex / ChatGPT - configuration command not detected" "Codex / ChatGPT - 未检测到配置命令" }
        $codexCheck.Checked = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        $codexCheck.Enabled = [bool]($Detection.CodexCliFound -and -not $DisableCodex)
        $codexCheck.AutoSize = $true
        $codexCheck.Location = New-Object System.Drawing.Point(18, 30)
        $clientGroup.Controls.Add($codexCheck)

        $claudeCodeCheck = New-Object System.Windows.Forms.CheckBox
        $claudeCodeCheck.Text = if ($Detection.ClaudeCodeFound) {
            L "Claude Code CLI - detected: $($Detection.ClaudeCommand)" "Claude Code CLI - 已检测：$($Detection.ClaudeCommand)"
        } else { L "Claude Code CLI - not detected" "Claude Code CLI - 未检测到" }
        $claudeCodeCheck.Checked = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        $claudeCodeCheck.Enabled = [bool]($Detection.ClaudeCodeFound -and -not $DisableClaudeCode)
        $claudeCodeCheck.AutoSize = $true
        $claudeCodeCheck.Location = New-Object System.Drawing.Point(18, 75)
        $clientGroup.Controls.Add($claudeCodeCheck)

        $claudeDesktopCheck = New-Object System.Windows.Forms.CheckBox
        $claudeDesktopCheck.Text = L `
            "Claude Desktop - $($Detection.ClaudeDesktopEvidence) - automatic JSON registration (MCPB fallback)" `
            "Claude Desktop - $($Detection.ClaudeDesktopEvidence) - 自动写入 JSON（MCPB 备用）"
        $claudeDesktopCheck.Checked = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        $claudeDesktopCheck.Enabled = [bool]($Detection.ClaudeDesktopFound -and -not $DisableClaudeDesktop)
        $claudeDesktopCheck.AutoSize = $true
        $claudeDesktopCheck.Location = New-Object System.Drawing.Point(18, 120)
        $clientGroup.Controls.Add($claudeDesktopCheck)

        $blenderGroup = New-Object System.Windows.Forms.GroupBox
        $blenderGroup.Text = L "Blender installations" "Blender 安装版本"
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
        foreach ($blender in $BlenderInstallations) {
            $check = New-Object System.Windows.Forms.CheckBox
            $supportText = if ($blender.Supported) { L "supported" "支持" } else { L "requires Blender 4.2+" "需要 Blender 4.2+" }
            $check.Text = "$($blender.Name) - $supportText - $($blender.Path)"
            $check.Enabled = [bool]($blender.Supported -and -not $DisableBlender)
            $check.Checked = [bool]($defaultBlenderPaths -contains [string]$blender.Path)
            $check.AutoSize = $true
            $check.Location = New-Object System.Drawing.Point(14, $row)
            $check.Tag = $blender.Path
            $blenderPanel.Controls.Add($check)
            $blenderChecks += $check
            $row += 34
        }
        if ($BlenderInstallations.Count -eq 0) {
            $none = New-Object System.Windows.Forms.Label
            $none.Text = L `
                "No Blender installation was detected. Install Blender 4.2+ or use -BlenderPath." `
                "未检测到 Blender。请安装 Blender 4.2+，或使用 -BlenderPath。"
            $none.AutoSize = $true
            $none.ForeColor = [System.Drawing.Color]::DarkOrange
            $none.Location = New-Object System.Drawing.Point(14, 18)
            $blenderPanel.Controls.Add($none)
        }

        $installButton = New-Object System.Windows.Forms.Button
        $installButton.Text = L "Install selected" "安装所选项目"
        $installButton.Size = New-Object System.Drawing.Size(135, 36)
        $installButton.Location = New-Object System.Drawing.Point(607, 545)
        $installButton.Anchor = "Bottom,Right"
        $installButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.AcceptButton = $installButton
        $form.Controls.Add($installButton)

        $cancelButton = New-Object System.Windows.Forms.Button
        $cancelButton.Text = L "Cancel" "取消"
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
            CodexCli = [bool]$codexCheck.Checked
            CodexDesktop = [bool]$codexCheck.Checked
            ClaudeCode = [bool]$claudeCodeCheck.Checked
            ClaudeDesktop = [bool]$claudeDesktopCheck.Checked
            BlenderPaths = @($blenderChecks | Where-Object { $_.Checked } | ForEach-Object { $_.Tag })
        }
    }
    catch {
        Write-WarningLine (L "Graphical selector unavailable; using detected defaults." "图形选择器不可用，将使用检测到的默认目标。")
        Write-Info $_.Exception.Message
        return Select-InstallTargets -Detection $Detection -BlenderInstallations $BlenderInstallations -NoGui $true -DisableBlender $DisableBlender -DisableCodex $DisableCodex -DisableClaudeCode $DisableClaudeCode -DisableClaudeDesktop $DisableClaudeDesktop
    }
}

function Get-PythonLauncher {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolved = Get-AbsolutePath -Path $RequestedPath -BasePath (Get-Location).Path
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw (L "Python executable was not found: $resolved" "未找到 Python 可执行文件：$resolved")
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

    throw (L `
        "Python 3.10 or newer was not found. Install Python and run this script again." `
        "未找到 Python 3.10 或更高版本。请先安装 Python，再重新运行此脚本。")
}

function Test-PythonLauncher {
    param($Launcher)
    $arguments = @($Launcher.Prefix) + @(
        "-c",
        "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'; print(sys.version.split()[0])"
    )
    $version = & $Launcher.Command @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (L "The selected Python must be version 3.10 or newer." "所选 Python 必须为 3.10 或更高版本。")
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

function Test-LegacyBlenderMcpCommand {
    param(
        [string]$Command,
        [object[]]$Arguments
    )

    if (-not $Command) {
        return $false
    }
    $leaf = [System.IO.Path]::GetFileName(([string]$Command).Trim('"')).ToLowerInvariant()
    $args = @($Arguments | ForEach-Object { ([string]$_).Trim() })
    if ($leaf -eq "uvx" -or $leaf -eq "uvx.exe") {
        return [bool](@($args | Where-Object {
            $_ -eq "blender-mcp" -or $_ -like "blender-mcp@*"
        }).Count)
    }
    if ($leaf -eq "uv" -or $leaf -eq "uv.exe") {
        for ($index = 0; $index -lt $args.Count; $index++) {
            if ($args[$index] -ne "uvx") {
                continue
            }
            if ($index + 1 -lt $args.Count -and (
                $args[$index + 1] -eq "blender-mcp" -or
                $args[$index + 1] -like "blender-mcp@*"
            )) {
                return $true
            }
        }
    }
    return $false
}

function Test-LegacyBlenderMcpCommandLine {
    param([string]$CommandLine)

    $value = ([string]$CommandLine).Trim()
    if (-not $value) {
        return $false
    }
    if ($value -match '(?i)(?:^|[\\/\s"])(uvx(?:\.exe)?)["]?\s+(?:[^\r\n]*\s+)?blender-mcp(?:@[^\s]+)?(?:\s|$)') {
        return $true
    }
    return [bool]($value -match '(?i)(?:^|[\\/\s"])(uv(?:\.exe)?)["]?\s+(?:tool\s+)?uvx\s+blender-mcp(?:@[^\s]+)?(?:\s|$)')
}

function Get-CodexLegacyBlenderMcpEntries {
    param([string]$CodexExecutable)

    $probe = Invoke-CapturedCommand -FilePath $CodexExecutable -ArgumentList @(
        "mcp", "list", "--json"
    ) -IncludeErrorOutput
    if ($probe.ExitCode -ne 0) {
        Write-WarningLine (L "Could not list Codex MCP entries for legacy Blender MCP migration." "无法列出 Codex MCP 配置，不能迁移旧版 Blender MCP。")
        return @()
    }
    try {
        $parsedEntries = ($probe.Output -join "`n") | ConvertFrom-Json
        # Windows PowerShell 5.1 emits a top-level JSON array as one Object[]
        # pipeline item. Assign first so the array subexpression expands it.
        $entries = @($parsedEntries)
    }
    catch {
        Write-WarningLine (L "Codex returned an unreadable MCP list; legacy entries were not changed." "Codex 返回的 MCP 列表无法读取；未更改旧配置。")
        return @()
    }
    $results = @()
    foreach ($entry in $entries) {
        $name = [string](Get-JsonProperty -Object $entry -Name "name")
        if (-not $name -or $name -eq "blender_mcp") {
            continue
        }
        $transport = Get-JsonProperty -Object $entry -Name "transport"
        $command = [string](Get-JsonProperty -Object $transport -Name "command")
        $arguments = @(Get-JsonProperty -Object $transport -Name "args")
        if (Test-LegacyBlenderMcpCommand -Command $command -Arguments $arguments) {
            $results += [PSCustomObject]@{
                Name = $name
                Command = $command
                Arguments = $arguments
            }
        }
    }
    return @($results)
}

function Get-ClaudeLegacyBlenderMcpEntries {
    param([string]$ClaudeExecutable)

    $probe = Invoke-CapturedCommand -FilePath $ClaudeExecutable -ArgumentList @(
        "mcp", "list"
    ) -IncludeErrorOutput
    if ($probe.ExitCode -ne 0) {
        Write-WarningLine (L "Could not list Claude Code MCP entries for legacy Blender MCP migration." "无法列出 Claude Code MCP 配置，不能迁移旧版 Blender MCP。")
        return @()
    }
    $results = @()
    foreach ($rawLine in $probe.Output) {
        $line = ([string]$rawLine) -replace "$([char]27)\[[0-9;]*m", ""
        if ($line -notmatch '^\s*([^:]+):\s+(.+?)\s+-\s+.+$') {
            continue
        }
        $name = $Matches[1].Trim()
        $commandLine = $Matches[2].Trim()
        if ($name -eq "blender_mcp") {
            continue
        }
        if (Test-LegacyBlenderMcpCommandLine -CommandLine $commandLine) {
            $results += [PSCustomObject]@{
                Name = $name
                CommandLine = $commandLine
            }
        }
    }
    return @($results)
}

function Remove-CodexLegacyBlenderMcpEntries {
    param(
        [string]$CodexExecutable,
        [bool]$PreserveExisting
    )
    $entries = @(Get-CodexLegacyBlenderMcpEntries -CodexExecutable $CodexExecutable)
    foreach ($entry in $entries) {
        if ($PreserveExisting) {
            Write-WarningLine (L `
                "Preserving legacy Codex MCP entry '$($entry.Name)': $($entry.Command) $($entry.Arguments -join ' ')" `
                "保留旧版 Codex MCP 配置 '$($entry.Name)'：$($entry.Command) $($entry.Arguments -join ' ')")
            continue
        }
        Invoke-CheckedCommand -FilePath $CodexExecutable -ArgumentList @(
            "mcp", "remove", $entry.Name
        ) -Description (L "Removing legacy Codex Blender MCP entry '$($entry.Name)'..." "正在移除旧版 Codex Blender MCP 配置 '$($entry.Name)'……")
        if (-not $script:DryRunEnabled) {
            Write-Ok (L "Removed legacy Codex Blender MCP entry '$($entry.Name)'." "已移除旧版 Codex Blender MCP 配置 '$($entry.Name)'。")
        }
    }
}

function Remove-ClaudeLegacyBlenderMcpEntries {
    param(
        [string]$ClaudeExecutable,
        [bool]$PreserveExisting
    )
    $entries = @(Get-ClaudeLegacyBlenderMcpEntries -ClaudeExecutable $ClaudeExecutable)
    foreach ($entry in $entries) {
        if ($PreserveExisting) {
            Write-WarningLine (L `
                "Preserving legacy Claude Code MCP entry '$($entry.Name)': $($entry.CommandLine)" `
                "保留旧版 Claude Code MCP 配置 '$($entry.Name)'：$($entry.CommandLine)")
            continue
        }
        if ($script:DryRunEnabled) {
            Write-Info (L "Would remove legacy Claude Code user-scope entry '$($entry.Name)'." "将移除旧版 Claude Code 用户级配置 '$($entry.Name)'。")
            continue
        }
        $remove = Invoke-CapturedCommand -FilePath $ClaudeExecutable -ArgumentList @(
            "mcp", "remove", $entry.Name, "--scope", "user"
        ) -IncludeErrorOutput
        if ($remove.ExitCode -eq 0) {
            Write-Ok (L "Removed legacy Claude Code Blender MCP entry '$($entry.Name)'." "已移除旧版 Claude Code Blender MCP 配置 '$($entry.Name)'。")
        }
        else {
            Write-WarningLine (L `
                "Could not remove legacy Claude Code entry '$($entry.Name)' from user scope; it was retained." `
                "无法从用户级配置中移除旧版 Claude Code 项 '$($entry.Name)'，现予保留。")
        }
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

    Remove-CodexLegacyBlenderMcpEntries `
        -CodexExecutable $CodexExecutable `
        -PreserveExisting $PreserveExisting

    $existing = $null
    $existingProbe = Invoke-CapturedCommand -FilePath $CodexExecutable -ArgumentList @(
        "mcp", "get", "blender_mcp", "--json"
    )
    if ($existingProbe.ExitCode -eq 0 -and $existingProbe.Output.Count -gt 0) {
        try {
            $existing = ($existingProbe.Output -join "`n") | ConvertFrom-Json
        }
        catch {
            Write-WarningLine (L "Codex returned an unreadable existing blender_mcp configuration." "Codex 返回的现有 blender_mcp 配置无法读取。")
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
        Write-Ok (L "Codex already has the matching blender_mcp configuration." "Codex 已有完全一致的 blender_mcp 配置。")
        $script:CodexStatus = "already configured"
        return
    }

    if ($null -ne $existing -and $PreserveExisting) {
        Write-WarningLine (L "Codex already has a different blender_mcp entry; it was preserved." "Codex 已有不同的 blender_mcp 配置，现按要求保留。")
        Write-Info (L "Re-run without -PreserveExistingMcpEntries to update that entry." "如需更新，请不要使用 -PreserveExistingMcpEntries，重新运行安装器。")
        $script:CodexStatus = "existing different entry preserved"
        return
    }

    $wasExisting = $null -ne $existing
    if ($null -ne $existing) {
        Invoke-CheckedCommand -FilePath $CodexExecutable -ArgumentList @(
            "mcp", "remove", "blender_mcp"
        ) -Description (L "Removing the previous Codex blender_mcp entry..." "正在移除原有 Codex blender_mcp 配置……")
    }

    Invoke-CheckedCommand -FilePath $CodexExecutable -ArgumentList @(
        "mcp", "add", "blender_mcp",
        "--env", "BLENDER_MCP_WORKSPACE=$Workspace",
        "--env", "BLENDER_HOST=localhost",
        "--env", "BLENDER_PORT=$Port",
        "--", $ServerExecutable
    ) -Description (L "Registering blender_mcp with Codex..." "正在为 Codex 注册 blender_mcp……")

    if ($script:DryRunEnabled) {
        $script:CodexStatus = if ($wasExisting) { "would be updated" } else { "would be configured" }
    }
    else {
        if ($wasExisting) {
            Write-Ok (L "Codex global MCP entry blender_mcp was updated." "已更新 Codex 全局 MCP 配置 blender_mcp。")
            $script:CodexStatus = "updated"
        }
        else {
            Write-Ok (L "Codex global MCP entry blender_mcp is configured." "已配置 Codex 全局 MCP 项 blender_mcp。")
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

    Remove-ClaudeLegacyBlenderMcpEntries `
        -ClaudeExecutable $ClaudeExecutable `
        -PreserveExisting $PreserveExisting

    $existingProbe = Invoke-CapturedCommand -FilePath $ClaudeExecutable -ArgumentList @(
        "mcp", "get", "blender_mcp"
    )
    $wasExisting = $existingProbe.ExitCode -eq 0
    $removedUserEntry = $false
    if ($wasExisting -and $PreserveExisting) {
        Write-Ok (L "Claude Code already has a blender_mcp entry; it was preserved." "Claude Code 已有 blender_mcp 配置，现按要求保留。")
        $script:ClaudeCodeStatus = "existing entry preserved"
        return
    }

    if ($wasExisting) {
        if ($script:DryRunEnabled) {
            Write-Info (L "Would remove the previous Claude Code user-scope blender_mcp entry." "将移除原有 Claude Code 用户级 blender_mcp 配置。")
        }
        else {
            $removeProbe = Invoke-CapturedCommand -FilePath $ClaudeExecutable -ArgumentList @(
                "mcp", "remove", "blender_mcp", "--scope", "user"
            ) -IncludeErrorOutput
            if ($removeProbe.ExitCode -eq 0) {
                $removedUserEntry = $true
                Write-Info (L "Removed the previous Claude Code user-scope blender_mcp entry." "已移除原有 Claude Code 用户级 blender_mcp 配置。")
            }
            else {
                Write-WarningLine (L "Claude Code found blender_mcp outside user scope; that entry was not modified." "Claude Code 在用户级之外找到 blender_mcp；该配置未作更改。")
                if ($removeProbe.Output.Count -gt 0) {
                    Write-Info ([string]($removeProbe.Output | Select-Object -Last 1))
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
    ) -Description (L "Registering blender_mcp in Claude Code user scope..." "正在 Claude Code 用户级配置中注册 blender_mcp……")

    if ($script:DryRunEnabled) {
        $script:ClaudeCodeStatus = if ($wasExisting) { "would configure/update user scope" } else { "would be configured" }
    }
    else {
        if ($removedUserEntry) {
            Write-Ok (L "Claude Code user-scope MCP entry was updated." "已更新 Claude Code 用户级 MCP 配置。")
            $script:ClaudeCodeStatus = "updated"
        }
        else {
            Write-Ok (L "Claude Code user-scope MCP entry is configured." "已配置 Claude Code 用户级 MCP 项。")
            $script:ClaudeCodeStatus = if ($wasExisting) {
                "configured; higher-priority entry retained"
            }
            else {
                "configured"
            }
        }
    }
}

function Get-ClaudeDesktopConfigPath {
    $applicationData = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
    if (-not $applicationData) { $applicationData = $env:APPDATA }
    if (-not $applicationData) {
        throw (L `
            "Could not locate the current user's AppData directory for Claude Desktop." `
            "无法找到当前用户供 Claude Desktop 使用的 AppData 目录。")
    }
    return Join-Path $applicationData "Claude\claude_desktop_config.json"
}

function Register-ClaudeDesktopMcp {
    param(
        [string]$ConfigPath,
        [string]$ServerExecutable,
        [string]$Workspace,
        [int]$Port,
        [bool]$PreserveExisting
    )

    if (-not $ConfigPath) { $ConfigPath = Get-ClaudeDesktopConfigPath }
    $existingFile = Test-Path -LiteralPath $ConfigPath -PathType Leaf
    $config = [PSCustomObject]@{}
    if ($existingFile) {
        try {
            $configText = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
            if (-not [string]::IsNullOrWhiteSpace($configText)) {
                $config = $configText | ConvertFrom-Json
            }
        }
        catch {
            Write-WarningLine (L `
                "Claude Desktop config is not valid JSON; it was left unchanged: $ConfigPath" `
                "Claude Desktop 配置不是有效的 JSON，已保持原样：$ConfigPath")
            Write-Info $_.Exception.Message
            $script:ClaudeDesktopStatus = "invalid JSON; MCPB fallback required"
            return $false
        }
    }
    if ($null -eq $config -or $config -is [System.Array]) {
        Write-WarningLine (L `
            "Claude Desktop config must contain a top-level JSON object; it was left unchanged: $ConfigPath" `
            "Claude Desktop 配置的顶层必须是 JSON 对象，已保持原样：$ConfigPath")
        $script:ClaudeDesktopStatus = "invalid JSON shape; MCPB fallback required"
        return $false
    }

    $mcpServersProperty = $config.PSObject.Properties["mcpServers"]
    if ($null -eq $mcpServersProperty) {
        $mcpServers = [PSCustomObject]@{}
        $config | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value $mcpServers
    }
    else {
        $mcpServers = $mcpServersProperty.Value
        if ($null -eq $mcpServers) {
            $mcpServers = [PSCustomObject]@{}
            $mcpServersProperty.Value = $mcpServers
        }
        elseif ($mcpServers -is [System.Array] -or $mcpServers -is [string] -or $mcpServers -is [ValueType]) {
            Write-WarningLine (L `
                "Claude Desktop mcpServers is not a JSON object; the config was left unchanged: $ConfigPath" `
                "Claude Desktop 的 mcpServers 不是 JSON 对象，配置已保持原样：$ConfigPath")
            $script:ClaudeDesktopStatus = "invalid mcpServers; MCPB fallback required"
            return $false
        }
    }

    $existing = $mcpServers.PSObject.Properties["blender_mcp"]
    $entryMatches = $false
    if ($null -ne $existing -and $null -ne $existing.Value) {
        $existingEntry = $existing.Value
        $existingEnv = Get-JsonProperty -Object $existingEntry -Name "env"
        $existingArguments = @(Get-JsonProperty -Object $existingEntry -Name "args")
        $entryMatches = (
            (Test-SamePath -Left ([string](Get-JsonProperty -Object $existingEntry -Name "command")) -Right $ServerExecutable) -and
            $existingArguments.Count -eq 0 -and
            (Test-SamePath -Left ([string](Get-JsonProperty -Object $existingEnv -Name "BLENDER_MCP_WORKSPACE")) -Right $Workspace) -and
            [string](Get-JsonProperty -Object $existingEnv -Name "BLENDER_HOST") -eq "localhost" -and
            [string](Get-JsonProperty -Object $existingEnv -Name "BLENDER_PORT") -eq [string]$Port
        )
    }
    if ($entryMatches) {
        Write-Ok (L `
            "Claude Desktop already has the matching blender_mcp configuration." `
            "Claude Desktop 已有完全一致的 blender_mcp 配置。")
        $script:ClaudeDesktopStatus = "already configured"
        return $true
    }
    if ($null -ne $existing -and $PreserveExisting) {
        Write-WarningLine (L `
            "Claude Desktop already has a blender_mcp entry; it was preserved." `
            "Claude Desktop 已有 blender_mcp 配置，现按要求保留。")
        $script:ClaudeDesktopStatus = "existing entry preserved"
        return $true
    }

    $entry = [PSCustomObject][ordered]@{
        command = [System.IO.Path]::GetFullPath($ServerExecutable)
        args = @()
        env = [PSCustomObject][ordered]@{
            BLENDER_MCP_WORKSPACE = [System.IO.Path]::GetFullPath($Workspace)
            BLENDER_HOST = "localhost"
            BLENDER_PORT = [string]$Port
        }
    }
    if ($null -ne $existing) {
        $mcpServers.PSObject.Properties.Remove("blender_mcp")
    }
    $mcpServers | Add-Member -MemberType NoteProperty -Name "blender_mcp" -Value $entry

    if ($script:DryRunEnabled) {
        Write-Info (L `
            "Would update Claude Desktop config: $ConfigPath" `
            "将更新 Claude Desktop 配置：$ConfigPath")
        $script:ClaudeDesktopStatus = if ($null -ne $existing) { "would be updated" } else { "would be configured" }
        return $true
    }

    $directory = Split-Path -Parent $ConfigPath
    $temporary = "$ConfigPath.$([guid]::NewGuid().ToString('N')).tmp"
    $backup = "$ConfigPath.blender-mcp-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))-$([guid]::NewGuid().ToString('N').Substring(0, 8)).bak"
    try {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        $json = $config | ConvertTo-Json -Depth 100
        $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporary, $json + "`r`n", $utf8WithoutBom)
        if ($existingFile) {
            [System.IO.File]::Replace($temporary, $ConfigPath, $backup)
            Write-Info (L "Backup: $backup" "备份：$backup")
        }
        else {
            [System.IO.File]::Move($temporary, $ConfigPath)
        }
    }
    catch {
        Write-WarningLine (L `
            "Could not update Claude Desktop config; it was left unchanged: $($_.Exception.Message)" `
            "无法更新 Claude Desktop 配置；配置已保持原样：$($_.Exception.Message)")
        $script:ClaudeDesktopStatus = "config write failed; MCPB fallback required"
        return $false
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }

    if ($null -ne $existing) {
        Write-Ok (L "Claude Desktop blender_mcp entry was updated." "已更新 Claude Desktop 的 blender_mcp 配置。")
        $script:ClaudeDesktopStatus = "updated"
    }
    else {
        Write-Ok (L "Claude Desktop blender_mcp entry is configured." "已配置 Claude Desktop 的 blender_mcp。")
        $script:ClaudeDesktopStatus = "configured"
    }
    return $true
}

function Open-ClaudeDesktopBundle {
    param(
        [string]$BundlePath,
        [string]$LaunchKind = "",
        [string]$LaunchTarget = ""
    )

    $script:ClaudeDesktopMcpbFallbackUsed = $true

    if (-not $BundlePath) {
        Write-WarningLine (L "Claude Desktop MCPB asset is unavailable." "Claude Desktop MCPB 安装包不可用。")
        $script:ClaudeDesktopStatus = "bundle unavailable"
        return
    }
    if ($script:DryRunEnabled) {
        Write-Info (L `
            "Would launch Claude Desktop when a launch target is available." `
            "如检测到启动目标，将会启动 Claude Desktop。")
        Write-Info (L `
            "Would reveal the MCPB for Settings > Extensions > Advanced settings > Install Extension: $BundlePath" `
            "将显示 MCPB，供你在「设置 > 扩展 > 高级设置 > 安装扩展」中选择：$BundlePath")
        $script:ClaudeDesktopStatus = "would prepare in-app confirmation"
        return
    }
    if (-not (Test-Path -LiteralPath $BundlePath -PathType Leaf)) {
        throw (L "Claude Desktop MCPB was not downloaded: $BundlePath" "尚未下载 Claude Desktop MCPB：$BundlePath")
    }

    $claudeStarted = $false
    $bundleHandedOff = $false
    try {
        if ($LaunchKind -eq "Executable" -and $LaunchTarget) {
            # Claude Desktop accepts an MCPB path directly. Passing the file to
            # the detected executable bypasses Windows' optional .mcpb file
            # association and opens Claude's own confirmation dialog.
            $quotedBundlePath = '"{0}"' -f $BundlePath.Replace('"', '')
            Start-Process -FilePath $LaunchTarget -ArgumentList @($quotedBundlePath) | Out-Null
            $claudeStarted = $true
            $bundleHandedOff = $true
        }
        elseif ($LaunchKind -eq "StartApp" -and $LaunchTarget) {
            Start-Process -FilePath "explorer.exe" -ArgumentList @("shell:AppsFolder\$LaunchTarget") | Out-Null
            $claudeStarted = $true
        }
    }
    catch {
        Write-WarningLine (L `
            "Claude Desktop could not be launched automatically: $($_.Exception.Message)" `
            "无法自动启动 Claude Desktop：$($_.Exception.Message)")
    }

    if ($bundleHandedOff) {
        Write-Ok (L "Opened the MCPB with Claude Desktop." "已使用 Claude Desktop 打开 MCPB。")
        Write-Info (L "Review and confirm the extension installation inside Claude Desktop." "请在 Claude Desktop 中检查并确认扩展安装。")
        $script:ClaudeDesktopStatus = "confirmation requested"
        return
    }

    $bundleRevealed = $false
    try {
        $selectArgument = '/select,"{0}"' -f $BundlePath.Replace('"', '')
        Start-Process -FilePath "explorer.exe" -ArgumentList @($selectArgument) | Out-Null
        $bundleRevealed = $true
    }
    catch {
        Write-WarningLine (L `
            "Could not reveal the MCPB in File Explorer: $($_.Exception.Message)" `
            "无法在文件资源管理器中显示 MCPB：$($_.Exception.Message)")
    }

    Write-WarningLine (L `
        "Claude Desktop requires a final in-app confirmation for custom MCPB extensions." `
        "Claude Desktop 安装自定义 MCPB 扩展时，需要在应用内作最后确认。")
    Write-Info (L `
        "In Claude Desktop, open Settings > Extensions > Advanced settings > Install Extension..." `
        "请在 Claude Desktop 中打开「设置 > 扩展 > 高级设置 > 安装扩展…」。")
    Write-Info (L "Select this file: $BundlePath" "选择此文件：$BundlePath")
    Write-Info (L `
        "You can also drag the highlighted MCPB from File Explorer into Claude Desktop." `
        "也可以从文件资源管理器将已选中的 MCPB 拖入 Claude Desktop。")
    if ($claudeStarted) { Write-Ok (L "Claude Desktop was launched." "已启动 Claude Desktop。") }
    if ($bundleRevealed) { Write-Ok (L "The MCPB was highlighted in File Explorer." "已在文件资源管理器中选中 MCPB。") }
    $script:ClaudeDesktopStatus = "manual in-app confirmation required"
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
        $venvRoot = $null
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
    $venvPython = $null
    $serverExecutable = $null
    if (-not $releaseMode) {
        $venvPython = Join-Path $venvRoot "Scripts\python.exe"
        $serverExecutable = Join-Path $venvRoot "Scripts\blender-mcp.exe"
    }

    Write-Step (L "Detection and target selection" "检测并选择安装目标")
    $clientDetection = Get-ClientDetection
    $blenderInstallations = @(Get-BlenderInstallations -RequestedPaths $BlenderPath)
    Write-Info (L `
        "Codex / ChatGPT      : config=$($clientDetection.CodexCliFound), desktop=$($clientDetection.CodexDesktopFound)" `
        "Codex / ChatGPT      ：配置=$($clientDetection.CodexCliFound)，桌面端=$($clientDetection.CodexDesktopFound)")
    Write-Info (L "Claude Code CLI       : $($clientDetection.ClaudeCodeFound)" "Claude Code CLI       ：$($clientDetection.ClaudeCodeFound)")
    Write-Info (L "Claude Desktop        : $($clientDetection.ClaudeDesktopFound)" "Claude Desktop        ：$($clientDetection.ClaudeDesktopFound)")
    if ($blenderInstallations.Count -eq 0) {
        Write-WarningLine (L "No Blender installation was detected. The server can still be installed." "未检测到 Blender；仍可继续安装服务端。")
    }
    else {
        foreach ($blender in $blenderInstallations) {
            $support = if ($blender.Supported) { L "supported" "支持" } else { L "unsupported; requires 4.2+" "不支持；需要 4.2+" }
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
        Write-WarningLine (L "Installation cancelled; no changes were made." "安装已取消，未作任何更改。")
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
    $skillRequested = (
        -not [bool]$SkipSkillInstallation -and (
            $script:SelectedCodexCli -or
            $script:SelectedCodexDesktop -or
            $script:SelectedClaudeCode -or
            $script:SelectedClaudeDesktop
        )
    )
    if ($SkipSkillInstallation) {
        $script:CodexSkillStatus = "skipped"
        $script:ClaudeCodeSkillStatus = "skipped"
        $script:ClaudeDesktopSkillStatus = "skipped"
    }
    else {
        if (-not ($script:SelectedCodexCli -or $script:SelectedCodexDesktop)) {
            $script:CodexSkillStatus = "not selected"
        }
        if (-not $script:SelectedClaudeCode) {
            $script:ClaudeCodeSkillStatus = "not selected"
        }
        if (-not $script:SelectedClaudeDesktop) {
            $script:ClaudeDesktopSkillStatus = "not selected"
        }
    }

    Write-Step (L "Installation plan" "安装计划")
    $modeName = if ($releaseMode) { "GitHub Release" } else { L "local source checkout" "本地源码" }
    Write-Info (L "Mode      : $modeName" "模式      ：$modeName")
    Write-Info (L "Install   : $installBase" "安装目录  ：$installBase")
    Write-Info (L "Workspace : $workspace" "工作区    ：$workspace")
    Write-Info (L "MCP port  : $blenderPort" "MCP 端口  ：$blenderPort")
    Write-Info (L "Skill     : requested=$skillRequested, scope=$SkillScope" "Skill     ：请求=$skillRequested，范围=$SkillScope")
    Write-Info (L "Blender targets: $($selectedBlenderPaths.Count)" "Blender 目标：$($selectedBlenderPaths.Count)")
    Write-Info (L `
        "Codex / ChatGPT: $($script:SelectedCodexCli -or $script:SelectedCodexDesktop) (shared configuration)" `
        "Codex / ChatGPT：$($script:SelectedCodexCli -or $script:SelectedCodexDesktop)（共用配置）")
    Write-Info (L `
        "Claude targets: Code=$($script:SelectedClaudeCode), Desktop=$($script:SelectedClaudeDesktop)" `
        "Claude 目标：Code=$($script:SelectedClaudeCode)，Desktop=$($script:SelectedClaudeDesktop)")
    if ($script:DryRunEnabled) {
        Write-WarningLine (L "Dry-run mode is active; no machine state will be changed." "当前为试运行模式，不会更改系统状态。")
    }

    foreach ($directory in @($installBase, $workspace, $downloadDirectory)) {
        if (Test-Path -LiteralPath $directory -PathType Container) { continue }
        if ($script:DryRunEnabled) {
            Write-Info (L "Would create directory: $directory" "将创建目录：$directory")
        }
        else {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            Write-Ok (L "Created $directory" "已创建 $directory")
        }
    }

    $archivePath = $null
    $wheelPath = $null
    $mcpbPath = $null
    $skillArchivePath = $null
    $skillSourcePath = $null
    $skillVersion = $null
    $release = $null
    $releaseVersion = $null
    if ($releaseMode) {
        Write-Step (L "Verified GitHub Release assets" "校验 GitHub Release 资源")
        $release = Get-GitHubRelease -Repo $Repository -Tag $ReleaseTag
        if ($release.draft -or $release.prerelease) {
            Write-WarningLine "The explicitly selected release is marked draft or prerelease."
        }
        Write-Ok (L "Selected release $($release.tag_name)" "已选择版本 $($release.tag_name)")

        $releaseTagName = [string]$release.tag_name
        if ($releaseTagName -notmatch '^v([0-9]+\.[0-9]+\.[0-9]+)$') {
            throw "Stable Release tag must use vMAJOR.MINOR.PATCH: $releaseTagName"
        }
        $releaseVersion = $Matches[1]
        $skillVersion = $releaseVersion
        $venvRoot = Join-Path $installBase ("venv-{0}" -f $releaseVersion)
        $venvPython = Join-Path $venvRoot "Scripts\python.exe"
        $serverExecutable = Join-Path $venvRoot "Scripts\blender-mcp.exe"

        $checksumAsset = Get-ReleaseAsset -Release $release -Pattern "SHA256SUMS.txt" -Purpose "checksum"
        $wheelAsset = Get-ReleaseAsset -Release $release -Pattern "blender_mcp-*.whl" -Purpose "Python wheel"
        $expectedWheelName = "blender_mcp-{0}-py3-none-any.whl" -f $releaseVersion
        if ([string]$wheelAsset.name -ne $expectedWheelName) {
            throw "Release tag/package mismatch: expected $expectedWheelName, found $($wheelAsset.name)"
        }
        $assetsToDownload = @($checksumAsset, $wheelAsset)
        if ($selectedBlenderPaths.Count -gt 0) {
            $assetsToDownload += Get-ReleaseAsset -Release $release -Pattern "blender_mcp-*.zip" -Purpose "Blender Extension ZIP"
        }
        if ($script:SelectedClaudeDesktop) {
            $assetsToDownload += Get-ReleaseAsset -Release $release -Pattern "blender_mcp-*.mcpb" -Purpose "Claude Desktop MCPB"
        }
        if ($skillRequested) {
            $skillAsset = Get-ReleaseAsset -Release $release -Pattern "blender-mcp-skill-*.zip" -Purpose "portable Agent Skill ZIP"
            $expectedSkillName = "blender-mcp-skill-{0}.zip" -f $releaseVersion
            if ([string]$skillAsset.name -ne $expectedSkillName) {
                throw "Release tag/Skill mismatch: expected $expectedSkillName, found $($skillAsset.name)"
            }
            $assetsToDownload += $skillAsset
        }

        $downloaded = @()
        foreach ($asset in $assetsToDownload) {
            $downloaded += Save-ReleaseAsset -Asset $asset -Directory $downloadDirectory
        }
        $checksumPath = @($downloaded | Where-Object { (Split-Path -Leaf $_) -eq "SHA256SUMS.txt" })[0]
        $verifiedAssets = @($downloaded | Where-Object { (Split-Path -Leaf $_) -ne "SHA256SUMS.txt" })
        Test-ReleaseChecksums -ChecksumPath $checksumPath -AssetPaths $verifiedAssets
        $wheelPath = @($verifiedAssets | Where-Object { $_ -like "*.whl" })[0]
        $archivePath = $verifiedAssets | Where-Object { (Split-Path -Leaf $_) -like "blender_mcp-*.zip" } | Select-Object -First 1
        $mcpbPath = $verifiedAssets | Where-Object { $_ -like "*.mcpb" } | Select-Object -First 1
        $skillArchivePath = $verifiedAssets | Where-Object { (Split-Path -Leaf $_) -like "blender-mcp-skill-*.zip" } | Select-Object -First 1
    }
    else {
        $projectText = Get-Content -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Raw -Encoding UTF8
        if ($projectText -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
            throw "Could not read the local project version."
        }
        $skillVersion = $Matches[1]
    }

    Write-Step (L "Python MCP server" "Python MCP 服务端")
    $serverInstallRequired = $true
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $pythonVersion = & $venvPython -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'; print(sys.version.split()[0])" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "The existing environment must contain a working Python 3.10 or newer: $venvPython"
        }
        Write-Ok (L "Reusing Python $pythonVersion from $venvRoot" "继续使用 $venvRoot 中的 Python $pythonVersion")
        if ($releaseMode) {
            $installedReleaseVersion = & $venvPython -c "import importlib.metadata; print(importlib.metadata.version('blender-mcp'))" 2>$null
            if ($LASTEXITCODE -eq 0 -and ([string]$installedReleaseVersion).Trim() -eq $releaseVersion) {
                $serverInstallRequired = $false
                Write-Ok (L `
                    "Blender MCP $releaseVersion is already installed in its versioned environment." `
                    "Blender MCP $releaseVersion 已安装在对应版本的环境中。")
            }
        }
    }
    else {
        $launcher = Get-PythonLauncher -RequestedPath $PythonPath
        $pythonVersion = Test-PythonLauncher -Launcher $launcher
        Write-Ok (L "Found Python $pythonVersion" "已找到 Python $pythonVersion")
        $venvArguments = @($launcher.Prefix) + @("-m", "venv", $venvRoot)
        Invoke-CheckedCommand -FilePath $launcher.Command -ArgumentList $venvArguments -Description (L "Creating the Blender MCP virtual environment..." "正在创建 Blender MCP 虚拟环境……")
    }

    if ($releaseMode) {
        $pipArguments = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", $wheelPath)
    }
    else {
        $pipArguments = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--editable", $repoRoot)
    }
    if ($serverInstallRequired) {
        Invoke-CheckedCommand -FilePath $venvPython -ArgumentList $pipArguments -Description (L "Installing Blender MCP and Python dependencies..." "正在安装 Blender MCP 与 Python 依赖……")
    }
    Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
        "-c",
        "import asyncio; from blender_mcp.server import mcp; tools = asyncio.run(mcp.list_tools()); names = {tool.name for tool in tools}; required = {'get_blender_documentation_context', 'search_blender_docs', 'get_blender_doc_page', 'get_runtime_automation_context', 'search_geometry_node_types', 'search_blender_node_assets', 'import_blender_node_asset', 'list_node_trees', 'ensure_scene_compositor_tree', 'get_node_tree_index', 'export_node_tree', 'get_node_type_schema', 'validate_node_tree_patch', 'apply_node_tree_patch'}; missing = sorted(required - names); print(f'Registered MCP tools: {len(tools)}'); print(f'Missing required tools: {missing}' if missing else 'Knowledge and structured-node tools: ready'); assert len(tools) >= 42 and not missing"
    ) -Description (L "Verifying MCP imports and tool registration..." "正在验证 MCP 导入与工具注册……")
    if (-not $script:DryRunEnabled) {
        if (-not (Test-Path -LiteralPath $serverExecutable -PathType Leaf)) {
            throw "MCP console executable was not installed: $serverExecutable"
        }
        Write-Ok (L "Python MCP server is ready." "Python MCP 服务端已就绪。")
    }
    if ($releaseMode) {
        Set-CurrentServerPointer -InstallBase $installBase -ServerExecutable $serverExecutable | Out-Null
        Set-CurrentWorkspacePointer -InstallBase $installBase -Workspace $workspace | Out-Null
        if ($script:SelectedClaudeDesktop) {
            Set-ClaudeDesktopFallbackPointers -ServerExecutable $serverExecutable -Workspace $workspace
        }
    }

    if ($skillRequested) {
        if ($releaseMode) {
            $skillSourcePath = Expand-BlenderMcpSkillArchive -ArchivePath $skillArchivePath -InstallBase $installBase -Version $skillVersion
        }
        else {
            $skillSourcePath = Join-Path $repoRoot "skills\blender-mcp"
            Get-SkillFileHashes -SkillPath $skillSourcePath | Out-Null
            if ($script:SelectedClaudeDesktop) {
                $skillArchivePath = Join-Path $downloadDirectory ("blender-mcp-skill-{0}.zip" -f $skillVersion)
                Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
                    (Join-Path $repoRoot "scripts\build_skill_package.py"),
                    "--output-dir",
                    $downloadDirectory,
                    "--version",
                    $skillVersion
                ) -Description (L "Building the portable Agent Skill ZIP..." "正在构建可移植 Agent Skill ZIP……")
            }
        }
    }

    Write-Step (L "Blender Extension" "Blender 扩展")
    if ($selectedBlenderPaths.Count -eq 0) {
        if ($SkipBlenderExtension) {
            Write-WarningLine (L "Skipped by -SkipBlenderExtension." "已按 -SkipBlenderExtension 跳过。")
        }
        else {
            Write-WarningLine (L "No supported Blender target was selected." "未选择受支持的 Blender 目标。")
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
            ) -Description (L "Building and validating the installable Blender Extension ZIP..." "正在构建并校验可安装的 Blender 扩展 ZIP……") -Quiet
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
            ) -Description (L "Installing and enabling Blender MCP in Blender $blenderVersion..." "正在 Blender $blenderVersion 中安装并启用 Blender MCP……") -Quiet
            $installedVersions += [string]$blenderVersion
        }
        if ($script:DryRunEnabled) {
            $script:BlenderStatus = "would install for $($installedVersions -join ', ')"
        }
        else {
            Write-Ok (L `
                "Blender Extension installed and enabled in $($installedVersions.Count) installation(s)." `
                "已在 $($installedVersions.Count) 个 Blender 安装版本中安装并启用扩展。")
            $script:BlenderStatus = "installed for $($installedVersions -join ', ')"
        }
    }

    Write-Step (L "MCP client registration" "注册 MCP 客户端")
    if ($script:SelectedCodexCli -or $script:SelectedCodexDesktop) {
        if (-not $clientDetection.CodexCommand) {
            Write-WarningLine (L "Codex command was not found; Codex configuration was not changed." "未找到 Codex 命令；未更改 Codex 配置。")
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
            Write-WarningLine (L "Claude Code command was not found; its configuration was not changed." "未找到 Claude Code 命令；未更改其配置。")
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
        $claudeDesktopConfigured = Register-ClaudeDesktopMcp `
            -ConfigPath (Get-ClaudeDesktopConfigPath) `
            -ServerExecutable $serverExecutable `
            -Workspace $workspace `
            -Port $blenderPort `
            -PreserveExisting ([bool]$PreserveExistingMcpEntries)
        if (-not $claudeDesktopConfigured -and $releaseMode) {
            Open-ClaudeDesktopBundle `
                -BundlePath $mcpbPath `
                -LaunchKind $clientDetection.ClaudeDesktopLaunchKind `
                -LaunchTarget $clientDetection.ClaudeDesktopLaunchTarget
        }
        elseif (-not $claudeDesktopConfigured) {
            Write-WarningLine (L "Claude Desktop MCPB installation requires the stable release layout." "安装 Claude Desktop MCPB 需要稳定版发布结构。")
            Write-Info (L "Re-run with -UseRelease, or use the GitHub Raw one-line command." "请使用 -UseRelease 重新运行，或使用 GitHub Raw 一行安装命令。")
            $script:ClaudeDesktopStatus = "requires release mode"
        }
    }
    elseif (-not $SkipClaudeDesktop) {
        $script:ClaudeDesktopStatus = "not selected"
    }

    if ($skillRequested) {
        Write-Step (L "Portable Agent Skill" "可移植 Agent Skill")
        $skillSourceLabel = if ($releaseMode) { "$Repository@$($release.tag_name)" } else { "local source checkout" }
        $skillProjectRoot = if ($SkillProjectPath) { $SkillProjectPath } else { (Get-Location).Path }

        if ($script:SelectedCodexCli -or $script:SelectedCodexDesktop) {
            $codexSkillRoot = Get-SkillInstallRoot -Client "Codex" -Scope $SkillScope -ProjectPath $skillProjectRoot
            $script:CodexSkillStatus = Install-BlenderMcpSkill -SourcePath $skillSourcePath -DestinationRoot $codexSkillRoot -Version $skillVersion -SourceLabel $skillSourceLabel -ForceUpdate ([bool]$ForceSkillUpdate)
        }
        if ($script:SelectedClaudeCode) {
            $claudeCodeSkillRoot = Get-SkillInstallRoot -Client "ClaudeCode" -Scope $SkillScope -ProjectPath $skillProjectRoot
            $script:ClaudeCodeSkillStatus = Install-BlenderMcpSkill -SourcePath $skillSourcePath -DestinationRoot $claudeCodeSkillRoot -Version $skillVersion -SourceLabel $skillSourceLabel -ForceUpdate ([bool]$ForceSkillUpdate)
        }
        if ($script:SelectedClaudeDesktop) {
            if (-not $skillArchivePath) {
                throw "Claude Desktop Skill archive was not prepared."
            }
            if ($script:DryRunEnabled) {
                $script:ClaudeDesktopSkillStatus = "would prepare upload"
                Write-Info (L "Would prepare Claude Desktop Skill upload: $skillArchivePath" "将准备 Claude Desktop Skill 上传包：$skillArchivePath")
            }
            else {
                $script:ClaudeDesktopSkillStatus = "manual upload required"
                Write-Ok (L "Verified Claude Desktop Skill ZIP: $skillArchivePath" "已校验 Claude Desktop Skill ZIP：$skillArchivePath")
                Write-Info (L "In Claude Desktop, open Customize > Skills, choose Create skill, then Upload a skill and select this ZIP." "在 Claude Desktop 中打开「Customize > Skills」，选择「Create skill」，再选择「Upload a skill」并上传此 ZIP。")
            }
        }
    }

    Write-Step (L "Finished" "完成")
    Write-Host ""
    if ($script:DryRunEnabled) {
        Write-Host (L "  Dry run completed successfully." "  试运行已成功完成。") -ForegroundColor Green
    }
    else {
        Write-Host (L "  Blender MCP installation completed successfully." "  Blender MCP 安装成功。") -ForegroundColor Green
    }
    Write-Info (L "Server        : $serverExecutable" "服务端        ：$serverExecutable")
    Write-Info (L "Blender       : $script:BlenderStatus" "Blender       ：$script:BlenderStatus")
    Write-Info (L "Codex         : $script:CodexStatus" "Codex         ：$script:CodexStatus")
    Write-Info (L "Claude Code   : $script:ClaudeCodeStatus" "Claude Code   ：$script:ClaudeCodeStatus")
    Write-Info (L "Claude Desktop: $script:ClaudeDesktopStatus" "Claude Desktop：$script:ClaudeDesktopStatus")
    Write-Info (L "Codex Skill   : $script:CodexSkillStatus" "Codex Skill   ：$script:CodexSkillStatus")
    Write-Info (L "Claude Skill  : Code=$script:ClaudeCodeSkillStatus, Desktop=$script:ClaudeDesktopSkillStatus" "Claude Skill  ：Code=$script:ClaudeCodeSkillStatus，Desktop=$script:ClaudeDesktopSkillStatus")
    if ($archivePath) { Write-Info "ZIP           : $archivePath" }
    if ($skillArchivePath) { Write-Info "Skill ZIP     : $skillArchivePath" }
    if ($release) { Write-Info "Release       : $($release.html_url)" }
    Write-Host ""
    Write-Host (L "  Next steps" "  后续步骤") -ForegroundColor Cyan
    if ($selectedBlenderPaths.Count -gt 0) {
        Write-Info (L `
            "1. Open a selected Blender version and find BlenderMCP in the 3D View sidebar (N)." `
            "1. 打开任一所选 Blender 版本，在 3D 视图侧栏（N）中找到 BlenderMCP。")
        Write-Info (L `
            "2. The local bridge starts automatically on port $blenderPort by default." `
            "2. 本地桥接服务默认会在端口 $blenderPort 自动启动。")
    }
    else {
        Write-Info (L "1. Install the Blender Extension later by running this installer again." "1. 稍后重新运行安装器，即可安装 Blender 扩展。")
    }
    $anyClientSelected = (
        $script:SelectedCodexCli -or
        $script:SelectedCodexDesktop -or
        $script:SelectedClaudeCode -or
        $script:SelectedClaudeDesktop
    )
    if ($anyClientSelected) {
        Write-Info (L "Restart the selected MCP clients so they load the blender_mcp server." "请重启所选 MCP 客户端，以加载 blender_mcp 服务端。")
    }
    else {
        Write-Info (L "Run the installer again after installing an MCP client to configure it." "安装 MCP 客户端后，请重新运行安装器完成配置。")
    }
    if ($script:ClaudeDesktopMcpbFallbackUsed) {
        Write-Info (L "Complete the MCPB confirmation inside Claude Desktop." "请在 Claude Desktop 中完成 MCPB 确认。")
    }
    if ($script:SelectedCodexCli -or $script:SelectedCodexDesktop -or $script:SelectedClaudeCode) {
        Write-Info (L "Start a new client task, or restart the client if the installed Skill is not discovered immediately." "请新建客户端任务；若没有立即发现已安装的 Skill，请重启客户端。")
    }
    if ($script:SelectedClaudeDesktop -and -not $SkipSkillInstallation) {
        Write-Info (L "Complete the Blender MCP Skill upload in Claude Desktop." "请在 Claude Desktop 中完成 Blender MCP Skill 上传。")
    }
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host (L "  Installation failed" "  安装失败") -ForegroundColor Red
    Write-Host "  -------------------" -ForegroundColor DarkRed
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    Write-Host (L `
        "  Tip: run .\install.ps1 -DryRun to inspect detection and commands." `
        "  提示：运行 .\install.ps1 -DryRun 可检查检测结果与将要执行的命令。") -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
