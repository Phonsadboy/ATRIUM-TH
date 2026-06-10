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
Add-PathIfExists "$env:ProgramFiles\PowerShell\7"
Add-PathIfExists "${env:ProgramFiles(x86)}\PowerShell\7"
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

$OutputPath = [System.IO.Path]::GetFullPath($Output)
$OutputDirectory = [System.IO.Path]::GetDirectoryName($OutputPath)
if ($OutputDirectory -and -not [System.IO.Directory]::Exists($OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

function Get-CommandSource {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return $null
}

function ConvertTo-PosixSingleQuotedLiteral {
    param([string]$Value)
    $singleQuote = [string][char]39
    $replacement = $singleQuote + '"' + $singleQuote + '"' + $singleQuote
    return $singleQuote + ($Value -replace $singleQuote, $replacement) + $singleQuote
}

function ConvertTo-PowerShellSingleQuotedLiteral {
    param([string]$Value)
    $singleQuote = [string][char]39
    return $singleQuote + ($Value -replace $singleQuote, ($singleQuote + $singleQuote)) + $singleQuote
}

function Get-LiveProofFailureNextSteps {
    param([string]$FailedStage)
    $common = @(
        '.\atrium.ps1 doctor --json',
        '.\atrium.ps1 status --json',
        '.\atrium.ps1 logs --json',
        '.\atrium.ps1 report --bundle'
    )
    $stageCommands = switch ($FailedStage) {
        'windows_platform' { @('Run this proof from a signed-in native Windows desktop host.') }
        'interactive_session' { @('Sign in to the Windows desktop session directly, then rerun the live proof.') }
        'uv_path' { @('.\atrium.ps1 setup --yes', '.\atrium.ps1 doctor --json') }
        'source_validate' { @('.\atrium.ps1 automation source --json') }
        'mcp_external_write' { @('.\atrium.ps1 tools mcp-gateway --json', '.\atrium.ps1 tools mcp-probe --json') }
        'windows_full_probe' { @('.\atrium.ps1 automation windows-probe --full --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\Temp\atrium_host_bridge_windows_probe.json') }
        'artifact_validate' { @(".\atrium.ps1 automation artifact --label windows --max-artifact-age-hours 24.0 --json $OutputPath") }
        default { @('.\atrium.ps1 automation status --commands') }
    }
    return [ordered]@{
        failedStage = $FailedStage
        commands = @($stageCommands + $common)
        supportBundle = '.\atrium.ps1 report --bundle'
        rerun = '.\atrium.ps1 automation windows-live-proof --parity-run-id <run-id> --source-fingerprint <fingerprint> --source-manifest-sha256 <manifest> --source-file-count <count> --max-artifact-age-hours 24.0'
    }
}

function Get-LiveProofPreflight {
    param([string]$UvPath)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return [ordered]@{
        schemaVersion = 1
        mode = 'windows_live_preflight'
        generatedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        repoRoot = $RepoRoot
        outputPath = $OutputPath
        parityRunId = $ParityRunId
        sourceFingerprint = $SourceFingerprint.ToLowerInvariant()
        sourceManifestSha256 = $SourceManifestSha256.ToLowerInvariant()
        sourceFileCount = $SourceFileCount
        os = [ordered]@{
            platform = [System.Environment]::OSVersion.Platform.ToString()
            version = [System.Environment]::OSVersion.VersionString
            isWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
            sessionName = [System.Environment]::GetEnvironmentVariable('SESSIONNAME')
            userName = [System.Environment]::UserName
            userDomainName = [System.Environment]::UserDomainName
            isElevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        }
        tools = [ordered]@{
            uv = $UvPath
            python = Get-CommandSource 'python'
            py = Get-CommandSource 'py'
            git = Get-CommandSource 'git'
            node = Get-CommandSource 'node'
            pnpm = Get-CommandSource 'pnpm'
            docker = Get-CommandSource 'docker'
            powershell = Get-CommandSource 'powershell'
            pwsh = Get-CommandSource 'pwsh'
        }
        path = $env:Path
    }
}

function Write-LiveProofFailureArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Reason,

        [object]$Preflight,

        [string]$FailedStage = 'preflight'
    )
    $partialArtifact = $null
    if (Test-Path -LiteralPath $OutputPath) {
        try {
            $existingArtifact = Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json -Depth 64
            if ($existingArtifact -and $existingArtifact.mode -ne 'windows_live_proof_failed') {
                $checkNames = @()
                if ($existingArtifact.checks) {
                    $checkNames = @($existingArtifact.checks.PSObject.Properties.Name)
                }
                $partialArtifact = [ordered]@{
                    preserved = $true
                    ok = $existingArtifact.ok
                    mode = $existingArtifact.mode
                    generatedAt = $existingArtifact.generatedAt
                    parityRunId = $existingArtifact.parityRunId
                    sourceFingerprint = $existingArtifact.source.sourceFingerprint
                    sourceManifestSha256 = $existingArtifact.source.sourceManifestSha256
                    sourceFileCount = $existingArtifact.source.sourceFileCount
                    hostPlatform = $existingArtifact.host.platform
                    hostFingerprint = $existingArtifact.host.hostFingerprint
                    statusPlatform = $existingArtifact.status.platform
                    checkNames = $checkNames
                    checkCount = $checkNames.Count
                }
            }
        }
        catch {
            $partialArtifact = [ordered]@{
                preserved = $false
                error = $_.Exception.Message
            }
        }
    }
    $payload = [ordered]@{
        schemaVersion = 1
        ok = $false
        mode = 'windows_live_proof_failed'
        generatedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        parityRunId = $ParityRunId
        sourceFingerprint = $SourceFingerprint.ToLowerInvariant()
        sourceManifestSha256 = $SourceManifestSha256.ToLowerInvariant()
        sourceFileCount = $SourceFileCount
        source = [ordered]@{
            sourceFingerprint = $SourceFingerprint.ToLowerInvariant()
            sourceManifestSha256 = $SourceManifestSha256.ToLowerInvariant()
            sourceFileCount = $SourceFileCount
        }
        error = $Reason
        failedStage = $FailedStage
        nextSteps = Get-LiveProofFailureNextSteps -FailedStage $FailedStage
        partialArtifact = $partialArtifact
        preflight = $Preflight
    }
    $payload | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}

