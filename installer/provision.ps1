# Metascan's Windows install, after Setup has copied the files: prerequisites,
# virtualenv, torch, packages, NLTK, config.json, CLIP.
#
# metascan.iss runs this and shows its progress, but it stands alone, so a
# failed step can be re-run from a terminal. Every step is safe to repeat.
#
#   powershell -ExecutionPolicy Bypass -File installer\provision.ps1 [options]
#
# Steps follow docs/first_time_setup.md. The status file is an INI, the one
# format Setup's script can read: state (running, done, failed, cancelled),
# percent, stage, detail, warnings (joined with " | "), error. Creating the
# cancel file stops the run.

[CmdletBinding()]
param(
    [string]$AppDir = (Split-Path -Parent $PSScriptRoot),
    [string]$StatusFile = "",
    [string]$CancelFile = "",
    [string]$Settings = "",        # the wizard's answers; none keeps config.json
    [switch]$InstallPython,
    [switch]$InstallFfmpeg,
    [switch]$InstallExifTool,
    [switch]$SkipClip,
    [ValidateSet("auto", "cpu")]
    [string]$Torch = "auto"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PIP_NO_INPUT = "1"

$script:Warnings = New-Object System.Collections.ArrayList
$script:Percent = 0
$script:Stage = "Starting"
$script:Detail = ""
$script:LastStatusWrite = [DateTime]::MinValue

class CancelledException : System.Exception {
    CancelledException() : base("Cancelled") {}
}

# ---- Reporting ---------------------------------------------------------------

$logDir = Join-Path $AppDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir "install.log"
if (Test-Path $logPath) { Move-Item -Force $logPath "$logPath.1" }
$script:Log = New-Object System.IO.StreamWriter($logPath, $false, [Text.Encoding]::UTF8)
$script:Log.AutoFlush = $true

function Write-Log([string]$Line) {
    $script:Log.WriteLine("[$(Get-Date -Format 'HH:mm:ss')] $Line")
    Write-Host $Line
}

function Write-Status([string]$State = "running", [string]$ErrorText = "") {
    if (-not $StatusFile) { return }
    $clean = { param($s) ([string]$s) -replace "[\r\n]+", " " }
    $text = @(
        "[status]"
        "state=$State"
        "percent=$script:Percent"
        "stage=$(& $clean $script:Stage)"
        "detail=$(& $clean $script:Detail)"
        "warnings=$(& $clean ($script:Warnings -join ' | '))"
        "error=$(& $clean $ErrorText)"
    ) -join "`r`n"
    # UTF-16 keeps non-ASCII paths readable by GetPrivateProfileString, and the
    # rename means Setup never reads half a file.
    $temp = "$StatusFile.tmp"
    try {
        [IO.File]::WriteAllText($temp, $text, [Text.Encoding]::Unicode)
        Move-Item -Force $temp $StatusFile
        $script:LastStatusWrite = Get-Date
    } catch [System.IO.IOException] {
        # Setup is reading it. The next line of output tries again.
    }
}

function Set-Stage([int]$Percent, [string]$Stage) {
    $script:Percent = $Percent
    $script:Stage = $Stage
    $script:Detail = ""
    Write-Log "== $Stage"
    Write-Status
}

function Add-Warning([string]$Text) {
    [void]$script:Warnings.Add($Text)
    Write-Log "WARNING: $Text"
    Write-Status
}

function Test-Cancelled {
    if ($CancelFile -and (Test-Path $CancelFile)) { throw [CancelledException]::new() }
}

# Runs a program, sends its output to the log, and returns its exit code, or
# its lines with -Capture. cmd merges stderr itself, because PowerShell 5.1 turns
# native stderr into errors, and the output is read with a timeout so a long
# silent download can still be cancelled.
function Invoke-Tool {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [switch]$Capture
    )
    $quoted = @($Exe) + $Arguments | ForEach-Object {
        if ($_ -match '^[\w\-./:=+\\]+$') { $_ } else { '"' + ($_ -replace '"', '\"') + '"' }
    }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $env:ComSpec
    $info.Arguments = '/S /D /C "' + ($quoted -join " ") + ' 2>&1"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.WorkingDirectory = $AppDir

    Write-Log "> $($quoted -join ' ')"
    $process = [Diagnostics.Process]::Start($info)
    $lines = New-Object System.Collections.ArrayList
    $reader = $process.StandardOutput
    try {
        while ($true) {
            $pending = $reader.ReadLineAsync()
            while (-not $pending.Wait(500)) {
                Test-Cancelled
            }
            $line = $pending.Result
            if ($null -eq $line) { break }
            $line = $line.Trim()
            if (-not $line) { continue }
            $script:Log.WriteLine($line)
            if ($Capture) { [void]$lines.Add($line) }
            $script:Detail = if ($line.Length -gt 90) { $line.Substring(0, 90) + "..." } else { $line }
            if (((Get-Date) - $script:LastStatusWrite).TotalMilliseconds -gt 300) { Write-Status }
        }
    } catch [CancelledException] {
        & taskkill.exe /T /F /PID $process.Id 2>&1 | Out-Null
        throw
    }
    $process.WaitForExit()
    if ($Capture) { return $lines.ToArray() }
    return $process.ExitCode
}

