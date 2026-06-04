"""Chat streaming helpers shared by the REST endpoint and engine jobs."""
from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .clock import now_ms
from .db.base import commit_and_release, session_scope
from .db.repo import Repo
from .events import hub
from .provider.base import LLMMessage, LLMProvider, LLMResult, LLMStreamEvent


class ChatStreamCancelled(Exception):
    """Raised inside a streaming turn when the user requests cancellation."""


_ERROR_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|authorization|password|secret)=)[^&\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)[\"'\s:=]+)([^\"',\s}{]+)"),
]


def provider_exception_detail(exc: Exception, *, limit: int = 800) -> tuple[str, str]:
    """Return (class name, redacted user-facing detail) for provider failures."""
    error_type = type(exc).__name__
    detail = str(exc).strip()
    if not detail:
        detail = error_type
    for pattern in _ERROR_SECRET_PATTERNS:
        detail = pattern.sub(r"\1[redacted]", detail)
    if len(detail) > limit:
        detail = f"{detail[:limit]}..."
    return error_type, detail


class ChatStreamRegistry:
    def __init__(self) -> None:
        self._by_msg: dict[str, asyncio.Event] = {}
        self._thread_by_msg: dict[str, str] = {}
        self._latest_by_thread: dict[str, str] = {}

    def start(self, thread_id: str, msg_id: str) -> asyncio.Event:
        ev = asyncio.Event()
        self._by_msg[msg_id] = ev
        self._thread_by_msg[msg_id] = thread_id
        self._latest_by_thread[thread_id] = msg_id
        return ev

    def finish(self, thread_id: str, msg_id: str) -> None:
        self._by_msg.pop(msg_id, None)
        self._thread_by_msg.pop(msg_id, None)
        if self._latest_by_thread.get(thread_id) == msg_id:
            self._latest_by_thread.pop(thread_id, None)

    def stop(self, *, thread_id: str | None = None, msg_id: str | None = None) -> tuple[bool, str | None]:
        if not msg_id and thread_id:
            targets = [mid for mid, tid in self._thread_by_msg.items() if tid == thread_id]
            if targets:
                latest = self._latest_by_thread.get(thread_id) or targets[-1]
                for target in targets:
                    ev = self._by_msg.get(target)
                    if ev is not None:
                        ev.set()
                return True, latest
        target = msg_id or (self._latest_by_thread.get(thread_id) if thread_id else None)
        if not target:
            return False, None
        ev = self._by_msg.get(target)
        if ev is None:
            return False, target
        ev.set()
        return True, target


chat_streams = ChatStreamRegistry()


