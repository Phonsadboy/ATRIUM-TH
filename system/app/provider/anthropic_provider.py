"""Real Claude provider via the official Anthropic SDK.

Constructs an AsyncAnthropic client with `auth_token` + `base_url`. Follows the
claude-api guidance: adaptive thinking + `output_config.effort`, prompt caching
on the stable system prefix, and no app-level short-output cap. The SDK is an
optional dependency — import is lazy so the package installs but live chat/engine
work requires it.
"""
from __future__ import annotations

import re
import time
from typing import Any

from ..catalog import MODELS, coerce_model_speed
from .base import LLMMessage, LLMResult, LLMStreamEvent, LLMToolCall

FAST_MODE_BETA = "fast-mode-2026-02-01"
DEFAULT_ANTHROPIC_OUTPUT_CEILING = 64_000


class AnthropicProvider:
    def __init__(self, provider_id: str, base_url: str, auth_token: str, timeout: float | None = None):
        self.id = provider_id
        self.live = True
        self.base_url = base_url
        self._auth_token = auth_token
        self._timeout = timeout
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic  # lazy: optional dependency
        except ImportError as e:  # pragma: no cover - exercised only when extra missing
            raise RuntimeError(
                "anthropic SDK not installed — run `pip install -e '.[live]'`"
            ) from e
        self._client = anthropic.AsyncAnthropic(
            auth_token=self._auth_token,
            base_url=self.base_url,
            # A bounded SDK/httpx response timeout keeps one slow upstream
            # request from monopolizing the durable job worker.
            timeout=self._timeout,
            max_retries=2,
        )
        return self._client

    @staticmethod
    def _thinking(effort: str) -> dict:
        # Opus 4.8 / 4.7: adaptive thinking only; 'off' disables it.
        if effort == "off":
            return {"type": "disabled"}
        return {"type": "adaptive", "display": "summarized"}

    @staticmethod
    def _provider_required_output_ceiling(model: str) -> int:
        tier = str((MODELS.get(model) or {}).get("tier") or "").strip()
        if tier == "opus":
            return 128_000
        return DEFAULT_ANTHROPIC_OUTPUT_CEILING

    def _message_kwargs(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str,
        speed: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict:
        # Cache the (stable) system prefix; harmless when below the cache minimum.
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        kwargs: dict = {
            "model": model,
            # Anthropic-compatible Messages requires this field. Use the model
            # ceiling so ATRIUM does not impose its old short per-turn caps.
            "max_tokens": self._provider_required_output_ceiling(model),
            "system": system_blocks,
            "messages": [{"role": m.role, "content": self._normalize_message_content(m.content)} for m in messages],
            "thinking": self._thinking(effort),
        }
        if tools:
            kwargs["tools"] = tools
        if coerce_model_speed(model, speed) == "fast":
            kwargs["speed"] = "fast"
            kwargs["betas"] = [FAST_MODE_BETA]
        # effort lives under output_config; pass via extra_body for SDK-version robustness.
        if effort != "off":
            kwargs["extra_body"] = {"output_config": {"effort": effort}}
        return kwargs

    def _count_tokens_kwargs(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str,
        speed: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs = self._message_kwargs(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            speed=speed,
            tools=tools,
        )
        kwargs.pop("max_tokens", None)
        extra_body = kwargs.pop("extra_body", None)
        if isinstance(extra_body, dict) and isinstance(extra_body.get("output_config"), dict):
            kwargs["output_config"] = extra_body["output_config"]
        return kwargs

    async def count_context_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        client = self._ensure_client()
        requested_speed = coerce_model_speed(model, speed)
        kwargs = self._count_tokens_kwargs(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            speed=requested_speed,
            tools=tools,
        )
        attempts: list[tuple[dict[str, Any], str, bool, bool]] = [(kwargs, requested_speed, False, False)]
        last_exc: Exception | None = None
        while attempts:
            attempt_kwargs, attempt_speed, display_downgraded, fast_downgraded = attempts.pop(0)
            try:
                counter = client.beta.messages if attempt_speed == "fast" else client.messages
                count = await counter.count_tokens(**attempt_kwargs)
                tokens = self._int_attr(count, "input_tokens")
                if tokens <= 0:
                    raise RuntimeError("Anthropic token count returned no input_tokens")
                return tokens
            except Exception as exc:
                last_exc = exc
                if self._should_retry_without_display(exc) and not display_downgraded:
                    attempts.insert(0, (
                        self._without_thinking_display(attempt_kwargs),
                        attempt_speed,
                        True,
                        fast_downgraded,
                    ))
                    continue
                if attempt_speed == "fast" and self._should_retry_without_fast_mode(exc):
                    attempts.insert(0, (
                        self._without_fast_mode(attempt_kwargs),
                        "standard",
                        display_downgraded,
                        True,
                    ))
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _normalize_message_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        out: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "input_image":
                out.append(block)
                continue
            image_url = str(block.get("image_url") or "")
            match = re.match(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", image_url, re.DOTALL)
            if match:
                out.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": match.group(1),
                        "data": match.group(2),
                    },
                })
            elif block.get("file_id"):
                out.append({"type": "text", "text": f"[image file id: {block.get('file_id')}]"})
        return out

    @staticmethod
    def _without_fast_mode(kwargs: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in kwargs.items() if k not in {"speed", "betas"}}

    @staticmethod
    def _without_thinking_display(kwargs: dict[str, Any]) -> dict[str, Any]:
        thinking = kwargs.get("thinking")
        if not isinstance(thinking, dict) or "display" not in thinking:
            return kwargs
        return {**kwargs, "thinking": {k: v for k, v in thinking.items() if k != "display"}}

    @staticmethod
    def _should_retry_without_display(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        text = str(exc).lower()
        return status_code == 400 and "display" in text and "thinking" in text

    @staticmethod
    def _should_retry_without_fast_mode(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        text = str(exc).lower()
        fast_markers = ("fast", "speed", "beta", FAST_MODE_BETA, "rate limit", "rate_limit", "unsupported")
        return status_code in {400, 404, 422, 429} and any(marker in text for marker in fast_markers)

    @staticmethod
    async def _create_message(client, kwargs: dict[str, Any], speed: str):
        if speed == "fast":
            return await client.beta.messages.create(**kwargs)
        return await client.messages.create(**kwargs)

    @staticmethod
    def _block_dict(block) -> dict[str, Any]:
        if hasattr(block, "model_dump"):
            return block.model_dump(exclude_none=True)
        if isinstance(block, dict):
            return {k: v for k, v in block.items() if v is not None}
        block_type = getattr(block, "type", "")
        if block_type == "text":
            return {"type": "text", "text": getattr(block, "text", "")}
        if block_type == "thinking":
            out = {"type": "thinking", "thinking": getattr(block, "thinking", "")}
            signature = getattr(block, "signature", None)
            if signature:
                out["signature"] = signature
            return out
        if block_type == "redacted_thinking":
            return {"type": "redacted_thinking", "data": getattr(block, "data", "")}
        if block_type == "tool_use":
            return {
                "type": "tool_use",
                "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": getattr(block, "input", {}) or {},
            }
        return {"type": block_type}

    @staticmethod
    def _int_attr(obj: Any, name: str) -> int:
        if isinstance(obj, dict):
            value = obj.get(name, 0)
        else:
            value = getattr(obj, name, 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _thinking_tokens_from_usage(cls, usage: Any) -> int:
        details = usage.get("output_tokens_details") if isinstance(usage, dict) else getattr(usage, "output_tokens_details", None)
        if details is None:
            return cls._int_attr(usage, "thinking_tokens")
        return cls._int_attr(details, "thinking_tokens")

    @staticmethod
    def _reasoning_from_content(content: list[dict[str, Any]], effort: str) -> tuple[str, str, bool]:
        thinking_parts = [
            str(block.get("thinking") or "").strip()
            for block in content
            if block.get("type") == "thinking" and str(block.get("thinking") or "").strip()
        ]
        reasoning = "\n\n".join(thinking_parts).strip()
        saw_thinking = any(block.get("type") == "thinking" for block in content)
        saw_redacted = any(block.get("type") == "redacted_thinking" for block in content)
        if effort == "off":
            status = "disabled"
        elif reasoning:
            status = "available"
        elif saw_redacted:
            status = "redacted"
        elif saw_thinking:
            status = "omitted"
        else:
            status = "unavailable"
        return reasoning, status, saw_redacted

    def _result_from_response(
        self,
        resp,
        *,
        fallback_model: str,
        effort: str,
        speed: str,
        requested_speed: str | None = None,
        request_id: str | None = None,
        generation_ms: int = 0,
    ) -> LLMResult:
        content = [self._block_dict(block) for block in resp.content]
        text = "".join(str(block.get("text", "")) for block in content if block.get("type") == "text")
        reasoning, reasoning_status, redacted_thinking = self._reasoning_from_content(content, effort)
        tool_calls = [
            LLMToolCall(
                id=str(block.get("id") or ""),
                name=str(block.get("name") or ""),
                input=block.get("input") if isinstance(block.get("input"), dict) else {},
            )
            for block in content
            if block.get("type") == "tool_use" and block.get("id") and block.get("name")
        ]
        usage = resp.usage
        tokens_in = (
            self._int_attr(usage, "input_tokens")
            + self._int_attr(usage, "cache_read_input_tokens")
            + self._int_attr(usage, "cache_creation_input_tokens")
        )
        output_tokens = self._int_attr(usage, "output_tokens")
        thinking_tokens = self._thinking_tokens_from_usage(usage)
        stop_reason = getattr(resp, "stop_reason", "end_turn") or "end_turn"
        return LLMResult(
            text=text.strip() or ("" if tool_calls else "(ไม่มีเนื้อหาตอบกลับ)"),
            tokens_in=tokens_in,
            tokens_out=max(output_tokens - thinking_tokens, 0),
            thinking_tokens=thinking_tokens,
            reasoning=reasoning,
            reasoning_status=reasoning_status,
            generation_ms=generation_ms,
            model=resp.model or fallback_model,
            provider_id=self.id,
            speed=speed,
            stop_reason=stop_reason,
            content=content,
            tool_calls=tool_calls,
            meta={
                "cache_read": self._int_attr(usage, "cache_read_input_tokens"),
                "cache_write": self._int_attr(usage, "cache_creation_input_tokens"),
                "request_id": request_id if request_id is not None else getattr(resp, "_request_id", None),
                "redactedThinking": redacted_thinking,
                "requestedSpeed": requested_speed or speed,
                "actualSpeed": speed,
            },
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResult:
        client = self._ensure_client()
        requested_speed = coerce_model_speed(model, speed)
        kwargs = self._message_kwargs(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            speed=requested_speed,
            tools=tools,
        )
        started = time.perf_counter()
        attempts: list[tuple[dict[str, Any], str, bool, bool]] = [(kwargs, requested_speed, False, False)]
        last_exc: Exception | None = None
        while attempts:
            attempt_kwargs, attempt_speed, display_downgraded, fast_downgraded = attempts.pop(0)
            try:
                resp = await self._create_message(client, attempt_kwargs, attempt_speed)
                break
            except Exception as exc:
                last_exc = exc
                if self._should_retry_without_display(exc) and not display_downgraded:
                    attempts.insert(0, (
                        self._without_thinking_display(attempt_kwargs),
                        attempt_speed,
                        True,
                        fast_downgraded,
                    ))
                    continue
                if attempt_speed == "fast" and self._should_retry_without_fast_mode(exc):
                    attempts.insert(0, (
                        self._without_fast_mode(attempt_kwargs),
                        "standard",
                        display_downgraded,
                        True,
                    ))
                    continue
                raise
        else:
            assert last_exc is not None
            raise last_exc
        generation_ms = int((time.perf_counter() - started) * 1000)
        actual_speed = attempt_speed
        result = self._result_from_response(
            resp,
            fallback_model=model,
            effort=effort,
            speed=actual_speed,
            requested_speed=requested_speed,
            generation_ms=generation_ms,
        )
        if display_downgraded:
            result.meta["reasoningDisplayDowngraded"] = True
        if fast_downgraded:
            result.meta["fastModeDowngraded"] = True
        return result

    async def stream_complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ):
        client = self._ensure_client()
        requested_speed = coerce_model_speed(model, speed)
        kwargs = self._message_kwargs(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            speed=requested_speed,
            tools=tools,
        )
        started = time.perf_counter()

        async def run_stream(
            stream_kwargs: dict[str, Any],
            *,
            attempt_speed: str,
            display_downgraded: bool,
            fast_downgraded: bool,
        ):
            stream_messages = client.beta.messages if attempt_speed == "fast" else client.messages
            async with stream_messages.stream(**stream_kwargs) as stream:
                async for event in stream:
                    if getattr(event, "type", "") != "content_block_delta":
                        continue
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "text_delta":
                        text = getattr(delta, "text", "")
                        if text:
                            yield LLMStreamEvent(kind="text_delta", text=text)
                    elif delta_type == "thinking_delta":
                        thinking = getattr(delta, "thinking", "")
                        if thinking:
                            yield LLMStreamEvent(kind="thinking_delta", text=thinking)

                result = self._result_from_response(
                    stream.current_message_snapshot,
                    fallback_model=model,
                    effort=effort,
                    speed=attempt_speed,
                    requested_speed=requested_speed,
                    request_id=stream.request_id,
                    generation_ms=int((time.perf_counter() - started) * 1000),
                )
                if display_downgraded:
                    result.meta["reasoningDisplayDowngraded"] = True
                if fast_downgraded:
                    result.meta["fastModeDowngraded"] = True
                yield LLMStreamEvent(kind="message_stop", result=result)

        streamed_any = False
        attempts: list[tuple[dict[str, Any], str, bool, bool]] = [(kwargs, requested_speed, False, False)]
        while attempts:
            attempt_kwargs, attempt_speed, display_downgraded, fast_downgraded = attempts.pop(0)
            try:
                async for item in run_stream(
                    attempt_kwargs,
                    attempt_speed=attempt_speed,
                    display_downgraded=display_downgraded,
                    fast_downgraded=fast_downgraded,
                ):
                    if item.text:
                        streamed_any = True
                    yield item
                return
            except Exception as exc:
                if streamed_any:
                    raise
                if self._should_retry_without_display(exc) and not display_downgraded:
                    attempts.insert(0, (
                        self._without_thinking_display(attempt_kwargs),
                        attempt_speed,
                        True,
                        fast_downgraded,
                    ))
                    continue
                if attempt_speed == "fast" and self._should_retry_without_fast_mode(exc):
                    attempts.insert(0, (
                        self._without_fast_mode(attempt_kwargs),
                        "standard",
                        display_downgraded,
                        True,
                    ))
                    continue
                raise
