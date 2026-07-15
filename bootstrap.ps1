#Requires -Version 5.1

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$installerUrl = "https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1"
$installerText = [string](Invoke-RestMethod -Uri $installerUrl -UseBasicParsing)
if ([string]::IsNullOrWhiteSpace($installerText)) {
    throw "The Blender MCP installer download was empty: $installerUrl"
}

# install.ps1 carries a UTF-8 BOM so Windows PowerShell 5.1 can parse its
# localized text from disk. ScriptBlock.Create receives that BOM as a character
# when the source comes from HTTP, so remove only the leading BOM before parsing.
$installer = [scriptblock]::Create($installerText.TrimStart([char]0xFEFF))
& $installer
