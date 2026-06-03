"""Context-budget helpers for chat compaction decisions."""
from __future__ import annotations

import math
import re
from typing import Any

from .catalog import DEFAULT_MODEL, MODELS
from .provider.base import LLMMessage

CLAUDE_AUTO_COMPACT_CONTEXT_TOKENS = 180_000
GPT_AUTO_COMPACT_CONTEXT_TOKENS = 230_000
SMALL_WINDOW_COMPACT_RATIO = 0.8

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
_MESSAGE_OVERHEAD_TOKENS = 6
_IMAGE_CONTEXT_TOKENS = 120


def estimate_text_tokens(text: Any) -> int:
    value = str(text or "")
    if not value.strip():
        return 0
    thai_chars = len(_THAI_RE.findall(value))
    non_thai_chars = max(len(value) - thai_chars, 0)
    return max(1, math.ceil(thai_chars / 2.2 + non_thai_chars / 4.0))


def _content_token_estimate(content: Any) -> int:
    if isinstance(content, str):
        return estimate_text_tokens(content)
    if not isinstance(content, list):
        return estimate_text_tokens(content)
    total = 0
    for block in content:
        if not isinstance(block, dict):
            total += estimate_text_tokens(block)
            continue
        block_type = block.get("type")
        if block_type in {"input_image", "image"}:
            total += _IMAGE_CONTEXT_TOKENS
            continue
        if block_type in {"tool_use", "function_call"}:
            total += estimate_text_tokens(block.get("name"))
            total += estimate_text_tokens(block.get("input") or block.get("arguments"))
            continue
        if block_type in {"tool_result", "function_call_output"}:
            total += estimate_text_tokens(block.get("content") or block.get("output"))
            continue
        total += estimate_text_tokens(block.get("text") or block.get("thinking") or block)
    return total


def estimate_llm_context_tokens(system: str, messages: list[LLMMessage]) -> int:
    total = estimate_text_tokens(system)
    for message in messages:
        total += _MESSAGE_OVERHEAD_TOKENS
        total += estimate_text_tokens(message.role)
        total += _content_token_estimate(message.content)
    return total


def model_context_window_tokens(model_id: str) -> int:
    model = MODELS.get(model_id) or MODELS[DEFAULT_MODEL]
    try:
        value = int(model.get("contextWindowTokens") or 0)
    except (TypeError, ValueError):
        value = 0
    return max(1, value or 200_000)


def model_auto_compact_context_tokens(
    model_id: str,
    *,
    claude_threshold: int = CLAUDE_AUTO_COMPACT_CONTEXT_TOKENS,
    gpt_threshold: int = GPT_AUTO_COMPACT_CONTEXT_TOKENS,
    small_window_ratio: float = SMALL_WINDOW_COMPACT_RATIO,
) -> int:
    model = MODELS.get(model_id) or MODELS[DEFAULT_MODEL]
    tier = str(model.get("tier") or "").strip().lower()
    target = gpt_threshold if tier == "gpt" else claude_threshold
    window = model_context_window_tokens(model_id)
    try:
        ratio = float(small_window_ratio)
    except (TypeError, ValueError):
        ratio = SMALL_WINDOW_COMPACT_RATIO
    ratio = min(max(ratio, 0.05), 1.0)
    if window < target:
        return max(1, int(window * ratio))
    return max(1, int(target))