function Get-McpExternalWriteProof {
    $atriumScript = Join-Path $RepoRoot 'atrium.ps1'
    if (-not (Test-Path -LiteralPath $atriumScript)) {
        throw 'atrium.ps1 is required before validating MCP external-write readiness.'
    }

    $raw = & $atriumScript tools mcp-probe --json 2>&1
    $exitCode = $LASTEXITCODE
    $rawText = ($raw | Out-String).Trim()
    if ($exitCode -ne 0) {
        throw "MCP external-write probe failed with exit code ${exitCode}: $rawText"
    }

    try {
        $payload = $rawText | ConvertFrom-Json -Depth 64
    }
    catch {
        throw "MCP external-write probe did not return valid JSON: $($_.Exception.Message)"
    }

    $mcpConnector = $payload.connector
    if (-not $mcpConnector -and $payload.connectors) {
        foreach ($connector in @($payload.connectors)) {
            if ($connector.id -eq 'mcp') {
                $mcpConnector = $connector
                break
            }
        }
    }
    if (-not $mcpConnector) {
        throw 'MCP connector is missing from tools mcp-probe JSON.'
    }

    $externalWriteRequires = @()
    if ($payload.requirements) {
        $externalWriteRequires = @($payload.requirements)
    }
    elseif ($mcpConnector.externalWriteRequires) {
        $externalWriteRequires = @($mcpConnector.externalWriteRequires)
    }
    $gatewayHealthOk = $payload.gatewayHealth -and $payload.gatewayHealth.ok -eq $true
    $ready = $payload.ready -eq $true
    $writeReady = $mcpConnector.writeReady -eq $true
    $localFallback = $mcpConnector.localFallback -eq $true
    if ((-not $ready) -or (-not $gatewayHealthOk) -or (-not $writeReady) -or $localFallback -or $externalWriteRequires.Count -gt 0) {
        $requirementsText = ($externalWriteRequires -join '; ')
        if (-not $requirementsText) {
            $requirementsText = 'no externalWriteRequires detail returned'
        }
        throw "MCP external-write readiness is required for OpenClaw-level Windows parity; ready=$ready; gatewayHealthOk=$gatewayHealthOk; writeReady=$writeReady; localFallback=$localFallback; requirements=$requirementsText; setupCommand=.\atrium.ps1 tools mcp-gateway --json; probeCommand=.\atrium.ps1 tools mcp-probe --json"
    }

    return [ordered]@{
        ok = $true
        verified = $true
        returnCode = 0
        stage = 'mcp_external_write'
        proofFacet = 'mcpExternalWriteReady'
        probe = $true
        ready = $ready
        gatewayHealthOk = $gatewayHealthOk
        id = $mcpConnector.id
        status = $mcpConnector.status
        readReady = $mcpConnector.readReady
        writeReady = $mcpConnector.writeReady
        localFallback = $mcpConnector.localFallback
        externalWriteRequires = $externalWriteRequires
        runtimeStatus = $mcpConnector.runtimeStatus
        probeCommand = '.\atrium.ps1 tools mcp-probe --json'
        setupCommand = '.\atrium.ps1 tools mcp-gateway --json'
    }
}

function Add-McpExternalWriteProofToArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Proof
    )

    $artifact = Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json -Depth 64
    if (-not $artifact.checks) {
        $artifact | Add-Member -MemberType NoteProperty -Name checks -Value ([ordered]@{})
    }
    $artifact.checks | Add-Member -MemberType NoteProperty -Name mcpExternalWriteReady -Value $Proof -Force
    $artifact | ConvertTo-Json -Depth 64 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}

