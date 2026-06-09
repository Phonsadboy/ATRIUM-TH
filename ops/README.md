# ATRIUM Operations

This folder is intentionally minimal. It contains only the files needed for
local installation, macOS startup, Windows native setup, account OAuth, and
database maintenance.

## Included

- `chatgpt_account_oauth_login.py` - local ChatGPT account OAuth login helper
- `install_macos.sh` - macOS one-command installer that checks Command Line
  Tools, clones ATRIUM, and hands off to `./atrium setup --yes`
- `install_windows_native.ps1` - native Windows PowerShell installer that
  prepares Git, Python, uv, Node/pnpm, Docker Desktop, Chrome/Edge/Brave/Chromium
  browser support, Claude Code CLI, clone, and hands off to `.\atrium.ps1 setup --yes`
- `..\atrium.cmd` - optional Windows Terminal/cmd.exe shim that forwards to
  `.\atrium.ps1` with ExecutionPolicy Bypass and falls back to `pwsh.exe`
- `macos_host_bridge_probe.py` - macOS HostBridge parity probe for shell,
  browser, desktop, notification, and Calculator Accessibility checks
- `windows_host_bridge_probe.py` - Windows HostBridge parity probe for shell,
  browser, desktop, notification, and interactive desktop checks
- `windows_host_bridge_live_proof.ps1` - Windows-side runner that validates
  source fingerprint, source manifest, and proof-bound file count, runs the
  full live Windows probe, and validates the resulting artifact before handoff
- `host_bridge_parity_report.py` - cross-OS proof verifier that refuses to pass
  unless both macOS and Windows full live probe JSON artifacts prove readiness
- `launchd/` - macOS LaunchAgent examples and wrapper scripts
- `scripts/backup_postgres.sh` - local Postgres backup script
- `scripts/migrate_sqlite_to_postgres.py` - one-time SQLite to Postgres migration helper

## Full Local Stack

From the repo root:

```bash
cp system/.env.example system/.env
docker compose up -d postgres ollama
cd system
uv run --extra postgres alembic -c alembic.ini upgrade head
uv run --extra live --extra postgres --extra graph python -m app
```

In another terminal:

```bash
cd ui
pnpm install
pnpm dev --host 127.0.0.1 --port 5173
```

## Windows Native Runtime Control

From a Windows checkout, use the root PowerShell wrapper:

```powershell
$script="$env:TEMP\atrium-windows-native-install.ps1"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Phonsadboy/ATRIUM-TH/main/ops/install_windows_native.ps1" -OutFile $script; powershell -ExecutionPolicy Bypass -File $script
```

Installer skip flags: `-NoStart`, `-SkipDockerInstall`, `-SkipBrowserInstall`,
and `-SkipClaudeCodeInstall`.

After the repo exists:

```powershell
.\atrium.ps1 setup
.\atrium.ps1 start
.\atrium.ps1 restart
.\atrium.ps1 tools status
.\atrium.ps1 tools status --json
.\atrium.ps1 tools catalog
.\atrium.ps1 tools catalog --json
.\atrium.ps1 provider status --probe
.\atrium.ps1 provider status --probe --json
.\atrium.ps1 provider login chatgpt
.\atrium.ps1 provider login claude-code
.\atrium.ps1 provider disconnect chatgpt
.\atrium.ps1 automation status --commands
.\atrium.ps1 automation status --json
.\atrium.ps1 automation audit
.\atrium.ps1 automation source
.\atrium.ps1 automation handoff --macos <macos-json>
.\atrium.ps1 automation windows-live-proof --parity-run-id <run-id> --source-fingerprint <fingerprint> --source-manifest-sha256 <manifest> --source-file-count <count>
.\atrium.ps1 automation artifact --label windows --expect-parity-run-id <run-id> --json <windows-json>
.\atrium.ps1 automation report --macos <macos-json> --windows <copied-windows-json>
.\atrium.ps1 status
.\atrium.ps1 status --json
.\atrium.ps1 logs
.\atrium.ps1 logs --json
.\atrium.ps1 report
.\atrium.ps1 report --bundle
.\atrium.ps1 stop
```

From `cmd.exe`, use `atrium.cmd <command>` for the same native workflow; it
prefers Windows PowerShell and falls back to PowerShell 7 (`pwsh.exe`).

