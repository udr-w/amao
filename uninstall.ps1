#requires -Version 5.1
# Removes what install.ps1 created. Nothing amao installs ever touches
# system Python or anything outside this repo directory -- everything
# lives in .venv, so removing that is the entire uninstall.
#
# .env is left in place by default since it may hold real API keys -- pass
# -WithEnv to remove it too.
param(
    [switch]$WithEnv
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($env:VIRTUAL_ENV -and (Get-Command deactivate -ErrorAction SilentlyContinue)) {
    deactivate
}

if (Test-Path ".venv") {
    Remove-Item -Recurse -Force ".venv"
    Write-Host "==> Removed .venv"
} else {
    Write-Host "==> .venv not present, nothing to remove"
}

if ($WithEnv) {
    if (Test-Path ".env") {
        Remove-Item -Force ".env"
        Write-Host "==> Removed .env"
    }
} elseif (Test-Path ".env") {
    Write-Host "==> Left .env in place (it may hold real API keys) -- pass -WithEnv to remove it too"
}

Write-Host "==> Nothing else to clean up -- amao was only ever installed inside .venv, never system-wide."
