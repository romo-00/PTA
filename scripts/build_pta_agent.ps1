param(
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"

& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build PTAAgent.spec

Write-Host "Build complete: dist\\PTAAgent\\PTAAgent.exe"
