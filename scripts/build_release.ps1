#Requires -Version 5.1

<#
.SYNOPSIS
    Build all assets required by a Blender MCP GitHub Release.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BlenderPath,

    [string]$OutputDirectory = "",

    [string]$PythonPath = ""
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $root "dist"
}
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
if ($PythonPath) {
    $python = [System.IO.Path]::GetFullPath($PythonPath)
}
else {
    $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found on PATH. Pass -PythonPath with a Python 3.10+ executable."
    }
    $python = $pythonCommand.Source
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python executable not found: $python"
}
if (-not (Test-Path -LiteralPath $BlenderPath -PathType Leaf)) {
    throw "Blender executable not found: $BlenderPath"
}

$projectText = Get-Content -LiteralPath (Join-Path $root "pyproject.toml") -Raw
if ($projectText -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
    throw "Could not read project version."
}
$version = $Matches[1]

$manifestPath = Join-Path $root "packaging\claude_desktop\manifest.json"
$mcpbManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($mcpbManifest.version -ne $version) {
    throw "MCPB manifest version $($mcpbManifest.version) differs from project $version."
}

New-Item -ItemType Directory -Path $output -Force | Out-Null

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    # Build tools (python -m build, npx, blender) legitimately write progress and
    # diagnostics to stderr. Under $ErrorActionPreference = 'Stop' (set above for
    # cmdlet safety), Windows PowerShell 5.1 promotes each stderr line to a
    # terminating NativeCommandError and aborts before $LASTEXITCODE is inspected,
    # so the build fails even when the tool exits 0. Run native tools with
    # 'Continue' so stderr is non-terminating, capture it to a temp file for
    # visibility on failure, and fail cleanly on a non-zero exit code. PowerShell 7
    # is unaffected but this keeps the documented PS 5.1 support honest.
    $previousEAP = $ErrorActionPreference
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        $ErrorActionPreference = 'Continue'
        & $FilePath @ArgumentList 2>$stderrFile
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            if (Test-Path -LiteralPath $stderrFile) {
                Get-Content -LiteralPath $stderrFile -ErrorAction SilentlyContinue |
                    ForEach-Object { Write-Host $_ -ForegroundColor Red }
            }
            throw $FailureMessage
        }
    }
    finally {
        $ErrorActionPreference = $previousEAP
        Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[1/5] Building Blender Extension ZIP..." -ForegroundColor Cyan
Invoke-Native -FilePath $python `
    -ArgumentList @((Join-Path $root "scripts\build_blender_extension.py"), "--blender", $BlenderPath, "--output-dir", $output) `
    -FailureMessage "Blender Extension build failed."

Write-Host "[2/5] Building Python wheel..." -ForegroundColor Cyan
Invoke-Native -FilePath $python `
    -ArgumentList @("-m", "build", "--wheel", "--outdir", $output, $root) `
    -FailureMessage "Python wheel build failed."

Write-Host "[3/5] Building portable Agent Skill ZIP..." -ForegroundColor Cyan
Invoke-Native -FilePath $python `
    -ArgumentList @((Join-Path $root "scripts\build_skill_package.py"), "--output-dir", $output, "--version", $version) `
    -FailureMessage "Agent Skill package build failed."
$skillPath = Join-Path $output ("blender-mcp-skill-{0}.zip" -f $version)

Write-Host "[4/5] Validating and packing Claude Desktop MCPB..." -ForegroundColor Cyan
$mcpbPath = Join-Path $output ("blender_mcp-{0}.mcpb" -f $version)
if (Test-Path -LiteralPath $mcpbPath) {
    Remove-Item -LiteralPath $mcpbPath -Force
}
$mcpbStage = Join-Path ([System.IO.Path]::GetTempPath()) (
    "blender-mcp-mcpb-{0}" -f [System.Guid]::NewGuid().ToString("N")
)
try {
    New-Item -ItemType Directory -Path $mcpbStage -Force | Out-Null
    Copy-Item -Path (Join-Path $root "packaging\claude_desktop\*") -Destination $mcpbStage -Recurse -Force
    $pythonStage = Join-Path $mcpbStage "server\python\blender_mcp"
    $schemaStage = Join-Path $mcpbStage "server\schemas"
    New-Item -ItemType Directory -Path $pythonStage -Force | Out-Null
    New-Item -ItemType Directory -Path $schemaStage -Force | Out-Null
    Copy-Item -Path (Join-Path $root "src\blender_mcp\*") -Destination $pythonStage -Recurse -Force
    Copy-Item -Path (Join-Path $root "schemas\*.json") -Destination $schemaStage -Force

    $stagedManifest = Join-Path $mcpbStage "manifest.json"
    Invoke-Native -FilePath "npx" `
        -ArgumentList @("--yes", "@anthropic-ai/mcpb", "validate", $stagedManifest) `
        -FailureMessage "MCPB manifest validation failed."
    Invoke-Native -FilePath "npx" `
        -ArgumentList @("--yes", "@anthropic-ai/mcpb", "pack", $mcpbStage, $mcpbPath) `
        -FailureMessage "MCPB packing failed."
}
finally {
    if (Test-Path -LiteralPath $mcpbStage) {
        Remove-Item -LiteralPath $mcpbStage -Recurse -Force
    }
}

Write-Host "[5/5] Writing SHA256SUMS.txt..." -ForegroundColor Cyan
$assets = @(
    (Join-Path $output ("blender_mcp-{0}.zip" -f $version)),
    (Join-Path $output ("blender_mcp-{0}-py3-none-any.whl" -f $version)),
    $mcpbPath,
    $skillPath
)
foreach ($asset in $assets) {
    if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) {
        throw "Expected release asset missing: $asset"
    }
}
$checksumPath = Join-Path $output "SHA256SUMS.txt"
$checksumLines = foreach ($asset in $assets) {
    $hash = (Get-FileHash -LiteralPath $asset -Algorithm SHA256).Hash.ToLowerInvariant()
    "{0}  {1}" -f $hash, (Split-Path -Leaf $asset)
}
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($checksumPath, $checksumLines, $utf8WithoutBom)

Write-Host "Verifying release contents and checksums..." -ForegroundColor Cyan
Invoke-Native -FilePath $python `
    -ArgumentList @((Join-Path $root "scripts\verify_release_assets.py"), "--dist", $output, "--version", $version) `
    -FailureMessage "Release asset verification failed."

Write-Host "Release assets ready:" -ForegroundColor Green
foreach ($asset in $assets + @($checksumPath)) {
    $item = Get-Item -LiteralPath $asset
    Write-Host ("  {0} ({1:N0} bytes)" -f $item.Name, $item.Length)
}