class ChatMessageStreamSink:
    def __init__(
        self,
        *,
        thread_id: str,
        msg_id: str,
        message: dict[str, Any],
        cancel_event: asyncio.Event,
        repo: Repo | None = None,
        persist_every_chars: int = 160,
        persist_every_ms: int = 750,
    ) -> None:
        self.thread_id = thread_id
        self.msg_id = msg_id
        self.message = dict(message)
        self.cancel_event = cancel_event
        self.repo = repo
        self.text = ""
        self.thinking = ""
        self.seq = 0
        self._last_persist_len = 0
        self._last_persist_at = 0
        self._persist_every_chars = persist_every_chars
        self._persist_every_ms = persist_every_ms

    async def _merge_latest_channel_reply(self, msg: dict[str, Any]) -> dict[str, Any]:
        if self.repo is None:
            return msg
        current = msg.get("channelReply") if isinstance(msg.get("channelReply"), dict) else None
        latest_msg = None
        with contextlib.suppress(Exception):
            latest_msg = await self.repo.get_message(self.msg_id, thread_id=self.thread_id)
        latest = latest_msg.get("channelReply") if isinstance(latest_msg, dict) and isinstance(latest_msg.get("channelReply"), dict) else None
        if not latest:
            return msg
        if not current:
            return {**msg, "channelReply": latest}
        progress_keys = {key: value for key, value in latest.items() if key.startswith("progress")}
        merged = {**latest, **current, **progress_keys}
        return {**msg, "channelReply": merged}

    async def start(self) -> None:
        self.message["text"] = ""
        self.message["pending"] = True
        self.message["status"] = "sending"
        self.message.pop("error", None)
        self.message.pop("runtime", None)
        await self.persist(pending=True, force=True)
        hub.pulse({"kind": "msg_start", "threadId": self.thread_id, "msgId": self.msg_id, "message": self.message})

    async def handle(self, event: LLMStreamEvent) -> None:
        if self.cancel_event.is_set():
            raise ChatStreamCancelled()
        if event.kind == "thinking_delta":
            self.thinking += event.text
            hub.pulse({
                "kind": "thinking_delta",
                "threadId": self.thread_id,
                "msgId": self.msg_id,
                "chunk": event.text,
                "text": self.thinking,
            })
            return
        if event.kind != "text_delta" or not event.text:
            return
        self.seq += 1
        self.text += event.text
        self.message["text"] = self.text
        hub.pulse({
            "kind": "msg_delta",
            "threadId": self.thread_id,
            "msgId": self.msg_id,
            "chunk": event.text,
            "text": self.text,
            "seq": self.seq,
        })
        await self.persist_if_due()

    async def persist_if_due(self) -> None:
        now = now_ms()
        enough_text = len(self.text) - self._last_persist_len >= self._persist_every_chars
        enough_time = self._last_persist_at == 0 or now - self._last_persist_at >= self._persist_every_ms
        if enough_text or enough_time:
            await self.persist(pending=True)

    async def persist(self, *, pending: bool, force: bool = False) -> None:
        if not force and len(self.text) == self._last_persist_len:
            return
        msg = {**self.message, "text": self.text, "pending": pending}
        msg = await self._merge_latest_channel_reply(msg)
        if self.repo is not None:
            await self.repo.update_message(msg)
            # Engine-backed chat streams can run for minutes. Commit each
            # persisted chunk so SQLite does not hold a write lock while the
            # provider is still streaming or waiting on tool calls.
            await commit_and_release(self.repo.s)
        else:
            async with session_scope() as s:
                await Repo(s).update_message(msg)
        self.message = msg
        self._last_persist_len = len(self.text)
        self._last_persist_at = now_ms()

    async def finish(
        self,
        *,
        result: LLMResult | None = None,
        stopped: bool = False,
        error: str | None = None,
    ) -> dict[str, Any]:
        stopped = stopped or self.cancel_event.is_set()
        if result and result.text and not stopped:
            if not self.text:
                self.seq += 1
                hub.pulse({
                    "kind": "msg_delta",
                    "threadId": self.thread_id,
                    "msgId": self.msg_id,
                    "chunk": result.text,
                    "text": result.text,
                    "seq": self.seq,
                })
            self.text = result.text
        if stopped and not self.text:
            self.text = "หยุดการตอบแล้วก่อนมีข้อความตอบกลับ"
        reasoning = ""
        reasoning_status = "unavailable"
        if result:
            reasoning = (result.reasoning or self.thinking).strip()
            reasoning_status = result.reasoning_status
            if reasoning and reasoning_status in {"unavailable", "omitted"}:
                reasoning_status = "available"
        reasoning_summary = result.reasoning_summary if result else None
        if reasoning and not reasoning_summary:
            first_line = next((line.strip() for line in reasoning.splitlines() if line.strip()), "")
            reasoning_summary = first_line if len(first_line) <= 180 else f"{first_line[:177]}..."
        status = "sent"
        error_payload = None
        if stopped:
            status = "cancelled"
            error_payload = {"code": "cancelled", "detail": "generation stopped by user", "retryable": True}
        elif error:
            status = "failed"
            error_payload = {"code": "provider_error", "detail": error, "retryable": True}
        message = {**self.message, "text": self.text, "pending": False, "status": status}
        if error_payload:
            message["error"] = error_payload
        else:
            message.pop("error", None)
        if result:
            message.update({
                "reasoning": reasoning or None,
                "reasoningSummary": reasoning_summary,
                "reasoningStatus": reasoning_status,
                "reasoningRedacted": bool(result.meta.get("redactedThinking")),
                "thinkingTokens": result.thinking_tokens,
                "generationMs": result.generation_ms,
            })
            if result.meta.get("toolRuns"):
                message["toolRuns"] = result.meta["toolRuns"]
        self.message = message
        await self.persist(pending=False, force=True)
        event: dict[str, Any] = {
            "kind": "msg_done",
            "threadId": self.thread_id,
            "msgId": self.msg_id,
            "text": self.text,
            "stopped": stopped,
        }
        if error:
            event["error"] = error
        if result:
            event["usage"] = {
                "usd": result.usd,
                "tokensIn": result.tokens_in,
                "tokensOut": result.tokens_out,
                "thinkingTokens": result.thinking_tokens,
                "speed": result.speed,
                "stopReason": result.stop_reason,
                "reasoningStatus": reasoning_status,
                "reasoningChars": len(reasoning),
                "redactedThinking": bool(result.meta.get("redactedThinking")),
                "generationMs": result.generation_ms,
                "toolRuns": result.meta.get("toolRuns", []),
            }
            if result.meta.get("toolRuns"):
                event["toolRuns"] = result.meta["toolRuns"]
        hub.pulse(event)
        return self.message


