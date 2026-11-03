# Builds installer\build\output\Setup.exe.
#
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
#
# Needs this repo's venv, Node.js and Inno Setup 6
# (winget install --id JRSoftware.InnoSetup -e). See docs/windows-installer.md.

[CmdletBinding()]
param(
    [switch]$SkipFrontend,     # reuse frontend\dist
    [string]$Version = ""      # default: setup.py's version
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $PSScriptRoot "build"
$payload = Join-Path $build "payload"
$assets = Join-Path $build "assets"
$python = Join-Path $root "venv\Scripts\python.exe"

# What the installed app runs on: an allow-list, so tests, docs and anything
# else in the checkout (config copy.json) cannot ship by accident.
$included = @(
    "backend/", "metascan/", "data/",
    "installer/__init__.py", "installer/apply_settings.py", "installer/launcher.py",
    "installer/provision.ps1", "installer/requirements-launcher.txt", "installer/serve.py",
    "installer/serve_protocol.py",
    "config_example.json", "icon.png", "LICENSE", "README.md",
    "requirements.txt", "requirements-train.txt", "run.bat", "setup_models.py"
)

function Find-Iscc {
    $known = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $known) {
        if (Test-Path $candidate) { return $candidate }
    }
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw "Inno Setup 6 not found. Install it: winget install --id JRSoftware.InnoSetup -e"
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path $python)) { throw "No venv at $python. See docs/first_time_setup.md." }
$iscc = Find-Iscc

if (-not $Version) {
    $match = Select-String -Path (Join-Path $root "setup.py") -Pattern 'version="([^"]+)"'
    if (-not $match) { throw "No version in setup.py" }
    $Version = $match.Matches[0].Groups[1].Value
}
Write-Host "[build] Metascan $Version"

if (-not $SkipFrontend) {
    Write-Host "[build] Building the UI"
    Push-Location (Join-Path $root "frontend")
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } finally {
        Pop-Location
    }
}
$dist = Join-Path $root "frontend\dist"
if (-not (Test-Path (Join-Path $dist "index.html"))) { throw "No UI build at $dist" }

Write-Host "[build] Staging the files"
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force -Path $payload | Out-Null

# Tracked and new files, never ignored ones (the database, models, thumbnails).
Push-Location $root
try {
    $files = @(& git ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw "git ls-files failed" }
} finally {
    Pop-Location
}
$copied = 0
foreach ($file in $files) {
    $file = $file.Trim('"')
    $wanted = $included | Where-Object { $file -eq $_ -or ($_.EndsWith("/") -and $file.StartsWith($_)) }
    if (-not $wanted) { continue }
    $source = Join-Path $root $file
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }  # deleted, not committed
    $target = Join-Path $payload $file
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target
    $copied++
}
New-Item -ItemType Directory -Force -Path (Join-Path $payload "frontend") | Out-Null
Copy-Item -Recurse $dist (Join-Path $payload "frontend")
Write-Host "[build] $copied files staged"

Invoke-Checked $python @((Join-Path $PSScriptRoot "make_assets.py"), $assets)

Write-Host "[build] Compiling"
Invoke-Checked $iscc @(
    "/Qp",
    "/DAppVersion=$Version",
    (Join-Path $PSScriptRoot "metascan.iss")
)
$setup = Join-Path $build "output\Setup.exe"
if (-not (Test-Path $setup)) { throw "Compiler finished but $setup is missing" }
$size = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host "[build] Done: $setup ($size MB)"
