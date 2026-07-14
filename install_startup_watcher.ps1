param(
    [string]$TaskName = "Halo Wars 2 Session Watcher",
    [string]$Python = "python",
    [int]$PollSeconds = 10,
    [int]$ApiDelaySeconds = 120,
    [string[]]$ProcessName = @("HaloWars2", "xgameFinal")
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$watcherPath = Join-Path $repoRoot "watch_halo_session.ps1"
$logPath = Join-Path $repoRoot "session_watcher.log"

if (-not (Test-Path -LiteralPath $watcherPath)) {
    throw "Could not find watcher script at $watcherPath"
}

$processArgs = $ProcessName | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }
$argumentList = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", '"' + $watcherPath + '"',
    "-Continuous",
    "-LogFile", '"' + $logPath + '"',
    "-Python", '"' + $Python + '"',
    "-PollSeconds", $PollSeconds,
    "-ApiDelaySeconds", $ApiDelaySeconds
)

if ($processArgs.Count -gt 0) {
    $argumentList += "-ProcessName"
    $argumentList += $processArgs
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($argumentList -join " ") `
    -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Watches for Halo Wars 2 and exports session results after the game closes." `
    -Force | Out-Null

Write-Host "Installed startup watcher task: $TaskName"
Write-Host "It will start automatically when you log into Windows."
Write-Host "Watcher log: $logPath"
Write-Host "To start it now, run:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
