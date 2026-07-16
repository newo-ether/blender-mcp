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
