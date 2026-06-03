import unittest

from app.chat_streaming import provider_exception_detail


class ProviderExceptionDetailTest(unittest.TestCase):
    def test_includes_runtime_error_message(self) -> None:
        error_type, detail = provider_exception_detail(
            RuntimeError("OpenAI Platform Responses provider returned empty output")
        )

        self.assertEqual(error_type, "RuntimeError")
        self.assertEqual(detail, "OpenAI Platform Responses provider returned empty output")

    def test_redacts_common_secret_shapes(self) -> None:
        _, detail = provider_exception_detail(
            RuntimeError(
                "failed Authorization: Bearer sk-live-secret token=abc123 password: hunter2"
            )
        )

        self.assertNotIn("sk-live-secret", detail)
        self.assertNotIn("abc123", detail)
        self.assertNotIn("hunter2", detail)
        self.assertIn("Bearer [redacted]", detail)
        self.assertIn("token=[redacted]", detail)
        self.assertIn("password: [redacted]", detail)

    def test_truncates_long_details(self) -> None:
        _, detail = provider_exception_detail(RuntimeError("x" * 20), limit=8)

        self.assertEqual(detail, "xxxxxxxx...")


if __name__ == "__main__":
    unittest.main()
