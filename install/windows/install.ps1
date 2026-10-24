# Installs the playtime agent for the current Windows user (no admin needed):
#   - copies playtime_agent\ to %LOCALAPPDATA%\SteamPlaytime\app
#   - writes %APPDATA%\SteamPlaytime\config.json (only if it doesn't exist yet)
#   - registers a Task Scheduler task that runs it hidden (pythonw) at every logon, and starts it now
#
#   powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -ServerUrl https://playlog.example.lan -Token <pc token>
#   powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -Uninstall
param(
    [string]$ServerUrl,
    [string]$Token,
    [string]$Device = "pc",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TaskName = "Steam Playtime Agent"
$AppDir = Join-Path $env:LOCALAPPDATA "SteamPlaytime\app"
$ConfigFile = Join-Path $env:APPDATA "SteamPlaytime\config.json"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
if ($Uninstall) {
    Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
    Write-Host "Removed the task and $AppDir. Kept $ConfigFile and the queue in $env:LOCALAPPDATA\SteamPlaytime."
    return
}

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { throw "pythonw.exe not found on PATH - install Python 3.10+ from python.org" }

$source = Join-Path $PSScriptRoot "..\..\playtime_agent"
New-Item -ItemType Directory -Force $AppDir | Out-Null
Remove-Item -Recurse -Force (Join-Path $AppDir "playtime_agent") -ErrorAction SilentlyContinue
Copy-Item -Recurse $source (Join-Path $AppDir "playtime_agent")

if (-not (Test-Path $ConfigFile)) {
    if (-not $Token -or -not $ServerUrl) { throw "No config yet: pass -ServerUrl <server> -Token <this device's token>" }
    New-Item -ItemType Directory -Force (Split-Path $ConfigFile) | Out-Null
    @{ server_url = $ServerUrl; device = $Device; token = $Token; interval_seconds = 60 } |
        ConvertTo-Json | Out-File -Encoding ascii $ConfigFile
    Write-Host "Wrote $ConfigFile"
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m playtime_agent" -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Records Steam play sessions and uploads them to the Playlog server" | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started '$TaskName'. Log: $env:LOCALAPPDATA\SteamPlaytime\agent.log"
