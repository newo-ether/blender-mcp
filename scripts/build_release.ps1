#Requires -Version 5.1

<#
.SYNOPSIS
    Build all assets required by a Blender MCP GitHub Release.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BlenderPath,

    [string]$OutputDirectory = ""
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $root "dist"
}
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Create .venv before building release assets: $python"
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

Write-Host "[1/4] Building Blender Extension ZIP..." -ForegroundColor Cyan
& $python (Join-Path $root "scripts\build_blender_extension.py") --blender $BlenderPath --output-dir $output
if ($LASTEXITCODE -ne 0) { throw "Blender Extension build failed." }

Write-Host "[2/4] Building Python wheel..." -ForegroundColor Cyan
& $python -m pip wheel --quiet --no-deps --wheel-dir $output $root
if ($LASTEXITCODE -ne 0) { throw "Python wheel build failed." }

Write-Host "[3/4] Validating and packing Claude Desktop MCPB..." -ForegroundColor Cyan
& npx --yes '@anthropic-ai/mcpb' validate $manifestPath
if ($LASTEXITCODE -ne 0) { throw "MCPB manifest validation failed." }
$mcpbPath = Join-Path $output ("blender_mcp-{0}.mcpb" -f $version)
if (Test-Path -LiteralPath $mcpbPath) {
    Remove-Item -LiteralPath $mcpbPath -Force
}
& npx --yes '@anthropic-ai/mcpb' pack (Join-Path $root "packaging\claude_desktop") $mcpbPath
if ($LASTEXITCODE -ne 0) { throw "MCPB packing failed." }

Write-Host "[4/4] Writing SHA256SUMS.txt..." -ForegroundColor Cyan
$assets = @(
    (Join-Path $output ("blender_mcp-{0}.zip" -f $version)),
    (Join-Path $output ("blender_mcp-{0}-py3-none-any.whl" -f $version)),
    $mcpbPath
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

Write-Host "Release assets ready:" -ForegroundColor Green
foreach ($asset in $assets + @($checksumPath)) {
    $item = Get-Item -LiteralPath $asset
    Write-Host ("  {0} ({1:N0} bytes)" -f $item.Name, $item.Length)
}
