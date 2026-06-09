#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/Phonsadboy/ATRIUM-TH.git",
    [string]$InstallPath = "$env:USERPROFILE\Projects\ai-company",
    [switch]$NoStart,
    [switch]$SkipDockerInstall,
    [switch]$SkipBrowserInstall,
    [switch]$SkipClaudeCodeInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-AnyCommand {
    param([string[]]$Names)
    foreach ($name in $Names) {
        if (Test-Command $name) {
            return $true
        }
    }
    return $false
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

function Add-UserPathIfExists {
    param([string]$PathValue)
    if (-not $PathValue -or -not (Test-Path $PathValue)) {
        return
    }
    try {
        $current = [System.Environment]::GetEnvironmentVariable("Path", "User")
        $parts = [System.Collections.Generic.List[string]]::new()
        foreach ($part in (($current -as [string]) -split [System.IO.Path]::PathSeparator)) {
            if ($part) {
                $parts.Add($part)
            }
        }
        if ($parts -notcontains $PathValue) {
            $parts.Add($PathValue)
            [System.Environment]::SetEnvironmentVariable("Path", ($parts -join [System.IO.Path]::PathSeparator), "User")
        }
    }
    catch {
        Write-Host "Could not persist PATH entry $PathValue to the user environment: $($_.Exception.Message)"
    }
}

function Add-CommonPaths {
    $paths = @(
        "$env:USERPROFILE\.local\bin",
        "$env:USERPROFILE\AppData\Local\Microsoft\WindowsApps",
        "$env:USERPROFILE\AppData\Roaming\npm",
        "$env:ProgramFiles\Git\cmd",
        "$env:ProgramFiles\nodejs",
        "$env:ProgramFiles\Docker\Docker\resources\bin",
        "$env:LocalAppData\Programs\Python\Launcher",
        "$env:LocalAppData\Programs\Python\Python312",
        "$env:LocalAppData\Programs\Python\Python312\Scripts",
        "$env:LocalAppData\Programs\Python\Python311",
        "$env:LocalAppData\Programs\Python\Python311\Scripts"
    )
    foreach ($path in $paths) {
        Add-PathIfExists $path
        Add-UserPathIfExists $path
    }
}

function Assert-SafeInstallPath {
    param([string]$PathValue)
    $full = [System.IO.Path]::GetFullPath($PathValue)
    $home = [System.IO.Path]::GetFullPath($env:USERPROFILE)
    $lower = $full.ToLowerInvariant()
    if ($lower.Contains("onedrive")) {
        throw "Refusing to install into OneDrive path: $full. Use a local path such as $home\Projects\ai-company."
    }
    foreach ($folder in @("Desktop", "Documents", "Downloads")) {
        $blocked = [System.IO.Path]::GetFullPath((Join-Path $home $folder)).TrimEnd("\")
        if ($full.TrimEnd("\").StartsWith($blocked, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to install into $folder path: $full. Use a local path such as $home\Projects\ai-company."
        }
    }
}

function Install-WingetPackageIfMissing {
    param(
        [string[]]$CommandName,
        [string]$PackageId,
        [string]$DisplayName
    )
    if (Test-AnyCommand $CommandName) {
        return
    }
    if (-not (Test-Command "winget")) {
        throw "$DisplayName is missing and winget is unavailable. Install $DisplayName manually, then rerun this installer."
    }
    Write-Step "Install $DisplayName"
    winget install --id $PackageId --exact --accept-source-agreements --accept-package-agreements
    Add-CommonPaths
    if (-not (Test-AnyCommand $CommandName)) {
        $expected = $CommandName -join " or "
        throw "$DisplayName installation did not expose '$expected' on PATH. Restart PowerShell and rerun this installer, or install $DisplayName manually."
    }
}

function Test-Python3Available {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3", "--version") },
        @{ Exe = "python"; Args = @("--version") }
    )
    foreach ($candidate in $candidates) {
        if (-not (Test-Command $candidate.Exe)) {
            continue
        }
        try {
            $candidateArgs = @($candidate.Args)
            $output = & $candidate.Exe @candidateArgs 2>&1
            if ($LASTEXITCODE -eq 0 -and (($output -join " ") -match "Python 3\.")) {
                return $true
            }
        }
        catch {
            continue
        }
    }
    return $false
}

function Install-PythonIfMissing {
    if (Test-Python3Available) {
        return
    }
    if (-not (Test-Command "winget")) {
        throw "Python 3 is missing and winget is unavailable. Install Python 3 manually, then rerun this installer."
    }
    Write-Step "Install Python 3"
    winget install --id Python.Python.3.12 --exact --accept-source-agreements --accept-package-agreements
    Add-CommonPaths
    if (-not (Test-Python3Available)) {
        throw "Python 3 installation did not expose a runnable Python 3 command. Restart PowerShell and rerun this installer, or install Python 3 manually."
    }
}

function Install-UvIfMissing {
    if (Test-Command "uv") {
        return
    }
    Write-Step "Install uv"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    Add-CommonPaths
    if (-not (Test-Command "uv")) {
        throw "uv installation finished, but uv is not on PATH yet. Restart PowerShell and rerun this installer."
    }
}

function Enable-PnpmIfMissing {
    if (Test-Command "pnpm") {
        return
    }
    if (-not (Test-Command "corepack")) {
        throw "corepack is missing. Install Node.js LTS, restart PowerShell, then rerun this installer."
    }
    Write-Step "Enable pnpm"
    corepack enable
    corepack prepare pnpm@10.15.0 --activate
    Add-CommonPaths
    if (-not (Test-Command "pnpm")) {
        throw "pnpm is still unavailable. Restart PowerShell and rerun this installer."
    }
}

function Install-ClaudeCodeIfMissing {
    if (Test-Command "claude") {
        return
    }
    $wingetError = $null
    if (Test-Command "winget") {
        Write-Step "Install Claude Code"
        try {
            winget install --id Anthropic.ClaudeCode --exact --accept-source-agreements --accept-package-agreements
        }
        catch {
            $wingetError = $_.Exception.Message
        }
        Add-CommonPaths
        if (Test-Command "claude") {
            return
        }
    }
    if (Test-Command "npm") {
        Write-Step "Install Claude Code through npm"
        npm install -g "@anthropic-ai/claude-code"
        Add-CommonPaths
        if (Test-Command "claude") {
            return
        }
    }
    if ($wingetError) {
        Write-Host "Claude Code winget install failed: $wingetError"
    }
    Write-Host "Claude Code CLI is not available yet. Install it manually, then run .\atrium.ps1 provider login claude-code."
}

function Start-DockerDesktopIfPresent {
    $dockerDesktopPaths = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LocalAppData "Docker\Docker Desktop.exe")
    )
    foreach ($dockerDesktop in $dockerDesktopPaths) {
        if ($dockerDesktop -and (Test-Path $dockerDesktop)) {
            Write-Step "Start Docker Desktop"
            Start-Process -FilePath $dockerDesktop | Out-Null
            return
        }
    }
}

