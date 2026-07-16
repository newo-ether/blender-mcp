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

function Get-CodexConfigPath {
    param(
        [string]$CodexHome = $env:CODEX_HOME,
        [string]$UserHome = ""
    )

    if ($CodexHome) {
        return Join-Path ([System.IO.Path]::GetFullPath($CodexHome)) "config.toml"
    }
    if (-not $UserHome) {
        $UserHome = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    }
    if (-not $UserHome) { $UserHome = $env:USERPROFILE }
    if (-not $UserHome) {
        throw (L "Could not locate the current user's home directory for Codex configuration." "无法确定当前用户的主目录，不能定位 Codex 配置。")
    }
    return Join-Path ([System.IO.Path]::GetFullPath($UserHome)) ".codex\config.toml"
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
        CodexConfigPath = Get-CodexConfigPath
        ClaudeCommand = $claudeCommand
        ClaudeCodeFound = [bool]$claudeCommand
        ClaudeDesktopFound = [bool]$claudeDesktop.Found
        ClaudeDesktopEvidence = [string]$claudeDesktop.Evidence
        ClaudeDesktopLaunchKind = [string]$claudeDesktop.LaunchKind
        ClaudeDesktopLaunchTarget = [string]$claudeDesktop.LaunchTarget
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
