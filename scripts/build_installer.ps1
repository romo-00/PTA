param(
    [string]$Python = "py",
    [string]$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    # 1) Delete prior artifacts for deterministic output.
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "dist" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "installer\installer" -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path "installer\Output" -Force | Out-Null
    Remove-Item -Force "installer\Output\*" -ErrorAction SilentlyContinue

    $numpyMajor = & $Python -c "import numpy as np; print(np.__version__.split('.')[0])"
    if ([int]$numpyMajor -ge 2) {
        throw "Incompatible NumPy detected (>=2). MetaTrader5 requires NumPy 1.x in this build. Run: `"$Python -m pip install -r requirements-desktop.txt`" and rebuild."
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmm"
    $gitHash = "nogit"
    if (Get-Command git -ErrorAction SilentlyContinue) {
        try {
            $short = (& git rev-parse --short HEAD 2>$null).Trim()
            if ($short) {
                $gitHash = $short
            }
        }
        catch {
            $gitHash = "nogit"
        }
    }
    $buildVersion = "${timestamp}_${gitHash}"
    $outputBase = "PTA_setup_$buildVersion"
    $buildInfoPath = "src\pta_agent\build_info.py"
    Set-Content -Path $buildInfoPath -Encoding UTF8 -NoNewline -Value @"
from __future__ import annotations

# Updated by scripts/build_installer.ps1 before packaging.
AGENT_BUILD_VERSION = "$buildVersion"
"@

    # 2) Rebuild executables from current source.
    & $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build PTAAgent.spec
    & $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build PTADashboard.spec

    $dashboardInternal = Join-Path (Get-Location) "dist\PTADashboard\_internal"
    if (-not (Test-Path $dashboardInternal)) {
        throw "Dashboard build sanity check failed: '$dashboardInternal' not found."
    }

    $streamlitHits = Get-ChildItem -Path $dashboardInternal -Recurse -Force | Where-Object {
        $_.Name -match "streamlit" -or $_.FullName -match "[\\/]streamlit([\\/]|$)"
    }
    if (-not $streamlitHits -or $streamlitHits.Count -eq 0) {
        throw "Dashboard build sanity check failed: no streamlit files found under '$dashboardInternal'."
    }

    $agentNumpyCore = Get-ChildItem -Path "dist\PTAAgent\_internal\numpy\core" -Filter "_multiarray_umath*.pyd" -ErrorAction SilentlyContinue
    if (-not $agentNumpyCore) {
        throw "Agent build sanity check failed: missing numpy core extension (_multiarray_umath*.pyd)."
    }

    $dashboardNumpyCore = Get-ChildItem -Path "dist\PTADashboard\_internal\numpy\core" -Filter "_multiarray_umath*.pyd" -ErrorAction SilentlyContinue
    if (-not $dashboardNumpyCore) {
        throw "Dashboard build sanity check failed: missing numpy core extension (_multiarray_umath*.pyd)."
    }

    $agentExe = Join-Path (Get-Location) "dist\PTAAgent\PTAAgent.exe"
    if (-not (Test-Path $agentExe)) {
        throw "Agent build sanity check failed: '$agentExe' not found."
    }
    $agentHelp = & $agentExe ingest --help 2>&1 | Out-String
    if ($agentHelp -notmatch "--lookback-minutes" -or $agentHelp -notmatch "--reset-watermark") {
        throw "Agent build sanity check failed: ingest --help missing expected flags (--lookback-minutes, --reset-watermark)."
    }

    if (-not (Test-Path $ISCC)) {
        throw "ISCC.exe not found at '$ISCC'. Install Inno Setup or pass -ISCC <path>."
    }

    # 3) Compile Inno Setup into installer\Output only.
    & $ISCC `
        "/DMyAppVersion=$buildVersion" `
        "/DMyOutputDir=Output" `
        "/DMyOutputBaseFilename=$outputBase" `
        "installer\pta_agent.iss"

    $versionedInstaller = Join-Path (Get-Location) "installer\Output\$outputBase.exe"
    if (-not (Test-Path $versionedInstaller)) {
        throw "Expected installer not found: $versionedInstaller"
    }

    # Canonical alias path.
    $canonicalInstaller = Join-Path (Get-Location) "installer\Output\PTA_setup.exe"
    Copy-Item -Force $versionedInstaller $canonicalInstaller

    # 4) Print final path exactly once.
    Write-Output "installer\Output\PTA_setup.exe"
}
finally {
    Pop-Location
}
