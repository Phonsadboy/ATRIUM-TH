import unittest
from pathlib import Path

from app.host_bridge_proof import SOURCE_FINGERPRINT_FILES, host_bridge_host_identity, host_bridge_parity_proof_id, host_bridge_source_provenance


REPO_ROOT = Path(__file__).resolve().parents[2]


class HostBridgeProofTest(unittest.TestCase):
    def test_source_provenance_covers_shared_proof_helper(self) -> None:
        provenance = host_bridge_source_provenance(REPO_ROOT)

        self.assertRegex(provenance["sourceFingerprint"], r"^[0-9a-f]{64}$")
        self.assertIn("system/app/host_bridge_proof.py", SOURCE_FINGERPRINT_FILES)
        self.assertIn("ops/host_bridge_source_summary.py", SOURCE_FINGERPRINT_FILES)
        self.assertIn("ops/windows_host_bridge_live_proof.ps1", SOURCE_FINGERPRINT_FILES)
        self.assertIn("system/app/host_bridge_proof.py", provenance["files"])
        self.assertIn("ops/windows_host_bridge_live_proof.ps1", provenance["files"])
        self.assertTrue(provenance["files"]["system/app/host_bridge_proof.py"]["present"])
        self.assertTrue(provenance["files"]["ops/windows_host_bridge_live_proof.ps1"]["present"])
        self.assertIn("gitHead", provenance)
        self.assertIn("gitDirty", provenance)

    def test_host_identity_has_stable_fingerprint_shape(self) -> None:
        identity = host_bridge_host_identity()

        self.assertEqual(identity["schemaVersion"], 1)
        self.assertRegex(identity["hostFingerprint"], r"^[0-9a-f]{64}$")
        self.assertIn("platform", identity)
        self.assertTrue(str(identity.get("hostname") or "").strip())

    def test_parity_proof_id_includes_proof_schema_version(self) -> None:
        source = {"sourceFingerprint": "a" * 64, "gitHead": "b" * 40}
        result = {
            "present": True,
            "proofSchemaVersion": 1,
            "artifactSha256": "1" * 64,
            "artifactBytes": 100,
            "generatedAt": 1234,
            "sourceFingerprint": "a" * 64,
            "gitHead": "b" * 40,
            "parityRunId": "parity-run-1",
            "mode": "live",
            "platform": "darwin",
            "hostFingerprint": "c" * 64,
            "hostPlatform": "darwin",
            "probeOk": True,
            "desktopAutomationReady": True,
            "proofs": {"browserActVerified": True},
        }

        first = host_bridge_parity_proof_id({"macos": result}, source, enforce_current_source=True)
        changed = dict(result, proofSchemaVersion=2)
        second = host_bridge_parity_proof_id({"macos": changed}, source, enforce_current_source=True)

        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, second)

    def test_parity_proof_id_includes_parity_run_id(self) -> None:
        source = {"sourceFingerprint": "a" * 64, "gitHead": "b" * 40}
        result = {
            "present": True,
            "proofSchemaVersion": 1,
            "artifactSha256": "1" * 64,
            "artifactBytes": 100,
            "generatedAt": 1234,
            "sourceFingerprint": "a" * 64,
            "gitHead": "b" * 40,
            "parityRunId": "parity-run-1",
            "mode": "live",
            "platform": "darwin",
            "hostFingerprint": "c" * 64,
            "hostPlatform": "darwin",
            "probeOk": True,
            "desktopAutomationReady": True,
            "proofs": {"browserActVerified": True},
        }

        first = host_bridge_parity_proof_id({"macos": result}, source, enforce_current_source=True)
        changed = dict(result, parityRunId="parity-run-2")
        second = host_bridge_parity_proof_id({"macos": changed}, source, enforce_current_source=True)

        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, second)

    def test_parity_proof_id_includes_host_fingerprint(self) -> None:
        source = {"sourceFingerprint": "a" * 64, "gitHead": "b" * 40}
        result = {
            "present": True,
            "proofSchemaVersion": 1,
            "artifactSha256": "1" * 64,
            "artifactBytes": 100,
            "generatedAt": 1234,
            "sourceFingerprint": "a" * 64,
            "gitHead": "b" * 40,
            "parityRunId": "parity-run-1",
            "mode": "live",
            "platform": "darwin",
            "hostFingerprint": "c" * 64,
            "hostPlatform": "darwin",
            "probeOk": True,
            "desktopAutomationReady": True,
            "proofs": {"browserActVerified": True},
        }

        first = host_bridge_parity_proof_id({"macos": result}, source, enforce_current_source=True)
        changed = dict(result, hostFingerprint="d" * 64)
        second = host_bridge_parity_proof_id({"macos": changed}, source, enforce_current_source=True)

        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
