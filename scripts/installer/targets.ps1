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
    $codexAvailable = [bool]($Detection.CodexCliFound -or $Detection.CodexDesktopFound)
    $entries += [PSCustomObject]@{
        Group = L "MCP clients" "MCP 客户端"
        Label = if ($Detection.CodexCliFound) {
            L `
                "Codex / ChatGPT - shared MCP config - $($Detection.CodexCommand)" `
                "Codex / ChatGPT - 共用 MCP 配置 - $($Detection.CodexCommand)"
        }
        elseif ($Detection.CodexDesktopFound) {
            L `
                "Codex Desktop - shared config file - $($Detection.CodexConfigPath)" `
                "Codex Desktop - 共用配置文件 - $($Detection.CodexConfigPath)"
        }
        else { L "Codex / ChatGPT - not detected" "Codex / ChatGPT - 未检测到" }
        Enabled = [bool]($codexAvailable -and -not $DisableCodex)
        Selected = [bool]($codexAvailable -and -not $DisableCodex)
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
                    CodexCli = [bool]($codexSelected -and $Detection.CodexCliFound)
                    CodexDesktop = [bool]($codexSelected -and $Detection.CodexDesktopFound)
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
        $codexSelected = [bool](
            ($Detection.CodexCliFound -or $Detection.CodexDesktopFound) -and
            -not $DisableCodex
        )
        return [PSCustomObject]@{
            Cancelled = $false
            CodexCli = [bool]($codexSelected -and $Detection.CodexCliFound)
            CodexDesktop = [bool]($codexSelected -and $Detection.CodexDesktopFound)
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
        }
        elseif ($Detection.CodexDesktopFound) {
            L `
                "Codex Desktop - shared config file: $($Detection.CodexConfigPath)" `
                "Codex Desktop - 共用配置文件：$($Detection.CodexConfigPath)"
        }
        else { L "Codex / ChatGPT - not detected" "Codex / ChatGPT - 未检测到" }
        $codexAvailable = [bool]($Detection.CodexCliFound -or $Detection.CodexDesktopFound)
        $codexCheck.Checked = [bool]($codexAvailable -and -not $DisableCodex)
        $codexCheck.Enabled = [bool]($codexAvailable -and -not $DisableCodex)
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
            CodexCli = [bool]($codexCheck.Checked -and $Detection.CodexCliFound)
            CodexDesktop = [bool]($codexCheck.Checked -and $Detection.CodexDesktopFound)
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
