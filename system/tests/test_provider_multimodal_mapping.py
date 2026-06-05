import asyncio
import base64
import json
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import httpx

from app.provider.anthropic_provider import AnthropicProvider
from app.provider.base import LLMMessage
from app.provider.claude_code_provider import (
    ClaudeCodeCliProvider,
    _AUTH_STATUS_CACHE,
    _AUTH_STATUS_TIMEOUT_S,
    _ClaudeCodeImageContext,
    _extract_tool_calls,
    claude_code_auth_status,
    _messages_prompt,
)
from app.provider.chatgpt_oauth import ChatGPTAccountResponsesProvider
from app.provider.openai_provider import OpenAIResponsesProvider


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/ax9Y5kAAAAASUVORK5CYII="
)
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")


class ProviderMultimodalMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        _AUTH_STATUS_CACHE.clear()

    def test_openai_responses_preserves_input_image_blocks(self) -> None:
        items = OpenAIResponsesProvider._input_items([
            LLMMessage(
                role="user",
                content=[
                    {"type": "text", "text": "inspect"},
                    {"type": "input_image", "image_url": PNG_DATA_URL, "detail": "auto"},
                ],
            )
        ])

        content = items[0]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": "inspect"})
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[1]["image_url"], PNG_DATA_URL)
        self.assertEqual(content[1]["detail"], "auto")

    def test_anthropic_provider_maps_input_image_to_base64_source(self) -> None:
        content = AnthropicProvider._normalize_message_content([
            {"type": "text", "text": "inspect"},
            {"type": "input_image", "image_url": PNG_DATA_URL},
        ])

        self.assertEqual(content[0], {"type": "text", "text": "inspect"})
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["source"]["type"], "base64")
        self.assertEqual(content[1]["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(content[1]["source"]["data"]), PNG_BYTES)

    def test_claude_code_materializes_input_image_for_cli_access(self) -> None:
        image_context = _ClaudeCodeImageContext()
        try:
            prompt = _messages_prompt(
                [
                    LLMMessage(
                        role="user",
                        content=[
                            {"type": "text", "text": "inspect"},
                            {"type": "input_image", "image_url": PNG_DATA_URL},
                        ],
                    )
                ],
                image_context=image_context,
            )

            self.assertIn("Claude Code image input 1:", prompt)
            self.assertRegex(prompt, r"Claude Code image input 1: @.+image-1\.png")
            self.assertNotIn("omitted", prompt.lower())
            self.assertEqual(image_context.count, 1)
            self.assertEqual(len(image_context.extra_dirs), 1)
            image_path = next(Path(image_context.extra_dirs[0]).glob("image-1.png"))
            self.assertEqual(image_path.read_bytes(), PNG_BYTES)

            args = ClaudeCodeCliProvider("claude_code")._args(
                command="claude",
                model="claude-sonnet-4-6",
                effort="low",
                system_prompt="system",
                stream=False,
                extra_dirs=image_context.extra_dirs,
            )
            self.assertIn("--add-dir", args)
            self.assertIn(image_context.extra_dirs[0], args)
        finally:
            image_context.close()

    def test_claude_code_token_estimate_does_not_embed_base64_image_payload(self) -> None:
        prompt = _messages_prompt([
            LLMMessage(
                role="user",
                content=[
                    {"type": "text", "text": "inspect"},
                    {"type": "input_image", "image_url": PNG_DATA_URL},
                ],
            )
        ])

        self.assertIn("image/png data URL", prompt)
        self.assertNotIn(PNG_DATA_URL, prompt)
        self.assertNotIn("omitted", prompt.lower())

    def test_claude_code_stream_extracts_tool_calls_from_single_result_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atrium-claude-code-stream-test-") as tmp:
            command = Path(tmp) / "fake_claude.py"
            command.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({\n"
                "  'result': json.dumps({'tool_calls': [{\n"
                "    'id': 'call_1',\n"
                "    'name': 'call_atrium_api',\n"
                "    'input': {'method': 'GET', 'path': '/health'}\n"
                "  }]}),\n"
                "  'usage': {'input_tokens': 10, 'output_tokens': 5},\n"
                "  'model': 'opus'\n"
                "}))\n",
                encoding="utf-8",
            )
            command.chmod(command.stat().st_mode | stat.S_IXUSR)
            provider = ClaudeCodeCliProvider("claude_code", command=str(command), timeout=5.0)

            async def collect():
                return [
                    event
                    async for event in provider.stream_complete(
                        system="Use tools when useful.",
                        messages=[LLMMessage(role="user", content="ตรวจ /health ก่อนตอบ")],
                        model="claude-opus-4-8",
                        effort="high",
                        tools=[{
                            "name": "call_atrium_api",
                            "description": "Call ATRIUM local API",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "method": {"type": "string"},
                                    "path": {"type": "string"},
                                },
                            },
                        }],
                    )
                ]

            events = asyncio.run(collect())
            stop = next(event.result for event in events if event.kind == "message_stop")
            self.assertEqual(stop.text, "")
            self.assertEqual(stop.stop_reason, "tool_calls")
            self.assertEqual(len(stop.tool_calls), 1)
            self.assertEqual(stop.tool_calls[0].name, "call_atrium_api")
            self.assertEqual(stop.tool_calls[0].input, {"method": "GET", "path": "/health"})
            self.assertEqual(stop.meta["toolSupport"], "atrium-json-shim")

    def test_claude_code_extracts_loose_multiline_tool_call_text(self) -> None:
        text = (
            'รับทราบครับ จะส่งไปยัง executive room\n\n'
            '{"tool_calls":[{"id":"call_1","name":"post_visible_chat_message","input":{"text":"'
            '## Concept 1 — "Shock Value + Curiosity Gap"\n'
            'Headline เช่น "ทำไมคนซื้อซ้ำถึง 3 ครั้ง?"\n'
            '- bullet หนึ่ง\n'
            '","targetDepartmentId":"exec"}}]}'
        )

        calls = _extract_tool_calls(text, [{
            "name": "post_visible_chat_message",
            "description": "Post a visible message",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "targetDepartmentId": {"type": "string"},
                },
                "required": ["text"],
            },
        }])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].id, "call_1")
        self.assertEqual(calls[0].name, "post_visible_chat_message")
        self.assertEqual(calls[0].input["targetDepartmentId"], "exec")
        self.assertIn('Concept 1 — "Shock Value + Curiosity Gap"', calls[0].input["text"])
        self.assertIn('Headline เช่น "ทำไมคนซื้อซ้ำถึง 3 ครั้ง?"', calls[0].input["text"])

    def test_claude_code_auth_status_allows_slow_keychain_probe(self) -> None:
        completed = stat_result = mock.Mock()
        stat_result.returncode = 0
        stat_result.stdout = json.dumps({
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "max",
            "email": "user@example.test",
        })
        stat_result.stderr = ""

        with mock.patch("app.provider.claude_code_provider.shutil.which", return_value="/usr/local/bin/claude"):
            with mock.patch("app.provider.claude_code_provider.subprocess.run", return_value=completed) as run:
                status = claude_code_auth_status("claude")

        self.assertTrue(status["ready"])
        self.assertTrue(status["loggedIn"])
        self.assertEqual(status["authMethod"], "claude.ai")
        self.assertGreaterEqual(run.call_args.kwargs["timeout"], _AUTH_STATUS_TIMEOUT_S)

    def test_claude_code_auth_status_keeps_recent_ready_status_on_probe_timeout(self) -> None:
        ready_status = {
            "ready": True,
            "command": "claude",
            "installed": True,
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "max",
            "email": "user@example.test",
            "status": "ready",
            "state": "ready",
            "fresh": True,
            "stale": False,
            "probeFailed": False,
            "lastFreshAt": 1000.0,
        }
        _AUTH_STATUS_CACHE["/usr/local/bin/claude"] = (1000.0, ready_status)

        with mock.patch("app.provider.claude_code_provider.shutil.which", return_value="/usr/local/bin/claude"):
            with mock.patch("app.provider.claude_code_provider.time.time", return_value=1000.0 + _AUTH_STATUS_TIMEOUT_S + 60):
                with mock.patch(
                    "app.provider.claude_code_provider.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["claude", "auth", "status", "--json"], _AUTH_STATUS_TIMEOUT_S),
                ):
                    status = claude_code_auth_status("claude")

        self.assertTrue(status["ready"])
        self.assertTrue(status["loggedIn"])
        self.assertTrue(status["stale"])
        self.assertFalse(status["fresh"])
        self.assertEqual(status["state"], "ready_stale")
        self.assertEqual(status["probeStatus"], "probe_failed:TimeoutExpired")

    def test_claude_code_auth_status_reports_unknown_on_uncached_probe_timeout(self) -> None:
        with mock.patch("app.provider.claude_code_provider.shutil.which", return_value="/usr/local/bin/claude"):
            with mock.patch(
                "app.provider.claude_code_provider.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["claude", "auth", "status", "--json"], _AUTH_STATUS_TIMEOUT_S),
            ):
                status = claude_code_auth_status("claude")

        self.assertIsNone(status["ready"])
        self.assertIsNone(status["loggedIn"])
        self.assertEqual(status["state"], "unknown")
        self.assertEqual(status["status"], "probe_failed:TimeoutExpired")
        self.assertTrue(status["probeFailed"])

    def test_responses_provider_retries_empty_visible_output_once(self) -> None:
        class FakeResponse:
            def __init__(self, payload: dict) -> None:
                self._payload = payload

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self._payload

        class FakeClient:
            def __init__(self) -> None:
                self.payloads: list[dict] = []

            async def post(self, *args, **kwargs) -> FakeResponse:
                self.payloads.append(kwargs["json"])
                if len(self.payloads) == 1:
                    return FakeResponse({
                        "id": "resp_empty",
                        "model": "gpt-5.5",
                        "status": "completed",
                        "output": [],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 12,
                            "output_tokens_details": {"reasoning_tokens": 12},
                        },
                    })
                return FakeResponse({
                    "id": "resp_text",
                    "model": "gpt-5.5",
                    "status": "completed",
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "OK"}],
                    }],
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 4,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                })

        provider = OpenAIResponsesProvider("openai", "https://example.invalid/v1", "token")
        provider._request_retry_delay_s = lambda attempt: 0.0
        fake_client = FakeClient()
        provider._client = fake_client

        result = asyncio.run(provider.complete(
            system="system",
            messages=[LLMMessage(role="user", content="reply OK")],
            model="gpt-5.5",
            effort="low",
        ))

        self.assertEqual(result.text, "OK")
        self.assertTrue(result.meta["emptyOutputRetry"])
        self.assertEqual(len(fake_client.payloads), 2)
        self.assertIn("visible output_text", fake_client.payloads[1]["instructions"])
        self.assertEqual(len(fake_client.payloads[1]["input"]), len(fake_client.payloads[0]["input"]) + 1)

    def test_responses_provider_retries_read_timeout_once(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "id": "resp_text",
                    "model": "gpt-5.5",
                    "status": "completed",
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "OK"}],
                    }],
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 4,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                }

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            async def post(self, *args, **kwargs) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ReadTimeout("slow upstream")
                return FakeResponse()

        provider = OpenAIResponsesProvider("openai", "https://example.invalid/v1", "token")
        provider._request_retry_delay_s = lambda attempt: 0.0
        fake_client = FakeClient()
        provider._client = fake_client

        result = asyncio.run(provider.complete(
            system="system",
            messages=[LLMMessage(role="user", content="reply OK")],
            model="gpt-5.5",
            effort="low",
        ))

        self.assertEqual(result.text, "OK")
        self.assertEqual(fake_client.calls, 2)

    def test_responses_provider_retries_bad_gateway_once(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "id": "resp_text",
                    "model": "gpt-5.5",
                    "status": "completed",
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "OK"}],
                    }],
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 4,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                }

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            async def post(self, *args, **kwargs) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    request = httpx.Request("POST", "https://example.invalid/v1/responses")
                    response = httpx.Response(502, request=request, text="bad gateway")
                    raise httpx.HTTPStatusError("bad gateway", request=request, response=response)
                return FakeResponse()

        provider = OpenAIResponsesProvider("openai", "https://example.invalid/v1", "token")
        fake_client = FakeClient()
        provider._client = fake_client

        result = asyncio.run(provider.complete(
            system="system",
            messages=[LLMMessage(role="user", content="reply OK")],
            model="gpt-5.5",
            effort="low",
        ))

        self.assertEqual(result.text, "OK")
        self.assertEqual(fake_client.calls, 2)

    def test_responses_provider_retry_delay_uses_backoff_with_jitter(self) -> None:
        provider = OpenAIResponsesProvider("openai", "https://example.invalid/v1", "token")

        with mock.patch("app.provider.openai_provider.random.uniform", return_value=0.125):
            self.assertEqual(provider._request_retry_delay_s(0), 0.625)
            self.assertEqual(provider._request_retry_delay_s(2), 2.125)

    def test_responses_provider_retry_sleep_honors_retry_after_header(self) -> None:
        provider = OpenAIResponsesProvider("openai", "https://example.invalid/v1", "token")
        request = httpx.Request("POST", "https://example.invalid/v1/responses")
        response = httpx.Response(429, request=request, headers={"Retry-After": "7"})

        with mock.patch("app.provider.openai_provider.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            asyncio.run(provider._sleep_before_request_retry(0, response))

        sleep.assert_awaited_once_with(7.0)

    def test_responses_provider_stream_retries_read_timeout_before_output_once(self) -> None:
        class FakeStreamResponse:
            def raise_for_status(self) -> None:
                return None

            async def aiter_lines(self):
                yield 'data: {"type":"response.output_text.delta","delta":"OK"}'
                yield (
                    'data: {"type":"response.completed","response":{'
                    '"id":"resp_stream","model":"gpt-5.5","status":"completed",'
                    '"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"OK"}]}],'
                    '"usage":{"input_tokens":20,"output_tokens":4,'
                    '"output_tokens_details":{"reasoning_tokens":0}}'
                    "}}"
                )

        class FakeStream:
            def __init__(self, *, fail: bool) -> None:
                self.fail = fail

            async def __aenter__(self):
                if self.fail:
                    raise httpx.ReadTimeout("slow upstream")
                return FakeStreamResponse()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            def stream(self, *args, **kwargs) -> FakeStream:
                self.calls += 1
                return FakeStream(fail=self.calls == 1)

        provider = OpenAIResponsesProvider("openai", "https://example.invalid/v1", "token")
        provider._request_retry_delay_s = lambda attempt: 0.0
        fake_client = FakeClient()
        provider._client = fake_client

        async def collect():
            return [
                event
                async for event in provider.stream_complete(
                    system="system",
                    messages=[LLMMessage(role="user", content="reply OK")],
                    model="gpt-5.5",
                    effort="low",
                )
            ]

        events = asyncio.run(collect())
        self.assertEqual(fake_client.calls, 2)
        self.assertEqual([event.text for event in events if event.kind == "text_delta"], ["OK"])
        stop = next(event.result for event in events if event.kind == "message_stop")
        self.assertEqual(stop.text, "OK")
        self.assertTrue(stop.meta["streamRequestRetry"])

    def test_chatgpt_account_provider_retries_bad_gateway_once(self) -> None:
        class DummyTokenProvider:
            async def access_token(self) -> str:
                return "token"

        class FakeResponse:
            @property
            def text(self) -> str:
                return (
                    'data: {"type":"response.completed","response":{'
                    '"id":"resp_chatgpt","model":"gpt-5.5","status":"completed",'
                    '"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"OK"}]}],'
                    '"usage":{"input_tokens":20,"output_tokens":4,'
                    '"output_tokens_details":{"reasoning_tokens":0}}'
                    "}}\n"
                )

            def raise_for_status(self) -> None:
                return None

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            async def post(self, *args, **kwargs) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    request = httpx.Request("POST", "https://example.invalid/responses")
                    response = httpx.Response(500, request=request, text="")
                    raise httpx.HTTPStatusError("server error", request=request, response=response)
                return FakeResponse()

        provider = ChatGPTAccountResponsesProvider("chatgpt_account", "https://example.invalid", DummyTokenProvider())
        provider._request_retry_delay_s = lambda attempt: 0.0
        fake_client = FakeClient()
        provider._client = fake_client

        result = asyncio.run(provider.complete(
            system="system",
            messages=[LLMMessage(role="user", content="reply OK")],
            model="gpt-5.5",
            effort="low",
        ))

        self.assertEqual(result.text, "OK")
        self.assertEqual(result.meta["wireApi"], "chatgpt-codex-responses")
        self.assertEqual(fake_client.calls, 2)


if __name__ == "__main__":
    unittest.main()