The Windows native path runs the same `ops/atrium_cli.py` CLI but uses
PowerShell/PID-file lifecycle management instead of `screen`/`zsh`; CLI
diagnostics accept Windows PowerShell or PowerShell 7 (`pwsh`). `stop` and
`restart` stop the Windows frontend/backend process tree so child `uv`, Python,
Node, or Vite processes do not keep ports open after restart. Backend and
frontend logs and PID files live under `system/logs`. Docker Desktop is the
full-stack database/Ollama path on Windows.
The installer and `.\atrium.ps1 setup` validate a runnable Python 3 command
instead of trusting the Windows Store alias. The installer and CLI refresh PATH
for the current PowerShell session and persist common user PATH entries for
`uv`, `pnpm`, Docker, Git, Node, Python Launcher, and Claude Code so the native
workflow survives a new terminal.
The installer checks native command exit codes for dependency installers,
`git clone`, and `.\atrium.ps1 setup`, so failed prerequisite/clone/setup runs
stop the installer instead of printing a successful handoff.
`start` attempts to open Docker Desktop and waits for Docker before starting
Postgres/Ollama; if Docker is still blocked, it stops with a clear next step
instead of letting the backend fail later.
If browser install is blocked by local policy, install Chrome, Edge, Brave, or Chromium manually
and rerun `.\atrium.ps1 automation status --commands` to inspect the remaining
browser/desktop gaps.
`tools status/catalog` inspect the AI tool registry, tool catalog,
risk/executor summary, and connector readiness from the backend.
`provider status/login/disconnect` manages ChatGPT account and Claude Code
account readiness from the native terminal, while keeping account identities and
secrets out of normal support output.
`automation status/source/handoff/windows-probe/windows-live-proof/artifact/report/audit` exposes
HostBridge browser/desktop readiness, source/artifact validation commands, the
Windows full live proof workflow, owner permission mode, the cross-OS report
installer, and the OpenClaw-level gate from the same PowerShell entrypoint.
Backend self-update restart also uses the native PowerShell path on Windows:
`.\atrium.ps1 restart --force`, with output in
`system/logs/self-update-restart.log`.
`status` and `report` also summarize provider auth, AI tool registry counts,
connector readiness, browser/desktop HostBridge readiness, full-autonomy
permission state, local proof artifact freshness, Docker CLI/Compose/daemon
readiness, and the HostBridge cross-OS parity proof gap plus generated proof
commands. On Windows,
`status --json` also includes backend/frontend PID ownership, PID-file state,
and log paths for support handoff.
`report --bundle` writes a redacted support zip containing `support-report.txt`,
`manifest.json`, available backend/frontend logs, and machine-readable
`diagnostics/status.json`, `diagnostics/process.json`, `diagnostics/logs.json`,
`diagnostics/permission-mode.json`, `diagnostics/provider-status.json`,
`diagnostics/tools-status.json`, and `diagnostics/automation-status.json` for
Windows-native support handoff.
`automation status --commands` includes the OpenClaw-level Windows contract:
Windows is a native host, and browser/desktop readiness must not silently
degrade. The contract also checks the Windows-native entrypoints,
provider/runtime API surface, and required connector feature readiness for local
files, Git, sandbox execution, HTTP, web research, browser, desktop, and MCP
external tools. MCP local fallback remains usable for read/status/guidance, but
does not satisfy OpenClaw-level external-write parity without a healthy gateway.
It stays
blocked or unverified until the current host runtime, Windows full live proof
facets, required feature surfaces, and browser plus desktop connector proof gates
all pass. Use `.\atrium.ps1 automation audit` as the machine-checkable gate; it
exits non-zero and prints grouped local/API/feature/connector/proof gaps until
the contract reaches `cross_os_verified`.

## macOS launchd

See `ops/launchd/README.md` when ATRIUM should start automatically at login.

## macOS HostBridge

Use the simulation mode from any host for branch coverage:

```bash
uv --project system run python ops/macos_host_bridge_probe.py --simulate
```

Run the full parity probe only from a signed-in macOS desktop session with
Accessibility permission enabled:

```bash
uv --project system run python ops/macos_host_bridge_probe.py --full
```

## Browser control profiles

`browser.open profile=atrium` launches a visible isolated browser window using
`data/browser-profiles/<profile>`. Deterministic `browser.snapshot` and
`browser.act` use a separate Playwright profile under
`data/browser-control-profiles/<profile>` so a user-visible ATRIUM browser
window or a stale Chromium `SingletonLock` cannot block DOM-ref automation.

## macOS native desktop refs

On macOS, foreground coordinate/input tools stay blocked when the active GUI
session is `loginwindow`. `desktop.act` may still pass that preflight only when
the call targets a saved desktop ref and sets `requireNative=true`; in that mode
ATRIUM attempts the Accessibility action directly and refuses coordinate/input
fallback.

## Windows HostBridge

Use the simulation mode from macOS/Linux for branch coverage:

```bash
uv --project system run python ops/windows_host_bridge_probe.py --simulate
```