function Invoke-Required {
    param([string]$Exe, [string[]]$Arguments, [string]$Failure)
    $code = Invoke-Tool -Exe $Exe -Arguments $Arguments
    if ($code -ne 0) { throw "$Failure (exit code $code). See $logPath" }
}

# ---- Prerequisites -----------------------------------------------------------

# Tools installed a moment ago are not on this process's PATH yet.
function Update-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user;$env:Path"
}

function Find-Winget {
    Update-Path
    $found = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $alias = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
    if (Test-Path $alias) { return $alias }
    # Present on Windows 10 and 11 but not registered until first use.
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe
    } catch {
        Write-Log "Could not register App Installer: $($_.Exception.Message)"
    }
    if (Test-Path $alias) { return $alias }
    return $null
}

function Install-Package([string]$Id) {
    $winget = Find-Winget
    if (-not $winget) { return $false }
    $code = Invoke-Tool -Exe $winget -Arguments @(
        "install", "--id", $Id, "-e", "--silent", "--disable-interactivity",
        "--accept-source-agreements", "--accept-package-agreements"
    )
    Update-Path
    # 0x8A15002B: already installed, and no newer version to apply.
    return ($code -eq 0 -or $code -eq -1978335189)
}

function Get-Launchers {
    Update-Path
    $paths = @()
    $found = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($found) { $paths += $found.Source }
    $alias = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\py.exe"
    if (Test-Path $alias) { $paths += $alias }
    return @($paths | Select-Object -Unique)
}

function Find-Python311 {
    $candidates = New-Object System.Collections.ArrayList
    foreach ($launcher in Get-Launchers) {
        $out = @(Invoke-Tool -Exe $launcher -Arguments @(
            "-3.11", "-c", "import sys; print(sys.executable)"
        ) -Capture)
        if ($out.Count -gt 0) { [void]$candidates.Add([string]$out[-1]) }
    }
    # Classic installers, which may not have added py.
    [void]$candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"))
    [void]$candidates.Add((Join-Path $env:ProgramFiles "Python311\python.exe"))
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $code = Invoke-Tool -Exe $candidate -Arguments @(
            "-c", "import sys; sys.exit(sys.version_info[:2] != (3, 11))"
        )
        if ($code -eq 0) { return $candidate }
    }
    return $null
}

function Install-Python311 {
    Set-Stage 2 "Installing Python 3.11"
    # The Python Install Manager, then a 3.11 runtime beside any other Python.
    $launchers = @(Get-Launchers)
    $manager = $launchers | Where-Object {
        (Invoke-Tool -Exe $_ -Arguments @("install", "--help")) -eq 0
    } | Select-Object -First 1
    if (-not $manager) {
        if (Install-Package "Python.PythonInstallManager") {
            $manager = Get-Launchers | Where-Object {
                (Invoke-Tool -Exe $_ -Arguments @("install", "--help")) -eq 0
            } | Select-Object -First 1
        }
    }
    if ($manager) {
        [void](Invoke-Tool -Exe $manager -Arguments @("install", "--yes", "3.11"))
        $python = Find-Python311
        if ($python) { return $python }
    }
    # The last 3.11 release with a classic installer.
    Write-Log "Falling back to the classic Python 3.11 package"
    [void](Install-Package "Python.Python.3.11")
    return Find-Python311
}

