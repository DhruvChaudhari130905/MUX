# Run MUX on Windows, no WSL needed. The .cmd shortcut at the repo root calls this.
#
#   .\dev.cmd               backend API (:8000) + web app (:3000), each in its own terminal window
#   .\dev.cmd -Demo         web app only, in demo mode (no backend, no API keys)
#   .\dev.cmd -ServerOnly   backend API only (in this terminal)
#   .\dev.cmd -WebOnly      web app only, against the backend (in this terminal)
#   .\dev.cmd -SetupOnly    install backend venv + npm packages, then stop (no processes)
#   .\dev.cmd -SkipSetup    fast start: skip dependency checks and go
param(
    [switch]$Demo,
    [switch]$ServerOnly,
    [switch]$WebOnly,
    [switch]$SetupOnly,
    [switch]$SkipSetup
)

$ErrorActionPreference = "Stop"
$RepoRoot  = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot "mux\server"
$WebDir    = Join-Path $RepoRoot "mux\web"
$VenvPython  = Join-Path $ServerDir ".venv\Scripts\python.exe"
$VenvUvicorn = Join-Path $ServerDir ".venv\Scripts\uvicorn.exe"

function Info($m) { Write-Host "[MUX] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[MUX] ERROR: $m" -ForegroundColor Red; Read-Host "Press Enter to close" | Out-Null; exit 1 }

# --- tool check ------------------------------------------------------------
$missing = @()
foreach ($t in "python", "node", "npm") {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { $missing += $t }
}
if ($missing) {
    Fail "missing: $($missing -join ', '). Install Python 3.11+ (tick 'Add to PATH' in the installer) and Node 22 LTS, then re-open this terminal"
}

$nodeVer = (& node -v) 2>$null
if ($nodeVer -notmatch '^v(\d+)') { Fail "node -v failed" }
if ([int]$Matches[1] -lt 22) { Write-Host "[MUX] warning: Node $($nodeVer) is old; the web app wants >= 22.12 (Node 22 LTS)" -ForegroundColor Yellow }

$pyVer = (& python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
if ($pyVer -notmatch '\d') { Fail "python not usable in PATH" }
if ([version]$pyVer -lt [version]"3.11") { Fail "Python $pyVer is too old; install 3.11 or newer" }

# --- setup: backend venv ----------------------------------------------------
function Setup-Server {
    if (-not (Test-Path $VenvPython)) {
        Info "no backend venv yet - creating it and installing Python packages (first run only, takes a few minutes)"
        Push-Location $ServerDir
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "python -m venv .venv failed" }
        & ".venv\Scripts\python.exe" -m pip install --upgrade pip
        & ".venv\Scripts\pip.exe" install -e ".[dev]"
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "pip install failed" }
        Pop-Location
        Info "backend venv ready at mux\server\.venv"
    }
    if (-not (Test-Path (Join-Path $ServerDir ".env"))) {
        $example = Join-Path $ServerDir ".env.example"
        if (Test-Path $example) {
            Copy-Item $example (Join-Path $ServerDir ".env")
            Info "copied mux\server\.env.example to mux\server\.env - add TOKEN_FACTORY_API_KEY, TAVILY_API_KEY, ... to it"
        } else {
            Write-Host "[MUX] note: no mux\server\.env - runs fine without it (empty DATABASE_URL uses SQLite); per-room or server keys enable the agents (docs\03-getting-started.md)" -ForegroundColor Yellow
        }
    }
}

# --- setup: web packages ----------------------------------------------------
function Setup-Web {
    if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
        Info "no node_modules yet - installing npm packages (first run only, takes a few minutes)"
        Push-Location $WebDir
        & npm install
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install failed" }
        Pop-Location
        Info "web packages ready"
    }
}

# --- run helpers ------------------------------------------------------------
function Run-ServerInWindow {
    Start-Process powershell -WorkingDirectory $RepoRoot -ArgumentList @(
        "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\scripts\dev.ps1", "-ServerOnly"
    ) | Out-Null
}
function Run-WebInWindow {
    Start-Process powershell -WorkingDirectory $RepoRoot -ArgumentList @(
        "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\scripts\dev.ps1", "-WebOnly"
    ) | Out-Null
}

# --- modes ------------------------------------------------------------------
if ($ServerOnly) {
    Setup-Server
    Info "API starting on http://localhost:8000 (docs at /docs) - Ctrl+C to stop, window stays open"
    Push-Location $ServerDir
    & $VenvUvicorn mux.main:app --reload --reload-dir mux --port 8000
    $code = $LASTEXITCODE
    Pop-Location
    exit $code
}

if ($WebOnly) {
    Setup-Web
    if ($Demo) {
        $env:NEXT_PUBLIC_DEMO_MODE = "true"
        Info "demo mode ON: no backend needed; open http://localhost:3000/room/demo-yoga"
    } else {
        Info "web app starting against the API on http://localhost:8000; open http://localhost:3000"
    }
    Info "Ctrl+C to stop, window stays open"
    Push-Location $WebDir
    & npm run dev
    $code = $LASTEXITCODE
    Pop-Location
    exit $code
}

if ($SetupOnly) {
    Setup-Server
    Setup-Web
    Info "setup complete - backend venv and npm packages ready, nothing started"
    exit 0
}

# default: set up everything, then spawn both processes in their own windows
if (-not $SkipSetup) { Setup-Server; Setup-Web }
Info "starting the API and the web app in two terminal windows"
Info "  API      -> http://localhost:8000/docs"
Info "  web app  -> http://localhost:3000  (demo rooms: http://localhost:3000/room/demo-yoga)"
Info "stop each one with Ctrl+C in its own window; re-run .\dev.cmd any time"

Run-ServerInWindow
Run-WebInWindow
