param(
    [string[]]$ProcessName = @("HaloWars2_WinAppDX12Final", "HaloWars2", "xgameFinal"),
    [int]$PollSeconds = 10,
    [int]$ApiDelaySeconds = 15,
    [int]$StartPaddingSeconds = 30,
    [int]$EndPaddingSeconds = 300,
    [string]$Python = "python",
    [switch]$Continuous,
    [string]$LogFile = "",
    [switch]$NoPopup
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ScriptRoot

function Format-UtcIso {
    param([datetime]$Value)
    return $Value.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Get-HaloProcess {
    foreach ($name in $ProcessName) {
        $cleanName = $name -replace "\.exe$", ""
        $process = Get-Process -Name $cleanName -ErrorAction SilentlyContinue |
            Sort-Object StartTime |
            Select-Object -First 1
        if ($process) {
            return $process
        }
    }
    return $null
}

function Write-Status {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Write-Host $line
    if ($LogFile) {
        Add-Content -LiteralPath $LogFile -Value $line
    }
}

Write-Status "Watching for Halo Wars 2 process: $($ProcessName -join ', ')"
Write-Status "Start this watcher before opening the game for the cleanest session window."

do {
    $process = Get-HaloProcess
    while (-not $process) {
        Start-Sleep -Seconds $PollSeconds
        $process = Get-HaloProcess
    }

    $detectedAt = [datetime]::UtcNow
    try {
        $sessionStart = $process.StartTime.ToUniversalTime()
    } catch {
        $sessionStart = $detectedAt
    }
    $sessionStart = $sessionStart.AddSeconds(-1 * $StartPaddingSeconds)

    Write-Status "Detected $($process.ProcessName) at $(Format-UtcIso $detectedAt)."
    Write-Status "Session start filter: $(Format-UtcIso $sessionStart)"
    Write-Status "Waiting for the game process to close..."

    while (Get-HaloProcess) {
        Start-Sleep -Seconds $PollSeconds
    }

    $sessionEnd = ([datetime]::UtcNow).AddSeconds($EndPaddingSeconds)
    Write-Status "Game closed. Session end filter: $(Format-UtcIso $sessionEnd)"

    if ($ApiDelaySeconds -gt 0) {
        Write-Status "Waiting $ApiDelaySeconds seconds for the Halo API to publish recent matches..."
        Start-Sleep -Seconds $ApiDelaySeconds
    }

    $startArg = Format-UtcIso $sessionStart
    $endArg = Format-UtcIso $sessionEnd

    Write-Status "Running session export..."
    & $Python "main.py" --session --start $startArg --end $endArg
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "Session export failed with exit code $exitCode."
    }

    Write-Status "Session export complete."
    if (-not $NoPopup) {
        $reportPath = Join-Path $ScriptRoot "session_report.html"
        if (Test-Path -LiteralPath $reportPath) {
            Write-Status "Opening session report: $reportPath"
            Start-Process -FilePath $reportPath
        } else {
            Write-Status "Session report was not found at $reportPath"
        }
    }
    if ($Continuous) {
        Write-Status "Continuous mode is on. Waiting for the next Halo Wars 2 session..."
    }
} while ($Continuous)