function Install-Tool([string]$Id, [string]$Command, [string]$Label, [string]$Consequence) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) { return }
    if ((Install-Package $Id) -and (Get-Command $Command -ErrorAction SilentlyContinue)) { return }
    Add-Warning "$Label was not installed. $Consequence"
}

# ---- Torch -------------------------------------------------------------------

# @{ Cuda = [version]; Name = "..."} or $null
function Get-NvidiaGpu {
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $smi) { return $null }
    $lines = @(Invoke-Tool -Exe $smi.Source -Capture)
    $text = $lines -join "`n"
    # Driver 610 prints "CUDA UMD Version", older ones "CUDA Version".
    if ($text -notmatch "CUDA(?: UMD)? Version:\s*(\d+\.\d+)") {
        Add-Warning "nvidia-smi did not report a CUDA version, so Metascan will run on the CPU."
        return $null
    }
    $cuda = [version]$Matches[1]
    $names = @(Invoke-Tool -Exe $smi.Source -Arguments @(
        "--query-gpu=name", "--format=csv,noheader"
    ) -Capture)
    return @{ Cuda = $cuda; Name = [string]$names[0] }
}

# The table in docs/first_time_setup.md, step 5. Keep them in step.
function Get-TorchIndex($Gpu) {
    $v = $Gpu.Cuda
    if ($v -ge [version]"13.2") { return "cu132" }
    if ($v -ge [version]"13.0") { return "cu130" }
    # An RTX 50-series card needs a cu128 or newer build.
    if ($Gpu.Name -match "RTX\s*5\d{3}") {
        if ($v -ge [version]"12.8") { return "cu128" }
        return $null
    }
    if ($v -ge [version]"12.6") { return "cu126" }
    return $null
}

# "<version>|<True/False>" or "" when torch does not import
function Get-TorchState([string]$Python) {
    $out = @(Invoke-Tool -Exe $Python -Arguments @(
        "-c", "import torch; print(torch.__version__ + '|' + str(torch.cuda.is_available()))"
    ) -Capture)
    if ($out.Count -gt 0 -and ([string]$out[-1]) -match "^\S+\|(True|False)$") { return [string]$out[-1] }
    return ""
}