Run the full parity probe only from a signed-in Windows desktop session:

```powershell
.\atrium.ps1 automation windows-probe --full
```

For cross-OS proof handoff, prefer the live proof runner:

```powershell
.\atrium.ps1 automation windows-live-proof `
  --parity-run-id <run-id> `
  --source-fingerprint <fingerprint> `
  --source-manifest-sha256 <manifest> `
  --source-file-count <count> `
  --output C:\Temp\atrium_host_bridge_windows_live.json
```

## HostBridge parity proof gate

Simulation proves branch coverage only. Do not treat it as desktop automation
parity. To claim macOS/Windows parity, collect full live JSON artifacts on both
hosts and verify them together:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(uuidgen | tr '[:upper:]' '[:lower:]')"
SOURCE_PROVENANCE="$(uv --project system run python - <<'PY'
from app.host_bridge_proof import host_bridge_source_provenance
source = host_bridge_source_provenance()
print(source["sourceFingerprint"])
print(source["sourceManifestSha256"])
print(source["sourceFileCount"])
PY
)"
SOURCE_FINGERPRINT="$(printf '%s\n' "$SOURCE_PROVENANCE" | sed -n '1p')"
SOURCE_MANIFEST_SHA256="$(printf '%s\n' "$SOURCE_PROVENANCE" | sed -n '2p')"
SOURCE_FILE_COUNT="$(printf '%s\n' "$SOURCE_PROVENANCE" | sed -n '3p')"
printf 'HostBridge parity run ID: %s\n' "$RUN_ID"
printf 'HostBridge source fingerprint: %s\n' "$SOURCE_FINGERPRINT"
printf 'HostBridge source manifest SHA-256: %s\n' "$SOURCE_MANIFEST_SHA256"
printf 'HostBridge source file count: %s\n' "$SOURCE_FILE_COUNT"
uv --project system run python ops/macos_host_bridge_probe.py \
  --full \
  --parity-run-id "$RUN_ID" \
  --expect-source-fingerprint "$SOURCE_FINGERPRINT" \
  --expect-source-manifest-sha256 "$SOURCE_MANIFEST_SHA256" \
  --expect-source-file-count "$SOURCE_FILE_COUNT" \
  --output /tmp/atrium_host_bridge_macos_live.json
```

```powershell
$RunId = "<paste HostBridge parity run ID from macOS>"
$SourceFingerprint = "<paste HostBridge source fingerprint from macOS>"
$SourceManifestSha256 = "<paste HostBridge source manifest SHA-256 from macOS>"
$SourceFileCount = <paste HostBridge source file count from macOS>
.\ops\windows_host_bridge_live_proof.ps1 `
  -ParityRunId $RunId `
  -SourceFingerprint $SourceFingerprint `
  -SourceManifestSha256 $SourceManifestSha256 `
  -SourceFileCount $SourceFileCount `
  -Output C:\Temp\atrium_host_bridge_windows_live.json
```

The runner is the preferred Windows handoff command because it fails fast if
the Windows checkout has the wrong HostBridge source fingerprint, manifest, or
proof-bound file count, refreshes common Windows CLI paths for fresh PowerShell
sessions, then runs the full live probe and validates the artifact before you copy it back to the repo host for
`.\atrium.ps1 automation report` on Windows or `./atrium automation report` on
macOS. If you need to run the steps manually, use:

```powershell
uv --project system run python ops/windows_host_bridge_probe.py `
  --full `
  --parity-run-id $RunId `
  --expect-source-fingerprint $SourceFingerprint `
  --expect-source-manifest-sha256 $SourceManifestSha256 `
  --expect-source-file-count $SourceFileCount `
  --output C:\Temp\atrium_host_bridge_windows_live.json
```

Use the same run ID value on both hosts. The verifier rejects artifacts that are
missing `parityRunId` or have different values, even when both artifacts are
otherwise fresh.

Before running each full probe, verify that both hosts are on the same
HostBridge source fingerprint, source manifest, and proof-bound file count.
This catches stale Windows checkouts and missing dirty changes before a long
desktop probe:

```bash
uv --project system run python ops/host_bridge_source_summary.py \
  --expect-source-fingerprint "$SOURCE_FINGERPRINT" \
  --expect-source-manifest-sha256 "$SOURCE_MANIFEST_SHA256" \
  --expect-source-file-count "$SOURCE_FILE_COUNT"
```

After the macOS full live artifact exists, create a Windows handoff packet from
the current repo host:

```bash
./atrium automation handoff \
  --macos /tmp/atrium_host_bridge_macos_live.json \
  --output /tmp/atrium_windows_handoff.json
