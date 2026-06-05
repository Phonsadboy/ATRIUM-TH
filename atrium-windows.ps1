$Script = Join-Path $PSScriptRoot "ops\windows_wsl_install.ps1"

if (-not (Test-Path $Script)) {
    throw "Missing Windows installer script: $Script"
}

& $Script @args
exit $LASTEXITCODE
