function Invoke-BlenderMcpInstall {
    try {
        Write-Banner

        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $hasSourceCheckout = $false
        $repoRoot = $null
        if (-not [string]::IsNullOrWhiteSpace($script:InstallerEntryRoot)) {
            $candidateRoot = [System.IO.Path]::GetFullPath($script:InstallerEntryRoot)
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
            "Codex / ChatGPT      : cli=$($clientDetection.CodexCliFound), desktop=$($clientDetection.CodexDesktopFound), config=$($clientDetection.CodexConfigPath)" `
            "Codex / ChatGPT      ：CLI=$($clientDetection.CodexCliFound)，桌面端=$($clientDetection.CodexDesktopFound)，配置=$($clientDetection.CodexConfigPath)")
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
        $pythonBootstrapLauncher = $null
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
            $pythonBootstrapLauncher = $launcher
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
        $codexTomlPython = $venvPython
        $codexTomlPythonArguments = @()
        if ($script:DryRunEnabled -and -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            $codexTomlPython = $pythonBootstrapLauncher.Command
            $codexTomlPythonArguments = @($pythonBootstrapLauncher.Prefix)
        }
        if ($serverInstallRequired) {
            Invoke-CheckedCommand -FilePath $venvPython -ArgumentList $pipArguments -Description (L "Installing Blender MCP and Python dependencies..." "正在安装 Blender MCP 与 Python 依赖……")
        }
        Invoke-CheckedCommand -FilePath $venvPython -ArgumentList @(
            "-c",
            "import asyncio; from blender_mcp.app import mcp; tools = asyncio.run(mcp.list_tools()); names = {tool.name for tool in tools}; required = {'get_blender_documentation_context', 'search_blender_docs', 'get_blender_doc_page', 'get_runtime_automation_context', 'search_geometry_node_types', 'search_blender_node_assets', 'import_blender_node_asset', 'create_node_group', 'list_node_trees', 'ensure_scene_compositor_tree', 'ensure_geometry_nodes_modifier', 'get_node_tree_index', 'export_node_tree', 'get_node_type_schema', 'validate_node_tree_patch', 'apply_node_tree_patch'}; missing = sorted(required - names); print(f'Registered MCP tools: {len(tools)}'); print(f'Missing required tools: {missing}' if missing else 'Knowledge and structured-node tools: ready'); assert len(tools) >= 44 and not missing"
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
                $manifest = Get-Content -LiteralPath (Join-Path $repoRoot "blender_extension\blender_manifest.toml") -Raw
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
            if ($clientDetection.CodexCommand) {
                Register-CodexMcp -CodexExecutable $clientDetection.CodexCommand -ServerExecutable $serverExecutable -Workspace $workspace -Port $blenderPort -PreserveExisting ([bool]$PreserveExistingMcpEntries)
            }
            elseif ($script:SelectedCodexDesktop) {
                Register-CodexConfigMcp -ConfigPath $clientDetection.CodexConfigPath -PythonExecutable $codexTomlPython -PythonArguments $codexTomlPythonArguments -ServerExecutable $serverExecutable -Workspace $workspace -Port $blenderPort -PreserveExisting ([bool]$PreserveExistingMcpEntries)
            }
            else {
                Write-WarningLine (L "Codex configuration interface was not found; Codex configuration was not changed." "未找到 Codex 配置接口；未更改 Codex 配置。")
                $script:CodexStatus = "configuration unavailable"
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
                    Write-ClaudeDesktopSkillUploadReminder -ArchivePath $skillArchivePath
                }
            }
        }

        Write-Step (L "Finished" "完成")
        Write-Host ""
        Write-InstallerCompletionHeadline
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
        if ($script:ClaudeDesktopSkillStatus -eq "manual upload required") {
            Write-WarningLine (L `
                "ACTION REQUIRED: upload the Blender MCP Skill in Claude Desktop from $skillArchivePath" `
                "需要操作：请在 Claude Desktop 中上传 Blender MCP Skill：$skillArchivePath")
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
}