async def stream_llm(
    provider: LLMProvider,
    *,
    system: str,
    messages: list[LLMMessage],
    model: str,
    effort: str,
    speed: str,
    on_event: Callable[[LLMStreamEvent], Awaitable[None]],
    tools: list[dict[str, Any]] | None = None,
) -> LLMResult:
    async for event in provider.stream_complete(
        system=system,
        messages=messages,
        model=model,
        effort=effort,
        speed=speed,
        tools=tools,
    ):
        if event.kind == "message_stop":
            if event.result is None:
                break
            return event.result
        await on_event(event)
    raise RuntimeError("stream ended without a final message")


def runtime_event_to_hub_pulse(
    event: Any,
    *,
    thread_id: str | None = None,
    msg_id: str | None = None,
) -> dict[str, Any] | None:
    """Map agent-runtime stream events to UI-compatible hub.pulse shape."""
    kind = getattr(event, "kind", None) or (event.get("kind") if isinstance(event, dict) else None)
    data = getattr(event, "data", None) or (event.get("data") if isinstance(event, dict) else {})
    if not kind:
        return None
    base = {}
    if thread_id:
        base["threadId"] = thread_id
    if msg_id:
        base["msgId"] = msg_id
    if kind == "assistant_message":
        text = str((data or {}).get("text") or "")
        return {"kind": "msg_delta", **base, "chunk": text, "text": text}
    if kind == "status":
        message = str((data or {}).get("message") or "").strip()
        if not message:
            return None
        return {"kind": "thinking_delta", **base, "chunk": f"\n\n**สถานะ provider**\n{message}"}
    if kind == "tool_call":
        run = dict(data or {})
        run.setdefault("status", "running")
        return {"kind": "tool_call", **base, "run": run}
    if kind == "tool_result":
        run = (data or {}).get("run") if isinstance(data, dict) else None
        if not isinstance(run, dict):
            run = dict(data or {})
        return {"kind": "tool_result", **base, "run": run}
    if kind == "error":
        return {"kind": "runtime_error", **base, "message": (data or {}).get("message", "")}
    if kind == "done":
        return {"kind": "runtime_done", **base, **(data or {})}
    return {"kind": "runtime_event", "runtimeKind": kind, **base, **(data or {})}
