
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

function Write-ClaudeDesktopSkillUploadReminder {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchivePath
    )

    $script:ClaudeDesktopSkillStatus = "manual upload required"
    Write-WarningLine (L `
        "Claude Desktop Skill is not installed automatically." `
        "Claude Desktop Skill 不会自动安装。")
    Write-Info (L `
        "Verified upload ZIP: $ArchivePath" `
        "已校验的上传 ZIP：$ArchivePath")
    Write-WarningLine (L `
        "ACTION REQUIRED: in Claude Desktop, open Customize > Skills, choose Create skill, then Upload a skill and select this ZIP." `
        "需要操作：在 Claude Desktop 中打开「Customize > Skills」，选择「Create skill」，再选择「Upload a skill」并上传此 ZIP。")
}

function Write-InstallerCompletionHeadline {
    if ($script:DryRunEnabled) {
        Write-Host (L "  Dry run completed successfully." "  试运行已成功完成。") -ForegroundColor Green
        return
    }
    if ($script:ClaudeDesktopSkillStatus -eq "manual upload required") {
        Write-Host (L `
            "  Blender MCP core installation completed; Claude Desktop Skill upload is still required." `
            "  Blender MCP 核心安装已完成；Claude Desktop Skill 仍需手动上传。") -ForegroundColor Yellow
        return
    }
    Write-Host (L "  Blender MCP installation completed successfully." "  Blender MCP 安装成功。") -ForegroundColor Green
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
    $previousLastExitCodeVariable = Get-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
    $hadPreviousLastExitCode = $null -ne $previousLastExitCodeVariable
    $previousLastExitCode = if ($hadPreviousLastExitCode) {
        $previousLastExitCodeVariable.Value
    }
    else {
        $null
    }
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
        if ($hadPreviousLastExitCode) {
            Set-Variable -Name LASTEXITCODE -Scope Global -Value $previousLastExitCode
        }
        else {
            Remove-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
        }
    }

    return [PSCustomObject]@{
        ExitCode = $exitCode
        Output = @($capturedOutput)
    }
}
