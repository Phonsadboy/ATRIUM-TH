# ATRIUM Operations

This folder is intentionally minimal. It contains only the files needed for
local installation, macOS startup, Windows WSL setup, account OAuth, and
database maintenance.

## Included

- `chatgpt_account_oauth_login.py` - local ChatGPT account OAuth login helper
- `macos_host_bridge_probe.py` - macOS HostBridge parity probe for shell,
  browser, desktop, notification, and Calculator Accessibility checks
- `windows_host_bridge_probe.py` - Windows HostBridge parity probe for shell,
  browser, desktop, notification, and interactive desktop checks
- `windows_wsl_install.ps1` - Windows-side shortcut that prepares WSL/Ubuntu,
  Docker Desktop integration, dependencies, clone, bootstrap, and local start;
  the root `atrium-windows.ps1` wrapper is the user-facing entrypoint
- `windows_host_bridge_live_proof.ps1` - Windows-side runner that validates
  source fingerprint, runs the full live Windows probe, and validates the
  resulting artifact before handoff
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
uv --project system run python ops/windows_host_bridge_probe.py --full
```

## HostBridge parity proof gate

Simulation proves branch coverage only. Do not treat it as desktop automation
parity. To claim macOS/Windows parity, collect full live JSON artifacts on both
hosts and verify them together:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(uuidgen | tr '[:upper:]' '[:lower:]')"
SOURCE_FINGERPRINT="$(uv --project system run python - <<'PY'
from app.host_bridge_proof import host_bridge_source_provenance
print(host_bridge_source_provenance()["sourceFingerprint"])
PY
)"
printf 'HostBridge parity run ID: %s\n' "$RUN_ID"
printf 'HostBridge source fingerprint: %s\n' "$SOURCE_FINGERPRINT"
uv --project system run python ops/macos_host_bridge_probe.py \
  --full \
  --parity-run-id "$RUN_ID" \
  --expect-source-fingerprint "$SOURCE_FINGERPRINT" \
  --output /tmp/atrium_host_bridge_macos_live.json
```

```powershell
$RunId = "<paste HostBridge parity run ID from macOS>"
$SourceFingerprint = "<paste HostBridge source fingerprint from macOS>"
.\ops\windows_host_bridge_live_proof.ps1 `
  -ParityRunId $RunId `
  -SourceFingerprint $SourceFingerprint `
  -Output C:\Temp\atrium_host_bridge_windows_live.json
```

The runner is the preferred Windows handoff command because it fails fast if
the Windows checkout has the wrong HostBridge source fingerprint, then runs the
full live probe and validates the artifact before you copy it back to the repo
host. If you need to run the steps manually, use:

```powershell
uv --project system run python ops/windows_host_bridge_probe.py `
  --full `
  --parity-run-id $RunId `
  --expect-source-fingerprint $SourceFingerprint `
  --output C:\Temp\atrium_host_bridge_windows_live.json
```

Use the same run ID value on both hosts. The verifier rejects artifacts that are
missing `parityRunId` or have different values, even when both artifacts are
otherwise fresh.

Before running each full probe, verify that both hosts are on the same
HostBridge source fingerprint. This catches stale Windows checkouts and missing
dirty changes before a long desktop probe:

```bash
uv --project system run python ops/host_bridge_source_summary.py \
  --expect-source-fingerprint "$(uv --project system run python - <<'PY'
from app.host_bridge_proof import host_bridge_source_provenance
print(host_bridge_source_provenance()["sourceFingerprint"])
PY
)"
```

Copy the Windows JSON artifact from `C:\Temp\atrium_host_bridge_windows_live.json`
to a local path on the repo host, for example
`/tmp/atrium_host_bridge_windows_live.json`. Then run the cross-OS verifier from
the repo root:

```bash
uv --project system run python ops/host_bridge_artifact_summary.py \
  --label macos \
  --expect-parity-run-id "$RUN_ID" \
  --expect-source-fingerprint "$(uv --project system run python - <<'PY'
from app.host_bridge_proof import host_bridge_source_provenance
print(host_bridge_source_provenance()["sourceFingerprint"])
PY
)" \
  /tmp/atrium_host_bridge_macos_live.json

uv --project system run python ops/host_bridge_artifact_summary.py \
  --label windows \
  --expect-parity-run-id "$RUN_ID" \
  --expect-source-fingerprint "$(uv --project system run python - <<'PY'
from app.host_bridge_proof import host_bridge_source_provenance
print(host_bridge_source_provenance()["sourceFingerprint"])
PY
)" \
  /tmp/atrium_host_bridge_windows_live.json

uv --project system run python ops/host_bridge_parity_report.py \
  --macos /tmp/atrium_host_bridge_macos_live.json \
  --windows /tmp/atrium_host_bridge_windows_live.json \
  --windows-source-path 'C:\Temp\atrium_host_bridge_windows_live.json' \
  --output data/host-bridge-parity-report.json
```

If the Windows probe wrote to a different Windows-host path, pass
`--windows-source-path` so missing-artifact findings and the persisted report
show the correct copy source. The `host_bridge_artifact_summary.py` checks are
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
old, or was generated from a different HostBridge source fingerprint than the
other OS artifact, or no longer matches the current checkout. Regenerate both
full probe artifacts after any HostBridge source change. For deliberate
historical audits only, the verifier accepts `--skip-current-source-check`; do
not use that flag for connector proof reports.
When the report passes and is written to `data/host-bridge-parity-report.json`
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
