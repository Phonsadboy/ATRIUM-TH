$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Cli = Join-Path $Root "ops\atrium_cli.py"
$SystemProject = Join-Path $Root "system"

function Add-PathIfExists {
    param([string]$PathValue)
    if (-not $PathValue -or -not (Test-Path $PathValue)) {
        return
    }
    $parts = [System.Collections.Generic.List[string]]::new()
    foreach ($part in ($env:Path -split [System.IO.Path]::PathSeparator)) {
        if ($part) {
            $parts.Add($part)
        }
    }
    if ($parts -notcontains $PathValue) {
        $parts.Insert(0, $PathValue)
        $env:Path = ($parts -join [System.IO.Path]::PathSeparator)
    }
}

function Add-PythonInstallPaths {
    $pythonRoot = "$env:LocalAppData\Programs\Python"
    if (-not (Test-Path $pythonRoot)) {
        return
    }
    Get-ChildItem -Path $pythonRoot -Directory -Filter "Python3*" -ErrorAction SilentlyContinue | ForEach-Object {
        Add-PathIfExists $_.FullName
        Add-PathIfExists (Join-Path $_.FullName "Scripts")
    }
}

Add-PathIfExists "$env:USERPROFILE\.local\bin"
Add-PathIfExists "$env:USERPROFILE\AppData\Local\Microsoft\WindowsApps"
Add-PathIfExists "$env:USERPROFILE\AppData\Roaming\npm"
Add-PathIfExists "$env:ProgramFiles\Git\cmd"
Add-PathIfExists "$env:ProgramFiles\nodejs"
Add-PathIfExists "$env:ProgramFiles\Docker\Docker\resources\bin"
Add-PathIfExists "$env:LocalAppData\Programs\Python\Launcher"
Add-PathIfExists "$env:LocalAppData\Programs\Python\Python312"
Add-PathIfExists "$env:LocalAppData\Programs\Python\Python312\Scripts"
Add-PathIfExists "$env:LocalAppData\Programs\Python\Python311"
Add-PathIfExists "$env:LocalAppData\Programs\Python\Python311\Scripts"
Add-PythonInstallPaths

if (-not (Test-Path $Cli)) {
    throw "Missing ATRIUM CLI: $Cli"
}

function Test-Runner {
    param(
        [string]$Exe,
        [string[]]$RunnerArgs
    )
    $exists = if ($Exe -like "*\*") { Test-Path $Exe } else { Get-Command $Exe -ErrorAction SilentlyContinue }
    if (-not $exists) {
        return $false
    }
    try {
        $versionOutput = & $Exe @RunnerArgs --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        return (($versionOutput -join " ") -match "Python 3\.")
    }
    catch {
        return $false
    }
}

$candidates = @(
    @{ Exe = Join-Path $Root "system\.venv\Scripts\python.exe"; Args = @() },
    @{ Exe = "py"; Args = @("-3") },
    @{ Exe = "python"; Args = @() },
    @{ Exe = "uv"; Args = @("--project", $SystemProject, "run", "python") }
)

foreach ($candidate in $candidates) {
    $exe = $candidate.Exe
    $runnerArgs = @($candidate.Args)
    if (-not (Test-Runner -Exe $exe -RunnerArgs $runnerArgs)) {
        continue
    }

    & $exe @runnerArgs $Cli @args
    exit $LASTEXITCODE
}

throw "Python 3 is required. Install Python or uv, then rerun .\atrium.ps1."
