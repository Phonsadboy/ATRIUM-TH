"""Provider abstractions shared by live LLM backends."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..catalog import model_pricing

Role = Literal["user", "assistant"]
ReasoningStatus = Literal["available", "redacted", "omitted", "disabled", "unavailable"]


@dataclass
class LLMMessage:
    role: Role
    content: str | list[dict[str, Any]]


@dataclass
class LLMToolCall:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    thinking_tokens: int = 0
    reasoning: str = ""
    reasoning_status: ReasoningStatus = "unavailable"
    generation_ms: int = 0
    model: str = ""
    provider_id: str = ""
    speed: str = "standard"
    stop_reason: str = "end_turn"
    content: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def usd(self) -> float:
        in_rate, out_rate = model_pricing(self.model, self.speed)
        return round(
            (self.tokens_in * in_rate + (self.tokens_out + self.thinking_tokens) * out_rate) / 1_000_000,
            6,
        )

    @property
    def reasoning_summary(self) -> str | None:
        if self.reasoning:
            first_line = next((line.strip() for line in self.reasoning.splitlines() if line.strip()), "")
            return first_line if len(first_line) <= 180 else f"{first_line[:177]}..."
        if self.reasoning_status == "redacted":
            return "เหตุผลถูกซ่อนโดยผู้ให้บริการ"
        if self.reasoning_status == "omitted":
            return "เหตุผลถูก omit แต่ยังมีการคิดและคิดเงินตามปกติ"
        return None


@dataclass
class LLMStreamEvent:
    kind: Literal["text_delta", "thinking_delta", "message_stop"]
    text: str = ""
    result: LLMResult | None = None


class LLMProvider(Protocol):
    id: str
    live: bool

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResult: ...

    def stream_complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]: ...

    async def count_context_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ) -> int: ...
