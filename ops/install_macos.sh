#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${ATRIUM_REPO_URL:-https://github.com/Phonsadboy/ATRIUM-TH.git}"
INSTALL_DIR="${ATRIUM_INSTALL_DIR:-$HOME/Projects/ai-company}"

step() {
  printf '\n== %s ==\n' "$1"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

add_common_paths() {
  export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
}

install_homebrew_if_needed() {
  add_common_paths
  if have brew; then
    return
  fi
  step "Install Homebrew"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  add_common_paths
  if ! have brew; then
    printf 'Homebrew installed, but brew is not on PATH yet. Restart Terminal and rerun this installer.\n' >&2
    exit 2
  fi
}

wait_for_command_line_tools() {
  if xcode-select -p >/dev/null 2>&1; then
    return
  fi
  step "Install Apple Command Line Tools"
  xcode-select --install >/dev/null 2>&1 || true
  printf 'Finish the Apple Command Line Tools installer window if macOS opened one.\n'
  for _ in $(seq 1 120); do
    if xcode-select -p >/dev/null 2>&1; then
      return
    fi
    sleep 5
  done
  printf 'Command Line Tools are still not ready. Finish the macOS installer, then rerun this command.\n' >&2
  exit 3
}

safe_install_dir() {
  case "$INSTALL_DIR" in
    *"iCloud Drive"*|*"Library/Mobile Documents"*|"$HOME/Desktop"*|"$HOME/Documents"*)
      printf 'Refusing to install into an iCloud/Desktop/Documents path: %s\n' "$INSTALL_DIR" >&2
      printf 'Use a local path such as ~/Projects/ai-company.\n' >&2
      exit 4
      ;;
  esac
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'This installer is for macOS. On Windows, use atrium.ps1 or ops/install_windows_native.ps1.\n' >&2
  exit 1
fi

add_common_paths
safe_install_dir
wait_for_command_line_tools

if ! have git || ! have python3; then
  install_homebrew_if_needed
  step "Install required bootstrap tools"
  brew install git python@3.11
fi

mkdir -p "$(dirname "$INSTALL_DIR")"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  step "Use existing ATRIUM checkout"
  cd "$INSTALL_DIR"
elif [[ -e "$INSTALL_DIR" ]]; then
  printf '%s already exists but is not a git checkout. Move it or set ATRIUM_INSTALL_DIR to another path.\n' "$INSTALL_DIR" >&2
  exit 5
else
  step "Clone ATRIUM"
  git clone "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

step "Run ATRIUM guided setup"
./atrium setup --yes

cat <<'EOF'

Next macOS checks:
  ./atrium doctor --json
  ./atrium status --json
  ./atrium provider status --probe --json
  ./atrium provider reference --json
  ./atrium provider env --json
  ./atrium provider login chatgpt
  ./atrium provider login claude-code
  ./atrium permissions status --json
  ./atrium permissions set full_auto --agent-full-access true
  ./atrium tools status --json
  ./atrium tools mcp-gateway --json
  ./atrium tools catalog --json
  ./atrium automation status --commands
  ./atrium automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output /tmp/atrium_host_bridge_macos_smoke.json
  ./atrium logs --json
  ./atrium report --bundle
EOF
