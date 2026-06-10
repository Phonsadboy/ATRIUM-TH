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

from host_bridge_parity_report import _live_proof_failure_details, _proof_summary  # noqa: E402

SYSTEM = OPS.parent / "system"
if str(SYSTEM) not in sys.path:
    sys.path.insert(0, str(SYSTEM))

from app.host_bridge_proof import SOURCE_FINGERPRINT_FILES  # noqa: E402


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
        "mcpExternalWriteReady",
        "windowsLiveProofRunner",
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
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _hex40(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(ch in "0123456789abcdef" for ch in value)


def _append_basic_artifact_metadata_findings(
    data: dict[str, Any],
    findings: list[str],
    *,
    expect_parity_run_id: str | None,
    max_artifact_age_hours: float,
) -> None:
    if data.get("schemaVersion") != PROBE_SCHEMA_VERSION:
        findings.append(f"schemaVersion must be {PROBE_SCHEMA_VERSION}, got {data.get('schemaVersion')!r}")
    generated_at = data.get("generatedAt")
    now_ms = int(time.time() * 1000)
    if not isinstance(generated_at, int):
        findings.append("generatedAt is missing or invalid")
    else:
        age_ms = now_ms - generated_at
        if generated_at > now_ms + 5 * 60 * 1000:
            findings.append("generatedAt is in the future")
        if max_artifact_age_hours > 0 and age_ms > int(max_artifact_age_hours * 60 * 60 * 1000):
            findings.append(
                f"artifact is stale; ageHours={age_ms / 3600000:.1f}; "
                f"maxAgeHours={max_artifact_age_hours:.1f}"
            )
    parity_run_id = data.get("parityRunId")
    if not isinstance(parity_run_id, str) or not parity_run_id.strip():
        findings.append("parityRunId is missing or invalid")
    elif expect_parity_run_id and parity_run_id != expect_parity_run_id:
        findings.append(f"parityRunId mismatch: artifact={parity_run_id}; expected={expect_parity_run_id}")


def summarize_artifact(
    path: Path,
    *,
    label: str,
    expect_parity_run_id: str | None,
    expect_source_fingerprint: str | None,
    expect_source_manifest_sha256: str | None,
    expect_source_file_count: int | None,
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
    expected_source_fingerprint = str(expect_source_fingerprint or "").strip()
    expected_source_manifest_sha256 = str(expect_source_manifest_sha256 or "").strip()
    generated_at = data.get("generatedAt")
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    source_fingerprint = source.get("sourceFingerprint")
    source_manifest_sha256 = source.get("sourceManifestSha256")
    git_head = source.get("gitHead")
    parity_run_id = data.get("parityRunId")
    host = data.get("host") if isinstance(data.get("host"), dict) else {}
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    proofs = _proof_summary(label, data)
    required_proof_facets = list(REQUIRED_PROOF_FACETS[label])
    missing_proofs = [facet for facet in required_proof_facets if proofs.get(facet) is not True]
    failure_details = _live_proof_failure_details(label, data)
    if failure_details is not None:
        findings.append(str(failure_details["finding"]))
        _append_basic_artifact_metadata_findings(
            data,
            findings,
            expect_parity_run_id=expect_parity_run_id,
            max_artifact_age_hours=max_artifact_age_hours,
        )
        failure_source_fingerprint = failure_details.get("sourceFingerprint")
        failure_source_manifest_sha256 = failure_details.get("sourceManifestSha256")
        failure_source_file_count = failure_details.get("sourceFileCount")
        if expected_source_fingerprint and failure_source_fingerprint != expected_source_fingerprint:
            findings.append(
                "sourceFingerprint mismatch: "
                f"artifact={failure_source_fingerprint}; expected={expected_source_fingerprint}"
            )
        if expected_source_manifest_sha256 and failure_source_manifest_sha256 != expected_source_manifest_sha256:
            findings.append(
                "sourceManifestSha256 mismatch: "
                f"artifact={failure_source_manifest_sha256}; expected={expected_source_manifest_sha256}"
            )
        if expect_source_file_count is not None and failure_source_file_count != expect_source_file_count:
            findings.append(
                "sourceFileCount mismatch: "
                f"artifact={failure_source_file_count}; expected={expect_source_file_count}"
            )
        return {
            "ok": False,
            "path": str(path),
            "label": label,
            "findings": findings,
            "schemaVersion": data.get("schemaVersion"),
            "mode": data.get("mode"),
            "probeOk": data.get("ok"),
            "generatedAt": generated_at,
            "parityRunId": parity_run_id,
            "sourceFingerprint": failure_source_fingerprint,
            "sourceManifestSha256": failure_source_manifest_sha256,
            "sourceFileCount": failure_source_file_count,
            "failureError": failure_details.get("error"),
            "failurePreflight": failure_details.get("preflight"),
            "failureNextSteps": failure_details.get("nextSteps"),
            "proofs": proofs,
            "requiredProofFacets": required_proof_facets,
            "missingProofFacets": missing_proofs,
            "proofFacetCount": len(required_proof_facets),
            "missingProofFacetCount": len(missing_proofs),
            "failureStage": failure_details.get("failedStage"),
            "failurePartialArtifact": failure_details.get("partialArtifact"),
        }

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
    elif expected_source_fingerprint and source_fingerprint != expected_source_fingerprint:
        findings.append(
            "sourceFingerprint mismatch: "
            f"artifact={source_fingerprint}; expected={expected_source_fingerprint}"
        )
    manifest_valid = _hex64(source_manifest_sha256)
    if not manifest_valid:
        findings.append("sourceManifestSha256 is missing or invalid")
    if manifest_valid and expected_source_manifest_sha256 and source_manifest_sha256 != expected_source_manifest_sha256:
        findings.append(
            "sourceManifestSha256 mismatch: "
            f"artifact={source_manifest_sha256}; expected={expected_source_manifest_sha256}"
        )
    if manifest_valid and _hex64(source_fingerprint) and source_manifest_sha256 != source_fingerprint:
        findings.append("sourceManifestSha256 must match sourceFingerprint")
    if not _hex40(git_head):
        findings.append("gitHead is missing or invalid")
    source_files = source.get("files")
    source_file_count = source.get("sourceFileCount")
    if not isinstance(source_file_count, int) or isinstance(source_file_count, bool) or source_file_count <= 0:
        findings.append("sourceFileCount is missing or invalid")
    if not isinstance(source_files, dict):
        findings.append("source file provenance is missing")
    else:
        if isinstance(source_file_count, int) and not isinstance(source_file_count, bool) and source_file_count != len(source_files):
            findings.append(
                "sourceFileCount must match source file provenance: "
                f"sourceFileCount={source_file_count}; files={len(source_files)}"
            )
        if expect_source_file_count is not None and len(source_files) != expect_source_file_count:
            findings.append(
                "sourceFileCount mismatch: "
                f"artifact={len(source_files)}; expected={expect_source_file_count}"
            )
        missing_source_files = [path for path in SOURCE_FINGERPRINT_FILES if not isinstance(source_files.get(path), dict)]
        if missing_source_files:
            findings.append(f"source file provenance is incomplete; missing={', '.join(missing_source_files[:6])}")
        not_present = [
            path
            for path in SOURCE_FINGERPRINT_FILES
            if isinstance(source_files.get(path), dict) and source_files[path].get("present") is not True
        ]
        if not_present:
            findings.append(f"source file provenance has missing files; missing={', '.join(not_present[:6])}")
    if host.get("platform") != expected_platform:
        findings.append(f"host.platform must be {expected_platform!r}, got {host.get('platform')!r}")
    if status.get("platform") != expected_platform:
        findings.append(f"status.platform must be {expected_platform!r}, got {status.get('platform')!r}")
    if not _hex64(host.get("hostFingerprint")):
        findings.append("hostFingerprint is missing or invalid")
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
        "sourceManifestSha256": source_manifest_sha256,
        "sourceFileCount": source_file_count,
        "sourceFileProvenanceCount": len(source_files) if isinstance(source_files, dict) else 0,
        "gitHead": git_head,
        "hostPlatform": host.get("platform"),
        "hostName": host.get("hostname"),
        "hostFingerprint": host.get("hostFingerprint"),
        "statusPlatform": status.get("platform"),
        "desktopAutomationReady": status.get("desktopAutomationReady"),
        "browserBridge": status.get("browserBridge"),
        "desktopBridge": status.get("desktopBridge"),
        "proofs": proofs,
        "requiredProofFacets": required_proof_facets,
        "missingProofFacets": missing_proofs,
        "proofFacetCount": len(required_proof_facets),
        "missingProofFacetCount": len(missing_proofs),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--label", choices=sorted(OS_LABELS), required=True)
    parser.add_argument("--expect-parity-run-id")
    parser.add_argument("--expect-source-fingerprint")
    parser.add_argument("--expect-source-manifest-sha256")
    parser.add_argument("--expect-source-file-count", type=int)
    parser.add_argument("--max-artifact-age-hours", type=float, default=24.0)
    args = parser.parse_args()

    result = summarize_artifact(
        args.artifact,
        label=args.label,
        expect_parity_run_id=args.expect_parity_run_id,
        expect_source_fingerprint=args.expect_source_fingerprint,
        expect_source_manifest_sha256=args.expect_source_manifest_sha256,
        expect_source_file_count=args.expect_source_file_count,
        max_artifact_age_hours=args.max_artifact_age_hours,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
