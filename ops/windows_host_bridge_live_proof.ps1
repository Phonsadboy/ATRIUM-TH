#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*-[0-9a-fA-F-]{36}$')]
    [string]$ParityRunId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$SourceFingerprint,

    [string]$Output = 'C:\Temp\atrium_host_bridge_windows_live.json',

    [double]$MaxArtifactAgeHours = 24.0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-AtriumStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host "==> $Name"
    & uv @Arguments
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

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required on PATH before running the Windows HostBridge live proof.'
}

$OutputPath = [System.IO.Path]::GetFullPath($Output)
$OutputDirectory = [System.IO.Path]::GetDirectoryName($OutputPath)
if ($OutputDirectory -and -not [System.IO.Directory]::Exists($OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

Invoke-AtriumStep `
    -Name 'Validate HostBridge source fingerprint' `
    -Arguments @(
        '--project', 'system', 'run', 'python', 'ops/host_bridge_source_summary.py',
        '--expect-source-fingerprint', $SourceFingerprint.ToLowerInvariant()
    )

Invoke-AtriumStep `
    -Name 'Run Windows HostBridge full live probe' `
    -Arguments @(
        '--project', 'system', 'run', 'python', 'ops/windows_host_bridge_probe.py',
        '--full',
        '--parity-run-id', $ParityRunId,
        '--expect-source-fingerprint', $SourceFingerprint.ToLowerInvariant(),
        '--output', $OutputPath
    )

Invoke-AtriumStep `
    -Name 'Validate Windows HostBridge artifact' `
    -Arguments @(
        '--project', 'system', 'run', 'python', 'ops/host_bridge_artifact_summary.py',
        '--label', 'windows',
        '--expect-parity-run-id', $ParityRunId,
        '--expect-source-fingerprint', $SourceFingerprint.ToLowerInvariant(),
        '--max-artifact-age-hours', ([string]$MaxArtifactAgeHours),
        $OutputPath
    )

Write-Host ''
Write-Host 'Windows HostBridge live proof complete.'
Write-Host "Artifact: $OutputPath"
Write-Host 'Copy this file to the macOS repo host as /tmp/atrium_host_bridge_windows_live.json, then run ops/host_bridge_parity_report.py.'