```

The handoff command validates the macOS artifact against the current
HostBridge source fingerprint, source manifest, and proof-bound file count,
then writes the exact Windows live-proof, artifact validation, report, and audit
commands. It does not mark Windows verified; the copied Windows artifact still
has to pass `automation report` and `automation audit`.

Copy the Windows JSON artifact from `C:\Temp\atrium_host_bridge_windows_live.json`
to a local path on the repo host, for example
`/tmp/atrium_host_bridge_windows_live.json`. Then run the cross-OS verifier from
the repo root and install the backend report in one step:

```bash
uv --project system run python ops/host_bridge_artifact_summary.py \
  --label macos \
  --expect-parity-run-id "$RUN_ID" \
  --expect-source-fingerprint "$SOURCE_FINGERPRINT" \
  --expect-source-manifest-sha256 "$SOURCE_MANIFEST_SHA256" \
  --expect-source-file-count "$SOURCE_FILE_COUNT" \
  /tmp/atrium_host_bridge_macos_live.json

uv --project system run python ops/host_bridge_artifact_summary.py \
  --label windows \
  --expect-parity-run-id "$RUN_ID" \
  --expect-source-fingerprint "$SOURCE_FINGERPRINT" \
  --expect-source-manifest-sha256 "$SOURCE_MANIFEST_SHA256" \
  --expect-source-file-count "$SOURCE_FILE_COUNT" \
  /tmp/atrium_host_bridge_windows_live.json

./atrium automation report \
  --macos /tmp/atrium_host_bridge_macos_live.json \
  --windows /tmp/atrium_host_bridge_windows_live.json \
  --windows-source-path 'C:\Temp\atrium_host_bridge_windows_live.json'
```

On a Windows repo host, use the same options with `.\atrium.ps1 automation
report` from PowerShell.

If the Windows probe wrote to a different Windows-host path, pass
`--windows-source-path` so missing-artifact findings and the persisted report
show the correct copy source. The `automation report` wrapper calls
`ops/host_bridge_parity_report.py`, fails if the verifier fails, and writes the
report to `system/data/host-bridge-parity-report.json`, which is the path the
backend reads by default. The `host_bridge_artifact_summary.py` checks are
not a substitute for the parity verifier; they are a fast handoff guard that
catches wrong run IDs, stale files, copied simulated artifacts, source drift, or
wrong-platform artifacts before the final cross-OS report.

The verifier fails if either artifact is missing, simulated, runtime-blocked,
interactive-skipped, missing the shared `parityRunId`, paired with a different
`parityRunId`, lacks deterministic `browser.snapshot` + `browser.act` DOM-ref
proof through Playwright in ATRIUM's isolated browser profile, lacks native
desktop action proof for macOS Calculator `AXPress` with post-action display
verification and TextEdit `setValue`,
lacks macOS AppleScript clipboard proof, lacks Windows DPI/virtual-screen/foreground-activation
metadata proof, lacks Windows interactive-session identity, lacks Windows Unicode typing
or keyboard shortcut mapping proof, lacks Windows Notepad UIAutomation
`ValuePattern` proof with distinct post-action text verification, lacks exact
Windows clipboard round-trip proof, lacks the
probe `schemaVersion/generatedAt` stamp, is too
old, or was generated from different HostBridge source provenance than the other
OS artifact, or no longer matches the current checkout. Regenerate both full
probe artifacts after any HostBridge source change. For deliberate
historical audits only, the verifier accepts `--skip-current-source-check`; do
not use that flag for connector proof reports.
When the report passes and is written to `system/data/host-bridge-parity-report.json`
(or `ATRIUM_HOST_BRIDGE_PARITY_REPORT_PATH`), the browser/desktop connectors
can show `cross_os_verified`. A currently blocked local HostBridge runtime still
overrides an older verified report. Reports older than
`ATRIUM_HOST_BRIDGE_PARITY_REPORT_MAX_AGE_HOURS` (24 hours by default) are
treated as stale and must be regenerated. The connector catalog also re-checks
each OS result's probe stamp and source provenance before showing
`cross_os_verified`. Persisted reports include each raw probe artifact's
SHA-256, byte size, host identity fingerprint, and compact browser/desktop
proof facets inside the `proofId` digest so the exact inputs to the parity
decision can be audited and referenced from the connector UI. The backend
rejects persisted verified reports that are missing required proof facets or
host identity fields even when artifact metadata is present. Persisted proof
reports are schema-versioned and must contain only the required `macos` and
`windows` result labels.

## Backups

The backup script writes local dumps under `system/data/backups` by default:

```bash
ops/scripts/backup_postgres.sh
```

Do not commit `system/.env`, logs, database files, or backup dumps.
