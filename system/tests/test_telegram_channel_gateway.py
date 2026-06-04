import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import telegram_gateway


class FakeRepo:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}
        self.messages: dict[str, dict] = {}
        self.jobs: list[dict] = []
        self.activities: list[dict] = []
        self.updated_messages: list[dict] = []
        self.departments = {
            "exec": {
                "id": "exec",
                "name": "Executive",
                "role": "owner",
                "charter": "Run the company",
                "agentName": "Executive AI",
                "providerId": "claude_code",
                "model": "claude-sonnet-4-7",
                "thinkingEffort": "high",
                "speed": "standard",
            }
        }

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

    async def get_department(self, dept_id: str) -> dict | None:
        return self.departments.get(dept_id)

    async def list_departments(self) -> list[dict]:
        return list(self.departments.values())

    async def get_message(self, msg_id: str, *, thread_id: str | None = None) -> dict | None:
        msg = self.messages.get(msg_id)
        if msg and thread_id is not None and msg.get("threadId") != thread_id:
            return None
        return msg

    async def add_message(self, msg: dict) -> None:
        self.messages[msg["id"]] = msg

    async def update_message(self, msg: dict) -> None:
        self.messages[msg["id"]] = msg
        self.updated_messages.append(msg)

    async def enqueue(self, job_id: str, kind: str, payload: dict, run_after: int, priority: int = 5) -> None:
        self.jobs.append({
            "id": job_id,
            "kind": kind,
            "payload": payload,
            "runAfter": run_after,
            "priority": priority,
        })

    async def add_activity(self, ev: dict) -> None:
        self.activities.append(ev)


def _settings(tmp: str, **overrides):
    auth_overrides = {
        key: overrides.pop(key)
        for key in list(overrides)
        if key.startswith("telegram_")
    }
    values = {
        "data_dir": Path(tmp),
        "telegram_gateway_timeout_s": 1.0,
    }
    values.update(overrides)
    auth = {
        "botToken": "123:test-token",
        "dmPolicy": auth_overrides.get("telegram_dm_policy", "pairing"),
        "allowFrom": auth_overrides.get("telegram_allow_from", "1001"),
        "groupPolicy": auth_overrides.get("telegram_group_policy", "configured"),
        "groupAllowFrom": auth_overrides.get("telegram_group_allow_from", ""),
        "groupRequireMention": auth_overrides.get("telegram_group_require_mention", True),
        "groups": auth_overrides.get("telegram_groups_json", {}),
        "maxFileBytes": auth_overrides.get("telegram_max_file_bytes", 20_000_000),
        "outboundChunkChars": auth_overrides.get("telegram_outbound_chunk_chars", 3800),
        "outboundRetryAttempts": auth_overrides.get("telegram_outbound_retry_attempts", 3),
        "outboundRetryDelayS": auth_overrides.get("telegram_outbound_retry_delay_s", 1.0),
    }
    if isinstance(auth["groups"], str):
        import json

        auth["groups"] = json.loads(auth["groups"] or "{}")
    auth_dir = Path(tmp) / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    import json

    (auth_dir / "telegram-gateway.json").write_text(json.dumps(auth), encoding="utf-8")
    return SimpleNamespace(**values)


def _dm_update(text: str = "สรุปแผนวันนี้") -> dict:
    return {
        "update_id": 9001,
        "message": {
            "message_id": 42,
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 1001, "first_name": "Owner"},
            "text": text,
        },
    }


class TelegramChannelGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_dm_owner_maps_to_executive_thread_and_chat_reply_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp)

            result = await telegram_gateway.handle_telegram_update(repo, _dm_update(), settings=settings)

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["threadId"], "executive")
            self.assertEqual([job["kind"] for job in repo.jobs], ["chat_reply", "telegram_progress"])
            self.assertEqual(repo.jobs[0]["kind"], "chat_reply")
            self.assertEqual(repo.jobs[0]["payload"]["threadId"], "executive")
            self.assertEqual(repo.jobs[1]["payload"]["replyMessageId"], result["replyMessageId"])
            user_msg = repo.messages["telegram:555:42"]
            self.assertEqual(user_msg["channel"]["provider"], "telegram")
            self.assertEqual(user_msg["channel"]["direction"], "inbound")
            reply = repo.messages[result["replyMessageId"]]
            self.assertEqual(reply["channelReply"]["provider"], "telegram")
            self.assertEqual(reply["channelReply"]["chatId"], "555")

    async def test_unapproved_dm_creates_pairing_record_without_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp, telegram_allow_from="")

            with mock.patch.object(
                telegram_gateway,
                "_telegram_api_request",
                return_value={"ok": True, "result": {"message_id": 99}},
            ) as sent:
                result = await telegram_gateway.handle_telegram_update(repo, _dm_update(), settings=settings)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "pairing_required")
            self.assertEqual(repo.jobs, [])
            self.assertEqual(sent.call_args.args[1], "sendMessage")
            self.assertIn(result["pairingCode"], sent.call_args.args[2]["text"])
            pairings = [value for (etype, _), value in repo.entities.items() if etype == "telegram_pairing"]
            self.assertEqual(len(pairings), 1)
            self.assertEqual(pairings[0]["status"], "pending")

    async def test_group_requires_mention_and_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            await repo.put_entity(
                "telegram_gateway",
                {"id": "default", "bot": {"username": "atrium_bot"}},
            )
            settings = _settings(
                tmp,
                telegram_groups_json='{"-1001":{"threadId":"executive","requireMention":true}}',
            )
            update = {
                "update_id": 9002,
                "message": {
                    "message_id": 7,
                    "chat": {"id": -1001, "type": "supergroup"},
                    "from": {"id": 1001, "first_name": "Owner"},
                    "text": "ไม่ต้องตอบ",
                },
            }

            suppressed = await telegram_gateway.handle_telegram_update(repo, update, settings=settings)
            self.assertEqual(suppressed["status"], "suppressed")
            self.assertEqual(suppressed["reason"], "mention_required")
            self.assertEqual(repo.jobs, [])

            update["update_id"] = 9003
            update["message"]["message_id"] = 8
            update["message"]["text"] = "@atrium_bot สรุปให้ที"
            queued = await telegram_gateway.handle_telegram_update(repo, update, settings=settings)
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["threadId"], "executive")
            self.assertEqual(repo.messages["telegram:-1001:8"]["text"], "สรุปให้ที")

    async def test_telegram_file_enters_artifact_attachment_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp)
            update = _dm_update("อ่านไฟล์นี้")
            update["message"]["document"] = {
                "file_id": "file-1",
                "file_unique_id": "unique-1",
                "file_name": "brief.txt",
                "mime_type": "text/plain",
                "file_size": 5,
            }
            captured_store: list[dict] = []

            async def fake_loader(item, bot_token, settings):
                del bot_token, settings
                return {"data": b"hello", "filename": item["filename"], "mime": item["mime"]}

            async def fake_store(repo, **kwargs):
                del repo
                captured_store.append(kwargs)
                return {
                    "artifact": {
                        "id": "art_file",
                        "name": kwargs["filename"],
                        "kind": "report",
                        "mime": kwargs["mime"],
                        "uri": "/tmp/brief.txt",
                        "contentSizeBytes": len(kwargs["data"]),
                    }
                }

            result = await telegram_gateway.handle_telegram_update(
                repo,
                update,
                settings=settings,
                store_file=fake_store,
                file_loader=fake_loader,
            )

            self.assertEqual(result["attachmentCount"], 1)
            self.assertEqual(captured_store[0]["created_by"], "telegram")
            user_msg = repo.messages["telegram:555:42"]
            self.assertEqual(user_msg["attachments"][0]["artifactId"], "art_file")
            self.assertEqual(repo.jobs[0]["payload"]["attachments"][0]["artifactId"], "art_file")

    async def test_duplicate_update_does_not_enqueue_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp)

            first = await telegram_gateway.handle_telegram_update(repo, _dm_update(), settings=settings)
            second = await telegram_gateway.handle_telegram_update(repo, _dm_update(), settings=settings)

            self.assertEqual(first["status"], "queued")
            self.assertEqual(second["status"], "duplicate")
            self.assertEqual([job["kind"] for job in repo.jobs], ["chat_reply", "telegram_progress"])

    async def test_outbound_delivery_records_receipt_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp)
            reply = {
                "id": "msg_reply",
                "threadId": "executive",
                "role": "executive",
                "authorName": "Executive AI",
                "text": "พร้อมครับ",
                "ts": 1,
                "status": "sent",
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                    "dedupeKey": "telegram:reply:555:42",
                },
            }
            repo.messages[reply["id"]] = reply
            await repo.put_entity(
                "telegram_progress_message",
                {
                    "id": reply["id"],
                    "replyMessageId": reply["id"],
                    "threadId": "executive",
                    "chatId": "555",
                    "progressMessageId": "100",
                    "updatedAt": 2,
                },
            )
            sent_payloads: list[dict] = []

            async def fake_sender(bot_token, payload, settings):
                del settings
                sent_payloads.append({"token": bot_token, "payload": payload})
                return {"ok": True, "receipts": [{"method": "sendMessage", "response": {"message_id": 99}}]}

            first = await telegram_gateway.maybe_deliver_telegram_reply_for_message(
                repo,
                reply,
                settings=settings,
                sender=fake_sender,
            )
            second = await telegram_gateway.maybe_deliver_telegram_reply_for_message(
                repo,
                repo.messages[reply["id"]],
                settings=settings,
                sender=fake_sender,
            )

            self.assertEqual(first["status"], "sent")
            self.assertEqual(second["status"], "duplicate")
            self.assertEqual(len(sent_payloads), 1)
            self.assertEqual(sent_payloads[0]["payload"]["channelReply"]["progressMessageId"], "100")
            updated = repo.messages[reply["id"]]
            self.assertEqual(updated["channelReply"]["status"], "sent")
            self.assertEqual(updated["channelReply"]["progressMessageId"], "100")

    async def test_outbound_failure_records_receipt_and_queues_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp, telegram_outbound_retry_attempts=2)
            reply = {
                "id": "msg_reply",
                "threadId": "executive",
                "role": "executive",
                "authorName": "Executive AI",
                "text": "พร้อมครับ",
                "ts": 1,
                "status": "sent",
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                    "dedupeKey": "telegram:reply:555:42",
                },
            }
            repo.messages[reply["id"]] = reply

            async def failing_sender(bot_token, payload, settings):
                del bot_token, payload, settings
                raise RuntimeError("network down")

            result = await telegram_gateway.maybe_deliver_telegram_reply_for_message(
                repo,
                reply,
                settings=settings,
                sender=failing_sender,
            )

            self.assertEqual(result["status"], "retry_queued")
            self.assertEqual(len(repo.jobs), 1)
            self.assertEqual(repo.jobs[0]["kind"], "telegram_outbound")
            updated = repo.messages[reply["id"]]
            self.assertEqual(updated["channelReply"]["status"], "retry_queued")

    async def test_send_telegram_payload_chunks_text_and_sends_local_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(tmp, telegram_outbound_chunk_chars=500)
            attachment_path = Path(tmp) / "summary.txt"
            attachment_path.write_text("hello", encoding="utf-8")
            payload = {
                "text": " ".join(["long"] * 160),
                "attachments": [{"uri": str(attachment_path), "name": "summary.txt", "mime": "text/plain"}],
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                },
            }
            api_calls: list[tuple[str, dict]] = []
            multipart_calls: list[tuple[str, dict]] = []

            def fake_api(bot_token, method, body):
                del bot_token
                api_calls.append((method, body))
                return {"ok": True, "result": {"message_id": len(api_calls)}}

            def fake_multipart(bot_token, method, fields, *, file_field, filename, data, mime):
                del bot_token, file_field
                multipart_calls.append((method, {"fields": fields, "filename": filename, "data": data, "mime": mime}))
                return {"ok": True, "result": {"message_id": 99}}

            with (
                mock.patch.object(telegram_gateway, "_telegram_api_request", side_effect=fake_api),
                mock.patch.object(telegram_gateway, "_telegram_api_multipart", side_effect=fake_multipart),
            ):
                result = await telegram_gateway.send_telegram_payload("123:test-token", payload, settings)

            self.assertTrue(result["ok"])
            self.assertGreaterEqual([method for method, _ in api_calls].count("sendMessage"), 2)
            self.assertIn("sendChatAction", [method for method, _ in api_calls])
            self.assertEqual(multipart_calls[0][0], "sendDocument")
            self.assertEqual(multipart_calls[0][1]["filename"], "summary.txt")
            self.assertEqual(multipart_calls[0][1]["data"], b"hello")

    async def test_progress_job_sends_typing_and_reschedules_while_reply_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp)
            reply = {
                "id": "msg_reply",
                "threadId": "executive",
                "pending": True,
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                },
            }
            repo.messages[reply["id"]] = reply
            api_calls: list[tuple[str, dict]] = []

            def fake_api(bot_token, method, body):
                del bot_token
                api_calls.append((method, body))
                return {"ok": True, "result": {"message_id": 100}}

            with (
                mock.patch.object(telegram_gateway, "get_settings", return_value=settings),
                mock.patch.object(
                    telegram_gateway,
                    "_telegram_api_request",
                    side_effect=fake_api,
                ),
            ):
                result = await telegram_gateway.process_telegram_progress_job(
                    repo,
                    {
                        "replyMessageId": reply["id"],
                        "threadId": "executive",
                        "expiresAt": 20_000,
                    },
                    10_000,
                )

            self.assertEqual(result["status"], "typing_rescheduled")
            self.assertEqual([method for method, _ in api_calls], ["sendChatAction", "sendMessage"])
            self.assertEqual(api_calls[0][1]["action"], "typing")
            self.assertIn("ATRIUM กำลังทำงาน", api_calls[1][1]["text"])
            self.assertEqual(repo.messages[reply["id"]]["channelReply"]["progressMessageId"], "100")
            self.assertEqual(repo.entities[("telegram_progress_message", reply["id"])]["progressMessageId"], "100")
            self.assertEqual(repo.jobs[-1]["kind"], "telegram_progress")
            self.assertEqual(repo.jobs[-1]["payload"]["channelReply"]["progressMessageId"], "100")

    async def test_progress_job_edits_existing_progress_message_with_partial_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo()
            settings = _settings(tmp)
            reply = {
                "id": "msg_reply",
                "threadId": "executive",
                "pending": True,
                "status": "sending",
                "text": "กำลังร่างคำตอบส่วนแรก",
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                    "progressMessageId": "100",
                },
            }
            repo.messages[reply["id"]] = reply
            api_calls: list[tuple[str, dict]] = []

            def fake_api(bot_token, method, body):
                del bot_token
                api_calls.append((method, body))
                return {"ok": True, "result": {"message_id": 100}}

            with (
                mock.patch.object(telegram_gateway, "get_settings", return_value=settings),
                mock.patch.object(telegram_gateway, "_telegram_api_request", side_effect=fake_api),
            ):
                result = await telegram_gateway.process_telegram_progress_job(
                    repo,
                    {
                        "replyMessageId": reply["id"],
                        "threadId": "executive",
                        "expiresAt": 20_000,
                    },
                    10_000,
                )

            self.assertEqual(result["status"], "typing_rescheduled")
            self.assertEqual([method for method, _ in api_calls], ["sendChatAction", "editMessageText"])
            self.assertEqual(api_calls[1][1]["message_id"], 100)
            self.assertIn("กำลังร่างคำตอบส่วนแรก", api_calls[1][1]["text"])
            self.assertEqual(repo.jobs[-1]["payload"]["channelReply"]["progressMessageId"], "100")

    async def test_send_telegram_payload_edits_progress_message_into_final_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(tmp)
            payload = {
                "text": "คำตอบสุดท้าย",
                "attachments": [],
                "channelReply": {
                    "provider": "telegram",
                    "chatId": "555",
                    "replyToTelegramMessageId": "42",
                    "progressMessageId": "100",
                },
            }
            api_calls: list[tuple[str, dict]] = []

            def fake_api(bot_token, method, body):
                del bot_token
                api_calls.append((method, body))
                return {"ok": True, "result": {"message_id": 100}}

            with mock.patch.object(telegram_gateway, "_telegram_api_request", side_effect=fake_api):
                result = await telegram_gateway.send_telegram_payload("123:test-token", payload, settings)

            self.assertTrue(result["ok"])
            self.assertEqual([method for method, _ in api_calls], ["sendChatAction", "editMessageText"])
            self.assertEqual(api_calls[1][1]["message_id"], 100)
            self.assertEqual(api_calls[1][1]["text"], "คำตอบสุดท้าย")


if __name__ == "__main__":
    unittest.main()
