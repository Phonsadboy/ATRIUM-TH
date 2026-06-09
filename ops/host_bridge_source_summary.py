#!/usr/bin/env python3
"""Validate the current HostBridge source fingerprint before probe handoff."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system"
sys.path.insert(0, str(SYSTEM))

from app.host_bridge_proof import host_bridge_source_provenance  # noqa: E402


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def summarize_source(
    *,
    expect_source_fingerprint: str | None = None,
    expect_source_manifest_sha256: str | None = None,
    expect_source_file_count: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    source = host_bridge_source_provenance(root or ROOT)
    fingerprint = source.get("sourceFingerprint")
    manifest_sha = source.get("sourceManifestSha256")
    file_count = source.get("sourceFileCount")
    expected_fingerprint = str(expect_source_fingerprint or "").strip()
    expected_manifest_sha = str(expect_source_manifest_sha256 or "").strip()
    findings: list[str] = []
    if not _hex64(fingerprint):
        findings.append("sourceFingerprint is missing or invalid")
    elif expected_fingerprint and fingerprint != expected_fingerprint:
        findings.append(
            "sourceFingerprint mismatch: "
            f"current={fingerprint}; expected={expected_fingerprint}"
        )
    if not _hex64(manifest_sha):
        findings.append("sourceManifestSha256 is missing or invalid")
    elif expected_manifest_sha and manifest_sha != expected_manifest_sha:
        findings.append(
            "sourceManifestSha256 mismatch: "
            f"current={manifest_sha}; expected={expected_manifest_sha}"
        )
    if not isinstance(file_count, int) or file_count <= 0:
        findings.append("sourceFileCount is missing or invalid")
    elif expect_source_file_count is not None and file_count != expect_source_file_count:
        findings.append(
            "sourceFileCount mismatch: "
            f"current={file_count}; expected={expect_source_file_count}"
        )
    return {
        "ok": not findings,
        "findings": findings,
        "repoRoot": source.get("repoRoot"),
        "sourceFingerprint": fingerprint,
        "sourceManifestSha256": source.get("sourceManifestSha256"),
        "sourceFileCount": source.get("sourceFileCount"),
        "gitHead": source.get("gitHead"),
        "gitDirty": source.get("gitDirty"),
        "trackedFileCount": len(source.get("files") or {}),
        "gitStatusShort": source.get("gitStatusShort") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-source-fingerprint")
    parser.add_argument("--expect-source-manifest-sha256")
    parser.add_argument("--expect-source-file-count", type=int)
    args = parser.parse_args()

    result = summarize_source(
        expect_source_fingerprint=args.expect_source_fingerprint,
        expect_source_manifest_sha256=args.expect_source_manifest_sha256,
        expect_source_file_count=args.expect_source_file_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
