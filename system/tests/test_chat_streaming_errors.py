import asyncio
import unittest

from app.chat_streaming import ChatMessageStreamSink, provider_exception_detail
from app.provider.base import LLMResult


class _FakeSession:
    async def commit(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _SinkRepo:
    def __init__(self) -> None:
        self.s = _FakeSession()
        self.messages = {
            "msg_reply": {
                "id": "msg_reply",
                "threadId": "executive",
                "role": "executive",
                "text": "",
                "pending": True,
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                    "progressMessageId": "100",
                    "progressUpdatedAt": 10,
                },
            }
        }

    async def get_message(self, msg_id: str, *, thread_id: str | None = None) -> dict | None:
        msg = self.messages.get(msg_id)
        if msg and thread_id is not None and msg.get("threadId") != thread_id:
            return None
        return dict(msg) if msg else None

    async def update_message(self, msg: dict) -> None:
        self.messages[msg["id"]] = dict(msg)


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


class ChatMessageStreamSinkTest(unittest.IsolatedAsyncioTestCase):
    async def test_finish_preserves_latest_telegram_progress_message_id(self) -> None:
        repo = _SinkRepo()
        sink = ChatMessageStreamSink(
            thread_id="executive",
            msg_id="msg_reply",
            message={
                "id": "msg_reply",
                "threadId": "executive",
                "role": "executive",
                "text": "",
                "pending": True,
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                },
            },
            cancel_event=asyncio.Event(),
            repo=repo,
        )

        await sink.finish(result=LLMResult(text="คำตอบสุดท้าย", tokens_in=0, tokens_out=1))

        channel_reply = repo.messages["msg_reply"]["channelReply"]
        self.assertEqual(channel_reply["progressMessageId"], "100")
        self.assertFalse(repo.messages["msg_reply"]["pending"])


if __name__ == "__main__":
    unittest.main()
