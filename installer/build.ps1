# installer\build.ps1 — TradeRelay Engine build pipeline
#
# Run from the execution-engine\ directory:
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Clean
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -SkipInstaller
#
# Prerequisites (on PATH or in venv\Scripts\):
#   pyinstaller  — pip install pyinstaller  (into the execution-engine venv)
#   iscc         — Inno Setup 6: https://jrsoftware.org/isdl.php
#
# Outputs:
#   dist\TradeRelay-Engine\           <- packaged engine (onedir)
#   installer\Output\TradeRelaySetup.exe  <- full Windows installer (if ISCC found)

param(
    [switch]$SkipPackage,    # skip PyInstaller — use an existing dist\
    [switch]$SkipInstaller,  # skip Inno Setup step
    [switch]$Clean           # pass --clean to PyInstaller (forces full rebuild)
)

$ErrorActionPreference = "Stop"

# Resolve engine root — this script lives in installer\ so go up one level
$InstallerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EngineDir    = Split-Path -Parent $InstallerDir

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  TradeRelay Engine — Build Pipeline"    -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Engine dir : $EngineDir"
Write-Host ""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Find-Exe([string[]]$Candidates) {
    foreach ($c in $Candidates) {
        if ($c -match "[\\/]") {
            # Absolute or relative path
            if (Test-Path $c) { return $c }
        } else {
            $found = Get-Command $c -ErrorAction SilentlyContinue
            if ($found) { return $c }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Ensure version.txt exists (derived from pyproject.toml if missing)
# ---------------------------------------------------------------------------
$VersionFile = Join-Path $EngineDir "version.txt"
if (-not (Test-Path $VersionFile)) {
    $version = "0.1.0"
    $pyproject = Join-Path $EngineDir "pyproject.toml"
    if (Test-Path $pyproject) {
        $match = Select-String -Path $pyproject -Pattern '^version\s*=\s*"(.+)"' | Select-Object -First 1
        if ($match) { $version = $match.Matches[0].Groups[1].Value }
    }
    Set-Content -Path $VersionFile -Value $version -Encoding utf8
    Write-Host "  [auto] Created version.txt: $version" -ForegroundColor DarkGray
}
$EngineVersion = (Get-Content $VersionFile -Raw).Trim()
Write-Host "  Version     : $EngineVersion"
Write-Host ""

# ---------------------------------------------------------------------------
# Step 1: PyInstaller — package the engine into dist\TradeRelay-Engine\
# ---------------------------------------------------------------------------
if (-not $SkipPackage) {
    Write-Host "[1/2] PyInstaller packaging..." -ForegroundColor Yellow

    $PyInstaller = Find-Exe @(
        (Join-Path $EngineDir "venv\Scripts\pyinstaller.exe"),
        (Join-Path $EngineDir ".venv\Scripts\pyinstaller.exe"),
        "pyinstaller"
    )
    if (-not $PyInstaller) {
        Write-Host ""
        Write-Error @"
pyinstaller not found.
Install it into the execution-engine venv:
  venv\Scripts\pip install pyinstaller
"@
        exit 1
    }
    Write-Host "      Using : $PyInstaller" -ForegroundColor DarkGray

    Push-Location $EngineDir
    try {
        $args = @("engine.spec", "--noconfirm")
        if ($Clean) { $args += "--clean" }

        & $PyInstaller @args
        if ($LASTEXITCODE -ne 0) {
            Write-Error "PyInstaller exited with code $LASTEXITCODE"
            exit 1
        }
    } finally {
        Pop-Location
    }

    $distDir = Join-Path $EngineDir "dist\TradeRelay-Engine"
    if (-not (Test-Path $distDir)) {
        Write-Error "Expected dist\TradeRelay-Engine\ was not created by PyInstaller"
        exit 1
    }
    Write-Host "      Done  : dist\TradeRelay-Engine\" -ForegroundColor Green
} else {
    Write-Host "[1/2] Skipping PyInstaller (-SkipPackage)" -ForegroundColor DarkGray
    $distDir = Join-Path $EngineDir "dist\TradeRelay-Engine"
    if (-not (Test-Path $distDir)) {
        Write-Warning "dist\TradeRelay-Engine\ does not exist — the Inno Setup step may fail"
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# Step 2: Inno Setup — produce TradeRelaySetup.exe
# ---------------------------------------------------------------------------
if (-not $SkipInstaller) {
    Write-Host "[2/2] Inno Setup installer..." -ForegroundColor Yellow

    $Iscc = Find-Exe @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        "iscc"
    )

    if (-not $Iscc) {
        Write-Host ""
        Write-Warning @"
Inno Setup (ISCC.exe) not found — skipping installer build.
Install Inno Setup 6 from: https://jrsoftware.org/isdl.php
The packaged engine in dist\TradeRelay-Engine\ is still usable for testing.
"@
    } else {
        Write-Host "      Using : $Iscc" -ForegroundColor DarkGray
        $IssFile = Join-Path $EngineDir "installer\TradeRelay.iss"

        & $Iscc $IssFile
        if ($LASTEXITCODE -ne 0) {
            Write-Error "ISCC exited with code $LASTEXITCODE"
            exit 1
        }

        $setupExe = Join-Path $EngineDir "installer\Output\TradeRelaySetup.exe"
        if (Test-Path $setupExe) {
            $sizeMB = [math]::Round((Get-Item $setupExe).Length / 1MB, 1)
            Write-Host "      Done  : installer\Output\TradeRelaySetup.exe ($sizeMB MB)" -ForegroundColor Green
        } else {
            Write-Host "      Done  : installer\Output\TradeRelaySetup.exe" -ForegroundColor Green
        }
    }
} else {
    Write-Host "[2/2] Skipping Inno Setup (-SkipInstaller)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "  Build complete — v$EngineVersion" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host ""
