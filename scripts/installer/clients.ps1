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