function Install-Torch([string]$Python, $Gpu) {
    if ($Torch -eq "cpu" -or -not $Gpu) {
        Write-Log "No NVIDIA GPU in use: requirements.txt installs the CPU build"
        return
    }
    $index = Get-TorchIndex $Gpu
    if (-not $index) {
        Add-Warning ("$($Gpu.Name) needs a newer NVIDIA driver for GPU acceleration, " +
            "so Metascan will run on the CPU. Update the driver, then run Setup again.")
        return
    }
    if ((Get-TorchState $Python) -match "\|True$") {
        Write-Log "A working CUDA torch is already installed"
        return
    }
    if (Get-TorchState $Python) {
        # A CPU build, from a run before the driver was updated. pip would
        # call it satisfied.
        Invoke-Required $Python @("-m", "pip", "uninstall", "-y", "torch", "torchvision") `
            "Could not remove the old PyTorch"
    }
    # First: the torch on PyPI is CPU-only on Windows, and requirements.txt
    # keeps whatever build is already here.
    Set-Stage 22 "Downloading PyTorch for $($Gpu.Name) (about 3 GB)"
    Invoke-Required $Python @(
        "-m", "pip", "install", "--progress-bar", "off", "torch", "torchvision",
        "--index-url", "https://download.pytorch.org/whl/$index"
    ) "PyTorch could not be installed"
}

# ---- The install -------------------------------------------------------------

function Invoke-Install {
    Test-Cancelled
    Set-Stage 1 "Checking prerequisites"
    $python = Find-Python311
    if (-not $python -and $InstallPython) { $python = Install-Python311 }
    if (-not $python) {
        throw ("Python 3.11 is not installed. Run Setup again and leave Python ticked, " +
            "or install it from python.org.")
    }
    Write-Log "Python: $python"

    if ($InstallFfmpeg) {
        Set-Stage 8 "Installing FFmpeg"
        Install-Tool "Gyan.FFmpeg" "ffmpeg" "FFmpeg" "Video thumbnails will not work until you install it."
    }
    if ($InstallExifTool) {
        Set-Stage 12 "Installing ExifTool (Windows may ask for permission)"
        Install-Tool "OliverBetz.ExifTool" "exiftool" "ExifTool" "Video metadata will not be read until you install it."
    }
    Test-Cancelled

    Set-Stage 15 "Creating the Python environment"
    $venv = Join-Path $AppDir "venv"
    $venvPython = Join-Path $venv "Scripts\python.exe"
    if (Test-Path $venvPython) {
        $code = Invoke-Tool -Exe $venvPython -Arguments @(
            "-c", "import sys; sys.exit(sys.version_info[:2] != (3, 11))"
        )
        if ($code -ne 0) { Remove-Item -Recurse -Force $venv }
    }
    if (-not (Test-Path $venvPython)) {
        Invoke-Required $python @("-m", "venv", $venv) "The Python environment could not be created"
    }
    Invoke-Required $venvPython @(
        "-m", "pip", "install", "--progress-bar", "off", "--upgrade", "pip", "setuptools", "wheel"
    ) "pip could not be updated"

    Set-Stage 20 "Looking for an NVIDIA GPU"
    $gpu = if ($Torch -eq "cpu") { $null } else { Get-NvidiaGpu }
    Install-Torch $venvPython $gpu

    Set-Stage 55 "Installing Python packages"
    Invoke-Required $venvPython @(
        "-m", "pip", "install", "--progress-bar", "off",
        "-r", "requirements.txt", "-r", "installer\requirements-launcher.txt"
    ) "The Python packages could not be installed"

    Set-Stage 80 "Checking the install"
    $state = Get-TorchState $venvPython
    Write-Log "torch: $state"
    if (-not $state) { throw "PyTorch does not load. See $logPath" }
    if ($gpu -and (Get-TorchIndex $gpu) -and $state -notmatch "\|True$") {
        Add-Warning ("PyTorch cannot see $($gpu.Name), so Metascan will run on the CPU. " +
            "Update the NVIDIA driver, then run Setup again.")
    }

    Set-Stage 82 "Downloading language data"
    $code = Invoke-Tool -Exe $venvPython -Arguments @("setup_models.py")
    if ($code -ne 0) { Add-Warning "Language data did not download. Metascan fetches it when it first needs it." }

    Set-Stage 85 "Writing your settings"
    $apply = @("-m", "installer.apply_settings")
    if ($Settings) { $apply += @("--settings", $Settings) }
    $out = @(Invoke-Tool -Exe $venvPython -Arguments $apply -Capture)
    $last = if ($out.Count -gt 0) { [string]$out[-1] } else { "" }
    if ($last -notmatch "^clip_model=(\w+)$") {
        throw "Your settings could not be saved: $last. See $logPath"
    }
    $clip = $Matches[1]

    if (-not $SkipClip) {
        Set-Stage 87 "Downloading the search model (CLIP $clip)"
        $fetch = @("setup_models.py", "--skip-nltk", "--clip", $clip)
        $code = Invoke-Tool -Exe $venvPython -Arguments $fetch
        if ($code -ne 0) {
            # Seen once with a Hugging Face cache left by another tool: the
            # failed attempt rewrote the cache entry and the next one worked.
            Write-Log "Trying the search model again"
            $code = Invoke-Tool -Exe $venvPython -Arguments $fetch
        }
        if ($code -ne 0) {
            Add-Warning "The search model did not download. Metascan fetches it the first time it indexes your library."
        }
    }

    Set-Stage 100 "Finished"
}

$result = 0
try {
    Invoke-Install
    Write-Status "done"
} catch [CancelledException] {
    Write-Log "Cancelled"
    Write-Status "cancelled" "Cancelled"
    $result = 2
} catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    Write-Log $_.ScriptStackTrace
    Write-Status "failed" $_.Exception.Message
    $result = 1
} finally {
    $script:Log.Dispose()
}
exit $result
