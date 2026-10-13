# Build both packages, then (optionally) upload them to PyPI or TestPyPI.
#
# Usage (from the repository root, with your virtual environment activated):
#   .\publish_to_pypi.ps1 -BuildOnly            # build only, upload nothing
#   .\publish_to_pypi.ps1                       # build, then upload to PyPI (asks you to confirm)
#
# Needs:  pip install -r requirements-dev.txt   (installs build and twine)
# The plan package is always uploaded first, because the engine depends on it.

param(
    [ValidateSet("pypi", "testpypi")]
    [string]$Repository = "pypi",
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"

foreach ($module in @("build", "twine")) {
    python -c "import $module" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "'$module' is not installed. Run: pip install -r requirements-dev.txt"
    }
}

$Packages = @(
    @{ Name = "disaster-fund-plan";              Path = "packages\fm-plan" },
    @{ Name = "disaster-fund-calculation-engine"; Path = "packages\fm-calculation-engine" }
)

Write-Host "Building distributions..." -ForegroundColor Cyan
foreach ($pkg in $Packages) {
    Write-Host "  - $($pkg.Name)" -ForegroundColor Yellow
    $dist = Join-Path $pkg.Path "dist"
    if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
    python -m build $pkg.Path --outdir $dist
    if ($LASTEXITCODE -ne 0) { throw "Build failed for $($pkg.Name)" }
}

python -m twine check (Get-ChildItem "packages\*\dist\*" | ForEach-Object FullName)
if ($LASTEXITCODE -ne 0) { throw "twine check failed" }

if ($BuildOnly) {
    Write-Host "BuildOnly given: nothing uploaded." -ForegroundColor Cyan
    exit 0
}

Write-Host "About to upload to $Repository. A published version can never be replaced or re-used." -ForegroundColor Red
if ((Read-Host "Type YES to continue") -ne "YES") { Write-Host "Cancelled."; exit 1 }

foreach ($pkg in $Packages) {
    Write-Host "Uploading $($pkg.Name) to $Repository..." -ForegroundColor Yellow
    python -m twine upload --repository $Repository (Get-ChildItem "$($pkg.Path)\dist\*" | ForEach-Object FullName)
    if ($LASTEXITCODE -ne 0) { throw "Upload failed for $($pkg.Name)" }
}

Write-Host "Done." -ForegroundColor Green
