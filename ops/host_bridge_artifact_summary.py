#!/usr/bin/env python3
"""Validate and summarize one HostBridge probe artifact before parity verify."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

OPS = Path(__file__).resolve().parent
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

from host_bridge_parity_report import _proof_summary  # noqa: E402


PROBE_SCHEMA_VERSION = 1
OS_LABELS = {
    "macos": "darwin",
    "windows": "win32",
}
REQUIRED_PROOF_FACETS = {
    "macos": (
        "browserOpen",
        "browserOpenIsolatedProfile",
        "browserSnapshot",
        "browserSnapshotIsolatedPlaywright",
        "browserAct",
        "browserActIsolatedPlaywright",
        "browserActVerified",
        "appsDiscovery",
        "screenshotFile",
        "notification",
        "desktopAutomationReady",
        "foregroundSession",
        "appleScriptClipboard",
        "foregroundSnapshotNative",
        "appsNativeNSWorkspace",
        "macosNativeActionMetadata",
        "calculatorNativeAct",
        "textEditNativeAct",
        "textEditNativeScroll",
    ),
    "windows": (
        "browserOpen",
        "browserOpenIsolatedProfile",
        "browserSnapshot",
        "browserSnapshotIsolatedPlaywright",
        "browserAct",
        "browserActIsolatedPlaywright",
        "browserActVerified",
        "appsDiscovery",
        "screenshotFile",
        "notification",
        "desktopAutomationReady",
        "interactiveSession",
        "windowsInteractiveSessionIdentity",
        "windowsVisualPreflight",
        "helperSelftest",
        "powershellPreflight",
        "windowsDpiAwareness",
        "windowsVirtualScreen",
        "windowsForegroundActivation",
        "windowsUnicodeTyping",
        "windowsKeyboardShortcut",
        "notepadNativeAct",
        "clipboardRoundTrip",
    ),
}


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "artifact file is missing"
    except json.JSONDecodeError as exc:
        return None, f"artifact JSON is invalid: {exc}"
    except OSError as exc:
        return None, f"artifact could not be read: {type(exc).__name__}: {exc}"
    if not isinstance(loaded, dict):
        return None, "artifact root must be a JSON object"
    return loaded, None


def _nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def summarize_artifact(
    path: Path,
    *,
    label: str,
    expect_parity_run_id: str | None,
    expect_source_fingerprint: str | None,
    max_artifact_age_hours: float,
) -> dict[str, Any]:
    data, error = _load_json(path)
    findings: list[str] = []
    expected_platform = OS_LABELS[label]
    if error:
        findings.append(error)
        return {
            "ok": False,
            "path": str(path),
            "label": label,
            "findings": findings,
        }
    assert data is not None
    generated_at = data.get("generatedAt")
    source_fingerprint = _nested(data, "source", "sourceFingerprint")
    parity_run_id = data.get("parityRunId")
    host = data.get("host") if isinstance(data.get("host"), dict) else {}
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    proofs = _proof_summary(label, data)

    if data.get("schemaVersion") != PROBE_SCHEMA_VERSION:
        findings.append(f"schemaVersion must be {PROBE_SCHEMA_VERSION}, got {data.get('schemaVersion')!r}")
    if data.get("mode") != "live":
        findings.append(f"mode must be live, got {data.get('mode')!r}")
    if data.get("ok") is not True:
        findings.append("probe ok must be true")
    if not isinstance(generated_at, int):
        findings.append("generatedAt is missing or invalid")
    else:
        age_ms = int(time.time() * 1000) - generated_at
        if generated_at > int(time.time() * 1000) + 5 * 60 * 1000:
            findings.append("generatedAt is in the future")
        if max_artifact_age_hours > 0 and age_ms > int(max_artifact_age_hours * 60 * 60 * 1000):
            findings.append(
                f"artifact is stale; ageHours={age_ms / 3600000:.1f}; "
                f"maxAgeHours={max_artifact_age_hours:.1f}"
            )
    if not isinstance(parity_run_id, str) or not parity_run_id.strip():
        findings.append("parityRunId is missing or invalid")
    elif expect_parity_run_id and parity_run_id != expect_parity_run_id:
        findings.append(f"parityRunId mismatch: artifact={parity_run_id}; expected={expect_parity_run_id}")
    if not _hex64(source_fingerprint):
        findings.append("sourceFingerprint is missing or invalid")
    elif expect_source_fingerprint and source_fingerprint != expect_source_fingerprint:
        findings.append(
            "sourceFingerprint mismatch: "
            f"artifact={source_fingerprint}; expected={expect_source_fingerprint}"
        )
    if host.get("platform") != expected_platform:
        findings.append(f"host.platform must be {expected_platform!r}, got {host.get('platform')!r}")
    if status.get("platform") != expected_platform:
        findings.append(f"status.platform must be {expected_platform!r}, got {status.get('platform')!r}")
    if not _hex64(host.get("hostFingerprint")):
        findings.append("hostFingerprint is missing or invalid")
    missing_proofs = [facet for facet in REQUIRED_PROOF_FACETS[label] if proofs.get(facet) is not True]
    for facet in missing_proofs:
        findings.append(f"required proof facet {facet} is missing or false")

    return {
        "ok": not findings,
        "path": str(path),
        "label": label,
        "findings": findings,
        "schemaVersion": data.get("schemaVersion"),
        "mode": data.get("mode"),
        "probeOk": data.get("ok"),
        "generatedAt": generated_at,
        "parityRunId": parity_run_id,
        "sourceFingerprint": source_fingerprint,
        "gitHead": _nested(data, "source", "gitHead"),
        "hostPlatform": host.get("platform"),
        "hostName": host.get("hostname"),
        "hostFingerprint": host.get("hostFingerprint"),
        "statusPlatform": status.get("platform"),
        "desktopAutomationReady": status.get("desktopAutomationReady"),
        "browserBridge": status.get("browserBridge"),
        "desktopBridge": status.get("desktopBridge"),
        "proofs": proofs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--label", choices=sorted(OS_LABELS), required=True)
    parser.add_argument("--expect-parity-run-id")
    parser.add_argument("--expect-source-fingerprint")
    parser.add_argument("--max-artifact-age-hours", type=float, default=24.0)
    args = parser.parse_args()

    result = summarize_artifact(
        args.artifact,
        label=args.label,
        expect_parity_run_id=args.expect_parity_run_id,
        expect_source_fingerprint=args.expect_source_fingerprint,
        max_artifact_age_hours=args.max_artifact_age_hours,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
