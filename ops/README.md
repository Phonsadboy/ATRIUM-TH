# ATRIUM Native Runtime Runbook

This runbook is part of the OpenClaw-level host parity contract. It documents the native macOS and native Windows control paths that must stay available before browser, desktop, provider, MCP, and automation capabilities can be claimed as cross-OS verified.

## macOS Runtime Control

Use the local launcher from a macOS Terminal:

```bash
./atrium setup --yes
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
./atrium automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium
./atrium logs --json
./atrium report --bundle
```

`ops/install_macos.sh` prints the same next-check commands after running `./atrium setup --yes`. A macOS proof artifact is not enough by itself; the final claim still requires a matching native Windows live artifact and a persisted parity report.

## Windows Native Runtime Control

Use native PowerShell on the Windows host:

```powershell
.\atrium.ps1 setup
.\atrium.ps1 start
.\atrium.ps1 status --json
.\atrium.ps1 provider status --probe --json
.\atrium.ps1 provider reference --json
.\atrium.ps1 provider env --json
.\atrium.ps1 provider login chatgpt
.\atrium.ps1 provider login claude-code
.\atrium.ps1 permissions status --json
.\atrium.ps1 permissions set full_auto --agent-full-access true
.\atrium.ps1 tools status --json
.\atrium.ps1 tools mcp-gateway --json
.\atrium.ps1 tools mcp-probe --json
.\atrium.ps1 tools catalog --json
.\atrium.ps1 automation status --commands
.\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\Temp\atrium_host_bridge_windows_smoke.json
.\atrium.ps1 automation windows-live-proof --parity-run-id <run-id> --source-fingerprint <fingerprint> --source-manifest-sha256 <manifest> --source-file-count <count> --max-artifact-age-hours 24.0 --output C:\Temp\atrium_host_bridge_windows_live.json
.\atrium.ps1 automation artifact --label windows --expect-parity-run-id <run-id> --expect-source-fingerprint <fingerprint> --expect-source-manifest-sha256 <manifest> --expect-source-file-count <count> --max-artifact-age-hours 24.0 --json C:\Temp\atrium_host_bridge_windows_live.json
.\atrium.ps1 logs --json
.\atrium.ps1 report --bundle
```

The Windows path must produce the proof facets in `.\atrium.ps1 automation status --commands`: source validation, MCP gateway setup/probe/status, native browser and desktop smoke, raw Windows probe, Windows live proof, and artifact validation. Local fallback MCP is not enough for OpenClaw-level parity; configure `ATRIUM_MCP_GATEWAY_URL`, `ATRIUM_MCP_GATEWAY_TOKEN` or `ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_SERVICE`, and `ATRIUM_MCP_ENABLED_SERVERS` before running the live proof.

## Fresh Windows Install Runner

For a fresh checkout, choose a native PowerShell runner from the standard install paths, then execute `ops/install_windows_native.ps1`:

```powershell
$runnerPath = $null
foreach ($candidate in @(
  "$PSHOME\powershell.exe",
  "$PSHOME\pwsh.exe",
  "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe",
  "$env:SystemRoot\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
  "$env:ProgramFiles\PowerShell\7\pwsh.exe",
  "${env:ProgramFiles(x86)}\PowerShell\7\pwsh.exe"
)) {
  if ($candidate -and (Test-Path $candidate)) {
    $runnerPath = $candidate
    break
  }
}
if(-not $runnerPath) {
  throw "PowerShell runner not found"
}
& $runnerPath -NoProfile -ExecutionPolicy Bypass -File .\ops\install_windows_native.ps1
```

The installer stays on the native Windows host path. WSL2 and Docker may be useful for services, but they do not replace the Windows proof runner, the interactive desktop session, UIAutomation evidence, browser profile proof, or external-write MCP gateway proof.

## Acceptance Gate

After generating both host artifacts, copy the Windows artifact to the repo host path shown by the handoff packet, then accept it through the current gate:

```bash
./atrium automation accept-windows /tmp/atrium_host_bridge_windows_live.json --handoff /tmp/atrium_windows_handoff.json --max-artifact-age-hours 24.0 --windows-source-path 'C:\Temp\atrium_host_bridge_windows_live.json'
./atrium automation audit
```

Do not claim OpenClaw-level Windows parity unless `./atrium automation audit` reports `cross_os_verified`.