function Test-BrowserInstalled {
    if (Test-AnyCommand -Names @("chrome", "msedge", "brave", "chromium")) {
        return $true
    }
    $browserPaths = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LocalAppData\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:LocalAppData\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
        "${env:ProgramFiles(x86)}\BraveSoftware\Brave-Browser\Application\brave.exe",
        "$env:LocalAppData\BraveSoftware\Brave-Browser\Application\brave.exe",
        "$env:ProgramFiles\Chromium\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Chromium\Application\chrome.exe",
        "$env:LocalAppData\Chromium\Application\chrome.exe"
    )
    foreach ($path in $browserPaths) {
        if ($path -and (Test-Path $path)) {
            return $true
        }
    }
    return $false
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "This installer is for native Windows PowerShell. On macOS, use ops/install_macos.sh."
}

Add-CommonPaths
Assert-SafeInstallPath $InstallPath

Write-Step "Install Windows native prerequisites"
Install-WingetPackageIfMissing -CommandName "git" -PackageId "Git.Git" -DisplayName "Git"
Install-PythonIfMissing
Install-WingetPackageIfMissing -CommandName "node" -PackageId "OpenJS.NodeJS.LTS" -DisplayName "Node.js LTS"
Install-UvIfMissing
Enable-PnpmIfMissing

