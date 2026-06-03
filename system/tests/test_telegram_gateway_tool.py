import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import chat_tools


class FakeRepo:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}
        self.activities: list[dict] = []

    async def get_entity(self, etype: str, eid: str) -> dict | None:
        return self.entities.get((etype, eid))

    async def put_entity(
        self,
        etype: str,
        obj: dict,
        *,
        dept: str | None = None,
        project: str | None = None,
        status: str | None = None,
        ts: int | None = None,
    ) -> dict:
        del dept, project, status, ts
        self.entities[(etype, obj["id"])] = obj
        return obj

    async def add_activity(self, ev: dict) -> None:
        self.activities.append(ev)


class TelegramGatewayToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_telegram_gateway_redacts_public_payload(self) -> None:
        token = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"

        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                data_dir=Path(tmp),
                telegram_gateway_url="",
                telegram_gateway_token="",
                telegram_gateway_public_base_url="",
                telegram_gateway_timeout_s=1.0,
            )
            repo = FakeRepo()

            with (
                mock.patch.object(chat_tools, "get_settings", return_value=settings),
                mock.patch.object(
                    chat_tools,
                    "_telegram_api_request",
                    return_value={
                        "ok": True,
                        "result": {
                            "id": 42,
                            "username": "atrium_test_bot",
                            "first_name": "ATRIUM",
                            "is_bot": True,
                        },
                    },
                ),
            ):
                result = await chat_tools._connect_telegram_gateway_tool(
                    repo,
                    {"botToken": token},
                    {"id": "exec", "agentName": "Executive"},
                    "executive",
                    "exec",
                )

            public_json = json.dumps(result, sort_keys=True)
            entities_json = json.dumps(list(repo.entities.values()), sort_keys=True)
            self.assertTrue(result["ok"])
            self.assertNotIn(token, public_json)
            self.assertNotIn(token, entities_json)
            self.assertEqual(result["gateway"]["bot"]["username"], "atrium_test_bot")
            auth_file = Path(tmp) / "auth" / "telegram-gateway.json"
            self.assertIn(token, auth_file.read_text(encoding="utf-8"))

    def test_tool_record_args_redacts_token(self) -> None:
        token = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"

        recorded = chat_tools._chat_tool_record_args(
            "connect_telegram_gateway",
            {"botToken": token, "note": f"connect {token}"},
        )

        encoded = json.dumps(recorded, sort_keys=True)
        self.assertNotIn(token, encoded)
        self.assertIn("[redacted-telegram-bot-token]", encoded)

    def test_likely_needs_tools_detects_telegram_token(self) -> None:
        self.assertTrue(
            chat_tools.likely_needs_chat_tools("123456789:abcdefghijklmnopqrstuvwxyzABCDE")
        )


if __name__ == "__main__":
    unittest.main()
