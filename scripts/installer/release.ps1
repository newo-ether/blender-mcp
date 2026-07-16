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