if (-not $SkipDockerInstall) {
    Install-WingetPackageIfMissing -CommandName "docker" -PackageId "Docker.DockerDesktop" -DisplayName "Docker Desktop"
    Start-DockerDesktopIfPresent
}

if (-not $SkipBrowserInstall -and -not (Test-BrowserInstalled)) {
    if (Test-Command "winget") {
        Write-Step "Install Google Chrome"
        try {
            winget install --id Google.Chrome --exact --accept-source-agreements --accept-package-agreements
            Add-CommonPaths
        }
        catch {
            Write-Host "Google Chrome winget install failed: $($_.Exception.Message)"
            Write-Host "Continuing. Install Chrome, Edge, Brave, or Chromium manually, then run .\atrium.ps1 automation status --commands."
        }
    }
    else {
        Write-Host "No supported browser was found and winget is unavailable. Install Chrome, Edge, Brave, or Chromium manually, then run .\atrium.ps1 automation status --commands."
    }
}

if (-not $SkipClaudeCodeInstall) {
    Install-ClaudeCodeIfMissing
}

$installFullPath = [System.IO.Path]::GetFullPath($InstallPath)
$parent = [System.IO.Path]::GetDirectoryName($installFullPath)
if ($parent -and -not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}

if (Test-Path (Join-Path $installFullPath ".git")) {
    Write-Step "Use existing ATRIUM checkout"
}
elseif (Test-Path $installFullPath) {
    throw "$installFullPath already exists but is not a git checkout. Move it or choose another -InstallPath."
}
else {
    Write-Step "Clone ATRIUM"
    git clone $RepoUrl $installFullPath
}

Write-Step "Run ATRIUM native guided setup"
Push-Location $installFullPath
try {
    if ($NoStart) {
        powershell -NoProfile -ExecutionPolicy Bypass -File .\atrium.ps1 setup --yes --no-start
    }
    else {
        powershell -NoProfile -ExecutionPolicy Bypass -File .\atrium.ps1 setup --yes
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "ATRIUM frontend: http://127.0.0.1:5173"
Write-Host "ATRIUM backend:  http://127.0.0.1:8787"
Write-Host ""
Write-Host "Next native Windows checks:"
Write-Host "  .\atrium.ps1 provider status --probe"
Write-Host "  .\atrium.ps1 provider status --probe --json"
Write-Host "  .\atrium.ps1 provider login chatgpt"
Write-Host "  .\atrium.ps1 provider login claude-code"
Write-Host "  .\atrium.ps1 tools status"
Write-Host "  .\atrium.ps1 tools status --json"
Write-Host "  .\atrium.ps1 tools catalog"
Write-Host "  .\atrium.ps1 tools catalog --json"
Write-Host "  .\atrium.ps1 automation status --commands"
Write-Host "  .\atrium.ps1 automation status --json"
Write-Host "  .\atrium.ps1 automation audit"
Write-Host "  .\atrium.ps1 status"
Write-Host "  .\atrium.ps1 status --json"
Write-Host "  .\atrium.ps1 logs"
Write-Host "  .\atrium.ps1 logs --json"
Write-Host "  .\atrium.ps1 report"
Write-Host "  .\atrium.ps1 report --bundle"
Write-Host "  .\atrium.ps1 restart"
Write-Host "Optional cmd.exe shim:"
Write-Host "  atrium.cmd status"
