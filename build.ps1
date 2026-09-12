param(
    [ValidateSet("portable", "single")]
    [string]$Mode = "portable",
    [string]$ScrcpyDir = ""
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found. Run: py -3.13 -m venv .venv"
}

if ($Mode -eq "single") {
    if ($ScrcpyDir) {
        $env:SCRCPY_DIR = (Resolve-Path -LiteralPath $ScrcpyDir).Path
    }
    $spec = "ScrcpyControlCenterSingle.spec"
} else {
    $spec = "ScrcpyControlCenterPortable.spec"
}

& $python -m PyInstaller --noconfirm --clean $spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "Build finished: dist\$([IO.Path]::GetFileNameWithoutExtension($spec)).exe"
