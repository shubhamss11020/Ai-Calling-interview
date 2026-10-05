# One-command dev launcher:  .\run-dev.ps1            (add -NoTunnel to skip ngrok / real phone calls)
#   1. starts an ngrok tunnel to the backend and writes the public https URL into backend\.env (PUBLIC_BASE_URL)
#   2. starts the FastAPI backend   -> http://localhost:8000   (docs at /docs)
#   3. starts the Next.js dashboard -> http://localhost:3000
# Press Ctrl+C in this window to stop everything.
param([switch]$NoTunnel)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$envFile = Join-Path $backend ".env"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$procs = @()

function Set-EnvValue($key, $value) {
    $lines = Get-Content $envFile
    if ($lines -match "^$key=") { $lines = $lines -replace "^$key=.*", "$key=$value" } else { $lines += "$key=$value" }
    Set-Content -Path $envFile -Value $lines -Encoding utf8
}

try {
    if (-not (Test-Path $py)) { throw "backend\.venv not found. Run: cd backend; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt" }

    if (-not $NoTunnel) {
        if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) { throw "ngrok is not installed or not on PATH (or run with -NoTunnel)." }
        Write-Host "Starting ngrok tunnel..." -ForegroundColor Cyan
        $procs += Start-Process ngrok -ArgumentList "http 8000" -WindowStyle Hidden -PassThru
        $url = $null
        for ($i = 0; $i -lt 30 -and -not $url; $i++) {
            Start-Sleep -Seconds 1
            try {
                $t = Invoke-RestMethod http://127.0.0.1:4040/api/tunnels -TimeoutSec 2
                $url = ($t.tunnels | Where-Object { $_.public_url -like "https://*" } | Select-Object -First 1).public_url
            } catch { }
        }
        if (-not $url) { throw "ngrok did not report a tunnel. Run 'ngrok http 8000' manually to see the error (auth token?)." }
        Set-EnvValue "PUBLIC_BASE_URL" $url
        Write-Host "Public URL: $url  (written to backend\.env)" -ForegroundColor Green
    } else {
        Write-Host "Skipping ngrok: real phone calls are disabled; use the Practice interview in the dashboard." -ForegroundColor Yellow
    }

    Write-Host "Starting backend on :8000 ..." -ForegroundColor Cyan
    $procs += Start-Process $py -ArgumentList "-m uvicorn app.main:app --port 8000" -WorkingDirectory $backend -PassThru -NoNewWindow

    Write-Host "Starting dashboard on :3000 ..." -ForegroundColor Cyan
    $procs += Start-Process "npm.cmd" -ArgumentList "run dev" -WorkingDirectory $frontend -PassThru -NoNewWindow

    Write-Host "`nDashboard: http://localhost:3000   API docs: http://localhost:8000/docs   Readiness: http://localhost:8000/health/ready" -ForegroundColor Green
    Write-Host "Ctrl+C to stop.`n"
    Wait-Process -Id ($procs | Select-Object -Last 1).Id
}
finally {
    foreach ($p in $procs) { if ($p -and -not $p.HasExited) { & taskkill /PID $p.Id /T /F 2>$null | Out-Null } }
}
