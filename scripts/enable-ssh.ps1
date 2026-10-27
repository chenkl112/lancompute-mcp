#Requires -RunAsAdministrator
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Capability = Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
if ($Capability.State -ne "Installed") {
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
$Rule = Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue
if ($Rule) {
    Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Enabled True -RemoteAddress LocalSubnet
} else {
    New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName "OpenSSH (local subnet)" `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -RemoteAddress LocalSubnet `
        | Out-Null
}
Write-Host "OpenSSH is running. TCP 22 is allowed from the local subnet."
Write-Host "Configure public-key login before using the MCP connection."
whoami
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254.*"
} | Select-Object InterfaceAlias, IPAddress
