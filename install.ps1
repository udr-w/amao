#requires -Version 5.1
# Sets up a local amao environment on Windows: creates a virtual environment
# (falling back to `uv` if it fails), installs the package with dev extras,
# and seeds a .env file from .env.example. Safe to re-run -- never
# overwrites an existing .venv install or an existing .env file.
#
# Run directly: .\install.ps1
# Unlike install.sh on Linux/macOS, this does NOT need to be dot-sourced to
# activate the venv in your current session -- $env: variables aren't
# scoped the way regular PowerShell variables are, so Activate.ps1 running
# inside this script still takes effect in your session afterward.
#
# If you get "running scripts is disabled on this system", that's Windows'
# script execution policy blocking it (nothing to do with amao) -- run this
# once first, then retry:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$PythonCmd = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$VenvDir = ".venv"

if (-not (Get-Command $PythonCmd -ErrorAction SilentlyContinue)) {
    Write-Error "'$PythonCmd' not found on PATH. Install Python 3.10+ from python.org first (check 'Add python.exe to PATH' during setup)."
    exit 1
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warning "git not found on PATH -- amao requires it at runtime (GitHelper shells out to it)."
}

if (Test-Path $VenvDir) {
    Write-Host "==> $VenvDir already exists, reusing it (delete it first for a clean rebuild)"
} else {
    Write-Host "==> Creating virtual environment at $VenvDir"
    & $PythonCmd -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            Write-Host "==> '$PythonCmd -m venv' failed, falling back to uv instead"
            Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
            uv venv $VenvDir
        } else {
            Write-Error "Venv creation failed and uv isn't available either. Install uv (see https://astral.sh/uv) or repair your Python install, then re-run this script."
            exit 1
        }
    }
}

Write-Host "==> Installing amao with dev extras"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (Test-Path $VenvPip) {
    & $VenvPip install --quiet --upgrade pip
    & $VenvPip install --quiet -e ".[dev]"
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python $VenvPython -e ".[dev]"
} else {
    Write-Error "No pip in $VenvDir and uv isn't available -- can't install."
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "==> Seeding .env from .env.example -- fill in your real key(s) before running amao"
    Copy-Item ".env.example" ".env"
} else {
    Write-Host "==> .env already exists, leaving it untouched"
}

$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
Write-Host ""
if (Test-Path $ActivateScript) {
    & $ActivateScript
    Write-Host "Done -- .venv is now active in this session. Next step:"
    Write-Host "  Edit .env and set at least one provider's API key (see 'Rewiring the agents' in README.md),"
    Write-Host '  then: amao run --dir .\my-idea --goal "..."'
} else {
    Write-Host "Done. Next steps:"
    Write-Host "  1. $ActivateScript"
    Write-Host "  2. Edit .env and set at least one provider's API key (see 'Rewiring the agents' in README.md)"
    Write-Host '  3. amao run --dir .\my-idea --goal "..."'
}

Write-Host ""
Write-Host "amao reads .env automatically from the directory you run it in -- no need to set environment variables by hand unless you'd rather do that directly."
