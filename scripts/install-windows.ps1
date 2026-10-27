[CmdletBinding()]
param(
    [string]$Workspace = (Join-Path $env:USERPROFILE "lancompute-workspace"),
    [string]$StateDir = (Join-Path $env:USERPROFILE ".lancompute-mcp"),
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$SshHost = "lancompute-windows"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'"
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.10 or newer first." }
        & py -3 -m venv (Join-Path $ProjectRoot ".venv")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'"
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.10 or newer first." }
        & python -m venv (Join-Path $ProjectRoot ".venv")
    } else {
        throw "Python 3.10+ was not found. Install Python, then rerun this script."
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python virtual environment." }
}

& $VenvPython -m pip install --editable $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

$Workspace = [IO.Path]::GetFullPath($Workspace)
$StateDir = [IO.Path]::GetFullPath($StateDir)
New-Item -ItemType Directory -Force -Path $Workspace, $StateDir | Out-Null
$Launcher = Join-Path $ProjectRoot "start-mcp.local.cmd"
$LauncherContent = @"
@echo off
chcp 65001 >nul
setlocal
set "LANCOMPUTE_WORKSPACE=$Workspace"
set "LANCOMPUTE_STATE_DIR=$StateDir"
"%~dp0.venv\Scripts\lancompute-mcp.exe"
exit /b %errorlevel%
"@
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText(
    $Launcher, $LauncherContent.Replace("`r`n", "`n").Replace("`n", "`r`n"), $Utf8NoBom
)

# JSON string escaping is also valid for these TOML basic-string values.
$RemoteCommand = 'cmd.exe /d /c "' + $Launcher.Replace('\', '/') + '"'
$QuotedRemote = ConvertTo-Json -InputObject $RemoteCommand -Compress
$Config = @"
[mcp_servers.lancompute]
command = "ssh"
args = ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "$SshHost", $QuotedRemote]
startup_timeout_sec = 30
tool_timeout_sec = 60
"@
$ConfigPath = Join-Path $ProjectRoot "codex-config.local.toml"
[IO.File]::WriteAllText($ConfigPath, $Config, $Utf8NoBom)

Write-Host "Installed lancompute-mcp."
Write-Host "Workspace: $Workspace"
Write-Host "Launcher: $Launcher"
Write-Host "On the Codex host, configure SSH host '$SshHost', then add this to ~/.codex/config.toml:"
Write-Host ""
Write-Host $Config
