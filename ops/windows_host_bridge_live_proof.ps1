#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*-[0-9a-fA-F-]{36}$')]
    [string]$ParityRunId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$SourceFingerprint,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$SourceManifestSha256,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 10000)]
    [int]$SourceFileCount,

    [string]$Output = 'C:\Temp\atrium_host_bridge_windows_live.json',

    [double]$MaxArtifactAgeHours = 24.0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptPath = $PSCommandPath
if (-not $ScriptPath) {
    $ScriptPath = $MyInvocation.MyCommand.Path
}
$ScriptDirectory = Split-Path -Parent $ScriptPath
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDirectory '..'))
$RequiredRepoFiles = @(
    'system/pyproject.toml',
    'ops/host_bridge_source_summary.py',
    'ops/windows_host_bridge_probe.py',
    'ops/host_bridge_artifact_summary.py'
)

foreach ($RelativePath in $RequiredRepoFiles) {
    $CandidatePath = Join-Path $RepoRoot $RelativePath
    if (-not (Test-Path -LiteralPath $CandidatePath)) {
        throw "Windows HostBridge live proof runner could not find repo file: $RelativePath. Run the script from the checked-out ATRIUM/OpenClaw repo."
    }
}

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

Add-PathIfExists "$env:USERPROFILE\.local\bin"
Add-PathIfExists "$env:USERPROFILE\AppData\Local\Microsoft\WindowsApps"
Add-PathIfExists "$env:USERPROFILE\AppData\Roaming\npm"
Add-PathIfExists "$env:ProgramFiles\Git\cmd"
Add-PathIfExists "$env:ProgramFiles\nodejs"
Add-PathIfExists "$env:ProgramFiles\Docker\Docker\resources\bin"
Add-PathIfExists "$env:LocalAppData\Programs\Python\Launcher"

function Invoke-AtriumStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$UvPath
    )

    Write-Host "==> $Name"
    & $UvPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'This runner must be executed from a signed-in Windows desktop host.'
}

$sessionName = [System.Environment]::GetEnvironmentVariable('SESSIONNAME')
if ($sessionName -eq 'Services') {
    throw 'Windows HostBridge live proof requires an interactive desktop session, not Services.'
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $UvCommand) {
    throw 'uv is required on PATH before running the Windows HostBridge live proof.'
}
$UvPath = $UvCommand.Source

$OutputPath = [System.IO.Path]::GetFullPath($Output)
$OutputDirectory = [System.IO.Path]::GetDirectoryName($OutputPath)
if ($OutputDirectory -and -not [System.IO.Directory]::Exists($OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$PreviousLocation = Get-Location
try {
    Set-Location -LiteralPath $RepoRoot
    Write-Host "Repo: $RepoRoot"
    Write-Host "Session: $sessionName"

    Invoke-AtriumStep `
        -Name 'Validate HostBridge source fingerprint' `
        -UvPath $UvPath `
        -Arguments @(
            '--project', 'system', 'run', 'python', 'ops/host_bridge_source_summary.py',
            '--expect-source-fingerprint', $SourceFingerprint.ToLowerInvariant(),
            '--expect-source-manifest-sha256', $SourceManifestSha256.ToLowerInvariant(),
            '--expect-source-file-count', ([string]$SourceFileCount)
        )

    Invoke-AtriumStep `
        -Name 'Run Windows HostBridge full live probe' `
        -UvPath $UvPath `
        -Arguments @(
            '--project', 'system', 'run', 'python', 'ops/windows_host_bridge_probe.py',
            '--full',
            '--parity-run-id', $ParityRunId,
            '--expect-source-fingerprint', $SourceFingerprint.ToLowerInvariant(),
            '--expect-source-manifest-sha256', $SourceManifestSha256.ToLowerInvariant(),
            '--expect-source-file-count', ([string]$SourceFileCount),
            '--output', $OutputPath
        )

    Invoke-AtriumStep `
        -Name 'Validate Windows HostBridge artifact' `
        -UvPath $UvPath `
        -Arguments @(
            '--project', 'system', 'run', 'python', 'ops/host_bridge_artifact_summary.py',
            '--label', 'windows',
            '--expect-parity-run-id', $ParityRunId,
            '--expect-source-fingerprint', $SourceFingerprint.ToLowerInvariant(),
            '--expect-source-manifest-sha256', $SourceManifestSha256.ToLowerInvariant(),
            '--expect-source-file-count', ([string]$SourceFileCount),
            '--max-artifact-age-hours', ([string]$MaxArtifactAgeHours),
            $OutputPath
        )
}
finally {
    Set-Location -LiteralPath $PreviousLocation
}

Write-Host ''
Write-Host 'Windows HostBridge live proof complete.'
Write-Host "Artifact: $OutputPath"
Write-Host 'To install this proof from the Windows repo host, run .\atrium.ps1 automation report --macos <macos-json> --windows <windows-json>.'
Write-Host 'If the report is installed from macOS, copy this file to /tmp/atrium_host_bridge_windows_live.json and run ./atrium automation report --macos <macos-json> --windows /tmp/atrium_host_bridge_windows_live.json.'
