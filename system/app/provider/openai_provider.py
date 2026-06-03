"""Responses API provider for OpenAI-compatible GPT routes."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from .base import LLMMessage, LLMResult, LLMStreamEvent, LLMToolCall

OPENAI_EFFORTS = {"low", "medium", "high", "xhigh"}


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"_raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _summary_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("summary") or ""))
        return "\n".join(part.strip() for part in parts if part and part.strip())
    if isinstance(value, dict):
        return str(value.get("text") or value.get("summary") or "").strip()
    return ""


def _sse_data(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class OpenAIResponsesProvider:
    def __init__(self, provider_id: str, base_url: str, api_key: str, timeout: float | None = None):
        self.id = provider_id
        self.live = True
        self.base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}{path}"
        return f"{base}/v1{path}"

    def _provider_label(self) -> str:
        if self.id == "openai":
            return "OpenAI Platform"
        return "OpenAI-compatible"

    async def _bearer_token(self) -> str:
        return self._api_key

    @staticmethod
    def _effort(effort: str) -> str:
        return effort if effort in OPENAI_EFFORTS else "medium"

    @staticmethod
    def _tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        out: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            parameters = tool.get("input_schema") or tool.get("parameters") or {}
            if not isinstance(parameters, dict):
                parameters = {}
            out.append({
                "type": "function",
                "name": name,
                "description": str(tool.get("description") or ""),
                "parameters": parameters,
            })
        return out or None

    @staticmethod
    def _assistant_text_item(text: str) -> dict[str, Any]:
        return {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        }

    @classmethod
    def _input_items(cls, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message.content, str):
                text = message.content.strip()
                if not text:
                    continue
                if message.role == "assistant":
                    out.append(cls._assistant_text_item(text))
                else:
                    out.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
                continue

            blocks = [block for block in message.content if isinstance(block, dict)]
            if message.role == "assistant":
                text_parts: list[str] = []

                def flush_text() -> None:
                    text = "\n".join(part for part in text_parts if part).strip()
                    if text:
                        out.append(cls._assistant_text_item(text))
                    text_parts.clear()

                def append_function_call(block: dict[str, Any]) -> None:
                    name = str(block.get("name") or "").strip()
                    call_id = str(block.get("id") or "").strip()
                    if not name or not call_id:
                        return
                    item = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                        "status": "completed",
                    }
                    out.append(item)

                for block in blocks:
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(str(block.get("text") or ""))
                        continue
                    flush_text()
                    if block_type == "responses_reasoning":
                        item = block.get("item")
                        if isinstance(item, dict) and item.get("type") == "reasoning":
                            out.append(item)
                        continue
                    if block_type == "tool_use":
                        append_function_call(block)
                flush_text()
                continue

            normal_text_parts: list[str] = []
            image_parts: list[dict[str, Any]] = []
            for block in blocks:
                if block.get("type") == "tool_result":
                    call_id = str(block.get("tool_use_id") or "").strip()
                    if call_id:
                        out.append({
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": _stringify(block.get("content", "")),
                        })
                elif block.get("type") == "text":
                    normal_text_parts.append(str(block.get("text") or ""))
                elif block.get("type") == "input_image":
                    image_part: dict[str, Any] = {"type": "input_image"}
                    if block.get("image_url"):
                        image_part["image_url"] = str(block.get("image_url"))
                    if block.get("file_id"):
                        image_part["file_id"] = str(block.get("file_id"))
                    if block.get("detail"):
                        image_part["detail"] = str(block.get("detail"))
                    if image_part.get("image_url") or image_part.get("file_id"):
                        image_parts.append(image_part)
            normal_text = "\n".join(part for part in normal_text_parts if part).strip()
            if normal_text or image_parts:
                content: list[dict[str, Any]] = []
                if normal_text:
                    content.append({"type": "input_text", "text": normal_text})
                content.extend(image_parts)
                out.append({"role": "user", "content": content})
        return out

    def _payload(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        effort: str,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": self._input_items(messages),
            "reasoning": {"effort": self._effort(effort)},
            "store": False,
        }
        mapped_tools = self._tools(tools)
        if mapped_tools:
            payload["tools"] = mapped_tools
            payload["tool_choice"] = "auto"
            payload["include"] = ["reasoning.encrypted_content"]
        return payload

    @staticmethod
    def _empty_output_retry_payload(payload: dict[str, Any]) -> dict[str, Any]:
        instructions = str(payload.get("instructions") or "").strip()
        retry_instruction = (
            "The previous response completed without visible final text. "
            "Produce visible output_text in the final answer; do not finish with only hidden reasoning."
        )
        retry_input = list(payload.get("input") or [])
        retry_input.append({
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": (
                    "The previous response had no visible final answer. "
                    "Return the requested answer now as visible text."
                ),
            }],
        })
        return {
            **payload,
            "instructions": f"{instructions}\n\n{retry_instruction}" if instructions else retry_instruction,
            "input": retry_input,
        }

    @staticmethod
    def _has_empty_visible_output(result: LLMResult) -> bool:
        return not result.tool_calls and result.text.strip() == "(ไม่มีเนื้อหาตอบกลับ)"

    @staticmethod
    def _retryable_request_error(exc: httpx.RequestError) -> bool:
        return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))

    @staticmethod
    def _retryable_http_status(status_code: int) -> bool:
        return status_code in {408, 409, 425, 429} or 500 <= status_code <= 504

    def _request_retry_attempts(self) -> int:
        return 2

    def _request_retry_delay_s(self, attempt: int) -> float:
        return 0.0

    async def _sleep_before_request_retry(self, attempt: int) -> None:
        delay = self._request_retry_delay_s(attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _post_response_with_retry(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
        last_exc: httpx.RequestError | None = None
        attempts = max(self._request_retry_attempts(), 1)
        for attempt in range(attempts):
            try:
                bearer_token = await self._bearer_token()
                resp = await client.post(
                    self._url("/responses"),
                    headers={
                        "Authorization": f"Bearer {bearer_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                if attempt < attempts - 1 and self._retryable_http_status(exc.response.status_code):
                    await self._sleep_before_request_retry(attempt)
                    continue
                raise
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < attempts - 1 and self._retryable_request_error(exc):
                    await self._sleep_before_request_retry(attempt)
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def _token_count_payload(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": self._input_items(messages),
        }
        mapped_tools = self._tools(tools)
        if mapped_tools:
            payload["tools"] = mapped_tools
            payload["tool_choice"] = "auto"
        return payload

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
        payload = self._token_count_payload(
            system=system,
            messages=messages,
            model=model,
            tools=tools,
        )
        try:
            bearer_token = await self._bearer_token()
            resp = await client.post(
                self._url("/responses/input_tokens"),
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:800]
            raise RuntimeError(f"{self._provider_label()} token count error {exc.response.status_code}: {detail}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"{self._provider_label()} token count request failed: {type(exc).__name__}: {exc}") from exc
        tokens = _int_value(resp.json().get("input_tokens"))
        if tokens <= 0:
            raise RuntimeError(f"{self._provider_label()} token count returned no input_tokens")
        return tokens

    @staticmethod
    def _message_content(item: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        text_parts: list[str] = []
        content: list[dict[str, Any]] = []
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"output_text", "text"}:
                text = str(part.get("text") or "")
                if text:
                    text_parts.append(text)
                    content.append({"type": "text", "text": text})
            elif part_type == "refusal":
                text = str(part.get("refusal") or "")
                if text:
                    text_parts.append(text)
                    content.append({"type": "text", "text": text})
        return "".join(text_parts), content

    @staticmethod
    def _reasoning_from_response(data: dict[str, Any], thinking_tokens: int, effort: str) -> tuple[str, str]:
        reasoning_parts: list[str] = []
        reasoning = data.get("reasoning") if isinstance(data.get("reasoning"), dict) else {}
        summary = _summary_text(reasoning.get("summary"))
        if summary:
            reasoning_parts.append(summary)
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "reasoning":
                continue
            item_summary = _summary_text(item.get("summary"))
            if item_summary:
                reasoning_parts.append(item_summary)
        reasoning_text = "\n\n".join(part for part in reasoning_parts if part).strip()
        if effort == "off":
            return reasoning_text, "disabled"
        if reasoning_text:
            return reasoning_text, "available"
        if thinking_tokens:
            return "", "omitted"
        return "", "unavailable"

    def _result_from_response(
        self,
        data: dict[str, Any],
        *,
        fallback_model: str,
        effort: str,
        generation_ms: int,
    ) -> LLMResult:
        output_items = data.get("output") if isinstance(data.get("output"), list) else []
        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        content: list[dict[str, Any]] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                text, message_content = self._message_content(item)
                if text:
                    text_parts.append(text)
                content.extend(message_content)
            elif item_type == "function_call":
                call_id = str(item.get("call_id") or item.get("id") or "")
                name = str(item.get("name") or "")
                if not call_id or not name:
                    continue
                arguments = _json_object(item.get("arguments"))
                tool_calls.append(LLMToolCall(id=call_id, name=name, input=arguments))
                block = {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
                response_item_id = str(item.get("id") or "").strip()
                if response_item_id:
                    block["response_item_id"] = response_item_id
                content.append(block)
            elif item_type == "reasoning":
                content.append({
                    "type": "responses_reasoning",
                    "item": {key: value for key, value in item.items() if value is not None},
                })

        text = "".join(text_parts)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        details = (
            usage.get("output_tokens_details")
            if isinstance(usage.get("output_tokens_details"), dict)
            else usage.get("completion_tokens_details")
            if isinstance(usage.get("completion_tokens_details"), dict)
            else {}
        )
        thinking_tokens = _int_value(details.get("reasoning_tokens") or details.get("thinking_tokens"))
        output_tokens = _int_value(usage.get("output_tokens") or usage.get("completion_tokens"))
        reasoning, reasoning_status = self._reasoning_from_response(data, thinking_tokens, effort)
        status = str(data.get("status") or "completed")
        return LLMResult(
            text=text.strip() or ("" if tool_calls else "(ไม่มีเนื้อหาตอบกลับ)"),
            tokens_in=_int_value(usage.get("input_tokens") or usage.get("prompt_tokens")),
            tokens_out=max(output_tokens - thinking_tokens, 0),
            thinking_tokens=thinking_tokens,
            reasoning=reasoning,
            reasoning_status=reasoning_status,
            generation_ms=generation_ms,
            model=str(data.get("model") or fallback_model),
            provider_id=self.id,
            speed="standard",
            stop_reason="tool_calls" if tool_calls else status,
            content=content,
            tool_calls=tool_calls,
            meta={
                "request_id": data.get("id"),
                "wireApi": "responses",
                "store": False,
            },
        )

    @staticmethod
    def _stream_failure_message(event: dict[str, Any]) -> str:
        error = event.get("error") if isinstance(event.get("error"), dict) else {}
        return str(
            error.get("message")
            or error.get("detail")
            or event.get("message")
            or event.get("detail")
            or "stream failed"
        )

    @staticmethod
    def _response_from_stream_parts(
        *,
        completed: dict[str, Any] | None,
        output_items: list[dict[str, Any]],
        text_parts: list[str],
        fallback_model: str,
    ) -> dict[str, Any]:
        if completed is not None:
            if output_items and not completed.get("output"):
                return {**completed, "output": output_items}
            return completed
        if output_items:
            return {"model": fallback_model, "status": "completed", "output": output_items}
        if text_parts:
            return {
                "model": fallback_model,
                "status": "completed",
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "".join(text_parts)}],
                }],
            }
        raise RuntimeError("stream completed without response output")

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
        requested_effort = self._effort(effort)
        payload = self._payload(
            system=system,
            messages=messages,
            model=model,
            effort=requested_effort,
            tools=tools,
        )
        started = time.perf_counter()
        empty_output_retry = False
        for attempt in range(2):
            request_payload = payload if attempt == 0 else self._empty_output_retry_payload(payload)
            try:
                resp = await self._post_response_with_retry(client, request_payload)
            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.text[:800]
                except Exception:
                    detail = f"status {exc.response.status_code}"
                raise RuntimeError(f"{self._provider_label()} Responses provider error {exc.response.status_code}: {detail}") from exc
            except httpx.RequestError as exc:
                raise RuntimeError(f"{self._provider_label()} Responses provider request failed: {type(exc).__name__}: {exc}") from exc
            generation_ms = int((time.perf_counter() - started) * 1000)
            result = self._result_from_response(
                resp.json(),
                fallback_model=model,
                effort=requested_effort,
                generation_ms=generation_ms,
            )
            if self._has_empty_visible_output(result) and attempt == 0:
                empty_output_retry = True
                continue
            if self._has_empty_visible_output(result):
                raise RuntimeError(f"{self._provider_label()} Responses provider returned empty output")
            if empty_output_retry:
                result.meta["emptyOutputRetry"] = True
            return result
        raise RuntimeError(f"{self._provider_label()} Responses provider returned empty output")

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
        requested_effort = self._effort(effort)
        payload = self._payload(
            system=system,
            messages=messages,
            model=model,
            effort=requested_effort,
            tools=tools,
        )
        started = time.perf_counter()
        empty_output_retry = False
        for attempt in range(2):
            request_payload = payload if attempt == 0 else self._empty_output_retry_payload(payload)
            request_payload = {**request_payload, "stream": True}
            stream_request_retry = False
            request_attempts = max(self._request_retry_attempts(), 1)
            for request_attempt in range(request_attempts):
                completed: dict[str, Any] | None = None
                output_items: list[dict[str, Any]] = []
                text_parts: list[str] = []
                try:
                    bearer_token = await self._bearer_token()
                    async with client.stream(
                        "POST",
                        self._url("/responses"),
                        headers={
                            "Authorization": f"Bearer {bearer_token}",
                            "Content-Type": "application/json",
                        },
                        json=request_payload,
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            event = _sse_data(line)
                            if not event:
                                continue
                            event_type = str(event.get("type") or "")
                            if event_type in {"response.failed", "response.incomplete", "error"}:
                                raise RuntimeError(self._stream_failure_message(event))
                            if event_type == "response.completed" and isinstance(event.get("response"), dict):
                                completed = event["response"]
                                continue
                            if event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
                                output_items.append(event["item"])
                                continue
                            if event_type in {"response.output_text.delta", "response.text.delta"}:
                                delta = event.get("delta")
                                if isinstance(delta, str) and delta:
                                    text_parts.append(delta)
                                    yield LLMStreamEvent(kind="text_delta", text=delta)
                                continue
                            if event_type in {
                                "response.reasoning_summary_text.delta",
                                "response.reasoning_text.delta",
                            }:
                                delta = event.get("delta")
                                if isinstance(delta, str) and delta:
                                    yield LLMStreamEvent(kind="thinking_delta", text=delta)
                    break
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    can_retry_before_output = completed is None and not output_items and not text_parts
                    if (
                        request_attempt < request_attempts - 1
                        and can_retry_before_output
                        and self._retryable_http_status(status_code)
                    ):
                        stream_request_retry = True
                        await self._sleep_before_request_retry(request_attempt)
                        continue
                    try:
                        detail = exc.response.text[:800]
                    except Exception:
                        detail = f"status {status_code}"
                    raise RuntimeError(f"{self._provider_label()} Responses provider error {exc.response.status_code}: {detail}") from exc
                except httpx.RequestError as exc:
                    can_retry_before_output = completed is None and not output_items and not text_parts
                    if (
                        request_attempt < request_attempts - 1
                        and can_retry_before_output
                        and self._retryable_request_error(exc)
                    ):
                        stream_request_retry = True
                        await self._sleep_before_request_retry(request_attempt)
                        continue
                    raise RuntimeError(
                        f"{self._provider_label()} Responses provider request failed: {type(exc).__name__}: {exc}"
                    ) from exc

            generation_ms = int((time.perf_counter() - started) * 1000)
            data = self._response_from_stream_parts(
                completed=completed,
                output_items=output_items,
                text_parts=text_parts,
                fallback_model=model,
            )
            result = self._result_from_response(
                data,
                fallback_model=model,
                effort=requested_effort,
                generation_ms=generation_ms,
            )
            if self._has_empty_visible_output(result) and attempt == 0:
                empty_output_retry = True
                continue
            if self._has_empty_visible_output(result):
                raise RuntimeError(f"{self._provider_label()} Responses provider returned empty output")
            result.meta["stream"] = True
            if stream_request_retry:
                result.meta["streamRequestRetry"] = True
            if empty_output_retry:
                result.meta["emptyOutputRetry"] = True
            yield LLMStreamEvent(kind="message_stop", result=result)
            return
        raise RuntimeError(f"{self._provider_label()} Responses provider returned empty output")
