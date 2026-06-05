import inspect
import os
import unittest
from unittest import mock

from app.chat_input import estimate_input, input_character_limit_exceeded
from app.config import Settings, get_settings
from app.main import _llm_history, _rate_limit_state
from app.runtime.turns import complete_agent_via_runtime


class UnlockedAiLimitsTest(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_input_character_and_recommended_token_limits_are_disabled_at_zero(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "ATRIUM_CHAT_MAX_INPUT_CHARS": "0",
                "ATRIUM_CHAT_RECOMMENDED_INPUT_TOKENS": "0",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            estimate = estimate_input("x" * 50_000)

        self.assertTrue(estimate["withinLimit"])
        self.assertEqual(estimate["maxRecommendedTokens"], 0)
        self.assertEqual(estimate["warnings"], [])
        self.assertFalse(input_character_limit_exceeded(estimate))

    def test_positive_input_character_limit_still_blocks_when_configured(self) -> None:
        with mock.patch.dict(os.environ, {"ATRIUM_CHAT_MAX_INPUT_CHARS": "10"}, clear=False):
            get_settings.cache_clear()
            estimate = estimate_input("x" * 11)

        self.assertFalse(estimate["withinLimit"])
        self.assertTrue(input_character_limit_exceeded(estimate))

    def test_chat_history_zero_keeps_all_fetched_messages_in_prompt(self) -> None:
        with mock.patch.dict(os.environ, {"ATRIUM_CHAT_HISTORY_MESSAGES": "0"}, clear=False):
            get_settings.cache_clear()
            history = [
                {"role": "user", "text": "first", "attachments": []},
                {"role": "assistant", "authorName": "Agent", "text": "second", "attachments": []},
                {"role": "user", "text": "third", "attachments": []},
            ]
            messages = _llm_history(history, {"text": "current", "attachments": []})

        self.assertEqual([message.content for message in messages], ["first", "Agent: second", "third", "current"])

    def test_engine_chat_history_zero_keeps_all_fetched_messages_in_prompt(self) -> None:
        from app import engine

        with mock.patch.dict(os.environ, {"ATRIUM_CHAT_HISTORY_MESSAGES": "0"}, clear=False):
            get_settings.cache_clear()
            history = [
                {"role": "user", "text": "first", "attachments": []},
                {"role": "assistant", "authorName": "Agent", "text": "second", "attachments": []},
                {"role": "user", "text": "third", "attachments": []},
            ]
            messages = engine._llm_chat_history(history, {"text": "current", "attachments": []})

        self.assertEqual([message.content for message in messages], ["first", "Agent: second", "third", "current"])

    def test_rate_limit_zero_is_disabled(self) -> None:
        history = [{"role": "user", "ts": 1_000, "status": "sent"} for _ in range(100)]

        state, warnings, exceeded = _rate_limit_state(history, now=2_000, limit=0)

        self.assertIsNone(state)
        self.assertEqual(warnings, [])
        self.assertFalse(exceeded)

    def test_tool_round_limit_default_is_unbounded(self) -> None:
        self.assertEqual(Settings(runtime_max_tool_rounds=0).runtime_max_tool_round_limit, 0)
        self.assertIsNone(inspect.signature(complete_agent_via_runtime).parameters["max_tool_rounds"].default)


if __name__ == "__main__":
    unittest.main()
