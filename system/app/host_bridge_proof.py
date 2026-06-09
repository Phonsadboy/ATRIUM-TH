"""HostBridge proof source provenance helpers."""
from __future__ import annotations

import hashlib
import json
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any


SOURCE_FINGERPRINT_FILES = (
    "atrium",
    "atrium.cmd",
    "atrium.ps1",
    "ops/atrium_cli.py",
    "ops/install_windows_native.ps1",
    "ops/macos_host_bridge_probe.py",
    "ops/windows_host_bridge_probe.py",
    "ops/host_bridge_artifact_summary.py",
    "ops/host_bridge_parity_report.py",
    "ops/host_bridge_source_summary.py",
    "ops/windows_host_bridge_live_proof.ps1",
    "system/app/chat_tools.py",
    "system/app/db/repo.py",
    "system/app/host_bridge_proof.py",
    "system/app/main.py",
    "system/app/schema.py",
    "system/app/tools/host_bridge.py",
    "system/app/tools/visual_bridge.py",
    "ui/src/contract/types.ts",
    "ui/src/panels/console/ConnectorsPanel.tsx",
    "ui/src/panels/console/ToolsPanel.tsx",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_output(root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def host_bridge_source_provenance(root: Path | None = None) -> dict[str, Any]:
    resolved_root = root or repo_root()
    combined = hashlib.sha256()
    files: dict[str, Any] = {}
    for rel_path in SOURCE_FINGERPRINT_FILES:
        path = resolved_root / rel_path
        combined.update(rel_path.encode("utf-8") + b"\0")
        try:
            data = path.read_bytes()
        except OSError as exc:
            files[rel_path] = {"present": False, "error": f"{type(exc).__name__}: {exc}"}
            combined.update(b"missing\0")
            continue
        digest = hashlib.sha256(data).hexdigest()
        files[rel_path] = {"present": True, "sha256": digest, "bytes": len(data)}
        combined.update(digest.encode("ascii") + b"\0")
    source_manifest_sha256 = combined.hexdigest()
    status_short = _git_output(resolved_root, ["status", "--short"]) or ""
    return {
        "repoRoot": str(resolved_root),
        "gitHead": _git_output(resolved_root, ["rev-parse", "HEAD"]),
        "gitDirty": bool(status_short),
        "gitStatusShort": status_short.splitlines()[:80],
        "sourceFingerprint": source_manifest_sha256,
        "sourceManifestSha256": source_manifest_sha256,
        "sourceFileCount": len(files),
        "files": files,
    }


def host_bridge_host_identity() -> dict[str, Any]:
    identity = {
        "schemaVersion": 1,
        "platform": sys.platform,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
    }
    fingerprint_payload = {
        key: identity.get(key)
        for key in ("platform", "system", "release", "machine", "hostname")
    }
    encoded = json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    identity["hostFingerprint"] = hashlib.sha256(encoded).hexdigest()
    return identity


def host_bridge_parity_proof_id(
    results: dict[str, Any],
    current_source: dict[str, Any] | None,
    *,
    enforce_current_source: bool,
) -> str:
    payload = {
        "schemaVersion": 1,
        "currentSource": {
            "enforced": enforce_current_source,
            "sourceFingerprint": current_source.get("sourceFingerprint") if isinstance(current_source, dict) else None,
            "sourceManifestSha256": current_source.get("sourceManifestSha256") if isinstance(current_source, dict) else None,
            "sourceFileCount": current_source.get("sourceFileCount") if isinstance(current_source, dict) else None,
            "gitHead": current_source.get("gitHead") if isinstance(current_source, dict) else None,
        },
        "results": {
            label: {
                "present": (result if isinstance(result, dict) else {}).get("present"),
                "proofSchemaVersion": (result if isinstance(result, dict) else {}).get("proofSchemaVersion"),
                "artifactSha256": (result if isinstance(result, dict) else {}).get("artifactSha256"),
                "artifactBytes": (result if isinstance(result, dict) else {}).get("artifactBytes"),
                "generatedAt": (result if isinstance(result, dict) else {}).get("generatedAt"),
                "sourceFingerprint": (result if isinstance(result, dict) else {}).get("sourceFingerprint"),
                "sourceManifestSha256": (result if isinstance(result, dict) else {}).get("sourceManifestSha256"),
                "sourceFileCount": (result if isinstance(result, dict) else {}).get("sourceFileCount"),
                "gitHead": (result if isinstance(result, dict) else {}).get("gitHead"),
                "parityRunId": (result if isinstance(result, dict) else {}).get("parityRunId"),
                "mode": (result if isinstance(result, dict) else {}).get("mode"),
                "platform": (result if isinstance(result, dict) else {}).get("platform"),
                "hostFingerprint": (result if isinstance(result, dict) else {}).get("hostFingerprint"),
                "hostPlatform": (result if isinstance(result, dict) else {}).get("hostPlatform"),
                "probeOk": (result if isinstance(result, dict) else {}).get("probeOk"),
                "desktopAutomationReady": (result if isinstance(result, dict) else {}).get("desktopAutomationReady"),
                "proofs": (result if isinstance(result, dict) and isinstance(result.get("proofs"), dict) else {}).get("proofs"),
            }
            for label, result in sorted(results.items())
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
