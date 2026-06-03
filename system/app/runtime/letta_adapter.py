"""Letta / MemGPT runtime adapter (first default for ATRIUM v2)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

import httpx

from ..catalog import DEFAULT_MODEL, MODELS, PROVIDERS, normalize_ai_config
from ..clock import now_ms
from ..config import Settings, get_settings
from ..provider.base import LLMMessage, LLMResult, LLMToolCall
from .base import AgentRuntimeConfig, RuntimeEvent

logger = logging.getLogger(__name__)


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class LettaRuntimeAdapter:
    backend = "letta"
    provider_retry_count = 3
    provider_retry_delays_s = (1.0, 2.0, 4.0)

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.letta_base_url.rstrip("/")
        self.timeout = self.settings.letta_request_timeout_s
        self._agent_ids: dict[str, str] = {}
        self._agent_departments: dict[str, dict[str, Any]] = {}
        self._registered_tools: dict[str, dict[str, Any]] = {}
        self._responses_threads: dict[tuple[str, str], list[LLMMessage]] = {}
        self._responses_thread_tools: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._responses_pending_tool_calls: dict[tuple[str, str], dict[str, LLMToolCall]] = {}

    def _client(self, *, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/v1",
            timeout=self.timeout if timeout is None else timeout,
            follow_redirects=True,
        )

    def _model_handle(self, model: str) -> str:
        model = str(model or "").strip()
        if not model:
            return "letta/letta-free"
        if "/" in model:
            return model
        if model in MODELS:
            # ATRIUM executes catalog model turns through its provider registry.
            # Letta owns agent identity/core memory only, so it must not depend
            # on a separate Claude/GPT credential for message or memory calls.
            return "letta/letta-free"
        return f"anthropic/{model}"

    def model_handle(self, model: str) -> str:
        return self._model_handle(model)

    def bind_department(self, agent_key: str, dept: dict[str, Any]) -> None:
        """Bind ATRIUM department routing metadata for runtime-owned turns."""
        self._agent_departments[str(agent_key)] = dict(dept)

    def _app_provider_id(self, agent_key: str) -> str | None:
        dept = self._agent_departments.get(agent_key) or {}
        provider_id = str(dept.get("providerId") or "claude_code").strip()
        return provider_id if provider_id in PROVIDERS else None

    def _provider_fallback_id(self, provider_id: str) -> str | None:
        if provider_id in {"openai", "chatgpt_account"}:
            return "anthropic" if self.settings.anthropic_auth_token else None
        return "openai" if self.settings.openai_api_key else None

    @staticmethod
    def _is_retryable_provider_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 429, 500, 502, 503, 504}:
            return True
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                " 401",
                " 429",
                " 500",
                " 502",
                " 503",
                " 504",
                "authenticationerror",
                "unauthorized",
                "invalid bearer",
                "invalid api key",
                "bad gateway",
                "cloudflare",
                "empty output",
                "no output",
                "overloaded",
                "rate limit",
                "timeout",
                "temporarily unavailable",
            )
        )

    @staticmethod
    async def _emit_provider_status(
        status_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        message: str,
        **data: Any,
    ) -> None:
        if status_callback is None:
            return
        try:
            await status_callback({"message": message, **data})
        except Exception as exc:
            logger.warning("Provider status callback failed: %s", exc)

    async def health(self) -> dict[str, Any]:
        try:
            async with self._client(timeout=max(1.0, min(float(self.timeout), 5.0))) as client:
                resp = await client.get("/health")
                if resp.status_code == 404:
                    resp = await client.get("/agents", params={"limit": 1})
                ok = resp.status_code < 500
                return {
                    "ok": ok,
                    "backend": self.backend,
                    "baseUrl": self.base_url,
                    "statusCode": resp.status_code,
                    "degraded": not ok,
                }
        except Exception as exc:
            return {
                "ok": False,
                "backend": self.backend,
                "baseUrl": self.base_url,
                "degraded": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def create_agent(self, config: AgentRuntimeConfig) -> dict[str, Any]:
        blocks = []
        if config.owner_profile.strip():
            blocks.append({"label": "human", "value": config.owner_profile.strip()})
        persona = config.persona.strip() or config.charter.strip() or "ATRIUM agent"
        blocks.append({"label": "persona", "value": persona})
        if config.company_memory.strip():
            blocks.append({"label": "company", "value": config.company_memory.strip()})
        payload: dict[str, Any] = {
            "name": config.agent_key,
            "model": self._model_handle(config.model),
            "memory_blocks": blocks,
            "metadata": {"atriumAgentKey": config.agent_key, **config.metadata},
        }
        if config.tools:
            payload["tools"] = config.tools
        embedding = self.settings.letta_embedding_model.strip()
        if embedding:
            payload["embedding"] = embedding
        async with self._client() as client:
            resp = await client.post("/agents/", json=payload)
            resp.raise_for_status()
            data = resp.json()
        agent_id = str(data.get("id") or data.get("agent_id") or "")
        if agent_id:
            self._agent_ids[config.agent_key] = agent_id
        return {"agentKey": config.agent_key, "runtimeAgentId": agent_id, "modelHandle": payload["model"], "raw": data}

    async def get_agent(self, agent_key: str) -> dict[str, Any] | None:
        runtime_id = self._agent_ids.get(agent_key)
        if not runtime_id:
            return None
        async with self._client() as client:
            resp = await client.get(f"/agents/{runtime_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def update_agent_model(self, agent_key: str, model: str) -> dict[str, Any]:
        runtime_id = self._runtime_agent_id(agent_key)
        model_handle = self._model_handle(model)
        async with self._client() as client:
            resp = await client.patch(f"/agents/{runtime_id}", json={"model": model_handle})
            resp.raise_for_status()
            data = resp.json()
        return {
            "ok": True,
            "agentKey": agent_key,
            "runtimeAgentId": runtime_id,
            "modelHandle": data.get("model") or model_handle,
            "raw": data,
        }

    def _runtime_agent_id(self, agent_key: str) -> str:
        runtime_id = self._agent_ids.get(agent_key)
        if not runtime_id:
            raise ValueError(f"runtime agent not provisioned: {agent_key}")
        return runtime_id

    async def send_message(
        self,
        agent_key: str,
        *,
        message: str = "",
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_tools: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
        status_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        provider_id = self._app_provider_id(agent_key)
        if provider_id is not None:
            return await self._send_provider_message(
                agent_key,
                provider_id=provider_id,
                message=message,
                thread_id=thread_id,
                metadata=metadata,
                client_tools=client_tools,
                messages=messages,
                max_steps=max_steps,
                status_callback=status_callback,
            )
        runtime_id = self._runtime_agent_id(agent_key)
        body: dict[str, Any] = {}
        if messages is not None:
            body["messages"] = messages
        else:
            body["input"] = message
        if client_tools:
            body["client_tools"] = client_tools
        if max_steps is not None:
            body["max_steps"] = max_steps
        if thread_id:
            body["conversation_id"] = thread_id
        if metadata:
            body["metadata"] = metadata
        async with self._client() as client:
            resp = await client.post(f"/agents/{runtime_id}/messages", json=body)
            resp.raise_for_status()
            return resp.json()

    def _responses_thread_key(self, agent_key: str, thread_id: str | None) -> tuple[str, str]:
        return (agent_key, str(thread_id or "__default__"))

    def _responses_system_prompt(self, agent_key: str) -> str:
        dept = self._agent_departments.get(agent_key) or {}
        return (
            "You are the LLM loop for a Letta-bound ATRIUM runtime agent. "
            "The user message contains the runtime prompt, including system instructions, "
            "conversation history, and Letta recall context. Follow it exactly, use supplied "
            "tools when required, and answer in the requested language. The agent remains "
            f"bound to Letta agent key {agent_key!r}."
            + (f" Department: {dept.get('id')}." if dept.get("id") else "")
        )

    @staticmethod
    def _tool_return_messages(
        messages: list[dict[str, Any]] | None,
    ) -> tuple[list[LLMMessage], set[str], dict[str, LLMToolCall]]:
        out: list[LLMMessage] = []
        returned_call_ids: set[str] = set()
        returned_calls: dict[str, LLMToolCall] = {}
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            if message.get("type") == "tool_return":
                blocks: list[dict[str, Any]] = []
                for item in message.get("tool_returns") or []:
                    if not isinstance(item, dict):
                        continue
                    call_id = str(item.get("tool_call_id") or item.get("toolUseId") or item.get("id") or "").strip()
                    if not call_id:
                        continue
                    returned_call_ids.add(call_id)
                    name = str(item.get("name") or item.get("tool_name") or "").strip()
                    if name:
                        returned_calls[call_id] = LLMToolCall(
                            id=call_id,
                            name=name,
                            input=_parse_tool_args(item.get("arguments") or item.get("input") or item.get("args")),
                        )
                    content = item.get("tool_return")
                    if content is None:
                        content = item.get("content") if item.get("content") is not None else item.get("output")
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str),
                    })
                if blocks:
                    out.append(LLMMessage(role="user", content=blocks))
                continue
            if message.get("role") in {"user", "assistant"} and message.get("content"):
                role = "assistant" if message.get("role") == "assistant" else "user"
                out.append(LLMMessage(role=role, content=str(message.get("content") or "")))
        return out, returned_call_ids, returned_calls

    @staticmethod
    def _history_tool_call_ids(history: list[LLMMessage]) -> set[str]:
        out: set[str] = set()
        for message in history:
            if message.role != "assistant" or not isinstance(message.content, list):
                continue
            for block in message.content:
                if not isinstance(block, dict) or block.get("type") not in {"tool_use", "function_call"}:
                    continue
                call_id = str(block.get("id") or block.get("call_id") or "").strip()
                if call_id:
                    out.add(call_id)
        return out

    @staticmethod
    def _assistant_tool_context_message(tool_calls: list[LLMToolCall]) -> LLMMessage | None:
        blocks = [
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
            for call in tool_calls
            if call.id and call.name
        ]
        if not blocks:
            return None
        return LLMMessage(role="assistant", content=blocks)

    def _missing_tool_context_messages(
        self,
        history: list[LLMMessage],
        returned_call_ids: set[str],
        pending_calls: dict[str, LLMToolCall],
        returned_calls: dict[str, LLMToolCall],
    ) -> list[LLMMessage]:
        existing = self._history_tool_call_ids(history)
        missing_calls: list[LLMToolCall] = []
        for call_id in sorted(returned_call_ids):
            if call_id in existing:
                continue
            call = pending_calls.get(call_id) or returned_calls.get(call_id)
            if call is not None:
                missing_calls.append(call)
        message = self._assistant_tool_context_message(missing_calls)
        return [message] if message is not None else []

    @staticmethod
    def _responses_usage(result: LLMResult) -> dict[str, Any]:
        return {
            "prompt_tokens": result.tokens_in,
            "completion_tokens": result.tokens_out + result.thinking_tokens,
            "output_tokens_details": {"reasoning_tokens": result.thinking_tokens},
        }

    @staticmethod
    def _responses_runtime_payload(result: LLMResult, *, archived: bool | None = None) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if result.text.strip():
            messages.append({
                "message_type": "assistant_message",
                "content": result.text.strip(),
            })
        if result.tool_calls:
            messages.append({
                "message_type": "tool_call_message",
                "tool_calls": [
                    {
                        "id": call.id,
                        "tool_call_id": call.id,
                        "name": call.name,
                        "arguments": call.input,
                    }
                    for call in result.tool_calls
                ],
            })
        payload = {
            "messages": messages,
            "usage": LettaRuntimeAdapter._responses_usage(result),
            "runtimeProviderId": result.provider_id,
            "runtimeWireApi": result.meta.get("wireApi"),
            "runtimeModel": result.model,
            "runtimeStopReason": result.stop_reason,
            "reasoningStatus": result.reasoning_status,
        }
        if result.meta.get("providerFallbackFrom"):
            payload["runtimeProviderFallbackFrom"] = result.meta.get("providerFallbackFrom")
            payload["runtimeProviderFallbackTo"] = result.meta.get("providerFallbackTo")
            payload["runtimeProviderFallbackError"] = result.meta.get("providerFallbackError")
        if "providerRetryCount" in result.meta:
            payload["runtimeProviderRetryCount"] = result.meta.get("providerRetryCount")
        if archived is not None:
            payload["runtimeMemoryArchived"] = archived
        return payload

    async def _archive_responses_turn(
        self,
        agent_key: str,
        *,
        thread_id: str | None,
        prompt: str,
        result: LLMResult,
    ) -> bool:
        text = result.text.strip()
        if not text:
            return False
        runtime_id = self._runtime_agent_id(agent_key)
        passage = (
            "ATRIUM Responses runtime turn\n"
            f"thread={thread_id or ''}\n"
            f"model={result.model}\n\n"
            "Input:\n"
            f"{prompt[:4000]}\n\n"
            "Assistant:\n"
            f"{text[:4000]}"
        )
        tags = ["atrium", "runtime", "responses", f"agent:{agent_key}"]
        if thread_id:
            tags.append(f"thread:{thread_id}")
        try:
            async with self._client(timeout=max(1.0, min(float(self.timeout), 10.0))) as client:
                resp = await client.post(
                    f"/agents/{runtime_id}/archival-memory",
                    json={"text": passage, "tags": tags},
                )
                resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Responses runtime archival memory write failed for %s: %s", agent_key, exc)
            return False

    async def _send_provider_message(
        self,
        agent_key: str,
        *,
        provider_id: str,
        message: str = "",
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_tools: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
        status_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        from ..provider.registry import get_provider

        dept = self._agent_departments.get(agent_key) or {}
        model = str(dept.get("model") or DEFAULT_MODEL)
        effort = str(dept.get("thinkingEffort") or "high")
        speed = str(dept.get("speed") or "standard")
        provider_id, model, effort = normalize_ai_config(provider_id, model, effort)
        thread_key = self._responses_thread_key(agent_key, thread_id)
        if messages is None:
            history = [LLMMessage(role="user", content=message)]
            self._responses_threads[thread_key] = history
            self._responses_thread_tools[thread_key] = list(client_tools or [])
            self._responses_pending_tool_calls.pop(thread_key, None)
        else:
            history = self._responses_threads.get(thread_key)
            if history is None:
                history = [LLMMessage(role="user", content=message or "Continue the runtime turn.")]
                self._responses_threads[thread_key] = history
            tool_messages, returned_call_ids, returned_calls = self._tool_return_messages(messages)
            pending_calls = self._responses_pending_tool_calls.get(thread_key) or {}
            history.extend(self._missing_tool_context_messages(history, returned_call_ids, pending_calls, returned_calls))
            history.extend(tool_messages)
        tools = client_tools or self._responses_thread_tools.get(thread_key) or None
        total_retries = int(self.provider_retry_count)
        delays = tuple(self.provider_retry_delays_s)
        retry_count = 0
        last_exc: Exception | None = None

        async def complete_with(provider_to_use: str, model_to_use: str, effort_to_use: str) -> LLMResult:
            provider = get_provider(provider_to_use, self.settings)
            return await provider.complete(
                system=self._responses_system_prompt(agent_key),
                messages=history,
                model=model_to_use,
                effort=effort_to_use,
                speed=speed,
                tools=tools,
            )

        result: LLMResult | None = None
        for attempt in range(total_retries + 1):
            try:
                result = await complete_with(provider_id, model, effort)
                break
            except Exception as exc:
                if not self._is_retryable_provider_error(exc):
                    raise
                last_exc = exc
                if attempt >= total_retries:
                    break
                retry_count = attempt + 1
                await self._emit_provider_status(
                    status_callback,
                    f"provider หลัก {provider_id} ล้มเหลว ({type(exc).__name__}) กำลังลองใหม่ {retry_count}/{total_retries}",
                    stage="retry",
                    providerId=provider_id,
                    model=model,
                    attempt=attempt + 1,
                    retry=retry_count,
                    maxRetries=total_retries,
                    errorType=type(exc).__name__,
                )
                delay = delays[attempt] if attempt < len(delays) else delays[-1] if delays else 0
                if delay > 0:
                    await asyncio.sleep(delay)

        if result is None:
            fallback_id = (
                self._provider_fallback_id(provider_id)
                if self.settings.runtime_provider_fallback_enabled
                else None
            )
            if not fallback_id or last_exc is None:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(f"provider {provider_id} returned no result")
            fallback_id, fallback_model, fallback_effort = normalize_ai_config(fallback_id, model, effort)
            await self._emit_provider_status(
                status_callback,
                f"provider หลัก {provider_id} ยังไม่สำเร็จหลังลองใหม่ {total_retries} ครั้ง กำลัง fallback ไป {fallback_id}",
                stage="fallback",
                providerId=provider_id,
                fallbackProviderId=fallback_id,
                model=model,
                fallbackModel=fallback_model,
                retryCount=retry_count,
                maxRetries=total_retries,
            )
            try:
                result = await complete_with(fallback_id, fallback_model, fallback_effort)
            except Exception as fallback_exc:
                await self._emit_provider_status(
                    status_callback,
                    f"fallback ไป {fallback_id} ไม่สำเร็จ ({type(fallback_exc).__name__})",
                    stage="fallback_failed",
                    providerId=provider_id,
                    fallbackProviderId=fallback_id,
                    errorType=type(fallback_exc).__name__,
                )
                raise
            result.meta["providerFallbackFrom"] = provider_id
            result.meta["providerFallbackTo"] = fallback_id
            result.meta["providerFallbackError"] = f"{type(last_exc).__name__}: {str(last_exc)[:360]}"
            await self._emit_provider_status(
                status_callback,
                f"fallback ไป {fallback_id} สำเร็จ",
                stage="fallback_succeeded",
                providerId=provider_id,
                fallbackProviderId=fallback_id,
            )
        if retry_count:
            result.meta["providerRetryCount"] = retry_count
        if result.content:
            history.append(LLMMessage(role="assistant", content=result.content))
        elif result.text:
            history.append(LLMMessage(role="assistant", content=result.text))
        if result.tool_calls:
            pending_calls = self._responses_pending_tool_calls.setdefault(thread_key, {})
            for call in result.tool_calls:
                pending_calls[call.id] = call
            return self._responses_runtime_payload(result)
        original_prompt = ""
        for item in self._responses_threads.get(thread_key) or []:
            if item.role == "user" and isinstance(item.content, str):
                original_prompt = item.content
                break
        archived = await self._archive_responses_turn(
            agent_key,
            thread_id=thread_id,
            prompt=original_prompt or message,
            result=result,
        )
        self._responses_threads.pop(thread_key, None)
        self._responses_thread_tools.pop(thread_key, None)
        self._responses_pending_tool_calls.pop(thread_key, None)
        return self._responses_runtime_payload(result, archived=archived)

    async def stream_events(
        self,
        agent_key: str,
        *,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_tools: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        try:
            result = await self.send_message(
                agent_key,
                message=message,
                thread_id=thread_id,
                metadata=metadata,
                client_tools=client_tools,
                messages=messages,
                max_steps=max_steps,
            )
            text = _extract_assistant_text(result)
            if text:
                yield RuntimeEvent("assistant_message", {"text": text})
            yield RuntimeEvent("usage", {"raw": result})
            yield RuntimeEvent("done", {})
        except Exception as exc:
            yield RuntimeEvent("error", {"message": f"{type(exc).__name__}: {exc}"})
            yield RuntimeEvent("done", {"failed": True})

    async def register_tool(self, name: str, schema: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(name or "").strip()
        if not tool_name:
            raise ValueError("runtime tool name is required")
        parameters = _tool_parameters(schema)
        client_tool = {
            "name": tool_name,
            "description": str(schema.get("description") or schema.get("summary") or ""),
            "parameters": parameters,
        }
        self._registered_tools[tool_name] = client_tool
        return {
            "ok": True,
            "tool": tool_name,
            "backend": self.backend,
            "registered": True,
            "mode": "client_tool",
            "clientTool": client_tool,
            "note": (
                "Registered as a Letta client tool. ATRIUM remains the executor "
                "and runs calls through /api/tools/run for audit, checkpoint, "
                "kill-switch, and rollback metadata."
            ),
        }

    async def update_memory(self, agent_key: str, *, label: str, value: str) -> dict[str, Any]:
        runtime_id = self._runtime_agent_id(agent_key)
        async with self._client() as client:
            resp = await client.patch(
                f"/agents/{runtime_id}/core-memory/blocks/{label}",
                json={"value": value},
            )
            if resp.status_code == 404:
                resp = await client.post(
                    f"/agents/{runtime_id}/core-memory/blocks",
                    json={"label": label, "value": value},
                )
            resp.raise_for_status()
            return resp.json()

    async def recall(self, agent_key: str, *, query: str, limit: int = 8) -> list[dict[str, Any]]:
        runtime_id = self._runtime_agent_id(agent_key)
        async with self._client() as client:
            resp = await client.get(
                f"/agents/{runtime_id}/archival-memory/search",
                params={"query": query, "limit": limit},
            )
            if resp.status_code in {404, 405}:
                resp = await client.post(
                    f"/agents/{runtime_id}/archival-memory/search",
                    json={"query": query, "limit": limit},
                )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            return data["results"]
        return []

    async def checkpoint(self, agent_key: str, *, reason: str) -> dict[str, Any]:
        runtime_id = self._runtime_agent_id(agent_key)
        snapshot: dict[str, Any] | None = None
        try:
            snapshot = await self.get_agent(agent_key)
        except Exception as exc:
            logger.warning("Letta checkpoint snapshot fetch failed for %s: %s", agent_key, exc)
        return {
            "ok": True,
            "backend": self.backend,
            "agentKey": agent_key,
            "runtimeAgentId": runtime_id,
            "reason": reason,
            "ts": now_ms(),
            "snapshot": snapshot,
            "snapshotAvailable": snapshot is not None,
            "note": "ATRIUM persisted this runtime checkpoint for regression/rollback evidence",
        }


def _extract_assistant_text(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, list):
        parts: list[str] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            if item.get("role") == "assistant" and item.get("content"):
                parts.append(str(item["content"]))
            if item.get("message_type") == "assistant_message" and item.get("content"):
                content = item["content"]
                if isinstance(content, list):
                    text_parts = [
                        str(part.get("text"))
                        for part in content
                        if isinstance(part, dict) and part.get("text")
                    ]
                    parts.append("\n".join(text_parts) if text_parts else str(content))
                else:
                    parts.append(str(content))
            text = item.get("text")
            if text:
                parts.append(str(text))
        if parts:
            return "\n".join(parts)
    for key in ("assistant_message", "content", "text", "output"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _tool_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    raw = (
        schema.get("parameters")
        or schema.get("inputSchema")
        or schema.get("input_schema")
        or schema.get("argsJsonSchema")
        or schema.get("args_json_schema")
    )
    if not isinstance(raw, dict):
        raw = {}
    if raw.get("type") != "object":
        raw = {"type": "object", "properties": {}, **raw}
    raw.setdefault("properties", {})
    if not isinstance(raw.get("properties"), dict):
        raw["properties"] = {}
    raw.setdefault("required", [])
    if not isinstance(raw.get("required"), list):
        raw["required"] = []
    return raw
