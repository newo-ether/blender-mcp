function ConvertTo-TomlBasicString {
    param([string]$Value)

    $escaped = ([string]$Value).Replace('\', '\\').Replace('"', '\"')
    $escaped = $escaped.Replace("`b", '\b').Replace("`t", '\t')
    $escaped = $escaped.Replace("`n", '\n').Replace("`f", '\f').Replace("`r", '\r')
    return '"' + $escaped + '"'
}

function Get-TomlSections {
    param([string]$Text)

    $source = [string]$Text
    $headers = [regex]::Matches(
        $source,
        '(?m)^[ \t]*\[(?<name>[^\]\r\n]+)\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)'
    )
    $sections = @()
    for ($index = 0; $index -lt $headers.Count; $index += 1) {
        $header = $headers[$index]
        $end = if ($index + 1 -lt $headers.Count) { $headers[$index + 1].Index } else { $source.Length }
        $sections += [PSCustomObject]@{
            Name = [string]$header.Groups['name'].Value
            Start = $header.Index
            Length = $end - $header.Index
            Text = $source.Substring($header.Index, $end - $header.Index)
        }
    }
    return @($sections)
}

function Get-NormalizedTomlSectionName {
    param([string]$Name)

    return (([string]$Name).Replace('"', '').Replace("'", '') -replace '\s+', '')
}

function Test-CodexBlenderMcpSection {
    param(
        [string]$Name,
        [switch]$RootOnly
    )

    $normalized = Get-NormalizedTomlSectionName -Name $Name
    if ($RootOnly) {
        return $normalized -eq 'mcp_servers.blender_mcp'
    }
    return (
        $normalized -eq 'mcp_servers.blender_mcp' -or
        $normalized.StartsWith('mcp_servers.blender_mcp.', [System.StringComparison]::Ordinal)
    )
}

function Get-CodexBlenderMcpConfigState {
    param([string]$Text)

    $sections = @(Get-TomlSections -Text $Text)
    $main = $sections | Where-Object {
        Test-CodexBlenderMcpSection -Name $_.Name -RootOnly
    } | Select-Object -First 1
    if ($null -eq $main) {
        return [PSCustomObject]@{
            Exists = $false
            Sections = @()
        }
    }

    $targetSections = @($sections | Where-Object {
        Test-CodexBlenderMcpSection -Name $_.Name
    })
    return [PSCustomObject]@{
        Exists = $true
        Sections = @($targetSections)
    }
}

function Remove-CodexBlenderMcpConfigSections {
    param(
        [string]$Text,
        [object[]]$Sections
    )

    $updated = [string]$Text
    foreach ($section in @($Sections | Sort-Object Start -Descending)) {
        $updated = $updated.Remove([int]$section.Start, [int]$section.Length)
    }
    return $updated
}

function Get-CodexConfigStateWithPython {
    param(
        [string]$PythonExecutable,
        [string[]]$PythonArguments = @(),
        [string]$ConfigPath
    )

    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Python TOML validator was not found: $PythonExecutable"
    }
    $probeScript = @'
import json
import pathlib
import sys

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        from pip._vendor import tomli as tomllib

path = pathlib.Path(sys.argv[1])
data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
servers = data.get("mcp_servers", {})
if not isinstance(servers, dict):
    raise TypeError("mcp_servers must be a TOML table")
present = "blender_mcp" in servers
entry = servers.get("blender_mcp")
if present and not isinstance(entry, dict):
    raise TypeError("mcp_servers.blender_mcp must be a TOML table")
entry = entry or {}
env = entry.get("env", {})
if not isinstance(env, dict):
    raise TypeError("mcp_servers.blender_mcp.env must be a TOML table")
print(json.dumps({
    "exists": present,
    "command": entry.get("command"),
    "workspace": env.get("BLENDER_MCP_WORKSPACE"),
    "host": env.get("BLENDER_HOST"),
    "port": env.get("BLENDER_PORT"),
}))
'@
    $encodedProbe = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($probeScript))
    $pythonCommand = "import base64;exec(compile(base64.b64decode('$encodedProbe'),'<blender-mcp-installer>','exec'))"
    $arguments = @($PythonArguments) + @('-c', $pythonCommand, $ConfigPath)
    $probe = Invoke-CapturedCommand -FilePath $PythonExecutable -ArgumentList $arguments -IncludeErrorOutput
    if ($probe.ExitCode -ne 0) {
        throw (L `
            "Codex config.toml is invalid or cannot be safely parsed; it was not changed: $($probe.Output -join ' ')" `
            "Codex config.toml 无效或无法安全解析；未作更改：$($probe.Output -join ' ')")
    }
    try {
        return ($probe.Output -join "`n") | ConvertFrom-Json
    }
    catch {
        throw "Python TOML validator returned unreadable output; Codex configuration was not changed."
    }
}

