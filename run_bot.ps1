$ErrorActionPreference = "Continue"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project ".venv\Scripts\python.exe"
$bot = Join-Path $project "bot.py"
$dataDir = Join-Path $project "data"
$logDir = Join-Path $project "logs"
$supervisorLog = Join-Path $logDir "supervisor-events.log"
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Set-Content -LiteralPath (Join-Path $dataDir "supervisor.pid") -Value $PID -Encoding ascii

function Write-SupervisorLog($message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $supervisorLog -Value "$timestamp $message" -Encoding utf8
}

Write-SupervisorLog "Supervisor started"

while ($true) {
    if (-not (Test-Path -LiteralPath $python)) {
        Write-SupervisorLog "Python environment is missing; retrying in 60 seconds"
        Start-Sleep -Seconds 60
        continue
    }
    & $python $bot 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $supervisorLog -Value $_ -Encoding utf8
    }
    Write-SupervisorLog "Bot exited with code $LASTEXITCODE; retrying in 10 seconds"
    Start-Sleep -Seconds 10
}