function Add-WindowsLiveProofRunnerToArtifact {
    $artifact = Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json -Depth 64
    if (-not $artifact.checks) {
        $artifact | Add-Member -MemberType NoteProperty -Name checks -Value ([ordered]@{})
    }
    $proof = [ordered]@{
        ok = $true
        verified = $true
        runner = 'ops/windows_host_bridge_live_proof.ps1'
        command = '.\atrium.ps1 automation windows-live-proof'
        failureStages = @(
            'source_validate',
            'mcp_external_write',
            'windows_full_probe',
            'artifact_validate'
        )
        readinessGates = [ordered]@{
            source = 'source_validate'
            mcpExternalWrite = 'mcp_external_write'
            browserDesktopSmoke = 'windows_full_probe'
            artifactValidation = 'artifact_validate'
        }
        repoRoot = $RepoRoot
        outputPath = $OutputPath
        parityRunId = $ParityRunId
        sourceFingerprint = $SourceFingerprint.ToLowerInvariant()
        sourceManifestSha256 = $SourceManifestSha256.ToLowerInvariant()
        sourceFileCount = $SourceFileCount
        maxArtifactAgeHours = $MaxArtifactAgeHours
    }
    $artifact.checks | Add-Member -MemberType NoteProperty -Name windowsLiveProofRunner -Value $proof -Force
    $artifact | ConvertTo-Json -Depth 64 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
$UvPath = if ($UvCommand) { $UvCommand.Source } else { $null }
$Preflight = Get-LiveProofPreflight -UvPath $UvPath

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    $reason = 'This runner must be executed from a signed-in Windows desktop host.'
    Write-LiveProofFailureArtifact -Reason $reason -Preflight $Preflight -FailedStage 'windows_platform'
    throw $reason
}

$sessionName = [System.Environment]::GetEnvironmentVariable('SESSIONNAME')
if ($sessionName -eq 'Services') {
    $reason = 'Windows HostBridge live proof requires an interactive desktop session, not Services.'
    Write-LiveProofFailureArtifact -Reason $reason -Preflight $Preflight -FailedStage 'interactive_session'
    throw $reason
}

if (-not $UvCommand) {
    $reason = 'uv is required on PATH before running the Windows HostBridge live proof.'
    Write-LiveProofFailureArtifact -Reason $reason -Preflight $Preflight -FailedStage 'uv_path'
    throw $reason
}

$PreviousLocation = Get-Location
$CurrentStage = 'repo_setup'
try {
    Set-Location -LiteralPath $RepoRoot
    Write-Host "Repo: $RepoRoot"
    Write-Host "Session: $sessionName"
    Write-Host "Preflight: writing failure details to $OutputPath if a step fails"

    $CurrentStage = 'source_validate'
    Invoke-AtriumStep `
        -Name 'Validate HostBridge source fingerprint' `
        -UvPath $UvPath `
        -Arguments @(
            '--project', 'system', 'run', 'python', 'ops/host_bridge_source_summary.py',
            '--expect-source-fingerprint', $SourceFingerprint.ToLowerInvariant(),
            '--expect-source-manifest-sha256', $SourceManifestSha256.ToLowerInvariant(),
            '--expect-source-file-count', ([string]$SourceFileCount)
        )

    $CurrentStage = 'mcp_external_write'
    Write-Host '==> Validate MCP external-write readiness'
    $McpExternalWriteProof = Get-McpExternalWriteProof

    $CurrentStage = 'windows_full_probe'
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

    $CurrentStage = 'attach_mcp_proof'
    Add-McpExternalWriteProofToArtifact -Proof $McpExternalWriteProof
    $CurrentStage = 'attach_runner_proof'
    Add-WindowsLiveProofRunnerToArtifact

    $CurrentStage = 'artifact_validate'
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
catch {
    Write-LiveProofFailureArtifact -Reason $_.Exception.Message -Preflight $Preflight -FailedStage $CurrentStage
    throw
}
finally {
    Set-Location -LiteralPath $PreviousLocation
}

Write-Host ''
Write-Host 'Windows HostBridge live proof complete.'
Write-Host "Artifact: $OutputPath"
$OutputPathForPosixShell = ConvertTo-PosixSingleQuotedLiteral $OutputPath
$OutputPathForPowerShell = ConvertTo-PowerShellSingleQuotedLiteral $OutputPath
Write-Host 'Preferred gate: copy this artifact to the repo host path from the generated handoff packet, then run automation accept-windows with that handoff.'
Write-Host "  ./atrium automation accept-windows /tmp/atrium_host_bridge_windows_live.json --handoff /tmp/atrium_windows_handoff.json --max-artifact-age-hours 24.0 --windows-source-path $OutputPathForPosixShell"
Write-Host "  .\atrium.ps1 automation accept-windows <copied-windows-json> --handoff <handoff-json> --max-artifact-age-hours 24.0 --windows-source-path $OutputPathForPowerShell"
Write-Host 'Manual verifier fallback: run automation report with both macOS and Windows artifacts, then automation audit.'
Write-Host '  .\atrium.ps1 automation report --macos <macos-json> --windows <windows-json> --max-artifact-age-hours 24.0'
Write-Host '  ./atrium automation report --macos <macos-json> --windows /tmp/atrium_host_bridge_windows_live.json --max-artifact-age-hours 24.0'
