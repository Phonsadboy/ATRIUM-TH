param(
    [string]$RepoUrl = "https://github.com/Phonsadboy/ATRIUM-TH.git",
    [string]$Distro = "Ubuntu",
    [string]$InstallPath = "~/Projects/ai-company",
    [switch]$NoStart,
    [switch]$SkipDockerInstall
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function ConvertTo-BashSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

Write-Step "Check Windows prerequisites"

if (-not (Test-Command "wsl")) {
    throw "WSL is not available. Run this from Windows PowerShell on Windows 10/11."
}

if (-not $SkipDockerInstall) {
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dockerDesktop)) {
        if (Test-Command "winget") {
            Write-Step "Install Docker Desktop"
            winget install --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
        }
        else {
            throw "Docker Desktop is missing and winget is unavailable. Install Docker Desktop, enable WSL integration for Ubuntu, then rerun this script."
        }
    }
    elseif (-not (Test-Command "docker")) {
        Write-Host "Docker Desktop exists, but docker.exe is not on PATH yet."
    }

    if (Test-Path $dockerDesktop) {
        Write-Step "Start Docker Desktop"
        Start-Process $dockerDesktop | Out-Null
    }
}

$distros = (& wsl -l -q 2>$null) -replace "`0", ""
$distroExists = $false
foreach ($item in $distros) {
    if ($item.Trim() -eq $Distro) {
        $distroExists = $true
        break
    }
}

if (-not $distroExists) {
    Write-Step "Install WSL distro"
    wsl --install -d $Distro
    Write-Host ""
    Write-Host "Ubuntu/WSL installation was started. If Windows asks for a restart or Ubuntu asks you to create a UNIX user, finish that first, then rerun:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\ops\windows_wsl_install.ps1"
    exit 1
}

$repoUrlQuoted = ConvertTo-BashSingleQuoted $RepoUrl
$installPathQuoted = ConvertTo-BashSingleQuoted $InstallPath
$startStack = if ($NoStart) { "0" } else { "1" }

$bashScript = @'
#!/usr/bin/env bash
set -euo pipefail

REPO_URL=__ATRIUM_REPO_URL__
INSTALL_PATH=__ATRIUM_INSTALL_PATH__
START_STACK=__ATRIUM_START_STACK__

echo
echo "== Install WSL packages =="
sudo apt update
sudo apt install -y ca-certificates curl git lsof python3 python3-venv screen zsh

export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo
  echo "== Install uv =="
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

need_node=0
if ! command -v node >/dev/null 2>&1; then
  need_node=1
else
  node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)' || need_node=1
fi

if [ "$need_node" = "1" ]; then
  echo
  echo "== Install Node.js 20 through nvm =="
  if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
  fi
  export NVM_DIR="$HOME/.nvm"
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm install 20
  nvm use 20
fi

if [ -s "$HOME/.nvm/nvm.sh" ]; then
  export NVM_DIR="$HOME/.nvm"
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm use 20 >/dev/null 2>&1 || true
fi

echo
echo "== Enable pnpm =="
corepack enable
corepack prepare pnpm@latest --activate

echo
echo "== Check Docker Desktop WSL integration =="
if ! docker version >/dev/null 2>&1; then
  echo "Docker is not reachable from WSL."
  echo "Open Docker Desktop on Windows, enable Settings > Resources > WSL Integration for this Ubuntu distro, then rerun this script."
  exit 2
fi

case "$INSTALL_PATH" in
  "~")
    INSTALL_DIR="$HOME"
    ;;
  "~/"*)
    # `${INSTALL_PATH#~/}` triggers bash tilde expansion on the `~/` pattern
    # (bash expands `~/` to `$HOME/` before doing the prefix removal), so the
    # substitution silently fails and INSTALL_DIR ends up as
    # `$HOME/~/Ailab/atrium` (with a literal `~` directory). Use substring
    # extraction (`${VAR:2}` = skip 2 chars) to strip `~/` without expansion.
    INSTALL_DIR="$HOME/${INSTALL_PATH:2}"
    ;;
  *)
    INSTALL_DIR="$INSTALL_PATH"
    ;;
esac
mkdir -p "$(dirname "$INSTALL_DIR")"

if [ -d "$INSTALL_DIR/.git" ]; then
  echo
  echo "== Use existing ATRIUM checkout =="
  cd "$INSTALL_DIR"
else
  if [ -e "$INSTALL_DIR" ]; then
    echo "$INSTALL_DIR already exists but is not a git checkout. Move it or choose another -InstallPath."
    exit 3
  fi
  echo
  echo "== Clone ATRIUM =="
  git clone "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

echo
echo "== Bootstrap ATRIUM =="
./atrium doctor
./atrium bootstrap --full

if [ "$START_STACK" = "1" ]; then
  echo
  echo "== Start ATRIUM =="
  ./atrium start
  ./atrium status
fi

echo
echo "ATRIUM frontend: http://127.0.0.1:5173"
echo "ATRIUM backend:  http://127.0.0.1:8787"
'@

$bashScript = $bashScript.Replace("__ATRIUM_REPO_URL__", $repoUrlQuoted)
$bashScript = $bashScript.Replace("__ATRIUM_INSTALL_PATH__", $installPathQuoted)
$bashScript = $bashScript.Replace("__ATRIUM_START_STACK__", $startStack)

$tempScript = Join-Path $env:TEMP ("atrium-wsl-install-" + [Guid]::NewGuid().ToString("N") + ".sh")
Set-Content -Path $tempScript -Value $bashScript -Encoding ascii

try {
    Write-Step "Run ATRIUM setup inside WSL"
    # Pipe the bash script to WSL via stdin instead of translating a Windows path
    # to a WSL path. `wsl wslpath -a $tempScript` can return $null on some Windows
    # configurations (PowerShell argument passing eats backslashes), which then
    # caused `((... ) | Select-Object -Last 1).Trim()` to throw
    # "You cannot call a method on a null-valued expression".
    # Reading via stdin avoids any path translation; CRLF is normalised to LF so
    # `set -e` style scripts do not break on Windows line endings.
    $bashScript -replace "`r`n", "`n" | & wsl -d $Distro -- bash -s
    if ($LASTEXITCODE -ne 0) {
        throw "WSL bash script exited with code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -Path $tempScript -ErrorAction SilentlyContinue
}
