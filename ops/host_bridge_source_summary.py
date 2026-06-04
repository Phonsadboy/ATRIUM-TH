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
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def summarize_source(*, expect_source_fingerprint: str | None = None, root: Path | None = None) -> dict[str, Any]:
    source = host_bridge_source_provenance(root or ROOT)
    fingerprint = source.get("sourceFingerprint")
    findings: list[str] = []
    if not _hex64(fingerprint):
        findings.append("sourceFingerprint is missing or invalid")
    elif expect_source_fingerprint and fingerprint != expect_source_fingerprint:
        findings.append(
            "sourceFingerprint mismatch: "
            f"current={fingerprint}; expected={expect_source_fingerprint}"
        )
    return {
        "ok": not findings,
        "findings": findings,
        "repoRoot": source.get("repoRoot"),
        "sourceFingerprint": fingerprint,
        "gitHead": source.get("gitHead"),
        "gitDirty": source.get("gitDirty"),
        "trackedFileCount": len(source.get("files") or {}),
        "gitStatusShort": source.get("gitStatusShort") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-source-fingerprint")
    args = parser.parse_args()

    result = summarize_source(expect_source_fingerprint=args.expect_source_fingerprint)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
