import unittest

from app.config import Settings
from app.main import _api_exposure_status


class ApiExposureVisibilityTest(unittest.TestCase):
    def test_loopback_api_exposure_is_local_visibility_only(self) -> None:
        status = _api_exposure_status(Settings(host="127.0.0.1", port=8787))

        self.assertTrue(status["visibilityOnly"])
        self.assertTrue(status["bindsLoopback"])
        self.assertFalse(status["networkExposed"])
        self.assertEqual(status["risk"], "local")
        self.assertFalse(status["auth"]["required"])
        self.assertEqual(status["auth"]["enforcement"], "not_enforced")
        self.assertTrue(status["fullAutonomyPreserved"])

    def test_network_exposed_api_reports_warning_without_enforcing_auth(self) -> None:
        status = _api_exposure_status(Settings(host="0.0.0.0", port=8787, cors_origins="*"))

        self.assertTrue(status["visibilityOnly"])
        self.assertFalse(status["bindsLoopback"])
        self.assertTrue(status["networkExposed"])
        self.assertTrue(status["corsWildcard"])
        self.assertEqual(status["risk"], "elevated")
        self.assertIn("api-bound-to-non-loopback-host", status["warnings"])
        self.assertIn("cors-all-origins", status["warnings"])
        self.assertFalse(status["auth"]["required"])
        self.assertEqual(status["auth"]["mode"], "none")
        self.assertTrue(status["fullAutonomyPreserved"])


if __name__ == "__main__":
    unittest.main()