function Register-CodexConfigMcp {
    param(
        [string]$ConfigPath,
        [string]$PythonExecutable,
        [string[]]$PythonArguments = @(),
        [string]$ServerExecutable,
        [string]$Workspace,
        [int]$Port,
        [bool]$PreserveExisting
    )

    $resolvedConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
    $existingText = if (Test-Path -LiteralPath $resolvedConfigPath -PathType Leaf) {
        [System.IO.File]::ReadAllText($resolvedConfigPath, [System.Text.Encoding]::UTF8)
    }
    else { '' }
    $textState = Get-CodexBlenderMcpConfigState -Text $existingText
    $parsedState = if (Test-Path -LiteralPath $resolvedConfigPath -PathType Leaf) {
        Get-CodexConfigStateWithPython -PythonExecutable $PythonExecutable -PythonArguments $PythonArguments -ConfigPath $resolvedConfigPath
    }
    else {
        [PSCustomObject]@{
            exists = $false
            command = $null
            workspace = $null
            host = $null
            port = $null
        }
    }
    if ([bool]$parsedState.exists -ne [bool]$textState.Exists) {
        throw (L `
            "The existing blender_mcp TOML uses a layout the installer cannot safely replace; it was not changed." `
            "现有 blender_mcp TOML 使用了安装器无法安全替换的布局；未作更改。")
    }
    $configState = [PSCustomObject]@{
        Exists = [bool]$parsedState.exists
        Command = [string]$parsedState.command
        Workspace = [string]$parsedState.workspace
        Host = [string]$parsedState.host
        Port = [string]$parsedState.port
        Sections = @($textState.Sections)
    }
    $matches = (
        $configState.Exists -and
        (Test-SamePath -Left $configState.Command -Right $ServerExecutable) -and
        (Test-SamePath -Left $configState.Workspace -Right $Workspace) -and
        $configState.Host -eq 'localhost' -and
        $configState.Port -eq [string]$Port
    )

    if ($matches) {
        Write-Ok (L "Codex already has the matching blender_mcp configuration." "Codex 已有完全一致的 blender_mcp 配置。")
        $script:CodexStatus = "already configured"
        return
    }
    if ($configState.Exists -and $PreserveExisting) {
        Write-WarningLine (L "Codex already has a different blender_mcp entry; it was preserved." "Codex 已有不同的 blender_mcp 配置，现按要求保留。")
        Write-Info (L "Re-run without -PreserveExistingMcpEntries to update that entry." "如需更新，请不要使用 -PreserveExistingMcpEntries，重新运行安装器。")
        $script:CodexStatus = "existing different entry preserved"
        return
    }

    $newline = if ($existingText -match "`r`n") { "`r`n" } else { "`n" }
    $blockLines = @(
        '[mcp_servers.blender_mcp]',
        ('command = ' + (ConvertTo-TomlBasicString -Value ([System.IO.Path]::GetFullPath($ServerExecutable)))),
        '',
        '[mcp_servers.blender_mcp.env]',
        ('BLENDER_MCP_WORKSPACE = ' + (ConvertTo-TomlBasicString -Value ([System.IO.Path]::GetFullPath($Workspace)))),
        'BLENDER_HOST = "localhost"',
        ('BLENDER_PORT = ' + (ConvertTo-TomlBasicString -Value ([string]$Port)))
    )
    $block = $blockLines -join $newline
    $updatedText = if ($configState.Exists) {
        Remove-CodexBlenderMcpConfigSections -Text $existingText -Sections $configState.Sections
    }
    else { $existingText }
    $updatedText = $updatedText.TrimEnd()
    if ($updatedText) { $updatedText += $newline + $newline }
    $updatedText += $block + $newline

    if ($script:DryRunEnabled) {
        Write-Info (L "Would write the shared Codex configuration: $resolvedConfigPath" "将写入 Codex 共用配置：$resolvedConfigPath")
        $script:CodexStatus = if ($configState.Exists) { "would be updated" } else { "would be configured" }
        return
    }

    $configDirectory = Split-Path -Parent $resolvedConfigPath
    if (-not (Test-Path -LiteralPath $configDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
    }
    $tempPath = Join-Path $configDirectory ('.config.toml.' + [guid]::NewGuid().ToString('N') + '.tmp')
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tempPath, $updatedText, $utf8NoBom)
    try {
        $verifiedState = Get-CodexConfigStateWithPython -PythonExecutable $PythonExecutable -PythonArguments $PythonArguments -ConfigPath $tempPath
        $verified = (
            [bool]$verifiedState.exists -and
            (Test-SamePath -Left ([string]$verifiedState.command) -Right $ServerExecutable) -and
            (Test-SamePath -Left ([string]$verifiedState.workspace) -Right $Workspace) -and
            [string]$verifiedState.host -eq 'localhost' -and
            [string]$verifiedState.port -eq [string]$Port
        )
        if (-not $verified) {
            throw "The candidate Codex TOML did not contain the verified blender_mcp configuration."
        }
        if (Test-Path -LiteralPath $resolvedConfigPath -PathType Leaf) {
            $backupPath = $resolvedConfigPath + '.blender-mcp-' + (Get-Date -Format 'yyyyMMdd-HHmmssfff') + '.bak'
            [System.IO.File]::Replace($tempPath, $resolvedConfigPath, $backupPath, $true)
            Write-Ok (L "Backed up the previous Codex configuration to $backupPath" "已将原 Codex 配置备份到 $backupPath")
        }
        else {
            Move-Item -LiteralPath $tempPath -Destination $resolvedConfigPath
        }
    }
    finally {
        if (Test-Path -LiteralPath $tempPath -PathType Leaf) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }

    if ($configState.Exists) {
        Write-Ok (L "Codex shared MCP entry blender_mcp was updated without Codex CLI." "无需 Codex CLI，已更新 Codex 共用 MCP 配置 blender_mcp。")
        $script:CodexStatus = "updated (config file)"
    }
    else {
        Write-Ok (L "Codex shared MCP entry blender_mcp is configured without Codex CLI." "无需 Codex CLI，已配置 Codex 共用 MCP 项 blender_mcp。")
        $script:CodexStatus = "configured (config file)"
    }
}
