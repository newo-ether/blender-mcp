[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$installer = Join-Path $root "install.ps1"
$sourceSkill = Join-Path $root "skills\blender-mcp"
$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$caseRoot = Join-Path $tempBase ("blender-mcp-skill-installer-" + [guid]::NewGuid().ToString("N"))

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

try {
    . (Join-Path $PSScriptRoot "import_installer.ps1") -Root $root

    $script:UseChinese = $false
    $script:ClaudeDesktopSkillStatus = "not tested"
    $desktopReminder = & {
        Write-ClaudeDesktopSkillUploadReminder -ArchivePath "C:\verified\blender-mcp-skill-test.zip"
    } 6>&1 | Out-String
    Assert-True -Condition ($script:ClaudeDesktopSkillStatus -eq "manual upload required") -Message "Claude Desktop reminder did not preserve the pending status."
    Assert-True -Condition ($desktopReminder -match "not installed automatically") -Message "Claude Desktop reminder does not say that installation is incomplete."
    Assert-True -Condition ($desktopReminder -match "ACTION REQUIRED") -Message "Claude Desktop reminder does not identify the required user action."
    Assert-True -Condition ($desktopReminder -match "blender-mcp-skill-test\.zip") -Message "Claude Desktop reminder does not include the verified ZIP path."
    Assert-True -Condition ($desktopReminder -notmatch "\[OK\]") -Message "Claude Desktop pending upload was incorrectly reported as OK."

    $script:DryRunEnabled = $false
    $desktopCompletion = & { Write-InstallerCompletionHeadline } 6>&1 | Out-String
    Assert-True -Condition ($desktopCompletion -match "core installation completed") -Message "Final installer status does not distinguish core completion from pending Desktop Skill upload."
    Assert-True -Condition ($desktopCompletion -match "still required") -Message "Final installer status does not retain the required Desktop action."
    Assert-True -Condition ($desktopCompletion -notmatch "installation completed successfully") -Message "Final installer status incorrectly reports complete success while Desktop upload is pending."

    New-Item -ItemType Directory -Path $caseRoot -Force | Out-Null
    $testHome = Join-Path $caseRoot "home"
    $project = Join-Path $caseRoot "project"
    $sourceParent = Join-Path $caseRoot "source"
    New-Item -ItemType Directory -Path $testHome, $project, $sourceParent -Force | Out-Null
    $workingSkill = Join-Path $sourceParent "blender-mcp"
    Copy-Item -LiteralPath $sourceSkill -Destination $workingSkill -Recurse -Force

    $codexUser = Get-SkillInstallRoot -Client "Codex" -Scope "User" -UserHome $testHome
    $claudeUser = Get-SkillInstallRoot -Client "ClaudeCode" -Scope "User" -UserHome $testHome
    $codexProject = Get-SkillInstallRoot -Client "Codex" -Scope "Project" -ProjectPath $project
    $claudeProject = Get-SkillInstallRoot -Client "ClaudeCode" -Scope "Project" -ProjectPath $project
    Assert-True -Condition ($codexUser -eq (Join-Path $testHome ".agents\skills")) -Message "Codex user Skill path is incorrect."
    Assert-True -Condition ($claudeUser -eq (Join-Path $testHome ".claude\skills")) -Message "Claude Code user Skill path is incorrect."
    Assert-True -Condition ($codexProject -eq (Join-Path $project ".agents\skills")) -Message "Codex project Skill path is incorrect."
    Assert-True -Condition ($claudeProject -eq (Join-Path $project ".claude\skills")) -Message "Claude Code project Skill path is incorrect."

    $script:DryRunEnabled = $false
    $status = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $codexUser -Version "1.9.3" -SourceLabel "test" -ForceUpdate $false
    $destination = Join-Path $codexUser "blender-mcp"
    $manifest = Join-Path $codexUser ".blender-mcp-managed.json"
    Assert-True -Condition ($status -eq "installed") -Message "Fresh Skill installation status is incorrect."
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $destination "SKILL.md") -PathType Leaf) -Message "Fresh Skill installation is missing SKILL.md."
    Assert-True -Condition (Test-Path -LiteralPath $manifest -PathType Leaf) -Message "Fresh Skill installation is missing its ownership manifest."

    $repeat = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $codexUser -Version "1.9.3" -SourceLabel "test" -ForceUpdate $false
    Assert-True -Condition ($repeat -eq "already installed") -Message "Repeat installation was not idempotent."

    $installedSkill = Join-Path $destination "SKILL.md"
    Add-Content -LiteralPath $installedSkill -Value "`nLocal user edit" -Encoding UTF8
    $preserved = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $codexUser -Version "1.9.3" -SourceLabel "test" -ForceUpdate $false
    Assert-True -Condition ($preserved -eq "preserved local changes") -Message "Local Skill edits were not preserved by default."
    Assert-True -Condition ((Get-Content -LiteralPath $installedSkill -Raw -Encoding UTF8) -match "Local user edit") -Message "Preserved Skill content changed."

    $forced = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $codexUser -Version "1.9.3" -SourceLabel "test" -ForceUpdate $true
    Assert-True -Condition ($forced -eq "updated") -Message "Forced Skill update did not replace local edits."
    Assert-True -Condition ((Get-Content -LiteralPath $installedSkill -Raw -Encoding UTF8) -notmatch "Local user edit") -Message "Forced Skill update retained the local edit."

    $sourceMarker = Join-Path $workingSkill "references\update-marker.md"
    Set-Content -LiteralPath $sourceMarker -Value "managed update" -Encoding UTF8
    $managedUpdate = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $codexUser -Version "1.9.4" -SourceLabel "test-update" -ForceUpdate $false
    Assert-True -Condition ($managedUpdate -eq "updated") -Message "Unmodified owned Skill did not update automatically."
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $destination "references\update-marker.md") -PathType Leaf) -Message "Managed Skill update content is missing."

    $script:DryRunEnabled = $true
    $dryRoot = Join-Path $caseRoot "dry\.agents\skills"
    $dryStatus = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $dryRoot -Version "1.9.4" -SourceLabel "dry-run" -ForceUpdate $false
    Assert-True -Condition ($dryStatus -eq "would install") -Message "Dry-run Skill status is incorrect."
    Assert-True -Condition (-not (Test-Path -LiteralPath $dryRoot)) -Message "Dry-run created a Skill destination."

    $script:DryRunEnabled = $false
    $invalidRoot = Join-Path $caseRoot "invalid\.agents\skills"
    $invalidDestination = Join-Path $invalidRoot "blender-mcp"
    New-Item -ItemType Directory -Path $invalidDestination -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $invalidDestination "notes.txt") -Value "user content" -Encoding UTF8
    $invalidPreserved = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $invalidRoot -Version "1.9.4" -SourceLabel "test" -ForceUpdate $false
    Assert-True -Condition ($invalidPreserved -eq "preserved invalid install") -Message "Invalid same-name Skill folder was not preserved."
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $invalidDestination "notes.txt") -PathType Leaf) -Message "Preserved invalid Skill folder changed."
    $invalidForced = Install-BlenderMcpSkill -SourcePath $workingSkill -DestinationRoot $invalidRoot -Version "1.9.4" -SourceLabel "test" -ForceUpdate $true
    Assert-True -Condition ($invalidForced -eq "updated") -Message "Forced replacement of invalid Skill folder failed."
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $invalidDestination "SKILL.md") -PathType Leaf) -Message "Forced replacement did not install the Skill."

    $archive = Join-Path $caseRoot "blender-mcp-skill-test.zip"
    Compress-Archive -LiteralPath $workingSkill -DestinationPath $archive -CompressionLevel Optimal
    $expanded = Expand-BlenderMcpSkillArchive -ArchivePath $archive -InstallBase (Join-Path $caseRoot "install") -Version "1.9.4"
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $expanded "SKILL.md") -PathType Leaf) -Message "Verified Skill archive did not expand to the expected source layout."

    Write-Host "Installer Skill tests passed." -ForegroundColor Green
}
finally {
    if (
        (Test-Path -LiteralPath $caseRoot -PathType Container) -and
        $caseRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        Remove-Item -LiteralPath $caseRoot -Recurse -Force
    }
}
