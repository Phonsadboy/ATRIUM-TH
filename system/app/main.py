"""FastAPI entrypoint for the ATRIUM headless system.

The UI already has a strong local contract. This module exposes that contract
over REST plus a small WebSocket event stream, backed by the durable Repo and
the live provider layer.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import gzip
import hashlib
import hmac
import json
import locale
import logging
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import uuid
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, unquote, urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import Field

from .atrium_domain import (
    agent_message_metadata,
    agent_snapshot,
    handoff_flow,
    is_meeting_thread,
    is_war_room_thread,
    meeting_context,
    meeting_flow,
    meeting_id_from_thread,
    meeting_participants,
    meeting_thread_id,
    persona_prompt,
    system_chat_message,
    thread_cost_summary,
    update_agent_state,
    war_room_context,
    war_room_flow,
    war_room_id_from_thread,
    war_room_participants,
    war_room_thread_id,
)
from .audio_transcription import (
    AudioTranscriptionError,
    AudioTranscriptionNotConfigured,
    audio_transcription_status,
    execute_audio_transcription_tool,
    format_audio_transcript_preview,
    is_audio_file,
    transcribe_audio_bytes,
)
from .catalog import (
    DEFAULT_MODEL,
    catalog_payload,
    coerce_model_speed,
    coerce_thinking_effort,
    default_thinking_effort_for_model,
    model_pricing,
    normalize_ai_config,
    PROVIDERS,
    provider_bypasses_agent_runtime,
    provider_has_native_chat_stream,
)
from .chat_input import (
    COMMAND_SPECS,
    attachment_context,
    autocomplete_mentions,
    choose_mentioned_responder,
    estimate_input,
    input_character_limit_detail,
    input_character_limit_exceeded,
    message_content_with_attachment_images,
    message_text_with_attachment_refs,
    parse_slash_command,
    prompt_starters_for_thread,
    resolve_department_mentions,
    split_assign_command,
    suggested_followups_for_response,
    summarize_messages,
)
from .chat_rendering import citation_chips, ensure_rendering_metadata
from .chat_tools import (
    _owner_command_env_from_args,
    _owner_process_tool,
    _owner_start_background_shell_run,
    apply_result_totals,
    assistant_tool_message,
    chat_tool_definitions,
    chat_tool_surface_summary,
    chat_tool_system_instructions,
    likely_needs_chat_tools,
    recent_tool_run_context,
    run_chat_tool,
    should_enable_chat_tools,
    tool_policy_decision,
    tool_result_message,
)
from .chat_streaming import (
    ChatMessageStreamSink,
    ChatStreamCancelled,
    chat_streams,
    provider_exception_detail,
    runtime_event_to_hub_pulse,
    stream_llm,
)
from .clock import day_key, now_ms
from .config import get_settings
from .context_budget import estimate_llm_context_tokens, model_auto_compact_context_tokens
from .db.base import commit_and_release, dispose_db, init_db, session_scope
from .db.repo import Repo, TOOL_CATALOG
from .events import hub
from .file_intake import (
    FilePreview,
    artifact_kind_for_file,
    attachments_from_tool_runs,
    bytes_from_uri,
    extract_preview_from_bytes,
    extract_text_from_uri,
    guess_mime,
    message_attachment_from_artifact,
    safe_filename,
)
from .image_generation import (
    FALLBACK_IMAGE_MODEL,
    GPT_IMAGE_MODELS,
    PRIMARY_IMAGE_MODEL,
    ImageGenerationError,
    generate_image_assets,
    image_generation_auth_status,
    queue_image_generation_assets,
)
from .handoffs import (
    append_handoff_message,
    handoff_chat_message,
    handoff_participants,
    handoff_status_for_act,
    make_handoff_message,
    normalize_handoff_status,
)
from .host_bridge_proof import host_bridge_parity_proof_id, host_bridge_source_provenance
from .engine import (
    approve_task_close_request,
    engine_runtime_snapshot,
    reject_task_close_request,
    request_task_close_approval,
    run_chat_reply_loop,
    run_engine_loop,
    run_engine_tick,
    run_image_generation_loop,
    run_trigger_scheduler_loop,
)
from .ids import uid
from .memory.debt import compute_department_knowledge_debt
from .memory.embeddings import embedding_metadata, resolve_embedder
from .memory.graph_store import graph_health, init_graph_backend
from .mcp_local import (
    KNOWN_LOCAL_MCP_SERVERS,
    execute_local_mcp_call,
    mcp_enabled_servers,
    mcp_gateway_endpoint,
    mcp_gateway_token_status,
    mcp_gateway_token_value,
    mcp_runtime_block_reason,
    mcp_server_enabled,
    resolve_local_executable,
)
from .provider.base import LLMMessage, LLMResult, LLMToolCall
from .provider.registry import get_provider, provider_health
from .tools.host_bridge import HostBridge
from .tools.visual_bridge import (
    browser_profile_from_args,
    execute_browser_act,
    execute_browser_open,
    execute_browser_snapshot,
    execute_activate_app,
    execute_click,
    execute_desktop_act,
    execute_desktop_snapshot,
    execute_keypress as execute_visual_keypress,
    execute_list_apps,
    execute_notification,
    execute_open_app,
    execute_paste_text as execute_visual_paste_text,
    execute_quit_app,
    execute_screenshot_capture,
    execute_scroll,
    execute_type_text as execute_visual_type_text,
    list_browser_profiles,
    persist_screenshot_artifact,
    visual_process_error,
)
from .web_tools import execute_web_fetch, execute_web_search
from .schema import (
    AccentName,
    ActivityEvent,
    AiProviderId,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AuditLogEntry,
    AssignTaskInput,
    Artifact,
    ArtifactContentInput,
    ArtifactContentResponse,
    ArtifactDiffResponse,
    ArtifactPreviewResponse,
    ArtifactQualityReview,
    ArtifactVersion,
    Budget,
    BudgetCapInput,
    Bulletin,
    ChatMessage,
    CompanyState,
    Connector,
    ConnectorStatus,
    CostReportScope,
    CostReport,
    CreateArtifactInput,
    CreateAuditNoteInput,
    CreateBulletinInput,
    CreateCritiqueInput,
    CreateDecisionInput,
    CreateDepartmentInput,
    CreateEvidencePackInput,
    CreateHandoffMessageInput,
    CreateLessonInput,
    CreateMeetingInput,
    CreateNotificationInput,
    CreateObjectiveInput,
    CreateOrgPlanInput,
    CreatePreferenceInput,
    CreateProjectInput,
    CreatePlaybookInput,
    CreateSkillInput,
    CreateTriggerInput,
    CreateWarRoomInput,
    CritiqueReport,
    Decision,
    Department,
    DepartmentMemory,
    EditDepartmentInput,
    EditKnowledgeInput,
    EvidencePack,
    Executive,
    GenerateImageInput,
    GenerateImageResponse,
    GuardedActionResponse,
    HandoffMessage,
    HostBridgeParityStatusResponse,
    ImportFileInput,
    ImportFileResponse,
    InputEstimate,
    InputEstimateInput,
    KnowledgeDebtReport,
    Lesson,
    Meeting,
    MeetingCollaboration,
    MessageAttachment,
    MessageMentionTarget,
    ModelId,
    ModelSpeed,
    Notification,
    NotificationPreferences,
    NotificationReadInput,
    OrgPlan,
    OrgPlanDepartment,
    OwnerProfile,
    PeekDepartmentResponse,
    PermissionPolicy,
    PermissionPolicyInput,
    PolicyDecision,
    Playbook,
    Preference,
    Project,
    ReasoningStatus,
    ReassignTaskInput,
    RequestTaskClosureInput,
    ResolveApprovalInput,
    ResolveOrgPlanInput,
    ResolveProjectInput,
    Schema,
    ScheduledObjective,
    SendMessageInput,
    SetRunningInput,
    Skill,
    SlashCommandResult,
    SlashCommandSpec,
    SuggestedFollowUp,
    ThinkingEffort,
    ThreadCostSummary,
    ToggleInput,
    ToolCatalogItem,
    ToolRun,
    ToolRunInput,
    ToolRunResponse,
    ToolRiskClass,
    UpdateArtifactInput,
    UpdateDecisionInput,
    UpdateMeetingInput,
    UpdateNotificationPreferencesInput,
    UpdateObjectiveInput,
    UpdateOrgPlanInput,
    UpdatePlaybookInput,
    UpdatePreferenceInput,
    UpdateProjectInput,
    UpdateTaskReviewScheduleInput,
    UpdateTriggerInput,
    UpdateWarRoomInput,
    Trigger,
    ThreadDraft,
    ThreadDraftInput,
    PromptStarter,
    WarRoom,
    WarRoomCollaboration,
    RollbackArtifactInput,
    Task,
    TaskStatus,
    WorkspaceAuditResponse,
)
from .seed import EXEC_ID, ensure_executive, seed_if_empty
from .learning.reflection import enqueue_reflection, record_learning_signal, reflect_and_record
from .scheduling import (
    cadence_from_schedule_object,
    next_run_for_cadence,
    repair_trigger_schedule,
    resolve_trigger_cadence,
)
from .threads import dept_id_from_thread, is_exec, thread_id_for
from .task_review import apply_task_review_schedule, enqueue_task_review_reminder, normalize_review_interval_ms, review_interval_label
from .telegram_gateway import handle_telegram_update, run_telegram_polling_loop, telegram_webhook_secret
from .work_visibility import emit_work_status_notice, visibility_event_label

logger = logging.getLogger(__name__)

ACCENTS: list[AccentName] = ["amber", "teal", "coral", "lavender", "sky", "honey"]
NOTIFICATION_TYPES = (
    "approval",
    "budget",
    "blocked",
    "task_done",
    "digest",
    "crash",
    "knowledge_debt",
    "security",
)
DEFAULT_NOTIFICATION_DELIVERY = {
    "approval": "push",
    "budget": "push",
    "blocked": "push",
    "task_done": "inbox",
    "digest": "inbox",
    "crash": "push",
    "knowledge_debt": "inbox",
    "security": "push",
}
NOTIFICATION_PREFS_ID = "global"
CHAT_RATE_WINDOW_MS = 60_000


class ProviderInput(Schema):
    provider_id: AiProviderId


class ModelInput(Schema):
    model: ModelId


class ThinkingInput(Schema):
    thinking_effort: ThinkingEffort


class SpeedInput(Schema):
    speed: ModelSpeed


class EntityInput(Schema):
    data: dict[str, Any]


class CatalogResponse(Schema):
    providers: list[dict[str, Any]]
    models: list[dict[str, Any]]
    thinking_efforts: list[dict[str, Any]]
    speed_modes: list[dict[str, Any]]


class ProviderEnvUpdate(Schema):
    key: str
    value: str | None = None
    unset: bool = False


class ProviderEnvUpdateInput(Schema):
    updates: list[ProviderEnvUpdate]


class ApiCapability(Schema):
    method: str
    path: str
    name: str
    category: str
    mutates: bool
    chat_tool: str = "call_atrium_api"
    summary: str | None = None
    operation_id: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    request_body: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None


class ApiCapabilityResponse(Schema):
    endpoints: list[ApiCapability]
    guidance: list[str]
    schemas: dict[str, Any] = Field(default_factory=dict)


class GraphHealthResponse(Schema):
    configured: str
    backend: str
    enabled: bool
    path: str | None = None
    error: str | None = None


class HealthResponse(Schema):
    ok: bool
    provider: dict[str, Any]
    engine: dict[str, Any]
    jobs: dict[str, Any]
    graph: GraphHealthResponse
    memory: dict[str, Any]
    counts: dict[str, int]


class EngineTickResponse(Schema):
    objective_jobs: int
    trigger_jobs: int
    jobs: int
    departments: int
    compactions: int
    running: bool


class RunningResponse(Schema):
    running: bool


class OkResponse(Schema):
    ok: bool


class SafetyWarning(Schema):
    code: str
    message: str
    severity: str = "warn"
    retry_after_ms: int | None = None


class ChatRateLimitState(Schema):
    limit: int
    remaining: int
    reset_at: int
    window_ms: int
    exceeded: bool = False


class ChatBudgetState(Schema):
    daily_cap_usd: float
    spent_today_usd: float
    remaining_usd: float
    estimated_usd: float
    warning_ratio: float
    would_exceed: bool = False


class MessageUsage(Schema):
    usd: float
    rag_hits: list[str] = []
    compact_enqueued: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    thinking_tokens: int = 0
    provider_id: str | None = None
    model: str | None = None
    thinking_effort: ThinkingEffort | None = None
    speed: ModelSpeed | None = None
    stop_reason: str = "end_turn"
    generation_ms: int | None = None
    reasoning_status: ReasoningStatus | None = None
    reasoning_chars: int = 0
    redacted_thinking: bool = False
    tool_runs: list[dict[str, Any]] = []
    warnings: list[SafetyWarning] = []
    rate_limit: ChatRateLimitState | None = None
    budget: ChatBudgetState | None = None
    thread_usd: float = 0.0


class SendMessageResponse(Schema):
    message: ChatMessage
    usage: MessageUsage
    messages: list[ChatMessage] = Field(default_factory=list)
    activity: list[ActivityEvent] = Field(default_factory=list)
    command: SlashCommandResult | None = None
    mentions: list[MessageMentionTarget] = Field(default_factory=list)
    suggested_follow_ups: list[SuggestedFollowUp] = Field(default_factory=list)
    token_estimate: InputEstimate | None = None
    draft_cleared: bool = False


class StopGenerationInput(Schema):
    message_id: str | None = None


class RetryMessageInput(Schema):
    message_id: str
    client_message_id: str | None = None
    thinking_effort: ThinkingEffort | None = None
    speed: ModelSpeed | None = None


class StopGenerationResponse(Schema):
    stopped: bool
    message_id: str | None = None
    queued_cancelled: int = 0


class RegenerateMessageInput(Schema):
    message_id: str | None = None
    thinking_effort: ThinkingEffort | None = None
    speed: ModelSpeed | None = None


class EditMessageInput(Schema):
    text: str
    thinking_effort: ThinkingEffort | None = None
    speed: ModelSpeed | None = None
    branch_id: str | None = None


class BranchConversationResponse(Schema):
    branch_thread_id: str
    edited_message: ChatMessage
    response: SendMessageResponse
    copied_count: int = 0


class MessageActionInput(Schema):
    action: Literal["pin", "unpin", "react", "unreact", "copy"]
    reaction: str | None = None
    actor: str = "user"


class MessageActionResponse(Schema):
    action: str
    mutated: bool
    message: ChatMessage


class PromoteMessageInput(Schema):
    title: str | None = None
    tags: list[str] = []
    actor: str = "user"


class PromoteMessageResponse(Schema):
    message: ChatMessage
    knowledge: dict[str, Any]


class ThreadSearchHit(Schema):
    message: ChatMessage
    snippet: str
    score: float


class ThreadSearchResponse(Schema):
    thread_id: str
    query: str
    hits: list[ThreadSearchHit] = []


class ThreadExportResponse(Schema):
    thread_id: str
    format: Literal["md", "json"]
    content_type: str
    filename: str
    content: str
    message_count: int
    exported_at: int


class AuditLogExportResponse(Schema):
    format: Literal["md", "json", "jsonl"]
    content_type: str
    filename: str
    content: str
    row_count: int
    exported_at: int
    dept_id: str | None = None
    kind: str | None = None
    redacted: bool = True
    redacted_fields: list[str] = Field(default_factory=list)


class ThreadStatsResponse(Schema):
    thread_id: str
    total: int
    latest_ts: int | None = None
    latest_message_id: str | None = None
    new_count: int = 0


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    hub.reset_runtime()
    init_graph_backend(settings)
    await init_db()
    async with session_scope() as s:
        repo = Repo(s)
        await repo.ensure_company(settings.company_name, settings.daily_cap_usd)
        await repo.reset_stuck_jobs()
        if settings.seed_on_start:
            if not await repo.has_departments():
                await seed_if_empty(repo, await resolve_embedder(settings), settings.company_name, settings.daily_cap_usd)
        await ensure_executive(repo)
        from .memory.company_memory import ensure_company_memory_files

        ensure_company_memory_files(settings)

    flusher = asyncio.create_task(hub.run_flusher())
    startup_sync = asyncio.create_task(_run_delayed_background(8.0, lambda: _run_startup_sync_tasks(settings)))
    engine = asyncio.create_task(_run_delayed_background(6.0, lambda: run_engine_loop(settings))) if settings.engine_enabled else None
    chat_worker = asyncio.create_task(_run_delayed_background(6.0, lambda: run_chat_reply_loop(settings))) if settings.engine_enabled else None
    image_worker = asyncio.create_task(_run_delayed_background(8.0, lambda: run_image_generation_loop(settings))) if settings.engine_enabled else None
    trigger_scheduler = asyncio.create_task(_run_delayed_background(8.0, lambda: run_trigger_scheduler_loop(settings))) if settings.engine_enabled else None
    telegram_worker = (
        asyncio.create_task(_run_delayed_background(4.0, lambda: run_telegram_polling_loop(settings, store_file=_store_file_artifact)))
        if settings.engine_enabled
        else None
    )
    approval_sweeper = asyncio.create_task(_run_delayed_background(8.0, _run_full_auto_approval_sweeper))
    try:
        yield
    finally:
        startup_sync.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await startup_sync
        if engine:
            engine.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await engine
        if chat_worker:
            chat_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await chat_worker
        if image_worker:
            image_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await image_worker
        if trigger_scheduler:
            trigger_scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await trigger_scheduler
        if telegram_worker:
            telegram_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telegram_worker
        approval_sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await approval_sweeper
        flusher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await flusher
        await dispose_db()
        hub.reset_runtime()


async def _run_delayed_background(delay_s: float, factory) -> None:
    await asyncio.sleep(max(0.0, delay_s))
    await factory()


async def _run_startup_sync_tasks(settings) -> None:
    """Run slow startup reconciliation after the API is already serving."""
    await asyncio.sleep(1)

    from .learning.consolidation import enqueue_consolidation
    from .memory.company_memory import ensure_company_memory_files, sync_company_memory_to_runtime
    from .org.capabilities import sync_all_department_capabilities
    from .org.lifecycle import enqueue_org_lifecycle
    from .runtime.factory import get_agent_runtime
    from .runtime.provisioning import ensure_all_runtime_agents
    from .tools import build_default_tool_registry
    from .tools.foundry import load_custom_tools, sync_custom_tools_to_runtime

    async def run_step(name: str, fn) -> None:
        try:
            async with session_scope() as s:
                result = await fn(Repo(s))
            logger.info("startup sync step completed: %s %s", name, result)
            hub.mark_dirty()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("startup sync step failed: %s", name)

    ensure_company_memory_files(settings)
    await run_step("department_capabilities", sync_all_department_capabilities)
    await run_step("runtime_agents", lambda repo: ensure_all_runtime_agents(repo, settings=settings))
    await run_step("company_memory_runtime", lambda repo: sync_company_memory_to_runtime(repo, settings=settings))

    async def enqueue_startup_jobs(repo: Repo) -> dict[str, Any]:
        await enqueue_consolidation(repo)
        delay_ms = int(settings.org_lifecycle_interval_hours * 3600 * 1000)
        await enqueue_org_lifecycle(repo, run_after=now_ms() + delay_ms)
        return {"ok": True}

    await run_step("startup_jobs", enqueue_startup_jobs)

    async def sync_runtime_tools(repo: Repo) -> dict[str, Any]:
        loaded = await load_custom_tools(repo, build_default_tool_registry())
        synced = await sync_custom_tools_to_runtime(repo, get_agent_runtime(settings))
        return {"loaded": loaded, "synced": synced}

    await run_step("runtime_tools", sync_runtime_tools)
    await run_step("trigger_schedules", _repair_trigger_schedules)


app = FastAPI(title="ATRIUM System", version="0.4.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _job_stale_after_ms(settings: Any) -> int:
    """A running job is stale only after its own timeout window, not the engine heartbeat window."""
    engine_stale_s = float(getattr(settings, "engine_stale_after_s", 0) or 0)
    job_timeout_s = float(getattr(settings, "engine_job_timeout_s", 0) or 0)
    return int(max(1.0, engine_stale_s, job_timeout_s) * 1000)


def _database_fingerprint(settings: Any) -> dict[str, Any]:
    url = str(getattr(settings, "effective_database_url", "") or "")
    parsed = urlparse(url)
    backend = parsed.scheme.split("+", 1)[0] or "unknown"
    redacted = url
    if parsed.password:
        netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
        redacted = parsed._replace(netloc=netloc).geturl()
    elif backend == "sqlite":
        redacted = parsed.path or url
        if redacted.startswith("//"):
            redacted = redacted[1:]
    explicitly_configured = bool(getattr(settings, "database_url", "") or "")
    return {
        "backend": backend,
        "configured": bool(url),
        "explicitlyConfigured": explicitly_configured,
        "redacted": redacted,
        "fingerprint": hashlib.sha256(redacted.encode("utf-8")).hexdigest()[:12],
    }

AUDITED_API_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


def _api_audit_actor(request: Request) -> str:
    return (
        request.headers.get("x-atrium-actor")
        or request.headers.get("x-requested-by")
        or "owner-ui"
    )[:120]


def _api_audit_department(request: Request) -> str | None:
    explicit = request.headers.get("x-atrium-department")
    if explicit:
        return explicit[:64]
    match = re.match(r"^/api/messages/([^/]+)(?:/.*)?$", request.url.path)
    if match:
        return dept_id_from_thread(unquote(match.group(1)))
    match = re.match(r"^/api/departments/([^/]+)(?:/.*)?$", request.url.path)
    if match:
        return unquote(match.group(1))[:64]
    return None


def _should_audit_api_request(request: Request) -> bool:
    if request.method.upper() not in AUDITED_API_METHODS:
        return False
    path = request.url.path
    if not path.startswith("/api/"):
        return False
    return True


async def _record_api_audit(request: Request, status_code: int, elapsed_ms: int) -> None:
    actor = _api_audit_actor(request)
    method = request.method.upper()
    path = request.url.path
    dept_id = _api_audit_department(request)
    severity = "good" if status_code < 400 else "warn"
    ev = _activity(
        f"api {method} {path} โดย {actor} -> {status_code}",
        type_="api_mutation",
        department_id=dept_id,
        severity=severity,
    )
    ev.update({
        "actor": actor,
        "method": method,
        "path": path,
        "statusCode": status_code,
        "elapsedMs": elapsed_ms,
        "source": request.headers.get("x-atrium-source") or "http",
    })
    try:
        async with session_scope() as s:
            await Repo(s).add_activity(ev)
    except Exception:
        logger.exception("failed to record API audit event for %s %s", method, path)


@app.middleware("http")
async def api_mutation_audit_middleware(request: Request, call_next):
    should_audit = _should_audit_api_request(request)
    started = now_ms()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        if should_audit:
            status_code = response.status_code if response is not None else 500
            await _record_api_audit(request, status_code, max(0, now_ms() - started))


def _activity(
    text: str,
    *,
    type_: str = "system",
    department_id: str | None = None,
    severity: str = "info",
    ts: int | None = None,
) -> dict[str, Any]:
    return {
        "id": uid("ev"),
        "ts": ts or now_ms(),
        "type": type_,
        "departmentId": department_id,
        "text": text,
        "severity": severity,
    }


def _entity_uri(kind: str, entity_id: str) -> str:
    return f"atrium://{kind}/{entity_id}"


def _visibility_policy(dept_id: str) -> dict[str, str]:
    return {
        "dept": dept_id,
        "archive": "private",
        "knowledge": "on_request",
        "tasks": "company",
        "artifacts": "company",
    }


def _executive_from_department(dept: dict[str, Any]) -> dict[str, Any]:
    return Executive(
        id=dept["id"],
        agent_name=dept.get("agentName") or dept.get("name") or dept["id"],
        provider_id=dept.get("providerId", "claude_code"),
        model=dept.get("model", DEFAULT_MODEL),
        thinking_effort=dept.get("thinkingEffort", "high"),
        speed=coerce_model_speed(dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard")),
        system_prompt=dept.get("charter") or dept.get("role") or "",
        daily_budget_usd=float(dept.get("dailyBudgetUsd") or get_settings().executive_daily_budget_usd),
        workspace_path=dept.get("workspacePath"),
        tools=dept.get("tools", []),
        autonomy=bool(dept.get("autonomy", True)),
    ).dump()


def _owner_profile_from_preferences(preferences: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, list[dict[str, Any]]] = {}
    updated_at: int | None = None
    for pref in preferences:
        by_category.setdefault(pref["category"], []).append(pref)
        ts = pref.get("ts")
        if isinstance(ts, int):
            updated_at = max(updated_at or ts, ts)
    return OwnerProfile(
        preferences=preferences,
        by_category=by_category,
        updated_at=updated_at,
    ).dump()


def _default_notification_preferences(updated_at: int | None = None) -> dict[str, Any]:
    return {
        "id": NOTIFICATION_PREFS_ID,
        "byType": dict(DEFAULT_NOTIFICATION_DELIVERY),
        "quietHours": {
            "enabled": False,
            "start": "22:00",
            "end": "07:00",
            "timezone": "Asia/Bangkok",
        },
        "updatedAt": updated_at or now_ms(),
    }


def _validate_quiet_time(value: str) -> None:
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise HTTPException(status_code=400, detail="quiet hours must use HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise HTTPException(status_code=400, detail="quiet hours must use HH:MM")


def _normalize_notification_preferences(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    prefs = _default_notification_preferences(raw.get("updatedAt") if raw else None)
    if not raw:
        return prefs
    raw_by_type = raw.get("byType") or {}
    if isinstance(raw_by_type, dict):
        for notif_type, mode in raw_by_type.items():
            if notif_type in NOTIFICATION_TYPES and mode in {"off", "inbox", "push"}:
                prefs["byType"][notif_type] = mode
    raw_quiet = raw.get("quietHours") or {}
    if isinstance(raw_quiet, dict):
        prefs["quietHours"].update({
            key: value
            for key, value in raw_quiet.items()
            if key in {"enabled", "start", "end", "timezone"} and value is not None
        })
    return prefs


def _provision_workspace(dept_id: str) -> str:
    path = (get_settings().workspace_dir / dept_id).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _provision_project_workspace(project_id: str) -> str:
    path = (get_settings().workspace_dir / "projects" / project_id).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _workspace_for_dept(dept_id: str) -> Path:
    path = (get_settings().workspace_dir / dept_id).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_for_artifact(artifact: dict[str, Any]) -> Path:
    return _workspace_for_dept(artifact.get("ownerDept") or "company")


def _artifact_content_path(artifact: dict[str, Any], version: int) -> Path:
    path = _workspace_for_artifact(artifact) / "artifacts" / artifact["id"] / f"v{version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _local_path_from_uri(uri: str | None) -> Path | None:
    if not uri or uri.startswith("atrium://"):
        return None
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        raw_path = unquote(parsed.path)
    else:
        raw_path = uri
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = get_settings().workspace_dir / path
    path = path.resolve()
    if not _path_inside(path, get_settings().workspace_dir):
        return None
    return path


def _read_artifact_text(uri: str | None, *, filename: str | None = None, mime: str | None = None) -> str:
    if uri and uri.startswith("atrium-object://"):
        from .storage.object_store import ObjectStoreIntegrityError

        try:
            text = extract_text_from_uri(uri, filename=filename or "artifact.txt", mime=mime)
        except (ObjectStoreIntegrityError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f"artifact object integrity check failed: {exc}") from exc
        if text:
            return text
        raise HTTPException(status_code=404, detail="artifact content is not available")
    path = _local_path_from_uri(uri)
    if not path or not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact content is not available")
    text = extract_text_from_uri(str(path), filename=filename or path.name, mime=mime)
    if not text:
        raise HTTPException(status_code=404, detail="artifact content is not available")
    return text


def _preview_content(preview: dict[str, Any]) -> str | None:
    if preview.get("kind") not in {"md", "diff", "sheet"}:
        return None
    try:
        return _read_artifact_text(preview.get("uri"))
    except HTTPException:
        return None


def _git_run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def _git_status(path: Path, error: str | None = None) -> dict[str, Any]:
    try:
        inside = _git_run(path, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0:
            return {
                "workspacePath": str(path),
                "gitEnabled": False,
                "head": None,
                "dirty": False,
                "error": error or inside.stderr.strip() or "git repository not initialized",
            }
        head = _git_run(path, "rev-parse", "HEAD")
        status = _git_run(path, "status", "--porcelain")
        return {
            "workspacePath": str(path),
            "gitEnabled": True,
            "head": head.stdout.strip() if head.returncode == 0 else None,
            "dirty": bool(status.stdout.strip()) if status.returncode == 0 else False,
            "error": error,
        }
    except Exception as exc:
        return {
            "workspacePath": str(path),
            "gitEnabled": False,
            "head": None,
            "dirty": False,
            "error": str(exc),
        }


def _ensure_git_repo(path: Path) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    try:
        inside = _git_run(path, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0:
            init = _git_run(path, "init")
            if init.returncode != 0:
                return _git_status(path, init.stderr.strip() or "git init failed")
        _git_run(path, "config", "user.email", "atrium@local")
        _git_run(path, "config", "user.name", "ATRIUM System")
        return _git_status(path)
    except Exception as exc:
        return _git_status(path, str(exc))


def _git_commit_workspace(path: Path, message: str) -> dict[str, Any]:
    audit = _ensure_git_repo(path)
    if not audit.get("gitEnabled"):
        return audit
    add = _git_run(path, "add", ".")
    if add.returncode != 0:
        return _git_status(path, add.stderr.strip() or "git add failed")
    status = _git_run(path, "status", "--porcelain")
    if status.returncode != 0:
        return _git_status(path, status.stderr.strip() or "git status failed")
    if not status.stdout.strip():
        return _git_status(path)
    commit = _git_run(path, "commit", "-m", message)
    if commit.returncode != 0:
        return _git_status(path, commit.stderr.strip() or "git commit failed")
    return _git_status(path)


def _thinking_token_estimate(effort: str) -> int:
    return {"off": 0, "low": 40, "medium": 120, "high": 300, "xhigh": 600, "max": 1000}.get(effort, 200)


def _chat_cost_estimate(dept: dict[str, Any]) -> float:
    in_rate, out_rate = model_pricing(dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard"))
    tokens_in = 4500
    tokens_out = 2048 + _thinking_token_estimate(dept.get("thinkingEffort", "high"))
    return round((tokens_in * in_rate + tokens_out * out_rate) / 1_000_000, 6)


async def _budget_block_reason(repo: Repo, dept: dict[str, Any], estimated_usd: float) -> str | None:
    """Budget is telemetry/alert/forecast only; it never blocks chat or jobs."""
    return None


def _message_error(code: str, detail: str, *, retryable: bool = True) -> dict[str, Any]:
    return {"code": code, "detail": detail, "retryable": retryable}


ACTIVE_CHAT_REPLY_STATUSES = {"queued", "sending", "pending_approval"}
GENERIC_PENDING_REPLY_TEXTS = {
    "",
    "กำลังคิดและทำงานต่อในคิวเบื้องหลัง...",
    "กำลังคิดและทำงานต่อใน war room...",
}


def _is_active_chat_reply(msg: dict[str, Any]) -> bool:
    return msg.get("role") != "user" and (
        bool(msg.get("pending")) or str(msg.get("status") or "") in ACTIVE_CHAT_REPLY_STATUSES
    )


def _cancelled_chat_reply(msg: dict[str, Any], *, detail: str) -> dict[str, Any]:
    text = str(msg.get("text") or "").strip()
    if text in GENERIC_PENDING_REPLY_TEXTS:
        text = "หยุดการตอบแล้วก่อนเริ่มสร้างคำตอบ"
    return {
        **msg,
        "text": text,
        "pending": False,
        "streaming": False,
        "status": "cancelled",
        "error": _message_error("cancelled", detail),
    }


def _active_chat_reply(history: list[dict[str, Any]], *, ignore_reply_to: str | None = None) -> dict[str, Any] | None:
    for msg in reversed(history):
        if not _is_active_chat_reply(msg):
            continue
        if ignore_reply_to and msg.get("replyToMessageId") == ignore_reply_to:
            continue
        return msg
    return None


async def _thread_messages_for_live_prompt(repo: Repo, thread_id: str, *, minimum_limit: int = 500) -> list[dict[str, Any]]:
    limit = get_settings().chat_history_message_limit
    if limit <= 0:
        return await repo.all_thread_messages(thread_id)
    return await repo.thread_messages(thread_id, limit=max(minimum_limit, limit))


def _rate_limit_state(history: list[dict[str, Any]], now: int, limit: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
    if limit <= 0:
        return None, [], False
    since = now - CHAT_RATE_WINDOW_MS
    recent = [
        msg
        for msg in history
        if msg.get("role") == "user"
        and int(msg.get("ts") or 0) >= since
        and msg.get("status") != "blocked"
    ]
    used_after_accept = len(recent) + 1
    oldest = min((int(msg.get("ts") or now) for msg in recent), default=now)
    reset_at = max(now + 1_000, oldest + CHAT_RATE_WINDOW_MS)
    remaining = max(0, limit - used_after_accept)
    exceeded = used_after_accept > limit
    state = {
        "limit": limit,
        "remaining": remaining,
        "resetAt": reset_at,
        "windowMs": CHAT_RATE_WINDOW_MS,
        "exceeded": exceeded,
    }
    warnings: list[dict[str, Any]] = []
    if exceeded:
        warnings.append({
            "code": "rate_limited",
            "message": f"ส่งข้อความเกินเพดาน {limit} ครั้งต่อนาที รอให้หน้าต่างเวลา reset ก่อน",
            "severity": "warn",
            "retryAfterMs": max(0, reset_at - now),
        })
    elif remaining <= max(1, limit // 4):
        warnings.append({
            "code": "rate_limit_warning",
            "message": f"ใกล้ถึงเพดาน rate limit แล้ว เหลือ {remaining} ครั้งในนาทีนี้",
            "severity": "warn",
            "retryAfterMs": max(0, reset_at - now),
        })
    return state, warnings, exceeded


async def _budget_state(repo: Repo, estimated_usd: float) -> dict[str, Any]:
    settings = get_settings()
    company = await repo.get_company()
    daily_cap = float(company.daily_cap_usd if company else settings.daily_cap_usd)
    spent = float(await repo.spent_today()) if company else 0.0
    remaining = max(0.0, daily_cap - spent)
    ratio = min(max(float(settings.chat_budget_warning_ratio), 0.0), 1.0)
    return {
        "dailyCapUsd": round(daily_cap, 6),
        "spentTodayUsd": round(spent, 6),
        "remainingUsd": round(remaining, 6),
        "estimatedUsd": round(estimated_usd, 6),
        "warningRatio": ratio,
        "wouldExceed": daily_cap <= 0 or estimated_usd > remaining,
    }


def _budget_warnings(state: dict[str, Any]) -> list[dict[str, Any]]:
    cap = float(state.get("dailyCapUsd") or 0.0)
    spent = float(state.get("spentTodayUsd") or 0.0)
    estimated = float(state.get("estimatedUsd") or 0.0)
    ratio = float(state.get("warningRatio") or 0.85)
    if cap <= 0 or state.get("wouldExceed"):
        return []
    if (spent + estimated) / cap < ratio:
        return []
    pct = round(ratio * 100)
    return [{
        "code": "budget_warning",
        "message": f"ข้อความนี้จะทำให้งบรวมวันนี้แตะหรือเกิน {pct}% ของ daily cap",
        "severity": "warn",
    }]


def _warning_notices(warnings: list[dict[str, Any]]) -> list[str]:
    mapping = {
        "rate_limited": "rate_limited",
        "rate_limit_warning": "rate_limit_warning",
        "budget_warning": "budget_warning",
    }
    return [mapping[item["code"]] for item in warnings if item.get("code") in mapping]


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    return dict(value or {})


async def _normalize_chat_attachments(repo: Repo, input: SendMessageInput | InputEstimateInput | ThreadDraftInput) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = [{"artifactId": artifact_id} for artifact_id in getattr(input, "attachment_ids", [])]
    raw.extend(_model_dump(item) for item in getattr(input, "attachments", []))
    attachments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        artifact_id = item.get("artifactId") or item.get("artifact_id")
        key = str(artifact_id or item.get("uri") or item.get("name") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        if artifact_id:
            artifact = await repo.get_entity("artifact", str(artifact_id))
            if not artifact:
                raise HTTPException(status_code=404, detail=f"attachment artifact not found: {artifact_id}")
            attachments.append(message_attachment_from_artifact(artifact))
            continue
        attachments.append({
            key: value
            for key, value in {
                "name": item.get("name"),
                "kind": item.get("kind"),
                "mime": item.get("mime"),
                "uri": item.get("uri"),
                "sizeBytes": item.get("sizeBytes") or item.get("size_bytes"),
            }.items()
            if value is not None
        })
    return attachments


def _input_metadata(
    estimate: dict[str, Any],
    *,
    status: str = "sent",
    command: dict[str, str] | None = None,
    routed_department_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "command": command.get("name") if command else None,
        "commandArgs": command.get("args") if command else None,
        "routedDepartmentId": routed_department_id,
        "characterCount": estimate["characters"],
        "estimatedTokens": estimate["estimatedTokens"],
        "attachmentCount": estimate["attachmentCount"],
    }


def _chat_response(
    message: dict[str, Any],
    *,
    usage: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    activity: list[dict[str, Any]] | None = None,
    command: dict[str, Any] | None = None,
    mentions: list[dict[str, Any]] | None = None,
    suggestions: list[dict[str, Any]] | None = None,
    estimate: dict[str, Any] | None = None,
    draft_cleared: bool = False,
) -> dict[str, Any]:
    usage = usage or {"usd": 0.0, "ragHits": [], "compactEnqueued": False}
    suggestions = suggestions or message.get("suggestedFollowUps") or []
    if suggestions:
        message = {**message, "suggestedFollowUps": suggestions}
    return {
        "message": message,
        "usage": usage,
        "messages": messages or [],
        "activity": activity or [],
        "command": command,
        "mentions": mentions or [],
        "suggestedFollowUps": suggestions,
        "tokenEstimate": estimate,
        "draftCleared": draft_cleared,
    }


def _attach_tool_artifacts_to_message(message: dict[str, Any], tool_runs: list[dict[str, Any]] | None) -> dict[str, Any]:
    generated = attachments_from_tool_runs(tool_runs or [])
    if not generated:
        return message
    existing = list(message.get("attachments") or [])
    seen = {str(item.get("artifactId") or item.get("artifact_id") or "") for item in existing if isinstance(item, dict)}
    for item in generated:
        artifact_id = str(item.get("artifactId") or "")
        if artifact_id and artifact_id not in seen:
            existing.append(item)
            seen.add(artifact_id)
    return {**message, "attachments": existing}


def _draft_payload(thread_id: str, text: str, attachments: list[dict[str, Any]], updated_at: int | None = None) -> dict[str, Any]:
    return ThreadDraft(
        id=thread_id,
        thread_id=thread_id,
        text=text,
        attachments=attachments,
        updated_at=updated_at or now_ms(),
    ).dump()


async def _delete_thread_draft(repo: Repo, thread_id: str) -> bool:
    existing = await repo.get_entity("thread_draft", thread_id)
    if not existing:
        return False
    await repo.delete_entity("thread_draft", thread_id)
    return True


def _command_specs_payload(query: str | None = None) -> list[dict[str, Any]]:
    if not query:
        return COMMAND_SPECS
    q = query.lower().strip().lstrip("/")
    return [
        spec
        for spec in COMMAND_SPECS
        if q in str(spec["name"]).lower()
        or any(q in str(alias).lower() for alias in spec.get("aliases", []))
        or q in str(spec.get("description", "")).lower()
    ]


def _make_chat_task(
    *,
    title: str,
    detail: str,
    dept_id: str,
    priority: str = "normal",
    origin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_ms()
    return {
        "id": uid("task"),
        "title": title[:80] or "งานใหม่จากแชต",
        "detail": detail or title or "สร้างจากคำสั่งแชต",
        "status": "assigned",
        "priority": priority,
        "departmentId": dept_id,
        "origin": origin or {"kind": "user"},
        "progress": 0,
        "createdAt": now,
        "updatedAt": now,
        "handoffs": [],
        "log": ["สร้างจาก slash command ในแชต"],
        "projectId": None,
        "deliverables": [],
        "watchers": ["executive"],
        "parentTaskId": None,
        "subTaskIds": [],
        "deadlineAt": None,
        "result": None,
    }


async def _record_message_mentions(
    repo: Repo,
    *,
    thread_id: str,
    user_msg: dict[str, Any],
    mentions: list[dict[str, Any]],
    current_dept: dict[str, Any],
) -> None:
    for mention in mentions:
        dept_id = mention.get("departmentId")
        if not dept_id:
            continue
        target_label = "ผู้บริหาร" if is_exec(str(dept_id)) else f"ฝ่าย{mention.get('displayName') or dept_id}"
        target_thread = mention.get("threadId") or thread_id_for(dept_id)
        await repo.add_activity(_activity(
            f"{user_msg['authorName']} mention {target_label}",
            type_="message",
            department_id=dept_id,
        ))
        if target_thread == thread_id:
            notice = system_chat_message(
                thread_id,
                (
                    f"เรียก{target_label}เข้ามาอ่านบทสนทนานี้ "
                    f"จากข้อความของ{user_msg['authorName']}"
                ),
                department_id=dept_id,
                severity="info",
                ts=now_ms(),
            )
            notice["mentions"] = [mention]
            notice["input"] = {"status": "sent", "routedDepartmentId": current_dept["id"]}
            await repo.add_message(notice)
            continue
        notice = {
            "id": uid("msg"),
            "threadId": target_thread,
            "role": "system",
            "authorName": "ATRIUM",
            "text": (
                f"ถูก mention จาก {'ผู้บริหาร' if is_exec(current_dept['id']) else 'ฝ่าย' + current_dept.get('name', current_dept['id'])}: "
                f"{str(user_msg.get('text') or '').strip()[:220]}"
            ),
            "ts": now_ms(),
            "mentions": [mention],
            "input": {"status": "sent", "routedDepartmentId": current_dept["id"]},
        }
        await repo.add_message(notice)


async def _execute_slash_command(
    repo: Repo,
    *,
    thread_id: str,
    command: dict[str, str],
    user_msg: dict[str, Any],
    current_dept: dict[str, Any],
    responder_dept: dict[str, Any],
    departments: list[dict[str, Any]],
    history: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    estimate: dict[str, Any],
    draft_cleared: bool,
) -> dict[str, Any]:
    name = command.get("name") or ""
    if name == "clear":
        cleared = await repo.clear_thread_messages(thread_id)
        user_msg["input"] = _input_metadata(estimate, status="executed", command=command)
        await repo.add_message(user_msg)
        text = f"ล้างข้อความเดิมใน thread นี้แล้ว ({cleared} รายการ)"
        reply = {
            "id": uid("msg"),
            "threadId": thread_id,
            "role": "system",
            "authorName": "ATRIUM",
            "text": text,
            "ts": now_ms(),
            "status": "sent",
            "replyToMessageId": user_msg["id"],
        }
        await repo.add_message(reply)
        await repo.add_activity(_activity("ล้างประวัติแชตใน thread", type_="system", department_id=current_dept["id"]))
        suggestions = suggested_followups_for_response(responder_dept, user_msg["text"], text, command_name=name)
        return _chat_response(
            {**reply, "suggestedFollowUps": suggestions},
            usage={"usd": 0.0, "ragHits": [], "compactEnqueued": False},
            command={"name": name, "ok": True, "message": text, "data": {"cleared": cleared}},
            mentions=mentions,
            suggestions=suggestions,
            estimate=estimate,
            draft_cleared=draft_cleared,
        )

    existing_user = await repo.get_message(user_msg["id"])
    if existing_user:
        await repo.update_message({**existing_user, **user_msg})
    else:
        await repo.add_message(user_msg)
    if name not in {spec["name"] for spec in COMMAND_SPECS}:
        text = f"ไม่รู้จักคำสั่ง /{command.get('rawName') or name}. ใช้ /assign, /summarize, /cost, /memory หรือ /clear"
        reply = {
            "id": uid("msg"),
            "threadId": thread_id,
            "role": "system",
            "authorName": "ATRIUM",
            "text": text,
            "ts": now_ms(),
            "status": "sent",
            "replyToMessageId": user_msg["id"],
        }
        await repo.add_message(reply)
        return _chat_response(
            reply,
            usage={"usd": 0.0, "ragHits": [], "compactEnqueued": False},
            command={"name": name, "ok": False, "message": text, "data": {"commands": _command_specs_payload()}},
            mentions=mentions,
            estimate=estimate,
            draft_cleared=draft_cleared,
        )

    command_data: dict[str, Any] = {}
    ok = True
    if name == "assign":
        target, title, detail = split_assign_command(command.get("args") or "", departments)
        if not target:
            ok = False
            text = "ระบุฝ่ายปลายทางไม่เจอ ใช้รูปแบบ /assign @ฝ่าย ชื่องาน"
            command_data = {"availableDepartments": [d["name"] for d in departments if not is_exec(d["id"])]}
        else:
            task = _make_chat_task(
                title=title or "งานใหม่จากแชต",
                detail=detail or title or str(user_msg.get("text") or ""),
                dept_id=target["id"],
                origin={"kind": "executive"} if is_exec(current_dept["id"]) else {"kind": "user"},
            )
            await repo.save_task(task)
            await repo.add_activity(_activity(
                f"สร้างงานจากแชต “{task['title']}” → ฝ่าย{target['name']}",
                type_="task_created",
                department_id=target["id"],
            ))
            text = f"สร้างงาน “{task['title']}” ให้ฝ่าย{target['name']}แล้ว"
            command_data = {"task": task}
    elif name == "summarize":
        try:
            limit = int((command.get("args") or "").split()[0])
        except Exception:
            limit = 12
        text = summarize_messages(history, limit=limit)
        command_data = {"messageCount": min(max(limit, 1), 40)}
    elif name == "cost":
        report = await repo.cost_report("dept" if not is_exec(responder_dept["id"]) else "day", None if is_exec(responder_dept["id"]) else responder_dept["id"])
        text = (
            f"ค่าใช้จ่ายวันนี้ ${report['spentUsd']:.4f}; forecast ${report['forecastUsd']:.2f}. "
            f"หมวด chat ${report['byCategory'].get('chat', 0):.4f}"
        )
        command_data = {"report": report}
    elif name == "memory":
        if is_exec(responder_dept["id"]):
            rows = []
            for dept in departments:
                if is_exec(dept["id"]):
                    continue
                mem = dept.get("memory") or {}
                rows.append(f"- ฝ่าย{dept['name']}: RAG {mem.get('ragEntries', 0)}, archive {mem.get('archiveChunks', 0)}, graph {mem.get('graphNodes', 0)} nodes")
            text = "ภาพรวมความจำของทุกฝ่าย:\n" + ("\n".join(rows) if rows else "- ยังไม่มีฝ่าย")
            command_data = {"departments": rows}
        else:
            memory = await repo.department_memory(responder_dept["id"])
            text = (
                f"ความจำฝ่าย{responder_dept['name']}: "
                f"RAG {len(memory['knowledge'])}, archive {len(memory['archive'])}, "
                f"graph {len(memory['graph'].get('nodes', []))} nodes / {len(memory['graph'].get('edges', []))} edges"
            )
            command_data = {"memory": memory}
    else:
        text = "ยังไม่รองรับคำสั่งนี้"
        ok = False

    reply = {
        "id": uid("msg"),
        "threadId": thread_id,
        "role": "system",
        "authorName": "ATRIUM",
        "text": text,
        "ts": now_ms(),
        "status": "sent",
        "replyToMessageId": user_msg["id"],
    }
    suggestions = suggested_followups_for_response(responder_dept, user_msg["text"], text, command_name=name, mentions=mentions)
    reply = ensure_rendering_metadata({**reply, "suggestedFollowUps": suggestions}, usage={"usd": 0.0})
    await repo.add_message(reply)
    hub.pulse({"kind": "input_command", "threadId": thread_id, "command": name, "ok": ok})
    if name == "assign" and ok:
        hub.pulse({"kind": "state", "departmentId": command_data["task"]["departmentId"]})
    return _chat_response(
        reply,
        usage={"usd": 0.0, "ragHits": [], "compactEnqueued": False},
        command={"name": name, "ok": ok, "message": text, "data": command_data},
        mentions=mentions,
        suggestions=suggestions,
        estimate=estimate,
        draft_cleared=draft_cleared,
    )


async def _record_budget_exhaustion(repo: Repo, dept: dict[str, Any], now: int) -> None:
    company = await repo.get_company()
    if not company:
        return
    company_spent = await repo.spent_today()
    if company.daily_cap_usd > 0 and company_spent >= company.daily_cap_usd:
        marker = f"atrium://budget/company/over-cap/{day_key(now)}"
        existing = await repo.list_entities("notification", limit=1000)
        if any(marker in (notification.get("links") or []) for notification in existing):
            return
        await repo.add_activity(_activity(
            "งบรวมรายวันถึงหรือเกินเพดานแล้ว — บันทึกเป็น telemetry เท่านั้น ระบบยังไม่หยุดเอง",
            type_="budget",
            severity="alert",
        ))
        await repo.put_entity(
            "notification",
            {
                "id": uid("notif"),
                "type": "budget",
                "severity": "alert",
                "title": "งบรวมรายวันถึงเพดานแล้ว",
                "body": "Full Auto ยังทำงานต่อ; budget เป็น telemetry/alert/forecast ไม่ใช่ execution gate",
                "ts": now,
                "read": False,
                "links": ["atrium://budget/company", marker, f"atrium://department/{dept['id']}"],
            },
            status="unread",
            ts=now,
        )


def _next_run_for(cadence: str | None, one_shot_at: int | None = None) -> int | None:
    return next_run_for_cadence(cadence, one_shot_at)


async def _repair_trigger_schedules(repo: Repo) -> int:
    now = now_ms()
    repaired = 0
    for trigger in await repo.list_entities("trigger", limit=1000):
        if not repair_trigger_schedule(trigger, now=now):
            continue
        dept_id = _dept_id_from_target(trigger.get("target") or "")
        await repo.put_entity("trigger", trigger, dept=dept_id, status=trigger.get("kind"), ts=now)
        repaired += 1
    if repaired:
        await repo.add_activity(_activity(
            f"ซ่อมตาราง trigger ที่ขาด cadence/nextRunAt จำนวน {repaired} รายการ",
            type_="system",
            severity="good",
            ts=now,
        ))
    return repaired


def _dept_id_from_target(target: str) -> str | None:
    if target.startswith("dept:"):
        return target.removeprefix("dept:")
    if target in {"company", "executive"}:
        return None
    return target


def _scheduler_target_from_args(args: dict[str, Any], run: dict[str, Any]) -> str:
    target = args.get("target") or args.get("targetDepartmentId") or args.get("departmentId") or args.get("department_id")
    if target:
        value = str(target).strip()
        if value in {"company", "executive"} or value.startswith("dept:"):
            return value
        return f"dept:{value}"
    run_dept = str(run.get("departmentId") or "").strip()
    requested_by = str(run.get("requestedBy") or "").strip()
    if requested_by and requested_by not in {run_dept, "agent", "system", "owner"}:
        if not (requested_by in {"exec", "executive"} and run_dept in {"exec", "executive"}):
            raise ValueError("scheduler.create requires explicit target when requester differs from executing department")
    if run_dept:
        return f"dept:{run_dept}"
    raise ValueError("scheduler.create requires target")


def _artifact_kind_for(path: Path, mime: str | None) -> str:
    return artifact_kind_for_file(path.name, mime)


async def _link_artifact_to_tasks(repo: Repo, artifact_id: str, task_ids: list[str]) -> None:
    for task_id in task_ids:
        task = await repo.get_task(task_id)
        if not task:
            continue
        deliverables = list(task.get("deliverables", []))
        if artifact_id not in deliverables:
            deliverables.append(artifact_id)
            task["deliverables"] = deliverables
            task["updatedAt"] = now_ms()
            await repo.save_task(task)


async def _link_child_task(repo: Repo, parent_task_id: str | None, child_task_id: str) -> None:
    if not parent_task_id:
        return
    parent = await repo.get_task(parent_task_id)
    if not parent:
        raise HTTPException(status_code=404, detail="parent task not found")
    children = list(parent.get("subTaskIds", []))
    if child_task_id not in children:
        children.append(child_task_id)
        parent["subTaskIds"] = children
        parent["updatedAt"] = now_ms()
        parent["log"] = [*parent.get("log", []), f"แตก subtask {child_task_id}"]
        await repo.save_task(parent)


async def _wake_department_for_assigned_task(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    now: int,
    *,
    reason: str = "หลังรับมอบหมาย",
) -> bool:
    if is_exec(str(dept.get("id") or "")):
        return False
    current_task_id = str(dept.get("currentTaskId") or "").strip()
    if current_task_id and current_task_id != str(task.get("id") or ""):
        return False
    if str(dept.get("state") or "idle") not in {"idle", "handoff", "blocked"}:
        return False
    if task.get("status") not in {"assigned", "backlog", "revising", "in_progress"}:
        return False
    task["status"] = "in_progress"
    task["updatedAt"] = now
    task["log"] = [*task.get("log", []), f"ปลุกแผนกให้เริ่มงานทันที{reason}"]
    dept["state"] = "working"
    dept["currentTaskId"] = task["id"]
    await repo.save_task(task)
    await repo.save_department(dept)
    await repo.add_activity(_activity(
        f"{dept.get('agentName', dept['id'])} เริ่มงาน “{task['title']}” ทันที{reason}",
        type_="task_assigned",
        department_id=dept["id"],
        severity="good",
        ts=now,
    ))
    msg = system_chat_message(
        thread_id_for(EXEC_ID),
        f"ฝ่าย{dept.get('name', dept['id'])}เริ่มทำงานทันที{reason}: “{task['title']}”",
        department_id=dept["id"],
        flow={
            "kind": "department_work",
            "title": f"ฝ่าย{dept.get('name', dept.get('id'))}: {task.get('title')}",
            "steps": [{
                "kind": "task_started",
                "label": "task started",
                "departmentId": dept.get("id"),
                "taskId": task.get("id"),
            }],
            "refs": {
                "departmentId": dept.get("id"),
                "threadId": thread_id_for(str(dept.get("id") or "")),
                "taskId": task.get("id"),
                "status": task.get("status"),
                "progress": task.get("progress"),
            },
        },
        severity="good",
        ts=now,
    )
    await repo.add_message(msg)
    hub.pulse({
        "kind": "chat_activity",
        "threadId": thread_id_for(EXEC_ID),
        "msgId": msg["id"],
        "departmentId": dept["id"],
        "message": msg,
    })
    return True


async def _delete_department_now(
    repo: Repo,
    dept_id: str,
    *,
    actor: str = "full_auto",
    reason: str | None = None,
) -> dict[str, Any]:
    from .org.checkpoints import create_org_checkpoint, mark_org_checkpoint_applied, rollback_endpoint
    from .org.capabilities import deactivate_department_capabilities

    if is_exec(dept_id):
        raise HTTPException(status_code=400, detail="executive department cannot be closed")
    dept = await repo.get_department(dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="department not found")
    checkpoint = await create_org_checkpoint(
        repo,
        reason=reason or f"delete department {dept_id}",
        actor=actor,
        action="delete_department",
        metadata={"departmentId": dept_id, "surface": "api/departments/{dept_id}"},
    )
    for task in await repo.tasks_for_dept(dept_id):
        task["departmentId"] = None
        task["status"] = "backlog"
        task["updatedAt"] = now_ms()
        task.pop("waitingOn", None)
        await repo.save_task(task)
    await deactivate_department_capabilities(repo, dept_id, reason=reason or f"delete department {dept_id}", actor=actor)
    await repo.delete_department(dept_id)
    await mark_org_checkpoint_applied(
        repo,
        checkpoint["id"],
        metadata={
            "retiredDepartmentIds": [dept_id],
            "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
        },
    )
    await repo.add_activity(_activity(
        f"ปิดแผนก{dept['name']} ({dept['agentName']}) แล้ว",
        severity="warn",
    ))
    return {"checkpointId": checkpoint["id"], "rollbackEndpoint": rollback_endpoint(checkpoint["id"])}


async def _request_destructive_approval(
    repo: Repo,
    *,
    title: str,
    detail: str,
    action: dict[str, Any],
    department_id: str | None = None,
) -> dict[str, Any]:
    approval = Approval(
        id=uid("apr"),
        ts=now_ms(),
        kind="destructive_action",
        title=title,
        detail=detail,
        department_id=department_id,
        status="approved",
        action=action,
    ).dump()
    action["approvedBy"] = "full_auto"
    approval["action"] = action
    await repo.add_approval(approval)
    executed = await _execute_approval_action(repo, approval)
    await repo.save_approval(approval)
    await repo.add_activity(_activity(
        f"Full Auto executed destructive action: {title}" if executed else f"Full Auto recorded destructive action: {title}",
        type_="system",
        department_id=department_id,
        severity="warn",
    ))
    await _upsert_approval_chat_message(repo, approval)
    return approval


def _approval_message_id(approval_id: str) -> str:
    return f"msg_{approval_id}"


def _approval_message_status(approval_status: str) -> str:
    if approval_status == "pending":
        return "pending_approval"
    if approval_status == "approved":
        return "sent"
    return "cancelled"


def _approval_message_text(approval: dict[str, Any]) -> str:
    status = approval.get("status", "pending")
    prefix = {
        "pending": "Legacy pending",
        "approved": "Full Auto executed",
        "rejected": "ปฏิเสธแล้ว",
    }.get(status, "approval")
    detail = str(approval.get("detail") or "").strip()
    body = f"{prefix}: {approval.get('title', 'approval')}"
    if detail:
        body += f"\n{detail}"
    body += f"\napproval={approval.get('id')}"
    return body


async def _upsert_approval_chat_message(
    repo: Repo,
    approval: dict[str, Any],
    *,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action = approval.get("action") or {}
    dept_id = approval.get("departmentId") or action.get("departmentId") or EXEC_ID
    thread_id = thread_id_for(dept_id)
    message_id = _approval_message_id(approval["id"])
    existing = await repo.get_message(message_id)
    status = _approval_message_status(str(approval.get("status") or "pending"))
    error = None
    if approval.get("status") == "rejected":
        error = _message_error("approval_rejected", "approval rejected by owner", retryable=False)
    msg = {
        **(existing or {}),
        "id": message_id,
        "threadId": thread_id,
        "role": "system",
        "authorName": "Trust & Safety",
        "text": _approval_message_text(approval),
        "ts": int((existing or {}).get("ts") or approval.get("ts") or now_ms()),
        "pending": False,
        "status": status,
        "approvalId": approval["id"],
        "approvalStatus": approval.get("status"),
        "toolRunId": (run or {}).get("id") or action.get("toolRunId"),
        "error": error,
    }
    msg = ensure_rendering_metadata(
        msg,
        notices=["approval_required"] if approval.get("status") == "pending" else [],
        severity="warn" if approval.get("status") == "pending" else "info",
    )
    if existing:
        await repo.update_message(msg)
    else:
        await repo.add_message(msg)
    hub.pulse({
        "kind": "approval_message",
        "threadId": thread_id,
        "msgId": message_id,
        "approvalId": approval["id"],
        "approvalStatus": approval.get("status"),
    })
    return msg


def _clip_text(text: str, limit: int = 60_000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def _tool_workspace_path(dept_id: str, raw_path: Any) -> Path:
    path, root, inside = _tool_path_info(dept_id, raw_path)
    if not inside:
        raise HTTPException(status_code=400, detail="tool path must stay inside the department workspace")
    return path


TOOL_ALIASES = {
    "read_file": "fs.read",
    "write_file": "fs.write",
    "copy_file": "fs.copy",
    "move_file": "fs.move",
    "http_get": "http.get",
    "import_url": "import.url",
    "run_command": "shell.exec",
}
MCP_EXTERNAL_CONNECTORS = [
    {
        "id": "mcp_github",
        "server": "github",
        "name": "GitHub MCP",
        "description": "External GitHub MCP server for issues, pull requests, repositories, and Actions.",
        "capabilities": ["repositories", "issues", "pull_requests", "actions"],
        "writeCapabilities": [],
    },
    {
        "id": "mcp_email",
        "server": "email",
        "name": "Email MCP",
        "description": "External email MCP server for mailbox reads, drafts, and approval-gated sends.",
        "capabilities": ["mailbox", "draft_email", "send_email"],
        "writeCapabilities": ["send_email"],
    },
    {
        "id": "mcp_calendar",
        "server": "calendar",
        "name": "Calendar MCP",
        "description": "External calendar MCP server for availability checks, events, and reminders.",
        "capabilities": ["availability", "events", "reminders"],
        "writeCapabilities": ["events", "reminders"],
    },
    {
        "id": "mcp_notion",
        "server": "notion",
        "name": "Notion MCP",
        "description": "External Notion MCP server for pages, databases, and workspace knowledge.",
        "capabilities": ["pages", "databases", "workspace_search"],
        "writeCapabilities": ["pages", "databases"],
    },
    {
        "id": "mcp_drive",
        "server": "drive",
        "name": "Drive MCP",
        "description": "External Drive MCP server for files, folders, and shared document retrieval.",
        "capabilities": ["files", "folders", "document_retrieval"],
        "writeCapabilities": ["files", "folders"],
    },
]
MUTATING_TOOLS = {
    "fs.write",
    "fs.patch",
    "fs.copy",
    "fs.move",
    "fs.delete",
    "shell.exec",
    "sandbox.exec",
    "git.commit",
    "git.push",
    "browser.act",
    "browser.open",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.click",
    "desktop.act",
    "desktop.open_app",
    "desktop.activate_app",
    "desktop.quit_app",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "http.post",
    "import.url",
    "mcp.call",
    "notify.send",
    "scheduler.create",
    "logs.note",
    "audio.transcribe",
    "image.generate",
}
CHECKPOINT_RISKS = {"local_write", "host_write", "destructive", "external_send", "privileged"}
TERMINAL_TOOL_STATUSES = {"completed", "succeeded", "failed", "cancelled", "blocked"}
GENERIC_ENTITY_MUTATION_PROTECTED_TYPES = {
    "agent_tool_run",
    "artifact",
    "artifact_version",
    "audit_note",
    "checkpoint",
    "conversation_ledger",
    "decision",
    "decision_event",
    "handoff_message",
    "peek_log",
    "runtime_checkpoint",
    "tool_run",
}


def _canonical_tool(tool: str) -> str:
    return TOOL_ALIASES.get(tool, tool)


def _canonical_permission_mode(mode: str) -> str:
    normalized = str(mode or "full_auto").strip().lower()
    aliases = {
        "approve_all": "full_auto",
        "approve_everything": "full_auto",
        "critical_only": "ask",
        "yolo": "full",
    }
    return aliases.get(normalized, normalized)


def _permission_mode_is_full(mode: str | None) -> bool:
    return _canonical_permission_mode(str(mode or "full_auto")) in {"full", "full_auto"}


def _assert_generic_entity_mutable(entity_type: str) -> None:
    if entity_type in GENERIC_ENTITY_MUTATION_PROTECTED_TYPES:
        raise HTTPException(
            status_code=405,
            detail=(
                f"{entity_type} records are append-only or require a dedicated "
                "audited endpoint; generic mutation is not allowed"
            ),
        )


def _tool_catalog_item(tool: str) -> dict[str, Any] | None:
    canonical = _canonical_tool(tool)
    return next((item for item in TOOL_CATALOG if item["tool"] == canonical), None) or next(
        (item for item in TOOL_CATALOG if item["tool"] == tool),
        None,
    )


def _tool_executor(tool: str) -> str:
    item = _tool_catalog_item(tool) or {}
    return str(item.get("executor") or "host")


def _build_tool_route(
    tool: str,
    custom_catalog: dict[str, Any] | None = None,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .tools import ExecutorRouter, build_default_tool_registry, tool_spec_from_legacy

    registry = build_default_tool_registry()
    if custom_catalog:
        registry.register(tool_spec_from_legacy(custom_catalog))
    return ExecutorRouter(registry).route(tool, args).to_dict()


def _tool_path_info(dept_id: str, raw_path: Any, default: str | None = None) -> tuple[Path, Path, bool]:
    if (raw_path is None or raw_path == "") and default is not None:
        raw_path = default
    if not raw_path or not isinstance(raw_path, str):
        raise HTTPException(status_code=400, detail="tool path is required")
    root = _workspace_for_dept(dept_id)
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    return path, root, _path_inside(path, root)


def _tool_target_path(dept_id: str, raw_path: Any, default: str | None = None) -> Path:
    path, _, _ = _tool_path_info(dept_id, raw_path, default=default)
    return path


def _tool_cwd(dept_id: str, args: dict[str, Any]) -> Path:
    return _tool_target_path(dept_id, args.get("cwd") or args.get("path"), default=".")


def _mcp_gateway_endpoint() -> str:
    return mcp_gateway_endpoint(get_settings().mcp_gateway_url)


def _mcp_enabled_servers() -> set[str]:
    return mcp_enabled_servers(get_settings().mcp_enabled_servers)


def _mcp_server_enabled(server: str) -> bool:
    return mcp_server_enabled(server, get_settings().mcp_enabled_servers)


def _mcp_gateway_token_configured_light(settings: Any) -> bool:
    return bool(
        str(getattr(settings, "mcp_gateway_token", "") or "").strip()
        or str(getattr(settings, "mcp_gateway_token_keychain_service", "") or "").strip()
    )


def _mcp_gateway_token_value_light(settings: Any) -> str:
    return str(getattr(settings, "mcp_gateway_token", "") or "").strip()


def _mcp_gateway_token_status_light(settings: Any) -> dict[str, Any]:
    direct = _mcp_gateway_token_value_light(settings)
    service = str(getattr(settings, "mcp_gateway_token_keychain_service", "") or "").strip()
    account = str(getattr(settings, "mcp_gateway_token_keychain_account", "") or "atrium").strip() or "atrium"
    if direct:
        return {
            "configured": True,
            "source": "env",
            "envConfigured": True,
            "keychainServiceConfigured": bool(service),
            "keychainAccount": account,
            "secretRedacted": True,
        }
    return {
        "configured": bool(service),
        "source": "keychain_unprobed" if service else "missing",
        "envConfigured": False,
        "keychainCliAvailable": bool(shutil.which("security")),
        "keychainServiceConfigured": bool(service),
        "keychainAccount": account,
        "keychainReadable": None,
        "secretRedacted": True,
    }


def _external_write_requirements(settings: Any, *, include_gateway: bool = True) -> list[str]:
    requirements: list[str] = []
    endpoint = mcp_gateway_endpoint(getattr(settings, "mcp_gateway_url", ""))
    if include_gateway and not endpoint:
        requirements.append("ATRIUM_MCP_GATEWAY_URL configured for write-capable external MCP servers")
    if include_gateway and endpoint and not _mcp_gateway_token_configured_light(settings):
        requirements.append("ATRIUM_MCP_GATEWAY_TOKEN or Keychain token configured for the MCP gateway")
    return requirements


def _mcp_runtime_block_reason(args: dict[str, Any]) -> str | None:
    return mcp_runtime_block_reason(
        args,
        gateway_url=get_settings().mcp_gateway_url,
        enabled_servers=get_settings().mcp_enabled_servers,
    )


def _mcp_gateway_health(settings: Any, *, probe: bool = False) -> dict[str, Any]:
    endpoint = mcp_gateway_endpoint(getattr(settings, "mcp_gateway_url", ""))
    token = mcp_gateway_token_value(settings) if probe else _mcp_gateway_token_value_light(settings)
    token_status = mcp_gateway_token_status(settings) if probe else _mcp_gateway_token_status_light(settings)
    enabled = sorted(server for server in mcp_enabled_servers(getattr(settings, "mcp_enabled_servers", "")) if server != "*")
    probe_server = enabled[0] if enabled else "github"
    base = {
        "configured": bool(endpoint),
        "endpointConfigured": bool(endpoint),
        "tokenConfigured": bool(token),
        "tokenSource": token_status.get("source"),
        "tokenStatus": token_status,
        "checked": False,
        "ok": False,
        "server": probe_server,
        "tool": "list_tools",
    }
    if not endpoint:
        return {**base, "status": "not_configured"}
    if not token and not token_status.get("configured"):
        return {**base, "status": "token_missing"}
    if not probe:
        return {**base, "status": "not_probed", "ok": None}
    payload = {"server": probe_server, "tool": "list_tools", "arguments": {}}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ATRIUM/0.4 mcp-readiness-probe",
        "Authorization": f"Bearer {token}",
    }
    timeout = max(1.0, min(float(getattr(settings, "mcp_timeout_s", 3.0) or 3.0), 3.0))
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read(40_000)
            text = body.decode("utf-8", errors="ignore")
            parsed: Any = None
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(text)
            return {
                **base,
                "checked": True,
                "ok": 200 <= int(res.status) < 300,
                "status": res.status,
                "contentType": res.headers.get("content-type", ""),
                "responseOk": parsed.get("ok") if isinstance(parsed, dict) else None,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(2000).decode("utf-8", errors="ignore")
        return {**base, "checked": True, "status": exc.code, "ok": False, "error": _clip_text(body, 500)}
    except Exception as exc:
        return {**base, "checked": True, "status": "error", "ok": False, "error": f"{type(exc).__name__}: {_clip_text(str(exc), 500)}"}


def _external_write_requirements_with_gateway_health(settings: Any, gateway_health: dict[str, Any]) -> list[str]:
    requirements = _external_write_requirements(settings)
    if gateway_health.get("configured") and gateway_health.get("checked") and not gateway_health.get("ok"):
        requirements.append("MCP gateway list_tools health probe succeeds")
    return requirements


def _docker_executable() -> str | None:
    for candidate in (
        "docker",
        "docker.exe",
        "/usr/local/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
        "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
    ):
        if "/" not in candidate:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        elif Path(candidate).exists():
            return candidate
    return None


def _docker_runtime_block_reason(*, probe: bool = True) -> str | None:
    docker = _docker_executable()
    if not docker:
        return "Docker is unavailable for sandbox.exec"
    if not probe:
        return None
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{json .ServerVersion}}"],
            text=True,
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Docker daemon did not respond within 5s"
    except Exception as exc:
        return f"Docker runtime check failed: {type(exc).__name__}: {exc}"
    if result.returncode != 0:
        detail = _clip_text((result.stderr or result.stdout or "").strip(), 500)
        return f"Docker daemon is not ready: {detail or 'docker info failed'}"
    return None


def _sandbox_runtime_status(*, probe: bool = False) -> tuple[bool, str]:
    docker = _docker_executable()
    if docker and not probe:
        return True, "Docker CLI installed; daemon readiness checked when sandbox.exec runs"
    docker_block = _docker_runtime_block_reason(probe=probe)
    if not docker_block:
        return True, "Docker ready"
    if get_settings().sandbox_local_fallback:
        return True, f"local fallback ready; Docker unavailable: {docker_block}"
    return False, docker_block


def _sandbox_runtime_block_reason(*, probe: bool = False) -> str | None:
    available, status = _sandbox_runtime_status(probe=probe)
    return None if available else status


def _command_executable_names(command: list[Any]) -> tuple[str, str]:
    raw = str(command[0] if command else "").strip().strip('"')
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = name[:-4] if name.endswith(".exe") else name
    return name, stem


def _command_script_text(command: list[Any]) -> str:
    return " ".join(str(part).strip().lower() for part in command if str(part).strip())


def _command_text_has_token(text: str, tokens: set[str]) -> bool:
    for token in tokens:
        if re.search(rf"(?<![\w.-]){re.escape(token)}(?![\w.-])", text):
            return True
    return False


def _windows_shell_command_risk(command: list[Any]) -> ToolRiskClass | None:
    _name, stem = _command_executable_names(command)
    text = _command_script_text(command)
    shell_stems = {"cmd", "powershell", "pwsh"}
    if stem not in shell_stems:
        return None
    if stem in {"powershell", "pwsh"} and _command_text_has_token(text, {"start-process"}) and "-verb" in text and "runas" in text:
        return "privileged"
    if re.search(r"(?<![\w.-])git(?:\.exe)?\s+push(?![\w.-])", text):
        return "external_send"
    destructive_tokens = {"del", "erase", "rd", "rmdir", "rm", "remove-item", "remove-itemproperty", "remove-aduser"}
    if _command_text_has_token(text, destructive_tokens) or re.search(r"(?<![\w.-])git(?:\.exe)?\s+reset\s+--hard(?![\w.-])", text):
        return "destructive"
    network_tokens = {"curl", "wget", "ssh", "scp", "rsync", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "start-bitstransfer"}
    if _command_text_has_token(text, network_tokens):
        return "network"
    return None


def _command_risk(args: dict[str, Any], contains_credential: bool) -> ToolRiskClass:
    command = args.get("command") or []
    _executable, executable = _command_executable_names(command)
    lowered = [str(part).lower() for part in command]
    if executable in {"sudo", "su", "launchctl", "runas"}:
        return "privileged"
    windows_shell_risk = _windows_shell_command_risk(command)
    if windows_shell_risk in {"privileged", "destructive", "external_send"}:
        return windows_shell_risk
    if executable in {"rm", "rmdir", "unlink", "shred"} or (executable == "git" and lowered[1:3] == ["reset", "--hard"]):
        return "destructive"
    if executable == "git" and any(part in {"push"} for part in lowered[1:]):
        return "external_send"
    if contains_credential:
        return "credential"
    if windows_shell_risk == "network":
        return "network"
    if executable in {"curl", "wget", "ssh", "scp", "rsync"}:
        return "network"
    return "command"


def _tool_risk_class(run: dict[str, Any]) -> ToolRiskClass:
    tool = _canonical_tool(run["tool"])
    args = run.get("args", {})
    custom_catalog = run.get("customCatalogRow") if isinstance(run.get("customCatalogRow"), dict) else None
    if custom_catalog and custom_catalog.get("riskClass"):
        return str(custom_catalog["riskClass"])  # type: ignore[return-value]
    contains_credential = _tool_args_contain_credential(args)
    if tool in {"web.search", "web.fetch", "http.get"}:
        if contains_credential:
            return "credential"
        return "network"
    if tool == "http.post":
        return "credential" if contains_credential else "external_send"
    if tool == "import.url":
        if contains_credential:
            return "credential"
        if args.get("outputPath") or args.get("path"):
            _, _, inside = _tool_path_info(run["departmentId"], args.get("outputPath") or args.get("path"))
            if not inside:
                return "host_write"
        return "network"
    if tool == "shell.exec":
        if args.get("cwd"):
            _, _, inside = _tool_path_info(run["departmentId"], args.get("cwd"))
            if not inside and not contains_credential:
                return "command"
        return _command_risk(args, contains_credential)
    if tool == "process":
        action = str(args.get("action") or "").strip().lower().replace("_", "-")
        if action in {"kill", "remove", "cancel", "write", "submit", "paste", "send-keys", "sendkeys"}:
            return "command"
        return "credential" if contains_credential else "safe_read"
    if tool == "sandbox.exec":
        if contains_credential:
            return "credential"
        if args.get("network"):
            return "network"
        return "command"
    if tool in {"git.push"}:
        return "external_send"
    if tool in {"git.commit"}:
        if args.get("cwd"):
            _, _, inside = _tool_path_info(run["departmentId"], args.get("cwd"))
            if not inside:
                return "host_write"
        return "credential" if contains_credential else "local_write"
    if tool in {"git.status", "git.diff", "logs.query", "browser.snapshot", "browser.screenshot", "desktop.snapshot", "desktop.screenshot", "desktop.apps"}:
        return "credential" if contains_credential else "safe_read"
    if tool == "desktop.quit_app" and args.get("force"):
        return "credential" if contains_credential else "destructive"
    if tool in {"browser.open", "browser.act", "browser.click", "browser.type", "browser.keypress", "browser.paste_text", "browser.scroll", "desktop.open_app", "desktop.activate_app", "desktop.quit_app", "desktop.act", "desktop.click", "desktop.type", "desktop.keypress", "desktop.paste_text", "desktop.scroll"}:
        return "credential" if contains_credential else "desktop"
    if tool == "mcp.call":
        return "credential" if contains_credential else ("external_send" if args.get("changesExternalState") else "network")
    if tool == "notify.send":
        return "desktop"
    if tool == "scheduler.create":
        return "privileged"
    if tool == "logs.note":
        return "credential" if contains_credential else "local_write"
    if tool == "image.generate":
        return "credential" if contains_credential else "external_send"
    if tool == "audio.transcribe":
        return "credential" if contains_credential else "external_send"
    if tool in {"fs.list", "fs.read"}:
        if args.get("path"):
            _, _, inside = _tool_path_info(run["departmentId"], args.get("path"))
            if not inside:
                return "credential"
        return "credential" if contains_credential else "safe_read"
    if tool in {"fs.write", "fs.patch"}:
        path, _, inside = _tool_path_info(run["departmentId"], args.get("path"))
        if not inside:
            return "host_write"
        if run["tool"] == "write_file" and path.exists():
            return "destructive"
        return "credential" if contains_credential else "local_write"
    if tool == "fs.copy":
        _, _, _, source_inside, destination_inside = _file_op_paths(run["departmentId"], args)
        if not destination_inside:
            return "host_write"
        if not source_inside:
            return "credential"
        return "credential" if contains_credential else "local_write"
    if tool == "fs.move":
        _, _, _, source_inside, destination_inside = _file_op_paths(run["departmentId"], args)
        if not source_inside or not destination_inside:
            return "host_write"
        return "credential" if contains_credential else "local_write"
    if tool == "fs.delete":
        return "destructive"
    if contains_credential:
        return "credential"
    return "safe_read"


def _tool_runtime_block_reason(run: dict[str, Any]) -> str | None:
    settings = get_settings()
    tool = _canonical_tool(run["tool"])
    args = run.get("args") or {}
    if run.get("customTool"):
        return None
    if tool == "image.generate" and not image_generation_auth_status(settings).get("configured"):
        return "ChatGPT account OAuth is not configured; OpenAI /v1/images fallback is used only after the ChatGPT OAuth image route exhausts retries"
    if tool == "audio.transcribe":
        status = audio_transcription_status(settings)
        if not status.get("enabled"):
            return "audio transcription is disabled"
        if not status.get("configured"):
            return "OpenAI audio transcription is not configured; set ATRIUM_OPENAI_API_KEY or enable a supported ChatGPT OAuth audio route"
    if tool == "mcp.call":
        return _mcp_runtime_block_reason(args)
    if tool.startswith("browser.") or tool.startswith("desktop.") or tool == "notify.send":
        allowed, reason = HostBridge().can_run(tool, args)
        if not allowed:
            return reason
    if tool == "sandbox.exec":
        return _sandbox_runtime_block_reason()
    return None


_HOST_BRIDGE_PARITY_PROOF_GAP = (
    "Run ops/host_bridge_parity_report.py with macOS and Windows --full probe artifacts before claiming cross-OS HostBridge parity."
)
_HOST_BRIDGE_PARITY_REPORT_SCHEMA_VERSION = 1
_HOST_BRIDGE_PARITY_PROOF_SCHEMA_VERSION = 1
_HOST_BRIDGE_PARITY_RESULT_LABELS = {"macos", "windows"}
_HOST_BRIDGE_PARITY_RESULT_PLATFORMS = {"macos": "darwin", "windows": "win32"}
_HOST_BRIDGE_PARITY_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_COMMON_HOST_BRIDGE_PROOF_FACETS = {
    "browserOpen": "browser.open proof",
    "browserOpenIsolatedProfile": "browser.open isolated ATRIUM profile proof",
    "browserSnapshot": "browser.snapshot DOM-ref proof",
    "browserSnapshotIsolatedPlaywright": "browser.snapshot Playwright isolated profile proof",
    "browserAct": "browser.act DOM-ref click proof",
    "browserActIsolatedPlaywright": "browser.act Playwright isolated profile proof",
    "browserActVerified": "browser.act post-click DOM verification",
    "appsDiscovery": "desktop.apps proof",
    "screenshotFile": "screenshot file proof",
    "notification": "notification proof",
    "desktopAutomationReady": "desktop automation readiness proof",
}
_REQUIRED_HOST_BRIDGE_PROOF_FACETS = {
    "macos": {
        **_COMMON_HOST_BRIDGE_PROOF_FACETS,
        "foregroundSession": "macOS foreground session proof",
        "appleScriptClipboard": "macOS AppleScript clipboard proof",
        "foregroundSnapshotNative": "macOS native foreground snapshot proof",
        "appsNativeNSWorkspace": "macOS native app discovery proof",
        "calculatorNativeAct": "macOS Calculator native AXPress display proof",
        "textEditNativeAct": "macOS TextEdit native setValue proof",
    },
    "windows": {
        **_COMMON_HOST_BRIDGE_PROOF_FACETS,
        "interactiveSession": "Windows interactive session proof",
        "windowsInteractiveSessionIdentity": "Windows interactive session identity proof",
        "windowsVisualPreflight": "Windows visual preflight proof",
        "helperSelftest": "Windows SendInput/UI helper selftest proof",
        "powershellPreflight": "Windows PowerShell visual preflight proof",
        "windowsDpiAwareness": "Windows DPI awareness proof",
        "windowsVirtualScreen": "Windows virtual screen bounds proof",
        "windowsForegroundActivation": "Windows Notepad foreground activation proof",
        "windowsUnicodeTyping": "Windows Unicode typing proof",
        "windowsKeyboardShortcut": "Windows keyboard shortcut mapping proof",
        "notepadNativeAct": "Windows Notepad native UIAutomation ValuePattern text proof",
        "clipboardRoundTrip": "Windows Notepad clipboard round-trip proof",
    },
}


def _host_bridge_parity_report_proof(settings: Any) -> dict[str, Any]:
    raw_path = getattr(settings, "host_bridge_parity_report_path", None) or Path("./data/host-bridge-parity-report.json")
    path = Path(raw_path).expanduser()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "present": False,
            "ok": False,
            "path": str(path),
            "summary": "No persisted HostBridge parity report is available.",
            "findings": [_HOST_BRIDGE_PARITY_PROOF_GAP],
            "details": {"reportPath": str(path), "reportPresent": False},
        }
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "present": True,
            "ok": False,
            "path": str(path),
            "summary": "Persisted HostBridge parity report could not be read.",
            "findings": [f"Persisted parity report is unreadable: {type(exc).__name__}: {exc}"],
            "details": {"reportPath": str(path), "reportPresent": True, "reportReadable": False},
        }
    if not isinstance(loaded, dict):
        return {
            "present": True,
            "ok": False,
            "path": str(path),
            "summary": "Persisted HostBridge parity report is invalid.",
            "findings": ["Persisted parity report root must be a JSON object."],
            "details": {"reportPath": str(path), "reportPresent": True, "reportReadable": False},
        }

    findings: list[str] = []
    details: dict[str, Any] = {
        "reportPath": str(path),
        "reportPresent": True,
        "reportReadable": True,
        "reportOk": loaded.get("ok"),
        "proofId": loaded.get("proofId"),
        "reportGeneratedAt": loaded.get("generatedAt"),
        "reportSummary": loaded.get("summary"),
    }
    if loaded.get("schemaVersion") != _HOST_BRIDGE_PARITY_REPORT_SCHEMA_VERSION:
        findings.append("Persisted parity report schemaVersion is not 1; regenerate with ops/host_bridge_parity_report.py --output.")
    if loaded.get("proofSchemaVersion") != _HOST_BRIDGE_PARITY_PROOF_SCHEMA_VERSION:
        findings.append("Persisted parity report proofSchemaVersion is not 1; regenerate with ops/host_bridge_parity_report.py --output.")
    proof_id = loaded.get("proofId")
    if loaded.get("ok") is True and (not isinstance(proof_id, str) or len(proof_id) != 64):
        findings.append("Persisted parity report proofId is missing or invalid; regenerate with ops/host_bridge_parity_report.py --output.")
    generated_at = loaded.get("generatedAt")
    if not isinstance(generated_at, int):
        findings.append("Persisted parity report is missing generatedAt; regenerate with ops/host_bridge_parity_report.py --output.")
    else:
        now = now_ms()
        if generated_at > now + 5 * 60 * 1000:
            findings.append("Persisted parity report generatedAt is in the future; regenerate with ops/host_bridge_parity_report.py --output.")
        max_age_hours = float(getattr(settings, "host_bridge_parity_report_max_age_hours", 24.0) or 0.0)
        if max_age_hours > 0:
            max_age_ms = int(max_age_hours * 60 * 60 * 1000)
            age_ms = now - generated_at
            if age_ms > max_age_ms:
                findings.append(
                    f"Persisted parity report is stale; regenerate with ops/host_bridge_parity_report.py --output. "
                    f"ageHours={age_ms / 3600000:.1f}; maxAgeHours={max_age_hours:.1f}"
                )
    results = loaded.get("results") if isinstance(loaded.get("results"), dict) else {}
    result_labels = {str(label) for label in results} if isinstance(results, dict) else set()
    unexpected_result_labels = sorted(result_labels - _HOST_BRIDGE_PARITY_RESULT_LABELS)
    missing_result_labels = sorted(_HOST_BRIDGE_PARITY_RESULT_LABELS - result_labels)
    if unexpected_result_labels:
        findings.append(
            "Persisted parity report has unexpected OS result labels; regenerate with ops/host_bridge_parity_report.py --output. "
            f"labels={', '.join(unexpected_result_labels)}"
        )
    if missing_result_labels:
        findings.append(
            "Persisted parity report is missing required OS result labels; regenerate with ops/host_bridge_parity_report.py --output. "
            f"labels={', '.join(missing_result_labels)}"
        )
    source_fingerprints: dict[str, str] = {}
    git_heads: dict[str, str] = {}
    parity_run_ids: dict[str, str] = {}
    host_fingerprints: dict[str, str] = {}
    host_platforms: dict[str, str] = {}
    host_names: dict[str, str] = {}
    artifact_shas: dict[str, str] = {}
    artifact_bytes_by_label: dict[str, int] = {}
    artifact_generated_at_by_label: dict[str, int] = {}
    result_ok_by_label: dict[str, Any] = {}
    proofs_by_label: dict[str, dict[str, Any]] = {}
    for label in ("macos", "windows"):
        result = results.get(label) if isinstance(results, dict) else None
        if not isinstance(result, dict) or result.get("present") is not True or result.get("ok") is not True:
            findings.append(f"Persisted parity report does not prove {label}.")
            result_ok_by_label[label] = result.get("ok") if isinstance(result, dict) else None
            continue
        result_ok_by_label[label] = result.get("ok")
        artifact_sha = result.get("artifactSha256")
        if not isinstance(artifact_sha, str) or len(artifact_sha) != 64:
            findings.append(f"Persisted parity report {label} artifactSha256 is missing or invalid; regenerate with ops/host_bridge_parity_report.py --output.")
        else:
            artifact_shas[label] = artifact_sha
        artifact_bytes = result.get("artifactBytes")
        if not isinstance(artifact_bytes, int) or artifact_bytes <= 0:
            findings.append(f"Persisted parity report {label} artifactBytes is missing or invalid; regenerate with ops/host_bridge_parity_report.py --output.")
        else:
            artifact_bytes_by_label[label] = artifact_bytes
        if result.get("proofSchemaVersion") != _HOST_BRIDGE_PARITY_PROOF_SCHEMA_VERSION:
            findings.append(f"Persisted parity report {label} proofSchemaVersion is not 1; regenerate with ops/host_bridge_parity_report.py --output.")
        if result.get("schemaVersion") != 1:
            findings.append(f"Persisted parity report {label} artifact schemaVersion is not 1; regenerate with ops/host_bridge_parity_report.py --output.")
        artifact_generated_at = result.get("generatedAt")
        if not isinstance(artifact_generated_at, int):
            findings.append(f"Persisted parity report {label} artifact is missing generatedAt; regenerate with ops/host_bridge_parity_report.py --output.")
        elif generated_at is not None:
            artifact_generated_at_by_label[label] = artifact_generated_at
            now = now_ms()
            if artifact_generated_at > now + 5 * 60 * 1000:
                findings.append(f"Persisted parity report {label} artifact generatedAt is in the future; regenerate with ops/host_bridge_parity_report.py --output.")
            max_age_hours = float(getattr(settings, "host_bridge_parity_report_max_age_hours", 24.0) or 0.0)
            if max_age_hours > 0:
                max_age_ms = int(max_age_hours * 60 * 60 * 1000)
                age_ms = now - artifact_generated_at
                if age_ms > max_age_ms:
                    findings.append(
                        f"Persisted parity report {label} artifact is stale; regenerate with ops/host_bridge_parity_report.py --output. "
                        f"ageHours={age_ms / 3600000:.1f}; maxAgeHours={max_age_hours:.1f}"
                    )
        fingerprint = result.get("sourceFingerprint")
        if isinstance(fingerprint, str) and len(fingerprint) == 64:
            source_fingerprints[label] = fingerprint
        else:
            findings.append(f"Persisted parity report {label} artifact sourceFingerprint is missing or invalid; regenerate with ops/host_bridge_parity_report.py --output.")
        git_head = result.get("gitHead")
        if isinstance(git_head, str) and len(git_head) == 40:
            git_heads[label] = git_head
        else:
            findings.append(f"Persisted parity report {label} artifact gitHead is missing or invalid; regenerate with ops/host_bridge_parity_report.py --output.")
        host_fingerprint = result.get("hostFingerprint")
        if isinstance(host_fingerprint, str) and len(host_fingerprint) == 64:
            host_fingerprints[label] = host_fingerprint
        else:
            findings.append(f"Persisted parity report {label} artifact hostFingerprint is missing or invalid; regenerate with ops/host_bridge_parity_report.py --output.")
        host_platform = result.get("hostPlatform")
        expected_host_platform = _HOST_BRIDGE_PARITY_RESULT_PLATFORMS[label]
        if isinstance(host_platform, str) and host_platform == expected_host_platform:
            host_platforms[label] = host_platform
        else:
            findings.append(
                f"Persisted parity report {label} artifact hostPlatform must be {expected_host_platform}; "
                "regenerate with ops/host_bridge_parity_report.py --output."
            )
        host_name = str(result.get("hostName") or "").strip()
        if host_name:
            host_names[label] = host_name
        else:
            findings.append(f"Persisted parity report {label} artifact hostName is missing; regenerate with ops/host_bridge_parity_report.py --output.")
        parity_run_id = result.get("parityRunId")
        if isinstance(parity_run_id, str) and _HOST_BRIDGE_PARITY_RUN_ID_RE.fullmatch(parity_run_id):
            parity_run_ids[label] = parity_run_id
        else:
            findings.append(
                f"Persisted parity report {label} artifact parityRunId is missing or invalid; "
                "rerun both full probes with the same --parity-run-id and regenerate with ops/host_bridge_parity_report.py --output."
            )
        proofs = result.get("proofs") if isinstance(result.get("proofs"), dict) else {}
        proofs_by_label[label] = proofs
        for proof_key, proof_label in _REQUIRED_HOST_BRIDGE_PROOF_FACETS[label].items():
            if proofs.get(proof_key) is not True:
                findings.append(
                    f"Persisted parity report {label} lacks required {proof_label}; "
                    "regenerate with ops/host_bridge_parity_report.py --output."
                )
    if len(source_fingerprints) == 2 and len(set(source_fingerprints.values())) != 1:
        findings.append("Persisted parity report sourceFingerprint mismatch; regenerate both macOS and Windows full probe artifacts from the same HostBridge source.")
    if len(git_heads) == 2 and len(set(git_heads.values())) != 1:
        findings.append("Persisted parity report gitHead mismatch; regenerate both macOS and Windows full probe artifacts from the same commit.")
    if len(parity_run_ids) == 2 and len(set(parity_run_ids.values())) != 1:
        findings.append("Persisted parity report parityRunId mismatch; rerun macOS and Windows full probe artifacts with the same --parity-run-id.")
    if len(source_fingerprints) == 2 and len(set(source_fingerprints.values())) == 1:
        details["sourceFingerprint"] = next(iter(source_fingerprints.values()))
    if len(git_heads) == 2 and len(set(git_heads.values())) == 1:
        details["gitHead"] = next(iter(git_heads.values()))
    if len(parity_run_ids) == 2 and len(set(parity_run_ids.values())) == 1:
        details["parityRunId"] = next(iter(parity_run_ids.values()))
    details["hostFingerprint"] = host_fingerprints
    details["hostPlatform"] = host_platforms
    details["hostName"] = host_names
    details["artifactSha256"] = artifact_shas
    details["artifactBytes"] = artifact_bytes_by_label
    details["artifactGeneratedAt"] = artifact_generated_at_by_label
    details["resultOk"] = result_ok_by_label
    details["proofs"] = proofs_by_label
    report_findings = loaded.get("findings")
    if loaded.get("ok") is not True:
        if isinstance(report_findings, list) and report_findings:
            findings.extend(str(item) for item in report_findings[:8])
        else:
            findings.append("Persisted parity report ok is not true.")
    if not findings and loaded.get("ok") is True:
        current_source = host_bridge_source_provenance()
        current_fingerprint = current_source.get("sourceFingerprint")
        current_git_head = current_source.get("gitHead")
        details["currentSourceFingerprint"] = current_fingerprint
        details["currentGitHead"] = current_git_head
        details["currentGitDirty"] = current_source.get("gitDirty")
        expected_proof_id = host_bridge_parity_proof_id(results, current_source, enforce_current_source=True)
        details["expectedProofId"] = expected_proof_id
        if proof_id != expected_proof_id:
            findings.append(
                "Persisted parity report proofId does not match its artifact inputs and current HostBridge source; "
                "regenerate with ops/host_bridge_parity_report.py --output."
            )
        if isinstance(current_fingerprint, str) and len(source_fingerprints) == 2:
            proved_fingerprint = next(iter(source_fingerprints.values()))
            if proved_fingerprint != current_fingerprint:
                findings.append(
                    "Persisted parity report sourceFingerprint does not match current HostBridge source; "
                    "regenerate macOS and Windows full probe artifacts from the current checkout."
                )
        if isinstance(current_git_head, str) and len(git_heads) == 2:
            proved_git_head = next(iter(git_heads.values()))
            if proved_git_head != current_git_head:
                findings.append(
                    "Persisted parity report gitHead does not match current checkout; "
                    "regenerate macOS and Windows full probe artifacts from the current commit."
                )

    return {
        "present": True,
        "ok": not findings,
        "path": str(path),
        "summary": str(loaded.get("summary") or "Persisted HostBridge parity report is available."),
        "generatedAt": generated_at,
        "findings": findings,
        "details": details,
    }


def _host_bridge_connector_proof(
    *,
    local_ready: bool,
    runtime_status: str | None,
    parity_report: dict[str, Any],
) -> dict[str, Any]:
    gaps = list(parity_report.get("findings") or [_HOST_BRIDGE_PARITY_PROOF_GAP])
    detail = str(runtime_status or "").strip()
    if detail and detail != "ready":
        gaps.insert(0, detail)
    if not local_ready:
        if parity_report.get("ok") is True:
            gaps.insert(0, "Persisted parity report is verified, but current local HostBridge runtime is blocked; rerun full proof after fixing this host.")
        return {
            "proof_status": "local_blocked",
            "proof_summary": "Local HostBridge runtime is blocked; cross-OS parity proof cannot pass.",
            "proof_gaps": gaps,
            "proof_details": {**dict(parity_report.get("details") or {}), "localRuntimeStatus": runtime_status},
        }
    if parity_report.get("ok") is True:
        return {
            "proof_status": "cross_os_verified",
            "proof_summary": f"Cross-OS HostBridge parity verified by {parity_report.get('path')}.",
            "proof_gaps": [],
            "proof_details": dict(parity_report.get("details") or {}),
        }
    return {
        "proof_status": "cross_os_unverified",
        "proof_summary": "Local HostBridge runtime is ready, but no verified macOS+Windows full parity report is attached.",
        "proof_gaps": gaps,
        "proof_details": {**dict(parity_report.get("details") or {}), "localRuntimeStatus": runtime_status},
    }


def _unique_host_bridge_gaps(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    gaps: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        gaps.append(text)
    return gaps


def _host_bridge_parity_commands() -> dict[str, str]:
    parity_run_id = f"atrium-{now_ms()}-{uuid.uuid4()}"
    source = host_bridge_source_provenance()
    source_fingerprint = str(source.get("sourceFingerprint") or "")
    macos_artifact = "/tmp/atrium_host_bridge_macos_live.json"
    windows_artifact_on_windows = "C:\\Temp\\atrium_host_bridge_windows_live.json"
    windows_artifact_local = "/tmp/atrium_host_bridge_windows_live.json"
    output = "data/host-bridge-parity-report.json"
    quoted_windows_source = shlex.quote(windows_artifact_on_windows)
    return {
        "parityRunId": parity_run_id,
        "sourceFingerprint": source_fingerprint,
        "macosRunIdExport": f"RUN_ID={parity_run_id}",
        "macosSourceValidate": (
            "uv --project system run python ops/host_bridge_source_summary.py "
            f"--expect-source-fingerprint {source_fingerprint}"
        ),
        "windowsRunIdSet": f'$RunId = "{parity_run_id}"',
        "windowsSourceValidate": (
            "uv --project system run python ops/host_bridge_source_summary.py "
            f"--expect-source-fingerprint {source_fingerprint}"
        ),
        "macosProbe": (
            "uv --project system run python ops/macos_host_bridge_probe.py "
            f"--full --parity-run-id {parity_run_id} "
            f"--expect-source-fingerprint {source_fingerprint} --output {macos_artifact}"
        ),
        "macosArtifact": macos_artifact,
        "macosArtifactValidate": (
            "uv --project system run python ops/host_bridge_artifact_summary.py "
            f"--label macos --expect-parity-run-id {parity_run_id} "
            f"--expect-source-fingerprint {source_fingerprint} {macos_artifact}"
        ),
        "windowsProbe": (
            "uv --project system run python ops/windows_host_bridge_probe.py "
            f"--full --parity-run-id {parity_run_id} "
            f"--expect-source-fingerprint {source_fingerprint} --output {windows_artifact_on_windows}"
        ),
        "windowsLiveProofRunner": (
            "powershell -NoProfile -ExecutionPolicy Bypass -File .\\ops\\windows_host_bridge_live_proof.ps1 "
            f"-ParityRunId {parity_run_id} "
            f"-SourceFingerprint {source_fingerprint} "
            f"-Output {windows_artifact_on_windows}"
        ),
        "windowsArtifactValidateOnWindows": (
            "uv --project system run python ops/host_bridge_artifact_summary.py "
            f"--label windows --expect-parity-run-id {parity_run_id} "
            f"--expect-source-fingerprint {source_fingerprint} {windows_artifact_on_windows}"
        ),
        "windowsArtifactSource": windows_artifact_on_windows,
        "windowsArtifactLocal": windows_artifact_local,
        "windowsArtifactCopyHint": (
            f"Copy the Windows full-probe artifact from {windows_artifact_on_windows} "
            f"on the Windows host to {windows_artifact_local} on this repo host before running verify."
        ),
        "windowsArtifactValidateLocal": (
            "uv --project system run python ops/host_bridge_artifact_summary.py "
            f"--label windows --expect-parity-run-id {parity_run_id} "
            f"--expect-source-fingerprint {source_fingerprint} {windows_artifact_local}"
        ),
        "verify": (
            "uv --project system run python ops/host_bridge_parity_report.py "
            f"--macos {macos_artifact} --windows {windows_artifact_local} "
            f"--windows-source-path {quoted_windows_source} --output {output}"
        ),
    }


def _host_bridge_parity_status_payload() -> dict[str, Any]:
    settings = get_settings()
    host_bridge = HostBridge(settings).status().to_dict()
    parity_report = _host_bridge_parity_report_proof(settings)
    connectors_by_id = {connector["id"]: connector for connector in _connector_catalog()}
    bridge_connectors = [connectors_by_id.get("browser") or {}, connectors_by_id.get("desktop") or {}]
    proof_connectors = [
        {
            "id": str(connector.get("id") or ""),
            "proof_status": connector.get("proofStatus") or "not_required",
            "proof_summary": connector.get("proofSummary"),
            "proof_gaps": list(connector.get("proofGaps") or []),
            "proof_details": dict(connector.get("proofDetails") or {}),
        }
        for connector in bridge_connectors
        if connector
    ]
    proof_statuses = [str(item.get("proof_status") or "") for item in proof_connectors]
    if any(status == "local_blocked" for status in proof_statuses):
        status = "local_blocked"
        summary = "Local HostBridge runtime is blocked; full macOS+Windows parity proof cannot pass."
    elif proof_statuses and all(status == "cross_os_verified" for status in proof_statuses):
        status = "cross_os_verified"
        summary = f"Cross-OS HostBridge parity verified by {parity_report.get('path')}."
    else:
        status = "cross_os_unverified"
        summary = "HostBridge local runtime is ready, but no verified macOS+Windows full parity report is attached."
    gaps = _unique_host_bridge_gaps(
        [
            gap
            for connector in bridge_connectors
            for gap in (connector.get("proofGaps") if connector else []) or []
        ]
        + list(parity_report.get("findings") or [])
    )
    report_details = dict(parity_report.get("details") or {})
    report_payload = {
        **report_details,
        "path": parity_report.get("path"),
        "present": bool(parity_report.get("present")),
        "ok": bool(parity_report.get("ok")),
        "summary": parity_report.get("summary"),
        "generatedAt": parity_report.get("generatedAt"),
        "findings": list(parity_report.get("findings") or []),
    }
    browser_connector = connectors_by_id.get("browser") or {}
    desktop_connector = connectors_by_id.get("desktop") or {}
    local_payload = {
        "platform": host_bridge.get("platform") or sys.platform,
        "browser": {
            "status": browser_connector.get("status"),
            "runtimeStatus": browser_connector.get("runtimeStatus"),
            "readReady": browser_connector.get("readReady"),
            "writeReady": browser_connector.get("writeReady"),
            "proofStatus": browser_connector.get("proofStatus"),
        },
        "desktop": {
            "status": desktop_connector.get("status"),
            "runtimeStatus": desktop_connector.get("runtimeStatus"),
            "readReady": desktop_connector.get("readReady"),
            "writeReady": desktop_connector.get("writeReady"),
            "proofStatus": desktop_connector.get("proofStatus"),
        },
        "browserAutomationReady": host_bridge.get("browserAutomationReady"),
        "desktopAutomationReady": host_bridge.get("desktopAutomationReady"),
        "macosVisualPreflight": {
            "checked": host_bridge.get("macosVisualPreflightChecked"),
            "ok": host_bridge.get("macosVisualPreflightOk"),
            "error": host_bridge.get("macosVisualPreflightError"),
            "checks": host_bridge.get("macosVisualPreflightChecks"),
        },
        "windowsVisualPreflight": {
            "checked": host_bridge.get("windowsVisualPreflightChecked"),
            "ok": host_bridge.get("windowsVisualPreflightOk"),
            "error": host_bridge.get("windowsVisualPreflightError"),
            "checks": host_bridge.get("windowsVisualPreflightChecks"),
        },
    }
    return HostBridgeParityStatusResponse(
        ok=status == "cross_os_verified",
        status=status,
        summary=summary,
        gaps=gaps,
        report=report_payload,
        local=local_payload,
        connectors=proof_connectors,
        commands=_host_bridge_parity_commands(),
    ).dump()


def _connector_catalog() -> list[dict[str, Any]]:
    settings = get_settings()
    host_bridge = HostBridge(settings).status().to_dict()
    parity_report = _host_bridge_parity_report_proof(settings)
    host_platform = str(host_bridge.get("platform") or sys.platform)
    has_browser = bool(host_bridge.get("browserBridge"))
    browser_ready = bool(host_bridge.get("browserAutomationReady"))
    has_desktop = bool(host_bridge.get("desktopBridge"))
    desktop_ready = bool(host_bridge.get("desktopAutomationReady"))
    browser_available = browser_ready if host_platform in {"win32", "darwin"} else has_browser
    desktop_available = desktop_ready if host_platform in {"win32", "darwin"} else has_desktop
    browser_read_ready = True if host_platform == "win32" else has_browser
    desktop_read_ready = has_desktop
    isolated_browser_ready = bool(host_bridge.get("isolatedBrowserProfileReady"))
    browser_playwright_runtime_ready = bool(host_bridge.get("browserPlaywrightReady"))
    browser_playwright_error = str(host_bridge.get("browserPlaywrightError") or "").strip()
    windows_visual_preflight_failed = (
        host_platform == "win32"
        and host_bridge.get("windowsVisualPreflightChecked") is True
        and host_bridge.get("windowsVisualPreflightOk") is False
    )
    windows_visual_preflight_error = str(host_bridge.get("windowsVisualPreflightError") or "").strip()
    windows_visual_preflight_runtime = "Windows visual automation preflight failed"
    if windows_visual_preflight_error:
        windows_visual_preflight_runtime = f"{windows_visual_preflight_runtime}: {windows_visual_preflight_error}"
    macos_visual_preflight_failed = (
        host_platform == "darwin"
        and host_bridge.get("macosVisualPreflightChecked") is True
        and host_bridge.get("macosVisualPreflightOk") is False
    )
    macos_visual_preflight_error = str(host_bridge.get("macosVisualPreflightError") or "").strip()
    macos_visual_preflight_runtime = "macOS visual automation preflight failed"
    if macos_visual_preflight_error:
        macos_visual_preflight_runtime = f"{macos_visual_preflight_runtime}: {macos_visual_preflight_error}"
    browser_runtime = "ready" if browser_ready else (
        f"{host_platform} browser bridge unavailable" if not has_browser else f"{host_platform} browser automation bridge incomplete"
    )
    if host_platform == "win32" and not browser_ready:
        browser_runtime = (
            "Windows browser profile discovery ready; PowerShell automation bridge unavailable"
            if not has_browser
            else (
                f"Windows browser profile discovery ready; {windows_visual_preflight_runtime}"
                if windows_visual_preflight_failed
                else "Windows browser profile discovery ready; browser automation requires an interactive desktop session"
            )
        )
    if browser_ready and not isolated_browser_ready:
        browser_runtime = "default browser ready; isolated browser profile app missing"
    elif browser_ready and isolated_browser_ready and not browser_playwright_runtime_ready:
        detail = browser_playwright_error or "Playwright package missing"
        browser_runtime = f"browser bridge ready; browser.snapshot/browser.act blocked: {detail}"
    desktop_runtime = "ready" if desktop_ready else (
        f"{host_platform} desktop bridge unavailable" if not has_desktop else f"{host_platform} desktop automation bridge incomplete"
    )
    if host_platform == "win32" and not desktop_ready and has_desktop and windows_visual_preflight_failed:
        desktop_runtime = windows_visual_preflight_runtime
    if host_platform == "darwin" and not desktop_ready and has_desktop and macos_visual_preflight_failed:
        desktop_runtime = macos_visual_preflight_runtime
    browser_proof_ready = browser_ready
    browser_proof_runtime = browser_runtime
    if host_platform == "win32" and windows_visual_preflight_failed:
        browser_proof_ready = False
        browser_proof_runtime = windows_visual_preflight_runtime
    if host_platform == "darwin" and macos_visual_preflight_failed:
        browser_proof_ready = False
        browser_proof_runtime = macos_visual_preflight_runtime
    browser_requires = (
        ["PowerShell", "Win32 input APIs", "interactive user session", "DPI-aware visual preflight"]
        if host_platform == "win32"
        else ["open", "screencapture", "osascript"]
    )
    if not isolated_browser_ready:
        browser_requires = [*browser_requires, "Chrome/Edge/Brave/Chromium for isolated profile"]
    if not (shutil.which("node") or shutil.which("node.exe")):
        browser_requires = [*browser_requires, "Node.js for browser.snapshot/browser.act"]
    browser_requires = [*browser_requires, "Playwright package for browser.snapshot/browser.act"]
    browser_capabilities = [
        "profiles",
        *(['own_profile'] if isolated_browser_ready else []),
        *(['dom_snapshot', 'ref_action'] if browser_playwright_runtime_ready else []),
        "open_url",
        "screenshot",
        "coordinate_click",
        "type_text",
        "keypress",
        "paste_text",
        "scroll",
    ]
    desktop_requires = (
        ["PowerShell", "Win32 input APIs", "interactive user session", "DPI-aware visual preflight"]
        if host_platform == "win32"
        else ["osascript", "screencapture", "macOS Accessibility permission", "foreground-controllable user session"]
    )
    sandbox_available, sandbox_runtime_status = _sandbox_runtime_status()
    mcp_endpoint = _mcp_gateway_endpoint()
    mcp_gateway_health = _mcp_gateway_health(settings)
    external_write_requirements = _external_write_requirements_with_gateway_health(settings, mcp_gateway_health)
    mcp_status: ConnectorStatus = "configured" if mcp_endpoint and mcp_gateway_health.get("ok") else ("blocked_by_runtime" if mcp_endpoint else "available")
    if mcp_endpoint and not mcp_gateway_health.get("ok"):
        mcp_runtime_status = f"gateway configured at {mcp_endpoint}; health probe not ready"
    else:
        mcp_runtime_status = f"gateway configured at {mcp_endpoint}" if mcp_endpoint else "local fallback ready"
    connectors = [
        Connector(
            id="local_file",
            name="Local file import",
            kind="local_file",
            status="available",
            description="Import readable local files into a department workspace, artifact store, and RAG entry.",
            tools=["fs.list", "fs.read", "fs.write", "fs.patch", "fs.delete"],
            capabilities=["import_file", "workspace_files", "artifact_versioning", "knowledge_ingest"],
            requires=[],
            runtime_status="ready",
            read_ready=True,
            write_ready=True,
            local_fallback=True,
        ),
        Connector(
            id="git",
            name="Git workspace",
            kind="git",
            status="available" if shutil.which("git") else "blocked_by_runtime",
            description="Inspect diff/status, create commits, and push through Owner Mode policy.",
            tools=["git.status", "git.diff", "git.commit", "git.push"],
            capabilities=["status", "diff", "commit", "push"],
            requires=["git"],
            runtime_status="ready" if shutil.which("git") else "git missing",
            read_ready=bool(shutil.which("git")),
            write_ready=bool(shutil.which("git")),
            local_fallback=True,
            external_write_requires=[],
        ),
        Connector(
            id="sandbox",
            name="Sandbox command runner",
            kind="sandbox",
            status="available" if sandbox_available else "blocked_by_runtime",
            description="Run bounded commands in Docker when ready, or fall back to local argv in the department workspace.",
            tools=["sandbox.exec"],
            capabilities=["isolated_command", "workspace_mount", "local_fallback"],
            requires=["docker optional"],
            runtime_status=sandbox_runtime_status,
            read_ready=sandbox_available,
            write_ready=sandbox_available,
            local_fallback=True,
        ),
        Connector(
            id="http",
            name="HTTP/API",
            kind="http",
            status="available",
            description="Perform bounded HTTP reads and approval-gated external mutations.",
            tools=["http.get", "http.post", "http_get"],
            capabilities=["http_get", "http_post"],
            requires=[],
            runtime_status="ready",
            read_ready=True,
            write_ready=True,
            external_write_requires=[],
        ),
        Connector(
            id="web",
            name="Web search and page read",
            kind="web",
            status="available",
            description="Search and read public web pages without paid API keys. Uses configured SearXNG when present, otherwise DuckDuckGo HTML for search; browser screenshots remain available for visual pages.",
            tools=["web.search", "web.fetch"],
            capabilities=["key_free_search", "duckduckgo_html", "searxng_optional", "page_text_extract", "image_url_extract"],
            requires=[],
            runtime_status=(
                "SearXNG configured; DuckDuckGo fallback ready"
                if get_settings().web_search_searxng_url
                else "DuckDuckGo key-free fallback ready"
            ),
            read_ready=True,
            write_ready=False,
            local_fallback=True,
            external_write_requires=[],
        ),
        Connector(
            id="browser",
            name="Browser bridge",
            kind="browser",
            status="available" if browser_available else "blocked_by_runtime",
            description="Open browser targets, inspect DOM snapshots in an isolated profile, capture visible state, and route keyboard/mouse/scroll input through the local OS automation bridge when permitted.",
            tools=["browser.profiles", "browser.open", "browser.snapshot", "browser.act", "browser.screenshot", "browser.click", "browser.type", "browser.keypress", "browser.paste_text", "browser.scroll"],
            capabilities=browser_capabilities,
            requires=browser_requires,
            runtime_status=browser_runtime,
            read_ready=browser_read_ready,
            write_ready=browser_ready,
            local_fallback=True,
            **_host_bridge_connector_proof(
                local_ready=browser_proof_ready,
                runtime_status=browser_proof_runtime,
                parity_report=parity_report,
            ),
        ),
        Connector(
            id="desktop",
            name="Desktop bridge",
            kind="desktop",
            status="available" if desktop_available else "blocked_by_runtime",
            description="Use app discovery, accessibility/UIA snapshots, screenshots, local notifications, and keyboard/mouse input through the local OS automation bridge.",
            tools=["desktop.apps", "desktop.snapshot", "desktop.act", "desktop.open_app", "desktop.activate_app", "desktop.quit_app", "desktop.screenshot", "desktop.click", "desktop.type", "desktop.keypress", "desktop.paste_text", "desktop.scroll", "notify.send"],
            capabilities=["apps", "accessibility_snapshot", "ref_action", "open_app", "activate_app", "quit_app", "screenshot", "click", "type_text", "keypress", "paste_text", "scroll", "notification"],
            requires=desktop_requires,
            runtime_status=desktop_runtime,
            read_ready=desktop_read_ready,
            write_ready=desktop_ready,
            local_fallback=True,
            **_host_bridge_connector_proof(
                local_ready=desktop_ready,
                runtime_status=desktop_runtime,
                parity_report=parity_report,
            ),
        ),
        Connector(
            id="mcp",
            name="MCP external tools",
            kind="mcp",
            status=mcp_status,
            description="Policy/audit surface for external MCP servers such as GitHub, email, calendar, Notion, or Drive.",
            tools=["mcp.call"],
            capabilities=["external_tool_call", "local_fallback"],
            requires=[],
            runtime_status=mcp_runtime_status,
            read_ready=not bool(mcp_endpoint) or bool(mcp_gateway_health.get("ok")),
            write_ready=bool(mcp_endpoint) and not external_write_requirements,
            local_fallback=not bool(mcp_endpoint),
            external_write_requires=[] if mcp_endpoint and not external_write_requirements else external_write_requirements,
        ),
    ]
    for item in MCP_EXTERNAL_CONNECTORS:
        server = item["server"]
        local_supported = server in KNOWN_LOCAL_MCP_SERVERS
        gateway_ready = bool(mcp_endpoint) and bool(mcp_gateway_health.get("ok"))
        configured = gateway_ready
        configured_but_unhealthy = bool(mcp_endpoint) and not gateway_ready
        local_fallback = not mcp_endpoint and local_supported
        available = configured or local_fallback
        write_capabilities = list(item.get("writeCapabilities") or [])
        external_write_requires = [] if configured and not external_write_requirements else external_write_requirements
        write_ready = bool(write_capabilities) and configured and not external_write_requires
        if configured:
            runtime_status = (
                f"server {server} enabled through MCP gateway"
                if write_ready or not write_capabilities
                else f"server {server} gateway configured but external writes require: {', '.join(external_write_requires)}"
            )
        elif configured_but_unhealthy:
            runtime_status = f"server {server} gateway configured but health probe is not ready"
        elif local_supported:
            runtime_status = (
                f"server {server} local fallback is read/status/guidance only; external writes require MCP gateway"
                if write_capabilities
                else f"server {server} available through local fallback"
            )
        else:
            runtime_status = "no local fallback and no MCP gateway configured"
        connectors.append(Connector(
            id=item["id"],
            name=item["name"],
            kind="mcp",
            status="configured" if configured else ("available" if available else "blocked_by_runtime"),
            description=item["description"],
            tools=["mcp.call"],
            capabilities=item["capabilities"],
            requires=[],
            runtime_status=runtime_status,
            read_ready=available,
            write_ready=write_ready,
            local_fallback=local_fallback,
            external_write_requires=[] if write_ready else external_write_requires,
        ))
    return [connector.dump() for connector in connectors]


def _credential_readiness_status(settings: Any) -> dict[str, Any]:
    from .provider.chatgpt_oauth import chatgpt_oauth_status
    mcp_endpoint = _mcp_gateway_endpoint()
    mcp_gateway_health = _mcp_gateway_health(settings)
    mcp_token_status = _mcp_gateway_token_status_light(settings)
    chatgpt_status = chatgpt_oauth_status(settings)
    provider_light = provider_health(settings, probe_accounts=False)
    claude_code_status = provider_light.get("claudeCodeAuth") or {}
    claude_code_ready = claude_code_status.get("ready")
    claude_code_account = claude_code_ready if isinstance(claude_code_ready, bool) else None
    gh_installed = bool(resolve_local_executable("gh"))
    git_installed = bool(shutil.which("git"))
    ssh_agent_present = bool(os.environ.get("SSH_AUTH_SOCK"))
    keychain_cli_available = bool(shutil.which("security"))
    credentials_enabled = bool(getattr(settings, "entitlement_credentials", False))
    external_send_enabled = bool(getattr(settings, "entitlement_external_send", False))
    external_tools = ["git.push", "http.post", "mcp.call with changesExternalState=true", "custom external_send tools"]
    credential_sources = {
        "providerToken": bool(getattr(settings, "anthropic_auth_token", "")),
        "openAIPlatformKey": bool(getattr(settings, "openai_api_key", "")),
        "chatgptAccountOAuth": bool(chatgpt_status.get("ready")),
        "chatgptAccountOAuthSource": chatgpt_status.get("source"),
        "claudeCodeAccount": claude_code_account,
        "claudeCodeAuthMethod": claude_code_status.get("authMethod"),
        "claudeCodeSubscriptionType": claude_code_status.get("subscriptionType"),
        "mcpGatewayConfigured": bool(mcp_endpoint),
        "mcpGatewayToken": _mcp_gateway_token_configured_light(settings),
        "mcpGatewayTokenSource": mcp_token_status.get("source"),
        "mcpGatewayTokenKeychainServiceConfigured": bool(mcp_token_status.get("keychainServiceConfigured")),
        "githubCliInstalled": gh_installed,
        "sshAgentPresent": ssh_agent_present,
        "keychainCliAvailable": keychain_cli_available,
    }
    external_send_gaps: list[str] = []
    credential_gaps: list[str] = []
    if not mcp_endpoint:
        external_send_gaps.append("no external MCP gateway configured for email/calendar/notion/drive write operations")
    elif not credential_sources["mcpGatewayToken"]:
        external_send_gaps.append("MCP gateway is configured without an env or Keychain token")
    elif mcp_gateway_health.get("checked") and not mcp_gateway_health.get("ok"):
        external_send_gaps.append("MCP gateway health probe failed")
    if not git_installed:
        external_send_gaps.append("git is not installed for git.push")
    if not credential_sources["mcpGatewayToken"] and mcp_endpoint:
        credential_gaps.append("MCP gateway is configured without an env or Keychain token")
    elif mcp_gateway_health.get("checked") and not mcp_gateway_health.get("ok"):
        credential_gaps.append("MCP gateway health probe failed")
    if not gh_installed:
        credential_gaps.append("GitHub CLI is not installed for authenticated GitHub operations")
    if not ssh_agent_present:
        credential_gaps.append("SSH_AUTH_SOCK is not present for SSH-backed private remotes")
    external_write_requirements = _external_write_requirements_with_gateway_health(settings, mcp_gateway_health)
    mcp_gateway_ready = bool(mcp_endpoint and not external_write_requirements)
    git_push_requirements: list[str] = []
    if not git_installed:
        git_push_requirements.append("git installed")
    http_post_requirements: list[str] = []
    mcp_write_requirements = list(external_write_requirements)
    external_write_channels = {
        "gitPush": {
            "ready": bool(git_installed),
            "requirements": git_push_requirements,
            "verifyCommands": ["git remote -v", "gh auth status"],
        },
        "httpPost": {
            "ready": True,
            "requirements": http_post_requirements,
            "verifyCommands": ["curl -fsS http://127.0.0.1:8787/api/runtime"],
        },
        "mcpExternalWrites": {
            "ready": mcp_gateway_ready,
            "requirements": mcp_write_requirements,
            "verifyCommands": [
                "curl -fsS http://127.0.0.1:8787/api/runtime",
                "curl -fsS http://127.0.0.1:8787/api/connectors | jq '.[] | select(.kind==\"mcp\")'",
            ],
        },
    }
    launch_agent_path = Path.home() / "Library/LaunchAgents/com.atrium.system.plist"
    restart_command = "launchctl kickstart -k gui/$(id -u)/com.atrium.system"
    enablement_checkpoint = {
        "createdBy": "manual LaunchAgent environment update",
        "backupPattern": f"{launch_agent_path.name}.rollback-<timestamp>",
        "restoreCommand": f"cp <backup-path> {launch_agent_path}",
        "rollbackCommand": f"cp <backup-path> {launch_agent_path} && {restart_command}",
        "restartAfterRestoreCommand": restart_command,
        "why": "Every LaunchAgent environment write gets a local rollback plist before the atomic write.",
    }
    enablement_steps = [
        {
            "id": "credentials-entitlement",
            "ready": credentials_enabled,
            "env": {"ATRIUM_ENTITLEMENT_CREDENTIALS": "true"},
            "why": "Allows credential-bearing tool calls after account credentials are prepared; secrets stay outside logs.",
        },
        {
            "id": "external-send-entitlement",
            "ready": external_send_enabled,
            "env": {"ATRIUM_ENTITLEMENT_EXTERNAL_SEND": "true"},
            "why": "Allows tools that intentionally change external state, with checkpoint/audit metadata.",
        },
        {
            "id": "mcp-gateway",
            "ready": bool(mcp_endpoint and credential_sources["mcpGatewayToken"]),
            "env": {
                "ATRIUM_MCP_GATEWAY_URL": "<write-capable MCP gateway URL>",
                "ATRIUM_MCP_GATEWAY_TOKEN": "<secret token, or use Keychain fields below>",
                "ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_SERVICE": "atrium.mcp.gateway",
                "ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_ACCOUNT": "atrium",
                "ATRIUM_MCP_ENABLED_SERVERS": "github,email,calendar,notion,drive",
            },
            "keychainCommand": "security add-generic-password -U -s atrium.mcp.gateway -a atrium -w '<secret token>'",
            "why": "Required for email/calendar/Notion/Drive write operations; local MCP fallback is read/status/guidance only.",
        },
        {
            "id": "restart-runtime",
            "ready": False,
            "command": restart_command,
            "why": "Reloads LaunchAgent environment after entitlement/gateway variables change.",
        },
        {
            "id": "verify",
            "ready": bool(not external_send_gaps and not credential_gaps),
            "commands": [
                "curl -fsS http://127.0.0.1:8787/api/runtime | jq '.v2.credentialReadiness'",
                "curl -fsS http://127.0.0.1:8787/api/connectors | jq '.[] | select(.kind==\"mcp\")'",
            ],
            "why": "Confirms strict environment readiness after setup without exposing secret values.",
        },
    ]
    return {
        "externalSendReady": not external_send_gaps,
        "credentialsReady": not credential_gaps,
        "externalSendTools": external_tools,
        "externalSendCapabilities": {
            "gitPush": bool(external_write_channels["gitPush"]["ready"]),
            "httpPost": bool(external_write_channels["httpPost"]["ready"]),
            "mcpExternalWrites": bool(external_write_channels["mcpExternalWrites"]["ready"]),
        },
        "externalWriteChannels": external_write_channels,
        "credentialSources": credential_sources,
        "chatgptAccountOAuth": chatgpt_status,
        "claudeCodeAuth": claude_code_status,
        "mcpGatewayTokenStatus": mcp_token_status,
        "mcpGatewayHealth": mcp_gateway_health,
        "mcpGatewayEndpointConfigured": bool(mcp_endpoint),
        "mcpEnabledServers": sorted(_mcp_enabled_servers()),
        "localFallbackServers": sorted(KNOWN_LOCAL_MCP_SERVERS),
        "externalWriteRequirements": external_write_requirements,
        "externalSendGaps": external_send_gaps,
        "credentialGaps": credential_gaps,
        "enablementPlan": {
            "launchAgentPath": str(launch_agent_path),
            "restartCommand": restart_command,
            "checkpoint": enablement_checkpoint,
            "secretValuesRedacted": True,
            "steps": enablement_steps,
        },
        "note": (
            "Provider tokens make LLM/runtime calls available, but tool-level "
            "credential/external-send entitlements stay false until account "
            "credentials and write destinations are explicitly prepared."
        ),
    }


def _tool_policy_decision(
    run: dict[str, Any],
    *,
    require_approval: bool | None,
    policy: dict[str, Any],
    running: bool,
) -> PolicyDecision:
    return tool_policy_decision(run, policy, require_approval=require_approval, running=running)  # type: ignore[return-value]


REDACTED_VALUE = "[redacted]"
SENSITIVE_ARG_KEYS = {
    "authorization",
    "auth",
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "token",
    "cookie",
    "credential",
    "private_key",
    "refresh_token",
    "env",
    "stdin",
    "body",
    "content",
    "text",
}
SENSITIVE_TEXT_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key=",
    "apikey=",
    "access_key=",
    "secret=",
    "client_secret=",
    "password=",
    "passwd=",
    "token=",
    "cookie=",
    "private_key=",
    "refresh_token=",
)
CREDENTIAL_CONTENT_KEYS = {"body", "content", "text"}
CREDENTIAL_ARG_KEYS = SENSITIVE_ARG_KEYS - CREDENTIAL_CONTENT_KEYS


def _sensitive_arg_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SENSITIVE_ARG_KEYS or any(part in normalized for part in (
        "secret",
        "password",
        "passwd",
        "token",
        "credential",
        "private_key",
    ))


def _credential_arg_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in CREDENTIAL_ARG_KEYS or any(part in normalized for part in (
        "secret",
        "password",
        "passwd",
        "token",
        "credential",
        "private_key",
    ))


def _value_contains_secret_marker(value: str) -> bool:
    return any(marker in value.lower() for marker in SENSITIVE_TEXT_MARKERS)


def _tool_args_contain_credential(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _credential_arg_key(str(key)):
                return True
            if _tool_args_contain_credential(item):
                return True
        return False
    if isinstance(value, list):
        return any(_tool_args_contain_credential(item) for item in value)
    if isinstance(value, str):
        return _value_contains_secret_marker(value)
    return False


def _redact_tool_arg_value(value: Any, path: str) -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        fields: list[str] = []
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if _sensitive_arg_key(str(key)):
                out[key] = REDACTED_VALUE
                fields.append(child_path)
                continue
            out[key], child_fields = _redact_tool_arg_value(item, child_path)
            fields.extend(child_fields)
        return out, fields
    if isinstance(value, list):
        out_list: list[Any] = []
        fields: list[str] = []
        for idx, item in enumerate(value):
            child, child_fields = _redact_tool_arg_value(item, f"{path}[{idx}]")
            out_list.append(child)
            fields.extend(child_fields)
        return out_list, fields
    if isinstance(value, str) and _value_contains_secret_marker(value):
        return REDACTED_VALUE, [path]
    return value, []


def _redact_tool_result_value(value: Any, path: str) -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        fields: list[str] = []
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if _credential_arg_key(str(key)):
                out[key] = REDACTED_VALUE
                fields.append(child_path)
                continue
            out[key], child_fields = _redact_tool_result_value(item, child_path)
            fields.extend(child_fields)
        return out, fields
    if isinstance(value, list):
        out_list: list[Any] = []
        fields: list[str] = []
        for idx, item in enumerate(value):
            child, child_fields = _redact_tool_result_value(item, f"{path}[{idx}]")
            out_list.append(child)
            fields.extend(child_fields)
        return out_list, fields
    if isinstance(value, str) and _value_contains_secret_marker(value):
        return REDACTED_VALUE, [path]
    return value, []


def _public_tool_run(run: dict[str, Any]) -> dict[str, Any]:
    public = {**run}
    args, arg_fields = _redact_tool_arg_value(run.get("args") or {}, "args")
    result, result_fields = _redact_tool_result_value(run.get("result"), "result")
    public["args"] = args
    public["result"] = result
    public["argsRedacted"] = bool(arg_fields)
    public["redactedFields"] = sorted(set(arg_fields + result_fields))
    return public


def _redact_audit_entry(entry: dict[str, Any]) -> dict[str, Any]:
    public = {**entry}
    redacted_fields: list[str] = list(public.get("redactedFields") or [])
    for key in ("title", "detail", "author"):
        value = public.get(key)
        if value is None:
            continue
        redacted_value, fields = _redact_tool_result_value(value, key)
        public[key] = redacted_value
        redacted_fields.extend(fields)
    refs, refs_fields = _redact_tool_result_value(public.get("refs") or {}, "refs")
    public["refs"] = refs
    redacted_fields.extend(refs_fields)
    public["redactedFields"] = sorted(set(redacted_fields))
    public["redacted"] = bool(public["redactedFields"])
    return public


def _audit_export_markdown(
    rows: list[dict[str, Any]],
    *,
    exported_at: int,
    dept_id: str | None,
    kind: str | None,
) -> str:
    scope = []
    if dept_id:
        scope.append(f"deptId={dept_id}")
    if kind:
        scope.append(f"kind={kind}")
    lines = [
        "# ATRIUM Audit Export",
        "",
        f"- Exported at: {exported_at}",
        f"- Rows: {len(rows)}",
        f"- Scope: {', '.join(scope) if scope else 'all'}",
        "- Public redaction: enabled",
        "",
    ]
    for row in rows:
        refs = {k: v for k, v in (row.get("refs") or {}).items() if v is not None}
        lines.extend([
            f"## {row.get('ts')} {row.get('kind')}: {row.get('title')}",
            "",
            f"- ID: `{row.get('id')}`",
            f"- Severity: `{row.get('severity')}`",
            f"- Department: `{row.get('departmentId') or ''}`",
            f"- Author: `{row.get('author') or ''}`",
            f"- Redacted fields: `{', '.join(row.get('redactedFields') or [])}`",
            "",
            str(row.get("detail") or "").strip(),
            "",
        ])
        if refs:
            lines.extend([
                "```json",
                json.dumps(refs, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ])
    return "\n".join(lines).rstrip() + "\n"


def _run_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = 10.0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=False,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if exc.stdout is not None else getattr(exc, "output", None)
        stderr = _decode_process_output(exc.stderr) or f"command timed out after {timeout}s"
        return {
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "returnCode": None,
            "timeout": True,
            "stdout": _clip_text(_decode_process_output(stdout)),
            "stderr": _clip_text(stderr),
        }
    return {
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "returnCode": completed.returncode,
        "stdout": _clip_text(_decode_process_output(completed.stdout)),
        "stderr": _clip_text(_decode_process_output(completed.stderr)),
    }


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _decode_process_bytes(value)
    return str(value)


def _decode_process_bytes(value: bytes) -> str:
    if not value:
        return ""
    encodings = ["utf-8-sig", "utf-8", locale.getpreferredencoding(False)]
    if sys.platform == "win32":
        encodings.extend(["mbcs", "cp65001", "cp874", "cp1252", "cp437", "cp850"])
    seen: set[str] = set()
    for encoding in encodings:
        normalized = str(encoding or "").strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        try:
            return value.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


def _shell_timeout_seconds(args: dict[str, Any], *, default: float = 10.0) -> float | None:
    raw = args.get("timeoutSeconds")
    if raw is None:
        raw = args.get("timeout") or args.get("timeout_seconds")
    if raw is None:
        return default
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="shell.exec timeoutSeconds must be a number")
    return None if timeout <= 0 else timeout


def _tool_process_error(tool: str, result: Any) -> str | None:
    bridge_error = visual_process_error(tool, result)
    if bridge_error:
        return bridge_error
    if not isinstance(result, dict):
        return None
    return_code = result.get("returnCode")
    if return_code is None:
        return None
    try:
        if int(return_code) == 0:
            return None
    except (TypeError, ValueError):
        pass
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    detail = stderr or stdout
    if detail:
        return f"{tool} command exited with code {return_code}: {detail[:1000]}"
    return f"{tool} command exited with code {return_code}"


KEY_CODE_MAP = {
    "return": 36,
    "enter": 36,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "backspace": 51,
    "escape": 53,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "home": 115,
    "end": 119,
    "pageup": 116,
    "page_up": 116,
    "pagedown": 121,
    "page_down": 121,
}
MODIFIER_MAP = {
    "cmd": "command",
    "command": "command",
    "meta": "command",
    "ctrl": "control",
    "control": "control",
    "alt": "option",
    "option": "option",
    "shift": "shift",
}


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _keypress_script(keys: Any) -> str:
    if not isinstance(keys, list) or not keys or not all(isinstance(key, str) for key in keys):
        raise HTTPException(status_code=400, detail="keypress tools require keys as a string list")
    normalized = [key.strip().lower() for key in keys if key.strip()]
    modifiers = [MODIFIER_MAP[key] for key in normalized if key in MODIFIER_MAP]
    key_parts = [key for key in normalized if key not in MODIFIER_MAP]
    if len(key_parts) != 1:
        raise HTTPException(status_code=400, detail="keypress tools require exactly one non-modifier key")
    key = key_parts[0]
    suffix = f" using {{{', '.join(f'{mod} down' for mod in modifiers)}}}" if modifiers else ""
    if key in KEY_CODE_MAP:
        return f'tell application "System Events" to key code {KEY_CODE_MAP[key]}{suffix}'
    if len(key) == 1:
        return f'tell application "System Events" to keystroke {_applescript_string(key)}{suffix}'
    raise HTTPException(status_code=400, detail=f"unsupported key name: {key}")


def _execute_type_text(args: dict[str, Any]) -> dict[str, Any]:
    return execute_visual_type_text(args, _run_process)


def _execute_keypress(args: dict[str, Any]) -> dict[str, Any]:
    return execute_visual_keypress(args, _run_process)


def _execute_paste_text(args: dict[str, Any]) -> dict[str, Any]:
    return execute_visual_paste_text(args, _run_process)


def _http_headers(raw: Any) -> dict[str, str]:
    headers = {"User-Agent": "ATRIUM/0.4 tool-runner"}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, str):
                headers[key] = value
    return headers


def _execute_http_request(args: dict[str, Any], *, method: str) -> dict[str, Any]:
    url = args.get("url")
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{method.lower()} requires an http(s) URL")
    timeout = max(1.0, min(float(args.get("timeoutSeconds", 10)), 30.0))
    data: bytes | None = None
    headers = _http_headers(args.get("headers"))
    if method == "POST":
        if args.get("json") is not None:
            data = json.dumps(args.get("json"), ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif args.get("body") is not None:
            data = str(args.get("body")).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read(100_000)
            return {
                "url": res.geturl(),
                "status": res.status,
                "contentType": res.headers.get("content-type", ""),
                "body": _clip_text(body.decode("utf-8", errors="ignore")),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(40_000).decode("utf-8", errors="ignore")
        return {"url": url, "status": exc.code, "contentType": exc.headers.get("content-type", ""), "body": body}


def _file_op_paths(dept_id: str, args: dict[str, Any]) -> tuple[Path, Path, Path, bool, bool]:
    source_raw = args.get("sourcePath") or args.get("source") or args.get("from") or args.get("path")
    destination_raw = args.get("destinationPath") or args.get("destination") or args.get("to")
    source, root, source_inside = _tool_path_info(dept_id, source_raw)
    destination, _, destination_inside = _tool_path_info(dept_id, destination_raw)
    return source, destination, root, source_inside, destination_inside


def _resolve_file_destination(source: Path, destination: Path) -> Path:
    return destination / source.name if destination.exists() and destination.is_dir() else destination


def _remove_existing_destination(destination: Path, *, overwrite: bool) -> None:
    if not destination.exists():
        return
    if not overwrite:
        raise HTTPException(status_code=409, detail="destination already exists; pass overwrite=true")
    if destination.is_dir():
        shutil.rmtree(destination)
    else:
        destination.unlink()


def _copy_path(source: Path, destination: Path, *, overwrite: bool, recursive: bool) -> dict[str, Any]:
    if not source.exists():
        raise HTTPException(status_code=404, detail="source path not found")
    destination = _resolve_file_destination(source, destination)
    if source.is_dir():
        if not recursive:
            raise HTTPException(status_code=400, detail="directory copy requires recursive=true")
        _remove_existing_destination(destination, overwrite=overwrite)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        return {"sourcePath": str(source), "destinationPath": str(destination), "copied": True, "type": "dir"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    _remove_existing_destination(destination, overwrite=overwrite)
    shutil.copy2(source, destination)
    return {
        "sourcePath": str(source),
        "destinationPath": str(destination),
        "copied": True,
        "type": "file",
        "bytes": destination.stat().st_size,
    }


def _move_path(source: Path, destination: Path, *, overwrite: bool, recursive: bool) -> dict[str, Any]:
    if not source.exists():
        raise HTTPException(status_code=404, detail="source path not found")
    if source.is_dir() and not recursive:
        raise HTTPException(status_code=400, detail="directory move requires recursive=true")
    destination = _resolve_file_destination(source, destination)
    _remove_existing_destination(destination, overwrite=overwrite)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return {"sourcePath": str(source), "destinationPath": str(destination), "moved": True, "type": "dir" if destination.is_dir() else "file"}


def _default_import_output_path(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name or "index.txt"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "index.txt"
    return f"imports/{uid('url')}_{safe_name[:80]}"


def _execute_import_url(args: dict[str, Any], *, dept_id: str) -> dict[str, Any]:
    url = args.get("url")
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="import.url requires an http(s) URL")
    timeout = max(1.0, min(float(args.get("timeoutSeconds", 10)), 60.0))
    max_bytes = max(1, min(int(args.get("maxBytes") or 5_000_000), 20_000_000))
    output_path = args.get("outputPath") or args.get("path") or _default_import_output_path(str(url))
    target = _tool_target_path(dept_id, output_path)
    req = urllib.request.Request(str(url), headers=_http_headers(args.get("headers")), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = res.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise HTTPException(status_code=413, detail=f"URL response exceeds maxBytes={max_bytes}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return {
                "url": res.geturl(),
                "status": res.status,
                "contentType": res.headers.get("content-type", ""),
                "path": str(target),
                "bytes": len(data),
                "imported": True,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(40_000).decode("utf-8", errors="ignore")
        raise HTTPException(status_code=exc.code, detail=_clip_text(body, 1000) or f"URL returned {exc.code}")


def _execute_mcp_call(args: dict[str, Any], *, dept_id: str) -> dict[str, Any]:
    runtime_block = _mcp_runtime_block_reason(args)
    if runtime_block:
        raise HTTPException(status_code=400, detail=runtime_block)
    server = str(args.get("server") or "").strip()
    tool_name = str(args.get("tool") or "").strip()
    if not tool_name:
        raise HTTPException(status_code=400, detail="mcp.call requires tool")
    arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
    timeout = max(1.0, min(float(args.get("timeoutSeconds") or get_settings().mcp_timeout_s), 120.0))
    if not _mcp_gateway_endpoint():
        return execute_local_mcp_call(server, tool_name, arguments, cwd=_workspace_for_dept(dept_id))
    payload = {
        "server": server,
        "tool": tool_name,
        "arguments": arguments,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ATRIUM/0.4 mcp-tool-runner",
    }
    token = mcp_gateway_token_value(get_settings())
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        _mcp_gateway_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read(200_000)
            text = body.decode("utf-8", errors="ignore")
            parsed: Any
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(text)
                return {
                    "server": server,
                    "tool": tool_name,
                    "status": res.status,
                    "contentType": res.headers.get("content-type", ""),
                    "response": parsed,
                }
            return {
                "server": server,
                "tool": tool_name,
                "status": res.status,
                "contentType": res.headers.get("content-type", ""),
                "body": _clip_text(text),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(40_000).decode("utf-8", errors="ignore")
        raise HTTPException(status_code=502, detail=f"MCP gateway returned {exc.code}: {_clip_text(body, 1000)}")


def _reject_dangerous_delete(path: Path, workspace_root: Path) -> None:
    protected = {Path("/").resolve(), Path.home().resolve(), workspace_root.resolve()}
    if path.resolve() in protected:
        raise HTTPException(status_code=400, detail="refusing to delete a protected root path")


def _execute_tool_sync(run: dict[str, Any]) -> dict[str, Any]:
    args = run.get("args") or {}
    dept_id = run["departmentId"]
    tool = _canonical_tool(run["tool"])
    if tool == "fs.list":
        path, _, _ = _tool_path_info(dept_id, args.get("path"), default=".")
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=404, detail="directory not found")
        entries = []
        for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]:
            entries.append({
                "name": item.name,
                "path": str(item),
                "type": "dir" if item.is_dir() else "file",
                "bytes": item.stat().st_size if item.exists() and item.is_file() else None,
            })
        return {"path": str(path), "entries": entries, "count": len(entries)}
    if tool == "fs.read":
        path = _tool_target_path(dept_id, args.get("path"))
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        text = path.read_text(encoding="utf-8", errors="ignore")
        return {"path": str(path), "text": _clip_text(text), "bytes": path.stat().st_size}
    if tool == "fs.write":
        text = args.get("text")
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="fs.write requires text")
        path = _tool_target_path(dept_id, args.get("path"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return {"path": str(path), "bytes": len(text.encode("utf-8"))}
    if tool == "fs.patch":
        old_text = args.get("oldText")
        new_text = args.get("newText")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise HTTPException(status_code=400, detail="fs.patch requires oldText and newText")
        path = _tool_target_path(dept_id, args.get("path"))
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        current = path.read_text(encoding="utf-8", errors="ignore")
        if old_text not in current:
            raise HTTPException(status_code=409, detail="oldText not found")
        updated = current.replace(old_text, new_text, 1)
        path.write_text(updated, encoding="utf-8")
        return {"path": str(path), "bytes": len(updated.encode("utf-8")), "replacements": 1}
    if tool == "fs.copy":
        source, destination, _, _, _ = _file_op_paths(dept_id, args)
        return _copy_path(source, destination, overwrite=bool(args.get("overwrite")), recursive=bool(args.get("recursive")))
    if tool == "fs.move":
        source, destination, _, _, _ = _file_op_paths(dept_id, args)
        return _move_path(source, destination, overwrite=bool(args.get("overwrite")), recursive=bool(args.get("recursive")))
    if tool == "fs.delete":
        path, root, _ = _tool_path_info(dept_id, args.get("path"))
        _reject_dangerous_delete(path, root)
        if not path.exists():
            raise HTTPException(status_code=404, detail="path not found")
        if path.is_dir():
            if not args.get("recursive"):
                raise HTTPException(status_code=400, detail="directory delete requires recursive=true")
            shutil.rmtree(path)
            return {"path": str(path), "deleted": True, "type": "dir"}
        path.unlink()
        return {"path": str(path), "deleted": True, "type": "file"}
    if tool == "http.get":
        return _execute_http_request(args, method="GET")
    if tool == "http.post":
        return _execute_http_request(args, method="POST")
    if tool == "web.search":
        return execute_web_search(args)
    if tool == "web.fetch":
        return execute_web_fetch(args)
    if tool == "import.url":
        return _execute_import_url(args, dept_id=dept_id)
    if tool == "mcp.call":
        return _execute_mcp_call(args, dept_id=dept_id)
    if tool == "shell.exec":
        command = args.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
            raise HTTPException(
                status_code=400,
                detail=(
                    "shell.exec args.command must be a non-empty string array, never a single string. "
                    "Example on Unix/macOS: {'command':['/bin/bash','-lc','find . -name \"*.mp4\" | head'],'cwd':'/tmp'}; "
                    "on Windows: {'command':['powershell.exe','-NoProfile','-Command','Get-ChildItem -Recurse -Filter *.mp4 | Select-Object -First 5']}."
                ),
            )
        cwd = _tool_cwd(dept_id, args)
        if not cwd.exists() or not cwd.is_dir():
            raise HTTPException(status_code=400, detail="shell.exec cwd must be an existing directory")
        timeout = _shell_timeout_seconds(args)
        env, env_metadata = _owner_command_env_from_args(args)
        result = _run_process(command, cwd=cwd, timeout=timeout, env=env)
        result["environmentPolicy"] = env_metadata
        return result
    if tool == "sandbox.exec":
        command = args.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
            raise HTTPException(status_code=400, detail="sandbox.exec requires command as a string list")
        workspace = _workspace_for_dept(dept_id)
        timeout = max(1.0, min(float(args.get("timeoutSeconds", 30)), 120.0))
        docker = _docker_executable()
        docker_block = _docker_runtime_block_reason(probe=True)
        if docker and not docker_block:
            image = str(args.get("image") or "python:3.13-slim")
            docker_command = [
                docker,
                "run",
                "--rm",
                "-v",
                f"{workspace}:/workspace",
                "-w",
                "/workspace",
            ]
            if not args.get("network"):
                docker_command.extend(["--network", "none"])
            docker_command.extend([image, *command])
            result = _run_process(docker_command, cwd=workspace, timeout=timeout)
            result["sandbox"] = {"mode": "docker", "image": image, "workspaceMount": "/workspace", "network": bool(args.get("network"))}
            return result
        if not get_settings().sandbox_local_fallback:
            raise HTTPException(status_code=400, detail=docker_block or "Docker is unavailable for sandbox.exec")
        result = _run_process(command, cwd=workspace, timeout=timeout)
        result["sandbox"] = {
            "mode": "local_fallback",
            "workspace": str(workspace),
            "network": "host",
            "dockerBlockReason": docker_block or "Docker is unavailable for sandbox.exec",
        }
        return result
    if tool == "git.status":
        cwd = _tool_cwd(dept_id, args)
        return _git_status(cwd)
    if tool == "git.diff":
        cwd = _tool_cwd(dept_id, args)
        diff_args = ["diff", "--stat"] if args.get("statOnly") else ["diff"]
        if args.get("staged"):
            diff_args.insert(1, "--staged")
        return _run_process(["git", *diff_args], cwd=cwd, timeout=10.0)
    if tool == "git.commit":
        message = args.get("message")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(status_code=400, detail="git.commit requires message")
        cwd = _tool_cwd(dept_id, args)
        return _git_commit_workspace(cwd, message.strip()[:200])
    if tool == "git.push":
        cwd = _tool_cwd(dept_id, args)
        command = ["git", "push"]
        if args.get("remote"):
            command.append(str(args["remote"]))
        if args.get("branch"):
            command.append(str(args["branch"]))
        return _run_process(command, cwd=cwd, timeout=max(5.0, min(float(args.get("timeoutSeconds", 20)), 120.0)))
    if tool == "browser.open":
        return execute_browser_open(args, _run_process)
    if tool == "browser.snapshot":
        return execute_browser_snapshot(args, _run_process)
    if tool == "browser.act":
        return execute_browser_act(args, _run_process)
    if tool == "desktop.apps":
        return execute_list_apps(args, _run_process)
    if tool == "desktop.snapshot":
        return execute_desktop_snapshot(args, _run_process)
    if tool == "desktop.act":
        return execute_desktop_act(args, _run_process)
    if tool == "desktop.open_app":
        return execute_open_app(args, _run_process)
    if tool == "desktop.activate_app":
        return execute_activate_app(args, _run_process)
    if tool == "desktop.quit_app":
        return execute_quit_app(args, _run_process)
    if tool in {"browser.click", "desktop.click"}:
        return execute_click(args, _run_process)
    if tool in {"browser.type", "desktop.type"}:
        return _execute_type_text(args)
    if tool in {"browser.keypress", "desktop.keypress"}:
        return _execute_keypress(args)
    if tool in {"browser.paste_text", "desktop.paste_text"}:
        return _execute_paste_text(args)
    if tool in {"browser.scroll", "desktop.scroll"}:
        return execute_scroll(args, _run_process)
    if tool in {"browser.screenshot", "desktop.screenshot"}:
        path = _tool_target_path(dept_id, args.get("path"), default=f"screenshots/{uid('shot')}.png")
        result = execute_screenshot_capture(path, _run_process)
        if tool == "browser.screenshot" and ("profile" in args or "browserProfile" in args):
            result["browserProfile"] = browser_profile_from_args(args)
        return result
    if tool == "notify.send":
        return execute_notification(args, _run_process)
    raise HTTPException(status_code=400, detail="unsupported tool")


async def _save_tool_run(repo: Repo, run: dict[str, Any]) -> None:
    await repo.put_entity(
        "tool_run",
        run,
        dept=run.get("departmentId"),
        project=None,
        status=run.get("status"),
        ts=run.get("completedAt") or run.get("createdAt") or now_ms(),
    )


def _checkpoint_candidate_paths(run: dict[str, Any]) -> list[Path]:
    tool = _canonical_tool(run["tool"])
    args = run.get("args") or {}
    dept_id = run["departmentId"]
    paths: list[Path] = []
    if tool in {"fs.write", "fs.patch", "fs.delete", "fs.read", "fs.list"} and args.get("path"):
        paths.append(_tool_target_path(dept_id, args.get("path")))
    if tool in {"fs.copy", "fs.move"}:
        with contextlib.suppress(HTTPException):
            source, destination, _, _, _ = _file_op_paths(dept_id, args)
            paths.extend([source, _resolve_file_destination(source, destination)])
    if tool == "import.url" and (args.get("outputPath") or args.get("path")):
        with contextlib.suppress(HTTPException):
            paths.append(_tool_target_path(dept_id, args.get("outputPath") or args.get("path")))
    if tool in {"shell.exec", "git.status", "git.diff", "git.commit", "git.push"}:
        with contextlib.suppress(HTTPException):
            paths.append(_tool_cwd(dept_id, args))
    return paths


def _checkpoint_file_snapshot(path: Path) -> dict[str, Any]:
    snap: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return snap
    if path.is_file():
        data = path.read_bytes()
        snap.update({
            "type": "file",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "textCaptured": False,
        })
        return snap
    if path.is_dir():
        children = []
        for item in sorted(path.iterdir(), key=lambda p: p.name.lower())[:200]:
            children.append({"name": item.name, "type": "dir" if item.is_dir() else "file"})
        snap.update({"type": "dir", "entries": children, "entryCount": len(children)})
        git = _git_status(path)
        if git.get("gitEnabled"):
            diff = _git_run(path, "diff", "--stat")
            snap["git"] = {
                **git,
                "diffStat": _clip_text(diff.stdout if diff.returncode == 0 else diff.stderr, 20_000),
            }
        return snap
    snap["type"] = "other"
    return snap


def _checkpoint_required_for_run(run: dict[str, Any]) -> bool:
    risk = run.get("riskClass")
    tool = _canonical_tool(run["tool"])
    custom_catalog = run.get("customCatalogRow") if isinstance(run.get("customCatalogRow"), dict) else {}
    return bool(custom_catalog.get("supportsCheckpoint") or risk in CHECKPOINT_RISKS or tool in MUTATING_TOOLS)


async def _create_tool_checkpoint(repo: Repo, run: dict[str, Any]) -> dict[str, Any] | None:
    if not _checkpoint_required_for_run(run):
        return None
    risk = run.get("riskClass")
    tool = _canonical_tool(run["tool"])
    checkpoint_id = uid("chk")
    snapshots = []
    for path in _checkpoint_candidate_paths(run):
        with contextlib.suppress(Exception):
            snapshots.append(_checkpoint_file_snapshot(path))
    checkpoint = {
        "id": checkpoint_id,
        "ts": now_ms(),
        "toolRunId": run["id"],
        "departmentId": run.get("departmentId"),
        "tool": run.get("tool"),
        "canonicalTool": tool,
        "riskClass": risk,
        "policyDecision": run.get("policyDecision"),
        "snapshots": snapshots,
    }
    await repo.put_entity("checkpoint", checkpoint, dept=run.get("departmentId"), status=risk, ts=checkpoint["ts"])
    run["checkpointId"] = checkpoint_id
    run["checkpoint"] = {
        "id": checkpoint_id,
        "snapshotCount": len(snapshots),
        "paths": [snap["path"] for snap in snapshots],
    }
    return checkpoint


def _activity_author(ev: dict[str, Any], departments: dict[str, dict[str, Any]]) -> str:
    explicit = ev.get("actor") or ev.get("author") or ev.get("requestedBy")
    if explicit:
        return str(explicit)
    dept_id = ev.get("departmentId")
    dept = departments.get(str(dept_id)) if dept_id else None
    if dept:
        return str(dept.get("agentName") or dept.get("name") or dept_id)
    return str(ev.get("source") or "system")


def _audit_author(*values: Any, department_id: str | None = None, fallback: str = "system") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    if department_id:
        return str(department_id)
    return fallback


async def _collect_audit_logs(repo: Repo, dept_id: str | None, kind: str | None, limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if kind in {None, "activity"}:
        departments = {dept["id"]: dept for dept in await repo.list_departments()}
        entries.extend(_audit_from_activity(ev, departments) for ev in await repo.recent_activity(limit=limit))
    if kind in {None, "approval"}:
        entries.extend(_audit_from_approval(ap) for ap in await repo.list_approvals())
    if kind in {None, "tool_run"}:
        entries.extend(
            _audit_from_tool_run(run)
            for run in await repo.list_entities("tool_run", dept=dept_id, limit=limit)
        )
        entries.extend(
            _audit_from_agent_tool_run(run)
            for run in await repo.list_entities("agent_tool_run", dept=dept_id, limit=limit)
        )
    if kind in {None, "note"}:
        entries.extend(
            _audit_from_note(note)
            for note in await repo.list_entities("audit_note", dept=dept_id, limit=limit)
        )
    if kind not in {None, "activity", "approval", "tool_run", "note"}:
        raise HTTPException(status_code=400, detail="unsupported audit log kind")
    if dept_id:
        entries = [e for e in entries if e.get("departmentId") in {None, dept_id}]
    entries.sort(key=lambda e: e["ts"], reverse=True)
    return [_redact_audit_entry(entry) for entry in entries[:limit]]


async def _execute_repo_tool(repo: Repo, run: dict[str, Any]) -> dict[str, Any] | None:
    tool = _canonical_tool(run["tool"])
    args = run.get("args") or {}
    if run.get("threadId") and not (args.get("threadId") or args.get("thread_id")):
        args = {**args, "threadId": run["threadId"]}
        run["args"] = args
    from .video_editing import VIDEO_TOOL_NAMES, execute_video_tool

    if tool in VIDEO_TOOL_NAMES:
        return await execute_video_tool(repo, {**run, "tool": tool})
    if tool == "audio.transcribe":
        return await execute_audio_transcription_tool(repo, {**run, "tool": tool})
    if tool == "image.generate":
        explicit_async = args.get("asyncMode") if "asyncMode" in args else args.get("async_mode")
        wait_for_result = (
            _truthy(args.get("waitForResult"))
            or _truthy(args.get("wait_for_result"))
            or _truthy(args.get("sync"))
            or (explicit_async is not None and not _truthy(explicit_async))
        )
        requested_by = str(run.get("requestedBy") or run.get("departmentId") or "tool-runner")
        fallback_owner_dept = str(args.get("ownerDept") or args.get("owner_dept") or run.get("departmentId"))
        if not wait_for_result and (
            _truthy(explicit_async)
            or _truthy(args.get("async"))
            or _truthy(args.get("background"))
            or _truthy(args.get("statusUrl"))
        ):
            return await queue_image_generation_assets(
                repo,
                args,
                fallback_owner_dept=fallback_owner_dept,
                requested_by=requested_by,
                thread_id=str(run.get("threadId") or args.get("threadId") or args.get("thread_id") or "").strip() or None,
            )
        return await generate_image_assets(
            repo,
            args,
            fallback_owner_dept=fallback_owner_dept,
            requested_by=requested_by,
        )
    if tool == "browser.profiles":
        return list_browser_profiles()
    if tool == "browser.snapshot":
        return await asyncio.to_thread(execute_browser_snapshot, args, _run_process)
    if tool == "browser.act":
        return await asyncio.to_thread(execute_browser_act, args, _run_process)
    if tool == "desktop.snapshot":
        return await asyncio.to_thread(execute_desktop_snapshot, args, _run_process)
    if tool == "desktop.act":
        return await asyncio.to_thread(execute_desktop_act, args, _run_process)
    if tool in {"browser.screenshot", "desktop.screenshot"}:
        path = _tool_target_path(run["departmentId"], args.get("path"), default=f"screenshots/{uid('shot')}.png")
        result = await asyncio.to_thread(execute_screenshot_capture, path, _run_process)
        if tool == "browser.screenshot" and ("profile" in args or "browserProfile" in args):
            result["browserProfile"] = browser_profile_from_args(args)
        if result.get("returnCode") == 0 and path.is_file():
            artifact = await persist_screenshot_artifact(
                repo,
                path=path,
                owner_dept=run["departmentId"],
                created_by=str(run.get("requestedBy") or run["departmentId"]),
                source_tool=tool,
                artifact_name=args.get("artifactName") or args.get("artifact_name"),
                browser_profile=(
                    browser_profile_from_args(args)
                    if tool == "browser.screenshot" and ("profile" in args or "browserProfile" in args)
                    else None
                ),
            )
            result["artifact"] = artifact
            result["preview"] = {
                "kind": "image",
                "uri": artifact.get("uri") or str(path),
                "download": f"/api/artifacts/{artifact['id']}/download",
            }
        return result
    if tool == "logs.query":
        limit = max(1, min(int(args.get("limit") or 50), 500))
        return {
            "rows": await _collect_audit_logs(
                repo,
                dept_id=args.get("deptId") or args.get("departmentId") or run.get("departmentId"),
                kind=args.get("kind"),
                limit=limit,
            ),
            "limit": limit,
        }
    if tool == "logs.note":
        body = args.get("body")
        if not isinstance(body, str) or not body.strip():
            raise HTTPException(status_code=400, detail="logs.note requires body")
        note = {
            "id": uid("note"),
            "ts": now_ms(),
            "departmentId": args.get("departmentId") or run.get("departmentId"),
            "author": args.get("author") or run.get("requestedBy") or "agent",
            "body": body,
            "links": args.get("links") if isinstance(args.get("links"), list) else [],
            "severity": args.get("severity") or "info",
        }
        await repo.put_entity("audit_note", note, dept=note.get("departmentId"), status=note.get("severity"), ts=note["ts"])
        await repo.add_activity(_activity(
            f"เพิ่ม audit note โดย {note['author']}",
            type_="system",
            department_id=note.get("departmentId"),
            severity=note.get("severity", "info"),
        ))
        return _audit_from_note(note)
    if tool == "scheduler.create":
        title = args.get("title") or "Scheduled tool trigger"
        kind = args.get("kind") or "cron"
        action = args.get("action") if isinstance(args.get("action"), dict) else {}
        cadence = resolve_trigger_cadence(
            args.get("cadence") or cadence_from_schedule_object(args.get("schedule")),
            title,
            args.get("description"),
            action.get("message"),
        )
        one_shot_at = args.get("oneShotAt") or args.get("one_shot_at")
        if kind == "cron" and not cadence and not one_shot_at:
            raise ValueError("cron scheduler.create requires cadence or oneShotAt")
        if kind == "event" and not args.get("event"):
            raise ValueError("event scheduler.create requires event")
        target = _scheduler_target_from_args(args, run)
        trigger = Trigger(
            id=args.get("id") or uid("trig"),
            title=title,
            kind=kind,
            cadence=cadence,
            one_shot_at=one_shot_at,
            event=args.get("event"),
            target=target,
            enabled=bool(args.get("enabled", True)),
            last_run_at=None,
            next_run_at=_next_run_for(cadence, one_shot_at),
        ).dump()
        dept_id = _dept_id_from_target(trigger["target"])
        if dept_id:
            await _validate_departments(repo, [dept_id])
        await repo.put_entity("trigger", trigger, dept=dept_id, status=trigger.get("kind"), ts=now_ms())
        await repo.add_activity(_activity(
            f"สร้าง trigger ผ่าน tool: {trigger['title']}",
            type_="system",
            department_id=dept_id,
            severity="good",
        ))
        return trigger
    return None


async def _execute_tool_run_record(repo: Repo, run: dict[str, Any]) -> dict[str, Any]:
    now = now_ms()
    run["status"] = "running"
    run["startedAt"] = now
    await _save_tool_run(repo, run)
    await commit_and_release(repo.s)
    try:
        runtime_block = _tool_runtime_block_reason(run)
        if runtime_block:
            run["status"] = "blocked"
            run["error"] = runtime_block
            run["policyReason"] = runtime_block
            run["completedAt"] = now_ms()
            await _save_tool_run(repo, run)
            await repo.add_activity(_activity(
                f"tool {run['tool']} blocked: {runtime_block}",
                type_="system",
                department_id=run.get("departmentId"),
                severity="warn",
            ))
            return run
        await _create_tool_checkpoint(repo, run)
        await commit_and_release(repo.s)
        from .tools.foundry import execute_custom_tool

        if _canonical_tool(run["tool"]) == "process":
            run["result"] = await _owner_process_tool(repo, run.get("args") or {}, run.get("departmentId") or EXEC_ID)
            await repo.add_activity(_activity(
                f"tool {run['tool']} {run['result'].get('action', 'process')}",
                type_="system",
                department_id=run.get("departmentId"),
                severity="good" if run["result"].get("ok") else "warn",
            ))
        elif _canonical_tool(run["tool"]) == "shell.exec" and bool((run.get("args") or {}).get("background") or (run.get("args") or {}).get("async") or (run.get("args") or {}).get("asyncMode")):
            run["result"] = await _owner_start_background_shell_run(repo, run)
            await repo.add_activity(_activity(
                f"tool {run['tool']} running in background",
                type_="system",
                department_id=run.get("departmentId"),
                severity="good",
            ))
            return run

        else:
            custom_result = await execute_custom_tool(repo, run)
            if custom_result is not None:
                run["result"] = custom_result
            else:
                repo_result = await _execute_repo_tool(repo, run)
                run["result"] = repo_result if repo_result is not None else await asyncio.to_thread(_execute_tool_sync, run)
        process_error = _tool_process_error(_canonical_tool(run["tool"]), run["result"])
        if process_error:
            run["status"] = "failed"
            run["error"] = process_error
        else:
            run["status"] = "succeeded"
            run["error"] = None
    except subprocess.TimeoutExpired as exc:
        run["status"] = "failed"
        run["error"] = f"timeout after {exc.timeout}s"
    except HTTPException as exc:
        run["status"] = "failed"
        run["error"] = str(exc.detail)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        run["status"] = "failed"
        run["error"] = f"{type(exc).__name__}: {exc}"
    run["completedAt"] = now_ms()
    await _save_tool_run(repo, run)
    await repo.add_activity(_activity(
        f"tool {run['tool']} {run['status']}",
        type_="system",
        department_id=run.get("departmentId"),
        severity="good" if run["status"] in {"completed", "succeeded"} else "warn",
    ))
    return run


async def _request_tool_approval(repo: Repo, run: dict[str, Any]) -> dict[str, Any]:
    run["status"] = "pending_approval"
    approval = Approval(
        id=uid("apr"),
        ts=now_ms(),
        kind="external_action",
        title=f"อนุมัติ tool: {run['tool']}",
        detail=f"{run.get('requestedBy', 'user')} ขอรัน tool {run['tool']} ของฝ่าย {run['departmentId']}",
        department_id=run["departmentId"],
        status="pending",
        action={
            "action": "run_tool",
            "departmentId": run["departmentId"],
            "toolRunId": run["id"],
            "requestedBy": run.get("requestedBy"),
        },
    ).dump()
    await repo.add_approval(approval)
    run["approvalId"] = approval["id"]
    await _save_tool_run(repo, run)
    await repo.add_activity(_activity(
        f"รออนุมัติ tool {run['tool']}",
        type_="approval",
        department_id=run["departmentId"],
        severity="warn",
    ))
    await _upsert_approval_chat_message(repo, approval, run=run)
    return approval


async def _cancel_pending_tool_work(repo: Repo, reason: str) -> dict[str, int]:
    now = now_ms()
    cancelled_runs = 0
    rejected_approvals = 0
    for status in ("queued", "pending_approval", "running"):
        for run in await repo.list_entities("tool_run", status=status, limit=1000):
            if run.get("status") in TERMINAL_TOOL_STATUSES:
                continue
            run["status"] = "cancelled"
            run["error"] = reason
            run["completedAt"] = now
            run["cancelledAt"] = now
            await _save_tool_run(repo, run)
            cancelled_runs += 1

    for approval in await repo.list_approvals():
        action = approval.get("action") or {}
        if approval.get("status") != "pending" or action.get("action") != "run_tool":
            continue
        approval["status"] = "rejected"
        approval["rejectionReason"] = reason
        action["cancelledAt"] = now
        approval["action"] = action
        await repo.save_approval(approval)
        await _upsert_approval_chat_message(repo, approval)
        rejected_approvals += 1

    cancelled_jobs = await repo.cancel_active_jobs(reason)
    return {
        "toolRuns": cancelled_runs,
        "approvals": rejected_approvals,
        "jobs": cancelled_jobs,
    }


def _severity_for_tool_run(run: dict[str, Any]) -> str:
    if run.get("status") in {"completed", "succeeded"}:
        return "good"
    if run.get("status") in {"failed", "blocked"}:
        return "warn"
    return "info"


def _audit_from_activity(ev: dict[str, Any], departments: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    departments = departments or {}
    author = _activity_author(ev, departments)
    return AuditLogEntry(
        id=ev["id"],
        ts=ev["ts"],
        kind="activity",
        department_id=ev.get("departmentId"),
        severity=ev.get("severity", "info"),
        title=ev.get("text", ""),
        detail=ev.get("text", ""),
        author=author,
        refs={
            "actor": author,
            "actorId": ev.get("actor") or ev.get("departmentId") or ev.get("source") or "system",
            "activityType": ev.get("type"),
            "method": ev.get("method"),
            "path": ev.get("path"),
            "statusCode": ev.get("statusCode"),
            "source": ev.get("source"),
            "elapsedMs": ev.get("elapsedMs"),
        },
    ).dump()


def _audit_from_approval(ap: dict[str, Any]) -> dict[str, Any]:
    action = ap.get("action") if isinstance(ap.get("action"), dict) else {}
    author = _audit_author(
        action.get("requestedBy"),
        ap.get("approvedBy"),
        ap.get("resolvedBy"),
        department_id=ap.get("departmentId"),
    )
    return AuditLogEntry(
        id=ap["id"],
        ts=ap["ts"],
        kind="approval",
        department_id=ap.get("departmentId"),
        severity="warn" if ap.get("status") == "pending" else "good" if ap.get("status") == "approved" else "info",
        title=ap.get("title", ""),
        detail=ap.get("detail", ""),
        author=author,
        refs={
            "actor": author,
            "actorId": action.get("requestedBy") or ap.get("approvedBy") or ap.get("resolvedBy") or ap.get("departmentId") or "system",
            "approvalKind": ap.get("kind"),
            "status": ap.get("status"),
            "action": action or ap.get("action"),
        },
    ).dump()


def _audit_from_tool_run(run: dict[str, Any]) -> dict[str, Any]:
    detail = run.get("error") or f"tool {run.get('tool')} {run.get('status')}"
    author = _audit_author(run.get("requestedBy"), department_id=run.get("departmentId"), fallback="tool-runner")
    return AuditLogEntry(
        id=run["id"],
        ts=run.get("completedAt") or run.get("createdAt") or now_ms(),
        kind="tool_run",
        department_id=run.get("departmentId"),
        severity=_severity_for_tool_run(run),
        title=f"tool: {run.get('tool')}",
        detail=detail,
        author=author,
        refs={
            "actor": author,
            "actorId": run.get("requestedBy") or run.get("departmentId") or "tool-runner",
            "tool": run.get("tool"),
            "taskId": run.get("taskId"),
            "status": run.get("status"),
            "riskClass": run.get("riskClass"),
            "policyDecision": run.get("policyDecision"),
            "policyReason": run.get("policyReason"),
            "approvalId": run.get("approvalId"),
            "executor": run.get("executor"),
            "checkpointId": run.get("checkpointId"),
        },
    ).dump()


def _audit_from_agent_tool_run(run: dict[str, Any]) -> dict[str, Any]:
    detail = run.get("error") or f"chat tool {run.get('tool')} {run.get('status')}"
    author = _audit_author(run.get("requestedBy"), department_id=run.get("departmentId"), fallback="agent")
    return AuditLogEntry(
        id=run["id"],
        ts=run.get("completedAt") or run.get("startedAt") or run.get("createdAt") or now_ms(),
        kind="tool_run",
        department_id=run.get("departmentId"),
        severity=_severity_for_tool_run(run),
        title=f"chat tool: {run.get('tool')}",
        detail=detail,
        author=author,
        refs={
            "actor": author,
            "actorId": run.get("requestedBy") or run.get("departmentId") or "agent",
            "source": "agent_tool_run",
            "tool": run.get("tool"),
            "threadId": run.get("threadId"),
            "status": run.get("status"),
        },
    ).dump()


def _audit_from_note(note: dict[str, Any]) -> dict[str, Any]:
    author = _audit_author(note.get("author"), department_id=note.get("departmentId"))
    return AuditLogEntry(
        id=note["id"],
        ts=note["ts"],
        kind="note",
        department_id=note.get("departmentId"),
        severity=note.get("severity", "info"),
        title="audit note",
        detail=note.get("body", ""),
        author=author,
        refs={
            "actor": author,
            "actorId": note.get("author") or note.get("departmentId") or "system",
            "links": note.get("links", []),
        },
    ).dump()


def _is_user_approved_project_artifact(artifact: dict[str, Any] | None) -> bool:
    return bool(
        artifact
        and artifact.get("status") == "approved"
        and artifact.get("approvedBy")
    )


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_plist_env(path: Path) -> dict[str, str]:
    try:
        with path.open("rb") as fh:
            raw = plistlib.load(fh)
    except Exception:
        return {}
    env = raw.get("EnvironmentVariables") if isinstance(raw, dict) else {}
    if not isinstance(env, dict):
        return {}
    return {str(key): str(value) for key, value in env.items() if isinstance(key, str)}


def _read_key_value_file(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gzip_ok(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as fh:
            for _ in iter(lambda: fh.read(1024 * 1024), b""):
                pass
        return True
    except Exception:
        return False


def _backup_runtime_status(settings: Any, now: int) -> dict[str, Any]:
    launchd_path = Path.home() / "Library/LaunchAgents/com.atrium.backup.plist"
    use_launchd_env = bool(getattr(settings, "backup_use_launchd_env", True))
    launchd_env = _read_plist_env(launchd_path) if use_launchd_env else {}
    backup_dir = Path(
        launchd_env.get("ATRIUM_BACKUP_DIR")
        or str(getattr(settings, "backup_dir", ""))
        or str(settings.data_dir / "backups")
    ).expanduser()
    offsite_dir = str(
        launchd_env.get("ATRIUM_BACKUP_OFFSITE_DIR")
        or getattr(settings, "backup_offsite_dir", "")
        or ""
    ).strip()
    require_offsite = _truthy(launchd_env.get("ATRIUM_BACKUP_REQUIRE_OFFSITE")) or bool(
        getattr(settings, "backup_require_offsite", False)
    )
    max_age_hours = float(getattr(settings, "backup_max_age_hours", 30.0) or 30.0)
    backups = sorted(backup_dir.glob("atrium-*.sql.gz")) if backup_dir.exists() else []
    latest = backups[-1] if backups else None
    status: dict[str, Any] = {
        "launchdInstalled": launchd_path.exists(),
        "launchdPath": str(launchd_path),
        "backupDir": str(backup_dir),
        "offsiteDir": offsite_dir or None,
        "requireOffsite": require_offsite,
        "maxAgeHours": max_age_hours,
        "backupCount": len(backups),
        "latestBackup": None,
        "latestAgeHours": None,
        "gzipOk": None,
        "sha256Ok": None,
        "manifestOk": None,
        "localOk": False,
        "offsiteConfigured": bool(offsite_dir),
        "offsiteCopied": None,
        "offsiteDirMatches": False,
        "offsiteOk": False,
        "offsiteVerifiedBy": None,
        "offsiteLiveChecked": False,
        "ok": False,
        "gaps": [],
    }
    if not latest:
        status["gaps"].append("no backup files found")
        return status

    latest_age_hours = max(0.0, (now / 1000 - latest.stat().st_mtime) / 3600)
    actual_sha = _sha256_file(latest)
    sha_path = Path(str(latest) + ".sha256")
    expected_sha = sha_path.read_text(encoding="utf-8", errors="replace").strip().split()[0] if sha_path.exists() else None
    manifest_path = Path(str(latest) + ".manifest")
    manifest = _read_key_value_file(manifest_path)
    gzip_ok = _gzip_ok(latest)
    sha_ok = bool(expected_sha and expected_sha == actual_sha)
    manifest_ok = bool(
        manifest
        and manifest.get("format") == "atrium-backup-manifest-v1"
        and manifest.get("sha256") == actual_sha
        and manifest.get("size_bytes") == str(latest.stat().st_size)
        and manifest.get("gzip_ok") == "true"
    )
    manifest_offsite_dir = (manifest or {}).get("offsite_dir") or ""
    offsite_copied = (manifest or {}).get("offsite_copied") == "true" if manifest else None
    offsite_dir_matches = bool(
        offsite_dir
        and manifest_offsite_dir
        and Path(manifest_offsite_dir).expanduser() == Path(offsite_dir).expanduser()
    )
    # Avoid opening file-provider offsite paths on the API request path. The backup
    # script writes offsite_copied=true only after the copied file SHA256 matches.
    offsite_ok = bool(offsite_copied and offsite_dir_matches and manifest_ok)
    local_ok = bool(
        status["launchdInstalled"]
        and gzip_ok
        and sha_ok
        and manifest_ok
        and latest_age_hours <= max_age_hours
    )
    if not status["launchdInstalled"]:
        status["gaps"].append("backup LaunchAgent is not installed")
    if latest_age_hours > max_age_hours:
        status["gaps"].append("latest backup is stale")
    if not gzip_ok:
        status["gaps"].append("latest backup gzip check failed")
    if not sha_ok:
        status["gaps"].append("latest backup sha256 sidecar missing or mismatched")
    if not manifest_ok:
        status["gaps"].append("latest backup manifest missing or mismatched")
    if not offsite_dir:
        status["gaps"].append("offsite backup storage is not configured")
    elif manifest and not offsite_dir_matches:
        status["gaps"].append("offsite backup manifest does not match configured offsite dir")
    elif not offsite_ok:
        status["gaps"].append("offsite backup copy is not verified by latest manifest")
    status.update({
        "latestBackup": str(latest),
        "latestAgeHours": round(latest_age_hours, 3),
        "latestSizeBytes": latest.stat().st_size,
        "gzipOk": gzip_ok,
        "sha256Ok": sha_ok,
        "manifestOk": manifest_ok,
        "manifest": str(manifest_path) if manifest_path.exists() else None,
        "manifestOffsiteDir": manifest_offsite_dir or None,
        "localOk": local_ok,
        "offsiteConfigured": bool(offsite_dir),
        "offsiteDir": offsite_dir or None,
        "offsiteCopied": offsite_copied,
        "offsiteDirMatches": offsite_dir_matches,
        "offsiteOk": offsite_ok,
        "offsiteVerifiedBy": "backup_manifest" if offsite_ok else None,
        "offsiteLiveChecked": False,
        "ok": bool(local_ok and (offsite_ok if require_offsite else True)),
    })
    return status


async def _ensure_artifact_review_gate(
    repo: Repo,
    artifact: dict[str, Any],
    *,
    task_id: str | None = None,
    project_id: str | None = None,
    reason: str = "project-final",
) -> dict[str, Any]:
    now = now_ms()
    artifact_id = artifact["id"]
    evidence_rows = await repo.list_entities("evidence_pack", status=artifact_id, limit=1)
    if not evidence_rows:
        evidence_rows = await repo.list_entities("evidence_pack", status=f"artifact:{artifact_id}", limit=1)
    if evidence_rows:
        evidence = evidence_rows[0]
    else:
        evidence = EvidencePack(
            id=uid("evp"),
            artifact_id=artifact_id,
            citations=[
                {"source": link, "url": link if str(link).startswith(("http://", "https://")) else None, "quote": "linked by artifact"}
                for link in artifact.get("links", [])[:8]
            ],
            raw_notes=(
                "Auto-generated review gate evidence pack. "
                f"artifact={artifact_id}; project={project_id or artifact.get('projectId') or '-'}; task={task_id or '-'}"
            ),
            confidence=0.72 if artifact.get("links") else 0.55,
            gaps=[] if artifact.get("links") else ["ยังไม่มี citation ภายนอกหรือไฟล์หลักฐานแนบกับ artifact"],
            assumptions=["Artifact preview/content is the latest submitted version"],
            methodology="ATRIUM Full Auto review: collect artifact links, preview availability, known gaps, and audit evidence before closure.",
        ).dump()
        await repo.put_entity("evidence_pack", evidence, project=project_id, status=artifact_id, ts=now)

    critique_rows = [
        row
        for row in await repo.list_entities("critique_report", status="artifact", limit=20)
        if row.get("targetId") == artifact_id
    ]
    if critique_rows:
        critique = critique_rows[0]
    else:
        critique = CritiqueReport(
            id=uid("crit"),
            target_type="artifact",
            target_id=artifact_id,
            risks=[
                "คุณภาพของ final deliverable อาจยังไม่ครอบคลุม acceptance criteria ทุกข้อ",
                "หลักฐานอาจไม่พอถ้า artifact ไม่มี citation หรือ preview ที่เปิดตรวจได้",
            ],
            untested_assumptions=[
                "ผู้ใช้ต้องการอนุมัติ deliverable รวมของโปรเจกต์เป็น gate สุดท้ายเท่านั้น",
                "งานย่อยที่เกี่ยวข้องผ่านการตรวจระดับผู้บริหารแล้ว",
            ],
            missed_alternatives=[
                "ส่งกลับให้แผนกแก้ก่อนขอ user approval",
                "เปิด war room หรือ devil's advocate เพิ่มถ้า risk สูง",
            ],
            open_questions=[
                "preview เปิดดูได้ครบหรือไม่",
                "evidence pack มี gaps ที่ต้องแก้ก่อนส่งหรือไม่",
            ],
            ts=now,
        ).dump()
        await repo.put_entity("critique_report", critique, project=project_id, status="artifact", ts=now)

    gate = {
        "required": True,
        "reason": reason,
        "evidencePackId": evidence["id"],
        "critiqueReportId": critique["id"],
        "previewAvailable": bool(artifact.get("preview")),
        "checkedAt": now,
        "confidence": float(evidence.get("confidence") or 0.0),
        "gaps": list(evidence.get("gaps") or []),
        "riskCount": len(critique.get("risks") or []),
    }
    tags = list(dict.fromkeys([*artifact.get("tags", []), "review-gated", "evidence-pack", "devils-advocate"]))
    artifact.update({
        "tags": tags,
        "reviewGate": gate,
        "updatedAt": now,
        "updatedBy": artifact.get("updatedBy") or "executive",
    })
    await repo.put_entity(
        "artifact",
        artifact,
        dept=artifact.get("ownerDept"),
        project=project_id or artifact.get("projectId"),
        status=artifact.get("status"),
        ts=now,
    )
    await repo.add_activity(_activity(
        f"Full Auto review evidence พร้อมสำหรับ artifact “{artifact.get('name', artifact_id)}”",
        type_="system",
        department_id=artifact.get("ownerDept"),
        severity="good" if gate["previewAvailable"] and not evidence.get("gaps") else "warn",
    ))
    return gate


async def _artifact_quality_review_payload(repo: Repo, artifact_id: str, *, reason: str) -> dict[str, Any]:
    artifact = await repo.get_entity("artifact", artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    gate = await _ensure_artifact_review_gate(
        repo,
        artifact,
        task_id=(artifact.get("taskIds") or [None])[0],
        project_id=artifact.get("projectId"),
        reason=reason,
    )
    artifact = await repo.get_entity("artifact", artifact_id) or artifact
    evidence = await repo.get_entity("evidence_pack", str(gate.get("evidencePackId") or ""))
    critique = await repo.get_entity("critique_report", str(gate.get("critiqueReportId") or ""))
    if not evidence or not critique:
        raise HTTPException(status_code=500, detail="artifact review gate did not persist evidence and critique")
    return {
        "artifact": artifact,
        "evidencePack": evidence,
        "critiqueReport": critique,
        "gate": gate,
    }


async def _list_critique_reports(
    repo: Repo,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = await repo.list_entities("critique_report", status=target_type, limit=max(limit * 3, limit))
    if not rows and (target_type or target_id):
        rows = await repo.list_entities("critique_report", limit=max(limit * 3, limit))
    if target_type:
        rows = [row for row in rows if row.get("targetType") == target_type]
    if target_id:
        rows = [row for row in rows if row.get("targetId") == target_id]
    return rows[:limit]


async def _submit_project_artifact_for_user_review(
    repo: Repo,
    *,
    project: dict[str, Any],
    artifact: dict[str, Any],
    task_id: str | None = None,
    requested_by: str = "executive",
) -> dict[str, Any]:
    now = now_ms()
    project_id = project["id"]
    gate = await _ensure_artifact_review_gate(
        repo,
        artifact,
        task_id=task_id,
        project_id=project_id,
        reason="project-final",
    )
    artifact.update({
        "status": "approved",
        "approvalTier": "full_auto",
        "approvedBy": requested_by,
        "approvedAt": now,
        "updatedAt": now,
        "updatedBy": requested_by,
    })
    await repo.put_entity(
        "artifact",
        artifact,
        dept=artifact.get("ownerDept"),
        project=project_id,
        status="approved",
        ts=now,
    )
    project.update({
        "deliverableArtifactId": artifact["id"],
        "reviewStatus": "approved_by_full_auto",
        "status": "done",
        "completedAt": now,
        "resolvedBy": requested_by,
    })
    await repo.put_entity("project", project, project=project_id, status=project.get("status"), ts=now)
    await repo.add_activity(_activity(
        f"Full Auto ปิด final deliverable ของโปรเจกต์ {project_id}",
        type_="task_done",
        department_id=artifact.get("ownerDept"),
        severity="good",
    ))
    if task_id:
        task = await repo.get_task(task_id)
        if task:
            task["status"] = "done"
            task["progress"] = 1
            task["updatedAt"] = now
            task["result"] = {
                **(task.get("result") or {}),
                "summary": "Full Auto approved final deliverable",
                "artifactId": artifact["id"],
                "reviewStatus": "approved_by_full_auto",
                "completedAt": now,
            }
            task["log"] = [*task.get("log", []), "Full Auto approved final deliverable ของโปรเจกต์"]
            await repo.save_task(task)
    return {"approvalId": None, "gate": gate, "reused": False, "executed": True}


async def _resolve_project_review_action(
    repo: Repo,
    *,
    project_id: str,
    artifact_id: str | None,
    task_id: str | None,
    approved: bool,
    resolved_by: str = "user",
    approval_id: str | None = None,
) -> dict[str, Any]:
    if not project_id:
        raise HTTPException(status_code=400, detail="project id is required")
    now = now_ms()
    project = await repo.get_entity("project", project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    artifact = await repo.get_entity("artifact", artifact_id) if artifact_id else None
    if artifact_id and not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")

    task = await repo.get_task(task_id) if task_id else None
    if task_id and not task:
        raise HTTPException(status_code=404, detail="task not found")

    if approved:
        project["status"] = "done"
        project["deliverableArtifactId"] = artifact_id or project.get("deliverableArtifactId")
        project["reviewStatus"] = "approved_by_user"
        project["completedAt"] = now
        project["resolvedBy"] = resolved_by
        if approval_id:
            project["finalApprovalId"] = approval_id
        if artifact:
            artifact["status"] = "approved"
            artifact["approvalTier"] = "user"
            artifact["approvedBy"] = resolved_by
            artifact["approvedAt"] = now
            artifact["updatedAt"] = now
            artifact["updatedBy"] = resolved_by
            await repo.put_entity(
                "artifact",
                artifact,
                dept=artifact.get("ownerDept"),
                project=project_id,
                status="approved",
                ts=now,
            )
        if task:
            task["status"] = "done"
            task["progress"] = 1
            task["updatedAt"] = now
            task["result"] = {
                **(task.get("result") or {}),
                "summary": "ผู้ใช้อนุมัติ final deliverable ของโปรเจกต์แล้ว",
                "artifactId": artifact_id or (task.get("result") or {}).get("artifactId"),
                "approvalId": approval_id or (task.get("result") or {}).get("approvalId"),
                "reviewStatus": "approved_by_user",
                "completedAt": now,
            }
            task["log"] = [*task.get("log", []), "ผู้ใช้อนุมัติ final deliverable ของโปรเจกต์ ปิดงานหลัก"]
            await repo.save_task(task)
        decision_status = "approved"
        title = f"ผู้ใช้อนุมัติ final deliverable ของโปรเจกต์ {project.get('name', project_id)}"
        severity = "good"
    else:
        project["status"] = "active"
        project["reviewStatus"] = "rejected_by_user"
        project["resolvedBy"] = resolved_by
        if approval_id:
            project["finalApprovalId"] = approval_id
        if task:
            task["status"] = "revising"
            task["progress"] = min(float(task.get("progress", 1)), 0.92)
            task["updatedAt"] = now
            task["result"] = {
                **(task.get("result") or {}),
                "reviewStatus": "rejected_by_user",
                "completedAt": None,
            }
            task["log"] = [*task.get("log", []), "ผู้ใช้ไม่อนุมัติ final deliverable ของโปรเจกต์ ส่งกลับแก้"]
            await repo.save_task(task)
        await _record_learning_signal(
            repo,
            source="reject",
            task_id=task_id,
            artifact_id=artifact_id,
            what_went_wrong="ผู้ใช้ปฏิเสธ final deliverable ของโปรเจกต์",
            lesson_text="ก่อนส่ง final deliverable ให้ผู้ใช้ ต้องให้ผู้บริหารตรวจซ้ำด้วย evidence pack, critique report, preview และตอบ acceptance criteria ให้ครบ",
            applied_to=["knowledge", "playbook", "preference"],
        )
        decision_status = "rejected"
        title = f"ผู้ใช้ไม่อนุมัติ final deliverable ของโปรเจกต์ {project.get('name', project_id)}"
        severity = "warn"

    await repo.put_entity("project", project, project=project_id, status=project.get("status"), ts=now)
    decision = Decision(
        id=uid("dec"),
        title=title,
        proposed_by=project.get("lead") or "executive",
        approved_by=resolved_by if approved else None,
        rationale=(
            "ผ่าน user gate ของ two-tier review แล้ว"
            if approved
            else "ผู้ใช้ตีกลับ final deliverable เพื่อแก้ไขก่อนปิดโปรเจกต์"
        ),
        alternatives=[],
        impact=f"project={project_id}; artifact={artifact_id or '-'}; task={task_id or '-'}",
        linked_task=task_id,
        linked_artifacts=[artifact_id] if artifact_id else [],
        status=decision_status,
        ts=now,
    ).dump()
    await repo.put_entity("decision", decision, project=project_id, status=decision_status, ts=now)
    await repo.put_entity(
        "notification",
        {
            "id": uid("notif"),
            "type": "task_done" if approved else "blocked",
            "severity": severity,
            "title": title,
            "body": "two-tier project review user gate resolved",
            "ts": now,
            "read": False,
            "links": [
                _entity_uri("project", project_id),
                *([_entity_uri("artifact", artifact_id)] if artifact_id else []),
                *([_entity_uri("task", task_id)] if task_id else []),
            ],
        },
        project=project_id,
        status="unread",
        ts=now,
    )
    await repo.add_activity(_activity(title, type_="approval", severity=severity))
    return project


async def _assert_project_can_be_marked_done(
    repo: Repo,
    project: dict[str, Any],
    deliverable_artifact_id: str | None,
) -> None:
    artifact_id = deliverable_artifact_id or project.get("deliverableArtifactId")
    if not artifact_id:
        raise HTTPException(status_code=400, detail="project requires an approved final deliverable before done")
    artifact = await repo.get_entity("artifact", artifact_id)
    if not _is_user_approved_project_artifact(artifact):
        raise HTTPException(status_code=400, detail="project final deliverable is not approved")
    if artifact.get("projectId") != project["id"]:
        raise HTTPException(status_code=400, detail="project final deliverable belongs to another project")


async def _execute_approval_action(repo: Repo, approval: dict[str, Any]) -> bool:
    action = approval.get("action")
    if not action or action.get("executedAt"):
        return False
    kind = action.get("action")
    if kind == "delete_department":
        result = await _delete_department_now(
            repo,
            action.get("departmentId"),
            actor=str(action.get("requestedBy") or action.get("approvedBy") or "full_auto"),
            reason=approval.get("detail") or approval.get("title"),
        )
        action["checkpointId"] = result["checkpointId"]
        action["rollbackEndpoint"] = result["rollbackEndpoint"]
    elif kind == "delete_knowledge":
        dept_id = action.get("departmentId")
        knowledge_id = action.get("knowledgeId")
        ok = await repo.delete_knowledge(dept_id, knowledge_id)
        if not ok:
            raise HTTPException(status_code=404, detail="knowledge entry not found")
        await _refresh_memory_stats(repo, dept_id)
    elif kind == "delete_entity":
        entity_type = action.get("entityType")
        entity_id = action.get("entityId")
        _assert_generic_entity_mutable(entity_type)
        if not await repo.get_entity(entity_type, entity_id):
            raise HTTPException(status_code=404, detail="entity not found")
        await repo.delete_entity(entity_type, entity_id)
        await repo.add_activity(_activity(
            f"ลบ entity {entity_type}/{entity_id} หลังได้รับอนุมัติ",
            type_="approval",
            severity="warn",
        ))
    elif kind == "run_tool":
        tool_run_id = action.get("toolRunId")
        run = await repo.get_entity("tool_run", tool_run_id)
        if not run:
            raise HTTPException(status_code=404, detail="tool run not found")
        await _execute_tool_run_record(repo, run)
    elif kind == "resolve_project":
        await _resolve_project_review_action(
            repo,
            project_id=action.get("projectId"),
            artifact_id=action.get("artifactId"),
            task_id=action.get("taskId"),
            approved=True,
            resolved_by="user",
            approval_id=approval["id"],
        )
    elif kind == "close_task":
        await approve_task_close_request(
            repo,
            approval,
            approved_by=action.get("approvedBy") or "executive",
            now=now_ms(),
        )
    elif kind == "approve_org_plan":
        plan = await repo.get_entity("org_plan", action.get("orgPlanId"))
        if not plan:
            raise HTTPException(status_code=404, detail="org plan not found")
        await _apply_org_plan(
            repo,
            plan,
            approved_by=action.get("approvedBy") or "user",
            approval_id=approval["id"],
        )
    else:
        raise HTTPException(status_code=400, detail="unsupported approval action")
    action["executedAt"] = now_ms()
    approval["action"] = action
    return True


async def _approval_requires_user_review(repo: Repo, approval: dict[str, Any]) -> bool:
    action = approval.get("action") if isinstance(approval.get("action"), dict) else {}
    if not isinstance(action, dict):
        return False
    tool_run_id = str(action.get("toolRunId") or "").strip()
    if tool_run_id:
        run = await repo.get_entity("tool_run", tool_run_id)
        if isinstance(run, dict) and run.get("tool") == "video.approve_render":
            return True
    artifact_id = str(action.get("artifactId") or "").strip()
    if artifact_id:
        artifact = await repo.get_entity("artifact", artifact_id)
        if isinstance(artifact, dict) and artifact.get("reviewStatus") == "pending_video_review":
            return True
    return False


async def _approve_pending_approvals_for_full_auto(
    repo: Repo,
    *,
    approved_by: str,
    now: int,
) -> dict[str, Any]:
    candidates = [
        approval
        for approval in reversed(await repo.list_pending_approvals())
        if approval.get("status") == "pending"
    ]
    pending: list[dict[str, Any]] = []
    skipped_user_review = 0
    for approval in candidates:
        if await _approval_requires_user_review(repo, approval):
            skipped_user_review += 1
            continue
        pending.append(approval)
    approved = 0
    executed = 0
    failed: list[dict[str, str]] = []

    for approval in pending:
        try:
            approval["status"] = "approved"
            approval["resolvedAt"] = now
            action = approval.get("action")
            actor = "executive" if isinstance(action, dict) and action.get("action") == "close_task" else approved_by
            approval["resolvedBy"] = actor
            approval["autoApprovedBy"] = actor
            if isinstance(action, dict) and not action.get("approvedBy"):
                action["approvedBy"] = actor
                approval["action"] = action
            did_execute = await _execute_approval_action(repo, approval)
            if did_execute:
                executed += 1
            await repo.save_approval(approval)
            await _upsert_approval_chat_message(repo, approval)
            await repo.add_activity(_activity(
                (
                    f"ผู้บริหาร AI ตรวจและอนุมัติปิดงาน: {approval['title']}"
                    if isinstance(action, dict) and action.get("action") == "close_task"
                    else f"อนุมัติอัตโนมัติจากโหมดสิทธิ์เต็ม: {approval['title']}"
                ),
                type_="approval",
                department_id=approval.get("departmentId"),
                severity="good",
                ts=now,
            ))
            await repo.s.commit()
            approved += 1
        except Exception as exc:
            await repo.s.rollback()
            failed.append({"id": str(approval.get("id") or ""), "error": f"{type(exc).__name__}: {exc}"})
            await repo.add_activity(_activity(
                f"อนุมัติอัตโนมัติไม่สำเร็จ: {approval.get('title', approval.get('id'))} — {type(exc).__name__}: {_clip_text(str(exc), 240)}",
                type_="approval",
                department_id=approval.get("departmentId"),
                severity="alert",
                ts=now,
            ))
            await repo.s.commit()

    if pending:
        await repo.add_activity(_activity(
            f"โหมดสิทธิ์เต็มอนุมัติงานค้าง {approved}/{len(pending)} รายการ",
            type_="approval",
            severity="good" if not failed else "warn",
            ts=now,
        ))
    return {
        "pending": len(pending),
        "approved": approved,
        "executed": executed,
        "failed": failed,
        "skippedUserReview": skipped_user_review,
    }


async def _run_full_auto_approval_sweeper() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            async with session_scope() as s:
                repo = Repo(s)
                policy = await repo.get_permission_policy()
                if not _permission_mode_is_full(policy.get("mode")):
                    continue
                result = await _approve_pending_approvals_for_full_auto(repo, approved_by="full_auto", now=now_ms())
                if result["approved"] or result["failed"]:
                    hub.mark_dirty()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("full-auto approval sweep failed")


async def _get_artifact_version(repo: Repo, artifact_id: str, version: int) -> dict[str, Any]:
    row = await repo.get_entity("artifact_version", f"{artifact_id}:{version}")
    if not row:
        raise HTTPException(status_code=404, detail="artifact version not found")
    return row


async def _write_artifact_content_version(
    repo: Repo,
    artifact: dict[str, Any],
    *,
    text: str,
    author: str,
    note: str,
    status: str | None = None,
    preview_kind: str = "md",
) -> dict[str, Any]:
    from .storage.object_store import get_object_store

    now = now_ms()
    previous = int(artifact.get("version") or 1)
    next_version = previous + 1
    settings = get_settings()
    content_hash: str | None = None
    if settings.object_store_enabled:
        stored = get_object_store(settings).put_text(text)
        content_hash = stored.content_hash
        artifact["contentHash"] = content_hash
        artifact["contentSizeBytes"] = stored.size_bytes
        artifact["contentMime"] = stored.mime
        artifact["storage"] = "object_store"
        artifact["uri"] = stored.uri
        artifact["preview"] = {"kind": preview_kind, "uri": stored.uri}
        version_uri = stored.uri
    else:
        path = _artifact_content_path(artifact, next_version)
        path.write_text(text, encoding="utf-8")
        artifact["contentHash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        artifact["contentSizeBytes"] = len(text.encode("utf-8"))
        artifact["contentMime"] = "text/plain; charset=utf-8"
        artifact["storage"] = "filesystem"
        artifact["uri"] = str(path)
        artifact["preview"] = {"kind": preview_kind, "uri": str(path)}
        version_uri = str(path)
    artifact["version"] = next_version
    if status:
        artifact["status"] = status
    artifact["updatedAt"] = now
    artifact["updatedBy"] = author
    version = ArtifactVersion(
        artifact_id=artifact["id"],
        version=next_version,
        author=author,
        ts=now,
        note=note,
        parent=previous,
        uri=version_uri,
        preview=artifact["preview"],
    ).dump()
    version["storage"] = artifact.get("storage")
    version["contentHash"] = artifact.get("contentHash") or content_hash
    version["contentSizeBytes"] = artifact.get("contentSizeBytes")
    version["contentMime"] = artifact.get("contentMime")
    await repo.put_entity(
        "artifact",
        artifact,
        dept=artifact.get("ownerDept"),
        project=artifact.get("projectId"),
        status=artifact.get("status"),
        ts=now,
    )
    await repo.put_entity(
        "artifact_version",
        {**version, "id": f"{artifact['id']}:{next_version}"},
        dept=artifact.get("ownerDept"),
        project=artifact.get("projectId"),
        status=artifact.get("status"),
        ts=now,
    )
    audit = _git_commit_workspace(
        _workspace_for_artifact(artifact),
        f"artifact {artifact['id']} v{next_version}: {note[:80]}",
    )
    detail = f"อัปเดต artifact “{artifact['name']}” เป็น v{next_version}"
    if audit.get("head"):
        detail += f" ({str(audit['head'])[:7]})"
    await repo.add_activity(_activity(
        detail,
        type_="system",
        department_id=artifact.get("ownerDept"),
        severity="good",
    ))
    return {"artifact": artifact, "version": version, "content": text}


def _parse_upload_tags(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        items = re.split(r"[,;\n]+", str(raw))
    tags: list[str] = []
    for item in items:
        tag = str(item or "").strip()
        if tag and tag not in tags:
            tags.append(tag[:48])
    return tags


async def _store_file_artifact(
    repo: Repo,
    *,
    data: bytes,
    filename: str,
    owner_dept: str,
    project_id: str | None = None,
    artifact_name: str | None = None,
    tags: list[str] | None = None,
    links: list[str] | None = None,
    created_by: str = "upload",
    status: str = "approved",
    mime: str | None = None,
    force_audio_transcription: bool | None = None,
    audio_transcription_model: str | None = None,
    audio_transcription_language: str | None = None,
    audio_transcription_prompt: str | None = None,
) -> dict[str, Any]:
    from .storage.object_store import get_object_store

    settings = get_settings()
    now = now_ms()
    filename = safe_filename(filename)
    artifact_id = uid("art")
    mime = guess_mime(filename, mime)
    kind = artifact_kind_for_file(filename, mime)
    preview = extract_preview_from_bytes(data, filename=filename, mime=mime)
    audio_file = is_audio_file(filename, mime)
    audio_transcription: dict[str, Any] | None = None
    if audio_file:
        should_transcribe = (
            bool(settings.audio_transcription_enabled)
            and (
                force_audio_transcription is True
                or (
                    force_audio_transcription is None
                    and bool(settings.audio_transcription_auto_on_upload)
                )
            )
        )
        if should_transcribe:
            try:
                transcribed = await transcribe_audio_bytes(
                    data,
                    filename=filename,
                    mime=mime,
                    settings=settings,
                    model=audio_transcription_model,
                    language=audio_transcription_language,
                    prompt=audio_transcription_prompt,
                )
                audio_transcription = transcribed.public_dict()
                preview = FilePreview(
                    text=format_audio_transcript_preview(transcribed),
                    preview_kind="md",
                    extraction="audio_transcription",
                    metadata={
                        "filename": filename,
                        "mime": mime,
                        "sizeBytes": len(data),
                        "provider": transcribed.provider,
                        "model": transcribed.model,
                    },
                )
            except AudioTranscriptionNotConfigured as exc:
                audio_transcription = {
                    "status": "skipped",
                    "reason": str(exc),
                    "provider": settings.audio_transcription_provider,
                    "model": audio_transcription_model or settings.audio_transcription_model,
                    "mime": mime,
                    "filename": filename,
                }
            except AudioTranscriptionError as exc:
                audio_transcription = {
                    "status": "failed",
                    "error": str(exc),
                    "provider": settings.audio_transcription_provider,
                    "model": audio_transcription_model or settings.audio_transcription_model,
                    "mime": mime,
                    "filename": filename,
                }
        else:
            audio_transcription = {
                "status": "skipped",
                "reason": "audio transcription is disabled for uploads",
                "provider": settings.audio_transcription_provider,
                "model": audio_transcription_model or settings.audio_transcription_model,
                "mime": mime,
                "filename": filename,
            }
    content_hash = hashlib.sha256(data).hexdigest()
    content_size = len(data)
    storage = "filesystem"
    uri: str
    preview_record: dict[str, Any] | None = None

    if settings.object_store_enabled:
        store = get_object_store(settings)
        stored = store.put_bytes(data, mime=mime)
        uri = stored.uri
        content_hash = stored.content_hash
        content_size = stored.size_bytes
        if preview.text.strip():
            preview_obj = store.put_text(preview.text, mime="text/markdown; charset=utf-8")
            preview_record = {"kind": preview.preview_kind or "md", "uri": preview_obj.uri}
        storage = "object_store"
    else:
        dest_dir = _workspace_for_dept(owner_dept) / "imports"
        dest_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix
        dest = (dest_dir / f"{artifact_id}{suffix}").resolve()
        dest.write_bytes(data)
        uri = str(dest)
        if preview.text.strip():
            preview_path = (dest_dir / f"{artifact_id}.preview.md").resolve()
            preview_path.write_text(preview.text, encoding="utf-8")
            preview_record = {"kind": preview.preview_kind or "md", "uri": str(preview_path)}

    if preview_record is None and mime.startswith("image/"):
        preview_record = {"kind": "image", "uri": uri}
    elif preview_record is None and mime == "application/pdf":
        preview_record = {"kind": "pdf", "uri": uri}

    artifact_tags = _parse_upload_tags(tags or [])
    for tag in ("import", "attachment"):
        if tag not in artifact_tags:
            artifact_tags.append(tag)
    if audio_file and "audio" not in artifact_tags:
        artifact_tags.append("audio")
    if (audio_transcription or {}).get("status") == "succeeded" and "transcribed" not in artifact_tags:
        artifact_tags.append("transcribed")
    artifact = Artifact(
        id=artifact_id,
        name=(artifact_name or filename)[:180],
        kind=kind,
        mime=mime,
        owner_dept=owner_dept,
        task_ids=[],
        project_id=project_id,
        version=1,
        status=status,  # type: ignore[arg-type]
        uri=uri,
        storage=storage,  # type: ignore[arg-type]
        content_hash=content_hash,
        content_size_bytes=content_size,
        content_mime=mime,
        tags=artifact_tags,
        links=links or [],
        preview=preview_record,
        created_at=now,
        created_by=created_by,
        updated_at=now,
        updated_by=created_by,
    ).dump()
    if audio_transcription:
        artifact["audioTranscription"] = audio_transcription
    artifact["extraction"] = {
        "status": preview.extraction,
        "warnings": list(preview.warnings),
    }
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=1,
        author=created_by,
        ts=now,
        note=f"uploaded {filename}",
        parent=None,
        uri=uri,
        storage=storage,  # type: ignore[arg-type]
        content_hash=content_hash,
        content_size_bytes=content_size,
        content_mime=mime,
        preview=preview_record,
    ).dump()
    if audio_transcription:
        version["audioTranscription"] = audio_transcription
    await repo.put_entity("artifact", artifact, dept=owner_dept, project=project_id, status=status, ts=now)
    await repo.put_entity(
        "artifact_version",
        {**version, "id": f"{artifact_id}:1"},
        dept=owner_dept,
        project=project_id,
        status=status,
        ts=now,
    )

    knowledge = None
    if preview.text.strip() and preview.extraction != "metadata":
        knowledge = {
            "id": uid("kn"),
            "title": f"File: {artifact['name']}",
            "ts": now,
            "score": 0.78,
            "text": preview.text[:10_000],
            "tags": [*artifact_tags, artifact_id],
            "source": artifact_id,
        }
        # Upload must stay responsive. Store lexical knowledge immediately; the
        # existing re-embed/consolidation paths can enrich vectors asynchronously.
        await repo.add_knowledge(owner_dept, knowledge, source=artifact_id)

    await repo.add_activity(_activity(
        f"นำเข้าไฟล์ {filename} เป็น artifact สำหรับฝ่าย{owner_dept}",
        type_="system",
        department_id=owner_dept,
        severity="good",
    ))
    return {"artifact": artifact, "version": version, "knowledge": knowledge, "preview": preview}


async def _read_upload_bytes(file: UploadFile) -> bytes:
    limit = int(get_settings().max_upload_bytes)
    if limit <= 0:
        return await file.read()
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"upload exceeds {limit} bytes")
    return data


async def _validate_departments(repo: Repo, department_ids: list[str]) -> None:
    for dept_id in department_ids:
        if not await repo.get_department(dept_id):
            raise HTTPException(status_code=404, detail=f"department not found: {dept_id}")


async def _snapshot() -> dict[str, Any]:
    async with session_scope() as s:
        return await Repo(s).snapshot()


async def _refresh_memory_stats(repo: Repo, dept_id: str) -> None:
    await repo.refresh_department_memory_stats(dept_id)


async def _patch_department(repo: Repo, dept_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    dept = await repo.get_department(dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="department not found")
    clean_patch = {k: v for k, v in patch.items() if v is not None}
    next_dept = {**dept, **clean_patch}
    raw_model = next_dept.get("model", DEFAULT_MODEL)
    raw_effort = next_dept.get("thinkingEffort", "high")
    if "thinkingEffort" not in clean_patch and ("providerId" in clean_patch or "model" in clean_patch):
        provider_probe, model_probe, _ = normalize_ai_config(
            next_dept.get("providerId", "claude_code"),
            raw_model,
            raw_effort,
        )
        if provider_probe != dept.get("providerId") or model_probe != dept.get("model"):
            raw_effort = default_thinking_effort_for_model(model_probe)
    provider_id, model, effort = normalize_ai_config(
        next_dept.get("providerId", "claude_code"),
        raw_model,
        raw_effort,
    )
    next_dept["providerId"] = provider_id
    next_dept["model"] = model
    next_dept["thinkingEffort"] = effort
    next_dept["speed"] = coerce_model_speed(model, next_dept.get("speed", "standard"))
    await repo.save_department(next_dept)
    from .org.capabilities import sync_department_capabilities

    await sync_department_capabilities(repo, next_dept, source="department_patch")
    return next_dept


def _room_for_new_department(existing_departments: list[dict[str, Any]]) -> dict[str, int]:
    placed = len([d for d in existing_departments if not is_exec(d["id"])])
    col = placed % 3
    row = placed // 3
    return {"x": 1 + col * 6, "y": 6 + row * 5, "w": 5, "h": 4}


def _slug_id(prefix: str, text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return f"{prefix}_{slug[:40] or uid(prefix)}"


def _required_department_provider(raw: dict[str, Any]) -> str:
    provider_id = str(raw.get("providerId") or raw.get("provider_id") or "").strip()
    if not provider_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "providerId is required when creating a department; prefer claude_code "
                "(Claude Code) when connected, then chatgpt_account (ChatGPT OAuth) when connected; "
                "use openai for OpenAI Platform API-key chat/subsystems."
            ),
        )
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unsupported providerId: {provider_id}")
    return provider_id


def _org_department_spec(raw: dict[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name") or "").strip()[:120]
    role = str(raw.get("role") or "").strip()[:240]
    if not name or not role:
        raise HTTPException(status_code=400, detail="each org-plan department requires name and role")
    provider_id, model, effort = normalize_ai_config(
        _required_department_provider(raw),
        str(raw.get("model") or DEFAULT_MODEL),
        str(raw.get("thinkingEffort") or raw.get("thinking_effort") or "high"),
    )
    speed = coerce_model_speed(model, str(raw.get("speed") or "standard"))
    return {
        "id": str(raw.get("id") or "").strip() or None,
        "name": name,
        "role": role,
        "charter": str(raw.get("charter") or role).strip()[:2000],
        "agentName": str(raw.get("agentName") or raw.get("agent_name") or f"{name} Agent").strip()[:120],
        "providerId": provider_id,
        "model": model,
        "thinkingEffort": effort,
        "speed": speed,
        "emoji": str(raw.get("emoji") or "🟣")[:8],
        "accent": raw.get("accent"),
        "autonomy": bool(raw.get("autonomy", False)),
        "skills": [str(item).strip() for item in raw.get("skills", []) if str(item).strip()],
        "tools": [str(item).strip() for item in raw.get("tools", []) if str(item).strip()],
    }


def _department_from_org_spec(
    spec: dict[str, Any],
    *,
    dept_id: str,
    existing_departments: list[dict[str, Any]],
    created_at: int,
) -> dict[str, Any]:
    provider_id, model, effort = normalize_ai_config(
        _required_department_provider(spec),
        spec.get("model") or DEFAULT_MODEL,
        spec.get("thinkingEffort") or "high",
    )
    speed = coerce_model_speed(model, spec.get("speed") or "standard")
    accent = spec.get("accent") or ACCENTS[len(existing_departments) % len(ACCENTS)]
    if accent not in ACCENTS:
        accent = ACCENTS[len(existing_departments) % len(ACCENTS)]
    return {
        "id": dept_id,
        "name": spec["name"],
        "role": spec["role"],
        "charter": spec.get("charter") or spec["role"],
        "emoji": spec.get("emoji") or "🟣",
        "accent": accent,
        "providerId": provider_id,
        "model": model,
        "thinkingEffort": effort,
        "speed": speed,
        "agentName": spec.get("agentName") or f"{spec['name']} Agent",
        "state": "idle",
        "mood": 0.85,
        "currentTaskId": None,
        "autonomy": bool(spec.get("autonomy", False)),
        "createdAt": created_at,
        "room": _room_for_new_department(existing_departments),
        "memory": {
            "archiveChunks": 0,
            "ragEntries": 0,
            "graphNodes": 0,
            "graphEdges": 0,
            "lastCompactionAt": None,
            "tokensSaved": 0,
        },
        "skills": spec.get("skills", []),
        "tools": spec.get("tools", []),
        "workspacePath": _provision_workspace(dept_id),
        "visibilityPolicy": _visibility_policy(dept_id),
    }


async def _apply_org_plan(
    repo: Repo,
    plan: dict[str, Any],
    *,
    approved_by: str,
    approval_id: str | None = None,
) -> dict[str, Any]:
    if plan.get("status") == "applied":
        return plan
    if plan.get("status") == "rejected":
        raise HTTPException(status_code=400, detail="rejected org plan cannot be applied")

    now = now_ms()
    existing_departments = await repo.list_departments()
    applied_ids: list[str] = []
    for raw_spec in plan.get("departments") or []:
        spec = _org_department_spec(dict(raw_spec))
        dept_id = spec.get("id") or _slug_id("dept", spec["name"])
        existing_dept = await repo.get_department(dept_id)
        if existing_dept:
            applied_ids.append(dept_id)
            from .org.capabilities import sync_department_capabilities

            await sync_department_capabilities(repo, existing_dept, source="org_plan")
            await repo.add_activity(_activity(
                f"onboarding org plan: ใช้แผนกเดิม {dept_id}",
                type_="system",
                department_id=dept_id,
                severity="info",
            ))
            continue
        dept = _department_from_org_spec(
            spec,
            dept_id=dept_id,
            existing_departments=existing_departments,
            created_at=now,
        )
        await repo.save_department(dept)
        from .org.capabilities import sync_department_capabilities

        await sync_department_capabilities(repo, dept, source="org_plan")
        existing_departments.append(dept)
        applied_ids.append(dept_id)
        await repo.add_activity(_activity(
            f"onboarding org plan: เปิดแผนก {dept['name']} ({dept['agentName']})",
            type_="system",
            department_id=dept_id,
            severity="good",
        ))

    decision = Decision(
        id=uid("dec"),
        title=f"Apply ผังองค์กรแบบ Full Auto: {plan.get('objective', plan['id'])[:120]}",
        proposed_by=plan.get("createdBy") or "executive",
        approved_by=approved_by,
        rationale="Full Auto apply org chart ที่ผู้บริหารเสนอ พร้อม decision/audit record",
        alternatives=[
            f"{item.get('name')}: {item.get('role')}"
            for item in plan.get("departments", [])
        ],
        impact=f"created departments: {', '.join(applied_ids) or '-'}",
        linked_task=None,
        linked_artifacts=[],
        status="approved",
        ts=now,
    ).dump()
    await repo.put_entity("decision", decision, status="approved", ts=now)
    plan.update({
        "status": "applied",
        "approvedBy": approved_by,
        "approvalId": approval_id or plan.get("approvalId"),
        "appliedDepartmentIds": applied_ids,
        "decisionId": decision["id"],
        "updatedAt": now,
    })
    await repo.put_entity("org_plan", plan, status="applied", ts=now)
    await repo.add_activity(_activity(
        f"Full Auto สร้างผังองค์กร {len(applied_ids)} แผนก",
        type_="system",
        severity="good",
    ))
    return plan


async def _record_learning_signal(
    repo: Repo,
    *,
    source: str,
    what_went_wrong: str,
    lesson_text: str,
    task_id: str | None = None,
    artifact_id: str | None = None,
    applied_to: list[str] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper — prefer reflect_and_record for trajectory-based lessons."""
    dept_id = EXEC_ID
    if task_id:
        task = await repo.get_task(task_id)
        if task:
            dept_id = task.get("departmentId") or dept_id
    elif artifact_id:
        artifact = await repo.get_entity("artifact", artifact_id)
        if artifact:
            dept_id = artifact.get("ownerDept") or dept_id
    dept = await repo.get_department(dept_id) or await repo.get_department(EXEC_ID)
    if dept and get_settings().reflection_enabled:
        task = await repo.get_task(task_id) if task_id else None
        artifact = await repo.get_entity("artifact", artifact_id) if artifact_id else None
        return await reflect_and_record(
            repo,
            dept,
            source=source,
            what_went_wrong=what_went_wrong,
            fallback_lesson=lesson_text,
            task=task,
            artifact=artifact,
            task_id=task_id,
            artifact_id=artifact_id,
            applied_to=applied_to,
        )
    return await record_learning_signal(
        repo,
        source=source,
        what_went_wrong=what_went_wrong,
        lesson_text=lesson_text,
        task_id=task_id,
        artifact_id=artifact_id,
        applied_to=applied_to,
    )


def _format_memory_context(knowledge: list[dict[str, Any]], graph: dict[str, Any]) -> str:
    parts: list[str] = []
    if knowledge:
        lines = []
        for item in knowledge:
            text = str(item.get("text", "")).strip().replace("\n", " ")
            lines.append(f"- {item.get('title', item.get('id'))}: {text[:360]}")
        parts.append("Relevant department knowledge:\n" + "\n".join(lines))
    nodes = graph.get("nodes", [])[:8]
    edges = graph.get("edges", [])[:8]
    if nodes:
        labels = ", ".join(f"{n.get('label')}({n.get('type')})" for n in nodes if n.get("label"))
        rels = ", ".join(f"{e.get('from')}->{e.get('to')}:{e.get('rel')}" for e in edges)
        parts.append("Relevant graph hints:\n" + labels + (f"\nRelations: {rels}" if rels else ""))
    return "\n\n".join(parts)


def _system_prompt(dept: dict[str, Any], memory_context: str = "") -> str:
    if is_exec(dept["id"]):
        base = (
            f"คุณคือ {dept['agentName']} ผู้บริหารของบริษัท AI ATRIUM. "
            "หน้าที่คือรับโจทย์จากผู้ใช้ แตกงาน มอบหมายงาน ตรวจคุณภาพ และสรุปกลับเป็นภาษาไทยที่ชัดเจน. "
            "ตอบให้กระชับ มีเหตุผล และพร้อมนำไปปฏิบัติ. "
            "ในการคุยจริงครั้งแรกกับเจ้าของ ให้ถามว่าอยากให้ผู้บริหารคนนี้ชื่ออะไร; "
            "เมื่อเจ้าของบอกชื่อ ให้ใช้ tool rename_self เพื่อบันทึกชื่อนั้นเป็นชื่อของตัวเองก่อนทำงานต่อ."
        )
    else:
        base = (
            f"คุณคือ {dept['agentName']} จากฝ่าย{dept['name']} ({dept['role']}). "
            f"ขอบเขตงาน: {dept['charter']} "
            "ตอบเป็นภาษาไทยแบบมืออาชีพ ระบุสิ่งที่ทำได้จริง ข้อจำกัด และขั้นถัดไปเมื่อจำเป็น."
        )
    base = f"{base}{persona_prompt(dept)}"
    if not memory_context:
        return base
    return (
        f"{base}\n\n"
        "ใช้ความจำของแผนกต่อไปนี้เป็นบริบทสำคัญก่อนตอบ ถ้าความจำไม่เกี่ยวข้องให้ละไว้โดยไม่ฝืนอ้าง:\n"
        f"{memory_context}"
    )


def _llm_history(history: list[dict[str, Any]], user_msg: dict[str, Any]) -> list[LLMMessage]:
    out: list[LLMMessage] = []
    settings = get_settings()
    limit = settings.chat_history_message_limit
    recent_history = history if limit <= 0 else history[-limit:]
    for msg in recent_history:
        role = "user" if msg["role"] == "user" else "assistant"
        prefix = "" if role == "user" else f"{msg.get('authorName', 'agent')}: "
        text = message_text_with_attachment_refs(str(msg.get("text") or ""), msg.get("attachments") or [])
        out.append(LLMMessage(role=role, content=f"{prefix}{text}"))
    content = message_content_with_attachment_images(
        str(user_msg.get("text") or ""),
        user_msg.get("attachments") or [],
        max_images=int(settings.image_context_max_images),
        max_bytes=int(settings.image_context_max_bytes),
    )
    out.append(LLMMessage(role="user", content=content))
    return out


def _context_payload_for_turn(
    dept: dict[str, Any],
    history: list[dict[str, Any]],
    user_msg: dict[str, Any],
    *,
    memory_context: str = "",
    departments: list[dict[str, Any]] | None = None,
    system_extra: str = "",
) -> tuple[str, list[LLMMessage], list[dict[str, Any]]]:
    system = _system_prompt(dept, memory_context)
    if system_extra:
        system = f"{system}\n\n{system_extra}"
    departments = departments or []
    tools = chat_tool_definitions(departments, dept) if should_enable_chat_tools(user_msg.get("text", ""), dept) else []
    if (
        tools
        and likely_needs_chat_tools(str(user_msg.get("text") or ""))
    ):
        system = f"{system}\n\n{chat_tool_system_instructions(departments, dept)}"
    return system, _llm_history(history, user_msg), tools


async def _context_tokens_for_turn(
    dept: dict[str, Any],
    history: list[dict[str, Any]],
    user_msg: dict[str, Any],
    *,
    memory_context: str = "",
    departments: list[dict[str, Any]] | None = None,
    system_extra: str = "",
) -> tuple[int, str]:
    system, messages, tools = _context_payload_for_turn(
        dept,
        history,
        user_msg,
        memory_context=memory_context,
        departments=departments,
        system_extra=system_extra,
    )
    fallback = estimate_llm_context_tokens(system, messages)
    try:
        settings = get_settings()
        provider = get_provider(str(dept.get("providerId") or "claude_code"), settings)
        tokens = await asyncio.wait_for(
            provider.count_context_tokens(
                system=system,
                messages=messages,
                model=str(dept.get("model") or DEFAULT_MODEL),
                effort=coerce_thinking_effort(str(dept.get("model") or DEFAULT_MODEL), str(dept.get("thinkingEffort") or "high")),
                speed=coerce_model_speed(str(dept.get("model") or DEFAULT_MODEL), str(dept.get("speed") or "standard")),
                tools=tools,
            ),
            timeout=8.0,
        )
        if tokens > 0:
            return tokens, "provider"
    except Exception as exc:
        logger.debug("provider token count unavailable; using estimator: %s", exc)
    return fallback, "estimate"


async def _retrieval_context(repo: Repo, dept: dict[str, Any], text: str) -> tuple[str, list[dict[str, Any]]]:
    from .memory.executive_retrieval import retrieval_context_for_department
    from .runtime.turns import runtime_recall_snippet

    context, hits = await retrieval_context_for_department(repo, dept, text)
    recall = await runtime_recall_snippet(dept, text)
    if recall:
        context = (context + "\n\n" + recall) if context else recall
    return context, hits


async def _maybe_enqueue_context_compaction(
    repo: Repo,
    *,
    thread_id: str,
    dept: dict[str, Any],
    message_count: int | None = None,
    estimated_context_tokens: int | None = None,
    context_token_source: str = "estimate",
) -> bool:
    settings = get_settings()
    model = str(dept.get("model") or DEFAULT_MODEL)
    compact_context_threshold = model_auto_compact_context_tokens(
        model,
        claude_threshold=int(settings.compact_claude_context_tokens),
        gpt_threshold=int(settings.compact_gpt_context_tokens),
        small_window_ratio=float(settings.compact_context_window_ratio),
    )
    context_tokens = int(estimated_context_tokens or 0)
    reason = ""
    if context_tokens > compact_context_threshold:
        reason = "context_threshold"
    else:
        threshold = int(settings.compact_message_threshold)
        count = int(message_count or 0)
        if threshold > 0 and count >= threshold and count % threshold == 0:
            reason = "message_threshold"
    if not reason:
        return False
    await repo.enqueue(
        uid("job"),
        "compact_dept",
        {
            "departmentId": dept["id"],
            "threadId": thread_id,
            "reason": reason,
            "estimatedContextTokens": context_tokens or None,
            "contextTokenSource": context_token_source,
            "compactContextThreshold": compact_context_threshold,
            "model": model,
        },
        now_ms(),
        priority=2,
    )
    metric = (
        f" context {context_tokens:,}/{compact_context_threshold:,} tokens"
        if context_tokens
        else ""
    )
    await repo.add_activity(_activity(
        f"คิว compact ความจำของฝ่าย{dept['name']}จาก thread ที่ยาวขึ้น{metric}",
        type_="compaction",
        department_id=dept["id"],
        severity="info",
    ))
    return True


async def _complete_with_provider(
    dept: dict[str, Any],
    history: list[dict[str, Any]],
    user_msg: dict[str, Any],
    memory_context: str = "",
    departments: list[dict[str, Any]] | None = None,
    system_extra: str = "",
    on_stream_event=None,
    stream_msg_id: str | None = None,
) -> LLMResult:
    settings = get_settings()
    departments = departments or []
    tools = chat_tool_definitions(departments, dept) if should_enable_chat_tools(user_msg.get("text", ""), dept) else []
    system = _system_prompt(dept, memory_context)
    if system_extra:
        system = f"{system}\n\n{system_extra}"
    if tools and likely_needs_chat_tools(str(user_msg.get("text") or "")):
        system = f"{system}\n\n{chat_tool_system_instructions(departments, dept)}"
    messages = _llm_history(history, user_msg)
    runtime_tools = tools
    use_agent_runtime = settings.use_letta_runtime and not provider_has_native_chat_stream(dept.get("providerId"))
    if use_agent_runtime:
        from .runtime.provisioning import ensure_department_runtime_agent_safely
        from .runtime.turns import (
            RuntimeTurnUnavailable,
            complete_agent_via_runtime,
            runtime_dependency_result,
        )

        active = dept
        async with session_scope() as s:
            repo = Repo(s)
            active = await repo.get_department(dept["id"]) or dept
            meta = await ensure_department_runtime_agent_safely(repo, active, settings=settings)
            if meta and meta.get("lettaAgentId"):
                active = {**active, "runtime": meta}

        runtime_thread_id = str(user_msg.get("threadId") or "")

        async def emit_runtime_event(event: Any) -> None:
            if runtime_thread_id and stream_msg_id:
                pulse = runtime_event_to_hub_pulse(event, thread_id=runtime_thread_id, msg_id=stream_msg_id)
                if pulse:
                    hub.pulse(pulse)
            if runtime_thread_id:
                with contextlib.suppress(Exception):
                    from .memory.ledger import record_runtime_event_ledger

                    async with session_scope() as s:
                        await record_runtime_event_ledger(
                            Repo(s),
                            event,
                            thread_id=runtime_thread_id,
                            department_id=dept["id"],
                            message_id=stream_msg_id,
                            source="api",
                            category="chat",
                        )

        async def execute_runtime_tool(call: LLMToolCall) -> dict[str, Any]:
            async with session_scope() as s:
                repo = Repo(s)
                active_dept = await repo.get_department(dept["id"]) or dept
                record = await run_chat_tool(
                    repo,
                    call,
                    active_dept=active_dept,
                    thread_id=runtime_thread_id,
                    requested_by=dept.get("agentName", dept["id"]),
                )
            return record

        try:
            runtime_result = await complete_agent_via_runtime(
                active,
                thread_id=str(user_msg.get("threadId") or ""),
                system_prompt=system,
                messages=messages,
                metadata={"category": "chat", "source": "api"},
                on_stream_event=on_stream_event,
                client_tools=runtime_tools,
                tool_executor=execute_runtime_tool if runtime_tools else None,
                on_runtime_event=emit_runtime_event if runtime_thread_id else None,
                settings=settings,
                allow_provider_fallback=False,
            )
        except RuntimeTurnUnavailable as exc:
            return runtime_dependency_result(active, str(exc), category="chat", source="api", settings=settings)
        if runtime_result is not None:
            return runtime_result
        return runtime_dependency_result(active, "runtime returned no result", category="chat", source="api", settings=settings)

    provider = get_provider(dept.get("providerId", "claude_code"), settings)
    model = dept.get("model", DEFAULT_MODEL)
    effort = coerce_thinking_effort(model, dept.get("thinkingEffort", "high"))
    speed = coerce_model_speed(model, dept.get("speed", "standard"))
    if on_stream_event is not None:
        # Stream every turn — including tool-using turns. Text + thinking deltas
        # flow through on_stream_event; each tool call is announced live via
        # tool_call / tool_result pulses so the UI shows what the agent is doing
        # in real time. (tools == [] just makes this a single-shot stream.)
        stream_partials: list[LLMResult] = []
        stream_tool_runs: list[dict[str, Any]] = []
        thread_id = user_msg.get("threadId")
        first_turn = True
        prev_had_text = False
        while True:
            if not first_turn and prev_had_text:
                # visually separate consecutive assistant turns in the bubble
                await on_stream_event(LLMStreamEvent(kind="text_delta", text="\n\n"))
            first_turn = False
            result = await stream_llm(
                provider,
                system=system,
                messages=messages,
                model=model,
                effort=effort,
                speed=speed,
                on_event=on_stream_event,
                tools=tools,
            )
            prev_had_text = bool(result.text and result.text.strip())
            stream_partials.append(result)
            if not result.tool_calls:
                final = apply_result_totals(result, stream_partials)
                final.meta["toolRuns"] = stream_tool_runs
                return final
            messages.append(assistant_tool_message(result))
            round_records: list[dict[str, Any]] = []
            async with session_scope() as s:
                repo = Repo(s)
                active_dept = await repo.get_department(dept["id"]) or dept
                for call in result.tool_calls:
                    if stream_msg_id:
                        hub.pulse({
                            "kind": "tool_call",
                            "threadId": thread_id,
                            "msgId": stream_msg_id,
                            "run": {
                                "id": call.id,
                                "toolUseId": call.id,
                                "tool": call.name,
                                "departmentId": active_dept["id"],
                                "args": call.input or {},
                                "status": "running",
                                "startedAt": now_ms(),
                            },
                        })
                    record = await run_chat_tool(
                        repo,
                        call,
                        active_dept=active_dept,
                        thread_id=thread_id,
                        requested_by=dept.get("agentName", dept["id"]),
                    )
                    round_records.append(record)
                    if stream_msg_id:
                        hub.pulse({
                            "kind": "tool_result",
                            "threadId": thread_id,
                            "msgId": stream_msg_id,
                            "run": record,
                        })
            stream_tool_runs.extend(round_records)
            messages.append(tool_result_message(round_records))
    partials: list[LLMResult] = []
    tool_runs: list[dict[str, Any]] = []
    # งานจริงมีโอกาสวน tool loop มากกว่า 50 ครั้ง จึงไม่ควรใส่ max tool loop
    while True:
        result = await provider.complete(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            speed=speed,
            tools=tools,
        )

        partials.append(result)
        if not result.tool_calls:
            final = apply_result_totals(result, partials)
            final.meta["toolRuns"] = tool_runs
            return final

        messages.append(assistant_tool_message(result))
        round_records: list[dict[str, Any]] = []
        async with session_scope() as s:
            repo = Repo(s)
            active_dept = await repo.get_department(dept["id"]) or dept
            for call in result.tool_calls:
                round_records.append(await run_chat_tool(
                    repo,
                    call,
                    active_dept=active_dept,
                    thread_id=user_msg["threadId"],
                    requested_by=dept.get("agentName", dept["id"]),
                ))
        tool_runs.extend(round_records)
        messages.append(tool_result_message(round_records))


def _partial_chat_result(
    dept: dict[str, Any],
    text: str,
    *,
    stop_reason: str,
    error_type: str | None = None,
    error_detail: str | None = None,
) -> LLMResult:
    meta: dict[str, Any] = {}
    if stop_reason == "cancelled":
        meta["cancelled"] = True
    if error_type:
        meta["streamErrorType"] = error_type
    if error_detail:
        meta["streamErrorDetail"] = error_detail
    provider_error = error_detail or error_type or "unknown"
    return LLMResult(
        text=text
        or (
            "หยุดการตอบแล้วก่อนมีข้อความตอบกลับ"
            if stop_reason == "cancelled"
            else f"AI จริงตอบไม่ได้เพราะ provider error: {provider_error}"
        ),
        tokens_in=0,
        tokens_out=0,
        model=dept.get("model", DEFAULT_MODEL),
        provider_id=dept.get("providerId", "claude_code"),
        speed=coerce_model_speed(dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard")),
        stop_reason=stop_reason,
        meta=meta,
    )


def _message_usage_payload(
    result: LLMResult,
    dept: dict[str, Any],
    *,
    rag_hits: list[str],
    compact_enqueued: bool,
    warnings: list[dict[str, Any]] | None = None,
    rate_limit: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    thread_usd: float = 0.0,
) -> dict[str, Any]:
    return {
        "usd": result.usd,
        "ragHits": rag_hits,
        "compactEnqueued": compact_enqueued,
        "tokensIn": result.tokens_in,
        "tokensOut": result.tokens_out,
        "thinkingTokens": result.thinking_tokens,
        "providerId": result.provider_id,
        "model": result.model,
        "thinkingEffort": dept.get("thinkingEffort", "high"),
        "speed": result.speed or coerce_model_speed(result.model or dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard")),
        "stopReason": result.stop_reason,
        "generationMs": result.generation_ms,
        "reasoningStatus": result.reasoning_status,
        "reasoningChars": len(result.reasoning or ""),
        "redactedThinking": bool(result.meta.get("redactedThinking")),
        "toolRuns": result.meta.get("toolRuns", []),
        "warnings": warnings or [],
        "rateLimit": rate_limit,
        "budget": budget,
        "threadUsd": thread_usd,
    }


def _message_runtime_payload(result: LLMResult, dept: dict[str, Any]) -> dict[str, Any] | None:
    from .runtime.turns import runtime_result_metadata

    return runtime_result_metadata(result, dept)


def _runtime_dependency_detail(result: LLMResult) -> str:
    return str(result.meta.get("runtimeError") or "agent runtime unavailable")


def _replace_message(messages: list[dict[str, Any]], message: dict[str, Any]) -> list[dict[str, Any]]:
    replaced = False
    out: list[dict[str, Any]] = []
    for item in messages:
        if item.get("id") == message.get("id"):
            out.append(message)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(message)
    return out


async def _apply_thread_cost(repo: Repo, thread_id: str, message: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    messages = await repo.thread_messages(thread_id, limit=500)
    summary = thread_cost_summary(thread_id, _replace_message(messages, message))
    message = {**message, "threadCost": summary}
    return message, summary


async def _add_chat_system_line(
    repo: Repo,
    thread_id: str,
    text: str,
    *,
    activity: dict[str, Any] | None = None,
    department_id: str | None = None,
    flow: dict[str, Any] | None = None,
    war_room_id: str | None = None,
    meeting_id: str | None = None,
    severity: str = "info",
) -> dict[str, Any]:
    summary = thread_cost_summary(thread_id, await repo.thread_messages(thread_id, limit=500))
    msg = system_chat_message(
        thread_id,
        text,
        activity=activity,
        department_id=department_id,
        flow=flow,
        thread_cost=summary,
        war_room_id=war_room_id,
        meeting_id=meeting_id,
        severity=severity,
    )
    await repo.add_message(msg)
    hub.pulse({
        "kind": "chat_activity",
        "threadId": thread_id,
        "msgId": msg["id"],
        "departmentId": msg.get("departmentId"),
        "message": msg,
    })
    return msg


async def _tool_activity_lines(
    repo: Repo,
    thread_id: str,
    active_dept: dict[str, Any],
    tool_runs: list[dict[str, Any]],
    *,
    war_room_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    messages: list[dict[str, Any]] = []
    activities: list[dict[str, Any]] = []
    for run in tool_runs:
        tool = str(run.get("tool") or "tool")
        status = str(run.get("status") or "unknown")
        ok = status == "succeeded"
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        result = result if isinstance(result, dict) else {}
        activity_type = "system"
        activity_dept = run.get("departmentId") or active_dept.get("id")
        flow = None
        text = f"{active_dept.get('agentName', active_dept.get('id'))} ใช้ tool {tool}: {status}"

        task = result.get("task") if isinstance(result.get("task"), dict) else None
        if tool == "create_task" and task:
            target = await repo.get_department(str(task.get("departmentId") or ""))
            if target:
                activity_type = "task_assigned"
                activity_dept = target["id"]
                flow = handoff_flow(active_dept, target, task)
                text = f"ผู้บริหาร -> มอบ -> ฝ่าย{target['name']}: “{task['title']}”"
                hub.pulse({
                    "kind": "handoff",
                    "departmentId": active_dept.get("id"),
                    "toDepartmentId": target["id"],
                    "taskId": task.get("id"),
                    "threadId": thread_id,
                })
        elif tool == "schedule_meeting" and result.get("meeting"):
            text = f"{active_dept.get('agentName')} สร้าง meeting: {result['meeting'].get('title')}"
        elif tool == "create_artifact" and result.get("artifact"):
            text = f"{active_dept.get('agentName')} สร้าง artifact: {result['artifact'].get('name')}"
        elif tool == "propose_org_plan" and result.get("orgPlan"):
            org_plan = result["orgPlan"]
            text = f"{active_dept.get('agentName')} apply org chart {len(org_plan.get('departments', []))} แผนกแบบ Full Auto"
            activity_type = "system"
        elif tool == "run_owner_tool" and result.get("run"):
            run = result["run"]
            text = f"{active_dept.get('agentName')} เรียก Owner Mode tool {run.get('tool')}: {run.get('status')}"
            activity_type = "approval" if run.get("status") == "pending_approval" else "system"
            activity_dept = run.get("departmentId") or activity_dept
        elif result.get("summary"):
            text = f"{active_dept.get('agentName', active_dept.get('id'))} ใช้ tool {tool}: {result['summary']}"

        activity = _activity(
            text,
            type_=activity_type,
            department_id=activity_dept,
            severity="good" if ok else "warn",
        )
        activity["chatVisible"] = False
        await repo.add_activity(activity)
        activities.append(activity)
        messages.append(await _add_chat_system_line(
            repo,
            thread_id,
            text,
            activity=activity,
            department_id=activity_dept,
            flow=flow,
            war_room_id=war_room_id,
            severity="good" if ok else "warn",
        ))
    return messages, activities


def _assistant_role(dept: dict[str, Any]) -> str:
    return "executive" if is_exec(dept["id"]) else "agent"


def _with_turn_thinking_effort(
    dept: dict[str, Any],
    thinking_effort: ThinkingEffort | None,
) -> dict[str, Any]:
    if not thinking_effort:
        return dept
    model = dept.get("model", DEFAULT_MODEL)
    return {**dept, "thinkingEffort": coerce_thinking_effort(model, thinking_effort)}


def _with_turn_speed(
    dept: dict[str, Any],
    speed: ModelSpeed | None,
) -> dict[str, Any]:
    if speed is None:
        return {**dept, "speed": coerce_model_speed(dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard"))}
    return {**dept, "speed": coerce_model_speed(dept.get("model", DEFAULT_MODEL), speed)}


async def _send_war_room_message(
    thread_id: str,
    input: SendMessageInput,
    request: Request,
    user_msg: dict[str, Any],
) -> dict[str, Any]:
    settings = get_settings()
    war_room_id = war_room_id_from_thread(thread_id)
    if not war_room_id:
        raise HTTPException(status_code=400, detail="invalid war room thread")

    async with session_scope() as s:
        repo = Repo(s)
        war_room = await repo.get_entity("war_room", war_room_id)
        if not war_room:
            raise HTTPException(status_code=404, detail="war room not found")
        departments = await repo.list_departments()
        participants = war_room_participants(war_room, departments)
        if not participants:
            raise HTTPException(status_code=400, detail="war room has no valid participants")
        attachments = await _normalize_chat_attachments(repo, input)
        if not str(input.text or "").strip() and not attachments:
            raise HTTPException(status_code=400, detail="message text or attachments are required")
        estimate = estimate_input(input.text, attachments)
        if input_character_limit_exceeded(estimate):
            raise HTTPException(status_code=413, detail=input_character_limit_detail())
        mentions = resolve_department_mentions(input.text, departments)
        user_msg = {
            **user_msg,
            "attachments": attachments,
            "mentions": mentions,
            "status": "sent",
            "input": _input_metadata(estimate, status="sent"),
        }
        await _attach_message_refs(repo, thread_id, user_msg, input)
        draft_cleared = await _delete_thread_draft(repo, thread_id)

        history = await _thread_messages_for_live_prompt(repo, thread_id)
        user_msg = {**user_msg, "warRoomId": war_room_id}
        await repo.add_message(user_msg)
        activity = _activity(
            f"War room “{war_room.get('title', war_room_id)}” รับโจทย์จากคุณ",
            type_="message",
            severity="good",
        )
        await repo.add_activity(activity)
        system_line = system_chat_message(
            thread_id,
            (
                f"War room “{war_room.get('title', war_room_id)}” เปิดวงตอบร่วมกับ "
                f"{', '.join(str(dept.get('agentName') or dept.get('name')) for dept in participants)}"
            ),
            activity=activity,
            flow=war_room_flow(war_room, participants),
            war_room_id=war_room_id,
            severity="good",
        )
        await repo.add_message(system_line)
        response_messages: list[dict[str, Any]] = [system_line]
        response_activity: list[dict[str, Any]] = [activity]

        if settings.engine_enabled:
            pending_replies: list[dict[str, Any]] = []
            for participant in participants:
                active = await update_agent_state(repo, participant, "thinking", mood_delta=-0.01)
                reply = {
                    "id": uid("msg"),
                    "threadId": thread_id,
                    "role": _assistant_role(active),
                    "authorName": active["agentName"],
                    "text": "กำลังคิดและทำงานต่อใน war room...",
                    "ts": now_ms(),
                    "pending": True,
                    **agent_message_metadata(active, war_room_id=war_room_id),
                }
                await repo.add_message(reply)
                pending_replies.append(reply)
                await repo.enqueue(
                    uid("job"),
                    "chat_reply",
                    {
                        "threadId": thread_id,
                        "departmentId": active["id"],
                        "userMessageId": user_msg["id"],
                        "replyMessageId": reply["id"],
                        "text": input.text,
                        "userTs": user_msg["ts"],
                        "replyTs": reply["ts"],
                        "thinkingEffort": input.thinking_effort,
                        "speed": input.speed,
                        "warRoomId": war_room_id,
                    },
                    now_ms(),
                    priority=1,
                )
            queue_activity = _activity(
                f"คิว multi-agent war room {len(pending_replies)} คนสำหรับ “{war_room.get('title', war_room_id)}”",
                type_="message",
                severity="info",
            )
            await repo.add_activity(queue_activity)
            response_activity.append(queue_activity)
            response_messages.extend(pending_replies)
            summary = thread_cost_summary(thread_id, [*history, user_msg, system_line, *pending_replies])
            usage = {
                "usd": 0.0,
                "ragHits": [],
                "compactEnqueued": False,
                "toolRuns": [],
                "thinkingEffort": input.thinking_effort,
                "speed": input.speed,
                "threadUsd": summary["totalUsd"],
            }
            hub.pulse({
                "kind": "war_room",
                "threadId": thread_id,
                "warRoomId": war_room_id,
                "departmentId": pending_replies[0].get("departmentId"),
                "participants": [dept.get("id") for dept in participants],
            })
            hub.mark_dirty()
            return _chat_response(
                pending_replies[0],
                usage=usage,
                messages=response_messages,
                activity=response_activity,
                mentions=mentions,
                estimate=estimate,
                draft_cleared=draft_cleared,
            )

    hub.pulse({
        "kind": "war_room",
        "threadId": thread_id,
        "warRoomId": war_room_id,
        "departmentId": participants[0].get("id"),
        "participants": [dept.get("id") for dept in participants],
    })
    hub.mark_dirty()

    working_history = [*history, user_msg, system_line]
    total_usd = 0.0
    all_rag_hits: list[str] = []
    compact_any = False
    tool_runs_all: list[dict[str, Any]] = []
    final_replies: list[dict[str, Any]] = []

    for participant in participants:
        blocked = None
        memory_context = ""
        memory_hits: list[dict[str, Any]] = []
        compact_enqueued = False
        async with session_scope() as s:
            repo = Repo(s)
            dept = await repo.get_department(participant["id"]) or participant
            dept = await update_agent_state(repo, dept, "thinking", mood_delta=-0.01)
            turn_dept = _with_turn_speed(_with_turn_thinking_effort(dept, input.thinking_effort), input.speed)
            reply = {
                "id": uid("msg"),
                "threadId": thread_id,
                "role": _assistant_role(dept),
                "authorName": dept["agentName"],
                "text": "",
                "ts": now_ms(),
                "pending": True,
                **agent_message_metadata(dept, war_room_id=war_room_id),
            }
            await repo.add_message(reply)
            estimated_usd = _chat_cost_estimate(turn_dept)
            blocked = await _budget_block_reason(repo, turn_dept, estimated_usd)
            if not blocked:
                memory_context, memory_hits = await _retrieval_context(repo, dept, input.text)
                tool_memory_context = await recent_tool_run_context(repo, dept, thread_id)
                if tool_memory_context:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + tool_memory_context
                context_tokens, context_token_source = await _context_tokens_for_turn(
                    turn_dept,
                    working_history,
                    user_msg,
                    memory_context=memory_context,
                    departments=departments,
                    system_extra=war_room_context(war_room, participants),
                )
                compact_enqueued = await _maybe_enqueue_context_compaction(
                    repo,
                    thread_id=thread_id,
                    dept=dept,
                    message_count=len(working_history) + 2,
                    estimated_context_tokens=context_tokens,
                    context_token_source=context_token_source,
                )
        hub.pulse({"kind": "msg_start", "threadId": thread_id, "msgId": reply["id"], "message": reply})

        if blocked:
            result = _partial_chat_result(
                turn_dept,
                f"ยังไม่เรียกโมเดลเพราะ runtime dependency: {blocked}",
                stop_reason="blocked",
            )
        else:
            try:
                result = await _complete_with_provider(
                    turn_dept,
                    working_history,
                    user_msg,
                    memory_context,
                    departments=departments,
                    system_extra=war_room_context(war_room, participants),
                )
            except Exception as exc:
                error_type, error_detail = provider_exception_detail(exc)
                result = _partial_chat_result(
                    turn_dept,
                    "",
                    stop_reason="error",
                    error_type=error_type,
                    error_detail=error_detail,
                )

        result.meta["ragHitIds"] = [hit["id"] for hit in memory_hits]
        result.meta["compactEnqueued"] = compact_enqueued
        reply = {
            **reply,
            "text": result.text,
            "pending": False,
            "reasoning": result.reasoning or None,
            "reasoningSummary": result.reasoning_summary,
            "reasoningStatus": result.reasoning_status,
            "reasoningRedacted": bool(result.meta.get("redactedThinking")),
            "thinkingTokens": result.thinking_tokens,
            "generationMs": result.generation_ms,
        }
        runtime_dependency = bool(result.meta.get("runtimeDependency"))
        if runtime_dependency:
            reply["status"] = "blocked"
            reply["error"] = _message_error("runtime_dependency", _runtime_dependency_detail(result))
        elif result.stop_reason == "error":
            reply["status"] = "failed"
            reply["error"] = _message_error(
                "provider_error",
                str(result.meta.get("streamErrorDetail") or result.meta.get("streamErrorType") or "provider error"),
            )
        if result.meta.get("toolRuns"):
            reply["toolRuns"] = result.meta["toolRuns"]
            reply = _attach_tool_artifacts_to_message(reply, result.meta["toolRuns"])
        runtime_meta = _message_runtime_payload(result, turn_dept)
        if runtime_meta:
            reply["runtime"] = runtime_meta

        usage = _message_usage_payload(
            result,
            turn_dept,
            rag_hits=result.meta.get("ragHitIds", []),
            compact_enqueued=bool(result.meta.get("compactEnqueued")),
        )
        reply = ensure_rendering_metadata(
            reply,
            usage=usage,
            citations=citation_chips(memory_hits, department_id=dept["id"]),
            notices=[*(["runtime_dependency"] if runtime_dependency else []), *(["budget_guardrail"] if blocked else [])],
            severity="warn" if blocked or runtime_dependency else None,
        )

        async with session_scope() as s:
            repo = Repo(s)
            reply, summary = await _apply_thread_cost(repo, thread_id, reply)
            usage["threadUsd"] = summary["totalUsd"]
            await repo.update_message(reply)
            await update_agent_state(repo, dept, "idle", mood_delta=0.01)
            if not result.meta.get("cancelled") and not blocked and not runtime_dependency:
                await repo.add_cost(
                    uid("cost"),
                    now_ms(),
                    dept["id"],
                    "chat",
                    result.usd,
                    detail=f"war:{war_room_id}:{result.provider_id}:{result.model}:{turn_dept.get('thinkingEffort', 'high')}:{result.speed}",
                    provider_id=result.provider_id,
                    model=result.model,
                    speed=result.speed,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                )
            await _record_budget_exhaustion(repo, dept, now_ms())
            if memory_hits:
                rag_activity = _activity(
                    f"ดึง RAG {len(memory_hits)} รายการเข้าบริบทของ{dept['agentName']}",
                    type_="system",
                    department_id=dept["id"],
                    severity="info",
                )
                await repo.add_activity(rag_activity)
                response_activity.append(rag_activity)
            activity = _activity(
                f"{dept['agentName']} ตอบใน war room “{war_room.get('title', war_room_id)}”",
                type_="message",
                department_id=dept["id"],
                severity="good" if not blocked else "warn",
            )
            await repo.add_activity(activity)
            response_activity.append(activity)
            tool_messages, tool_activities = await _tool_activity_lines(
                repo,
                thread_id,
                dept,
                result.meta.get("toolRuns", []),
                war_room_id=war_room_id,
            )
            response_messages.extend(tool_messages)
            response_activity.extend(tool_activities)

        hub.pulse({
            "kind": "msg_done",
            "threadId": thread_id,
            "msgId": reply["id"],
            "text": reply["text"],
            "usage": usage,
        })
        hub.pulse({"kind": "spend", "departmentId": dept["id"], "threadId": thread_id})
        total_usd += result.usd
        all_rag_hits.extend(result.meta.get("ragHitIds", []))
        compact_any = compact_any or bool(result.meta.get("compactEnqueued"))
        tool_runs_all.extend(result.meta.get("toolRuns", []))
        final_replies.append(reply)
        response_messages.append(reply)
        working_history = [*working_history, reply]

    async with session_scope() as s:
        summary = thread_cost_summary(thread_id, await Repo(s).thread_messages(thread_id, limit=1000))
    usage = {
        "usd": round(total_usd, 6),
        "ragHits": all_rag_hits,
        "compactEnqueued": compact_any,
        "toolRuns": tool_runs_all,
        "threadUsd": summary["totalUsd"],
    }
    hub.mark_dirty()
    return _chat_response(
        final_replies[0],
        usage=usage,
        messages=response_messages,
        activity=response_activity,
        mentions=mentions,
        estimate=estimate,
        draft_cleared=draft_cleared,
    )


async def _send_meeting_message(
    thread_id: str,
    input: SendMessageInput,
    request: Request,
    user_msg: dict[str, Any],
) -> dict[str, Any]:
    del request
    settings = get_settings()
    meeting_id = meeting_id_from_thread(thread_id)
    if not meeting_id:
        raise HTTPException(status_code=400, detail="invalid meeting thread")

    async with session_scope() as s:
        repo = Repo(s)
        meeting = await repo.get_entity("meeting", meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="meeting not found")
        meeting = _normalize_meeting(meeting)
        departments = await repo.list_departments()
        participants = meeting_participants(meeting, departments)
        if not participants:
            raise HTTPException(status_code=400, detail="meeting has no valid participants")
        attachments = await _normalize_chat_attachments(repo, input)
        if not str(input.text or "").strip() and not attachments:
            raise HTTPException(status_code=400, detail="message text or attachments are required")
        estimate = estimate_input(input.text, attachments)
        if input_character_limit_exceeded(estimate):
            raise HTTPException(status_code=413, detail=input_character_limit_detail())
        mentions = resolve_department_mentions(input.text, departments)
        user_msg = {
            **user_msg,
            "attachments": attachments,
            "mentions": mentions,
            "status": "sent",
            "input": _input_metadata(estimate, status="sent"),
            "meetingId": meeting_id,
        }
        await _attach_message_refs(repo, thread_id, user_msg, input)
        draft_cleared = await _delete_thread_draft(repo, thread_id)
        history = await _thread_messages_for_live_prompt(repo, thread_id)
        await repo.add_message(user_msg)
        activity = _activity(
            f"Meeting “{meeting.get('title', meeting_id)}” รับวาระจากคุณ",
            type_="message",
            severity="good",
        )
        await repo.add_activity(activity)
        system_line = system_chat_message(
            thread_id,
            (
                f"Meeting “{meeting.get('title', meeting_id)}” เปิดวงตอบร่วมกับ "
                f"{', '.join(str(dept.get('agentName') or dept.get('name')) for dept in participants)}"
            ),
            activity=activity,
            flow=meeting_flow(meeting, participants),
            meeting_id=meeting_id,
            severity="good",
        )
        await repo.add_message(system_line)
        response_messages: list[dict[str, Any]] = [system_line]
        response_activity: list[dict[str, Any]] = [activity]

        if settings.engine_enabled:
            pending_replies: list[dict[str, Any]] = []
            for participant in participants:
                active = await update_agent_state(repo, participant, "thinking", mood_delta=-0.01)
                reply = {
                    "id": uid("msg"),
                    "threadId": thread_id,
                    "role": _assistant_role(active),
                    "authorName": active["agentName"],
                    "text": "กำลังคิดและทำงานต่อใน meeting...",
                    "ts": now_ms(),
                    "pending": True,
                    **agent_message_metadata(active, meeting_id=meeting_id),
                }
                await repo.add_message(reply)
                pending_replies.append(reply)
                await repo.enqueue(
                    uid("job"),
                    "chat_reply",
                    {
                        "threadId": thread_id,
                        "departmentId": active["id"],
                        "userMessageId": user_msg["id"],
                        "replyMessageId": reply["id"],
                        "text": input.text,
                        "userTs": user_msg["ts"],
                        "replyTs": reply["ts"],
                        "thinkingEffort": input.thinking_effort,
                        "speed": input.speed,
                        "meetingId": meeting_id,
                    },
                    now_ms(),
                    priority=1,
                )
            queue_activity = _activity(
                f"คิว multi-agent meeting {len(pending_replies)} คนสำหรับ “{meeting.get('title', meeting_id)}”",
                type_="message",
                severity="info",
            )
            await repo.add_activity(queue_activity)
            response_activity.append(queue_activity)
            response_messages.extend(pending_replies)
            summary = thread_cost_summary(thread_id, [*history, user_msg, system_line, *pending_replies])
            usage = {
                "usd": 0.0,
                "ragHits": [],
                "compactEnqueued": False,
                "toolRuns": [],
                "thinkingEffort": input.thinking_effort,
                "speed": input.speed,
                "threadUsd": summary["totalUsd"],
            }
            hub.pulse({
                "kind": "meeting",
                "threadId": thread_id,
                "meetingId": meeting_id,
                "departmentId": pending_replies[0].get("departmentId"),
                "participants": [dept.get("id") for dept in participants],
            })
            hub.mark_dirty()
            return _chat_response(
                pending_replies[0],
                usage=usage,
                messages=response_messages,
                activity=response_activity,
                mentions=mentions,
                estimate=estimate,
                draft_cleared=draft_cleared,
            )

        fallback = await _add_chat_system_line(
            repo,
            thread_id,
            "Meeting runtime is paused; message was recorded but participant replies were not queued.",
            flow=meeting_flow(meeting, participants),
            meeting_id=meeting_id,
            severity="warn",
        )
        hub.mark_dirty()
        return _chat_response(
            fallback,
            messages=[*response_messages, fallback],
            activity=response_activity,
            mentions=mentions,
            estimate=estimate,
            draft_cleared=draft_cleared,
        )


def _copy_message_for_branch(msg: dict[str, Any], branch_thread_id: str, source_thread_id: str) -> dict[str, Any]:
    copied = {
        **msg,
        "id": uid("msg"),
        "threadId": branch_thread_id,
        "branchFromThreadId": source_thread_id,
        "branchCopyOfMessageId": msg["id"],
    }
    copied.pop("pending", None)
    return copied


def _branch_thread_id(source_thread_id: str, branch_id: str | None = None) -> str:
    raw = branch_id or uid("branch")
    suffix = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})[:48] or uid("branch")
    return f"{source_thread_id}:branch:{suffix}"


def _messages_before(messages: list[dict[str, Any]], message_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("id") == message_id:
            break
        out.append(msg)
    return out


def _messages_through(messages: list[dict[str, Any]], message_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        out.append(msg)
        if msg.get("id") == message_id:
            break
    return out


def _latest_assistant_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            msg
            for msg in reversed(messages)
            if msg.get("role") in {"agent", "executive", "system"} and not msg.get("pending")
        ),
        None,
    )


def _previous_user_message(messages: list[dict[str, Any]], before_message_id: str) -> dict[str, Any] | None:
    prior = _messages_before(messages, before_message_id)
    return next((msg for msg in reversed(prior) if msg.get("role") == "user"), None)


def _message_turn_department_id(message: dict[str, Any] | None, thread_id: str) -> str:
    if not message:
        return dept_id_from_thread(thread_id)
    if message.get("role") in {"agent", "executive"} and message.get("departmentId"):
        return str(message.get("departmentId"))
    metadata = message.get("input") if isinstance(message.get("input"), dict) else {}
    routed = metadata.get("routedDepartmentId")
    if routed:
        return str(routed)
    if message.get("departmentId"):
        return str(message.get("departmentId"))
    return dept_id_from_thread(thread_id)


def _message_snippet(text: str, query: str, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    idx = clean.lower().find(query.lower())
    if idx < 0:
        return clean[: limit - 3].rstrip() + "..."
    start = max(0, idx - limit // 3)
    end = min(len(clean), start + limit)
    prefix = "..." if start else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix


def _thread_export_markdown(thread_id: str, messages: list[dict[str, Any]], exported_at: int) -> str:
    lines = [
        f"# ATRIUM Conversation Export",
        "",
        f"- Thread: `{thread_id}`",
        f"- Exported at: {exported_at}",
        f"- Messages: {len(messages)}",
        "",
    ]
    for msg in messages:
        author = msg.get("authorName") or msg.get("role") or "unknown"
        ts = msg.get("ts")
        lines.extend([
            f"## {author} ({msg.get('role', 'message')})",
            "",
            f"`{ts}`",
            "",
            str(msg.get("text") or "").strip(),
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


async def _attach_message_refs(repo: Repo, thread_id: str, msg: dict[str, Any], input: SendMessageInput) -> None:
    parent_id = input.parent_message_id
    quote_id = input.quote_message_id
    if parent_id:
        parent = await repo.get_message(parent_id, thread_id=thread_id)
        if not parent:
            raise HTTPException(status_code=404, detail="parent message not found")
        msg["parentMessageId"] = parent_id
        msg["replyToMessageId"] = parent_id
    if quote_id:
        quote = await repo.get_message(quote_id, thread_id=thread_id)
        if not quote:
            raise HTTPException(status_code=404, detail="quoted message not found")
        msg["quoteMessageId"] = quote_id
        msg["quoteText"] = _message_snippet(str(quote.get("text") or ""), "", limit=260)
        msg["quoteAuthorName"] = quote.get("authorName")
        msg.setdefault("parentMessageId", quote_id)
        msg.setdefault("replyToMessageId", quote_id)


async def _complete_chat_turn(
    *,
    thread_id: str,
    user_msg: dict[str, Any],
    history: list[dict[str, Any]],
    dept: dict[str, Any],
    request: Request,
    reply_overrides: dict[str, Any] | None = None,
    thinking_effort: ThinkingEffort | None = None,
    speed: ModelSpeed | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    turn_dept = _with_turn_speed(_with_turn_thinking_effort({**dept}, thinking_effort), speed)
    reply_overrides = reply_overrides or {}
    text = str(user_msg.get("text") or "")
    departments: list[dict[str, Any]] = []

    async with session_scope() as s:
        repo = Repo(s)
        departments = await repo.list_departments()
        estimated_usd = _chat_cost_estimate(turn_dept)
        blocked = await _budget_block_reason(repo, turn_dept, estimated_usd)
        if blocked:
            usage = {
                "usd": 0.0,
                "ragHits": [],
                "compactEnqueued": False,
                "thinkingEffort": turn_dept.get("thinkingEffort", "high"),
                "speed": turn_dept.get("speed", "standard"),
                "stopReason": "blocked",
            }
            reply = {
                "id": uid("msg"),
                "threadId": thread_id,
                "role": _assistant_role(turn_dept),
                "authorName": turn_dept["agentName"],
                "text": f"ยังไม่เรียกโมเดลเพราะ runtime dependency: {blocked}",
                "ts": now_ms(),
                "status": "blocked",
                **reply_overrides,
            }
            reply = ensure_rendering_metadata(reply, usage=usage, notices=["runtime_dependency"], severity="warn")
            await repo.add_message(reply)
            await repo.add_activity(_activity(
                f"runtime dependency บล็อกแชตของ{turn_dept['agentName']}: {blocked}",
                type_="budget",
                department_id=turn_dept["id"],
                severity="alert",
            ))
            hub.pulse({"kind": "spend", "departmentId": turn_dept["id"]})
            hub.mark_dirty()
            return _chat_response(reply, usage=usage)

        if settings.engine_enabled:
            reply = {
                "id": uid("msg"),
                "threadId": thread_id,
                "role": _assistant_role(turn_dept),
                "authorName": turn_dept["agentName"],
                "text": "กำลังคิดและทำงานต่อในคิวเบื้องหลัง...",
                "ts": now_ms(),
                "pending": True,
                "status": "queued",
                **reply_overrides,
            }
            await repo.add_message(reply)
            job_id = uid("job")
            await repo.enqueue(
                job_id,
                "chat_reply",
                {
                    "threadId": thread_id,
                    "departmentId": turn_dept["id"],
                    "userMessageId": user_msg["id"],
                    "replyMessageId": reply["id"],
                    "text": text,
                    "userTs": user_msg["ts"],
                    "replyTs": reply["ts"],
                    "thinkingEffort": thinking_effort,
                    "speed": speed,
                },
                now_ms(),
                priority=1,
            )
            await repo.add_activity(_activity(
                f"คิวแชตระยะยาวของ{turn_dept['agentName']} ({job_id})",
                type_="message",
                department_id=turn_dept["id"],
            ))
            hub.mark_dirty()
            return _chat_response(
                reply,
                usage={
                    "usd": 0.0,
                    "ragHits": [],
                    "compactEnqueued": False,
                    "thinkingEffort": turn_dept.get("thinkingEffort", "high"),
                    "speed": turn_dept.get("speed", "standard"),
                },
            )

        if (
            is_exec(turn_dept["id"])
            and settings.use_letta_runtime
            and not provider_bypasses_agent_runtime(turn_dept.get("providerId"))
        ):
            from .runtime.executive import ensure_executive_runtime_agent

            await ensure_executive_runtime_agent(repo, turn_dept)
        memory_context, memory_hits = await _retrieval_context(repo, turn_dept, text)
        tool_memory_context = await recent_tool_run_context(repo, turn_dept, thread_id)
        if tool_memory_context:
            memory_context = (memory_context + "\n\n" if memory_context else "") + tool_memory_context
        context_tokens, context_token_source = await _context_tokens_for_turn(
            turn_dept,
            history,
            user_msg,
            memory_context=memory_context,
            departments=departments,
        )
        compact_enqueued = await _maybe_enqueue_context_compaction(
            repo,
            thread_id=thread_id,
            dept=turn_dept,
            message_count=len(history) + 2,
            estimated_context_tokens=context_tokens,
            context_token_source=context_token_source,
        )
        reply = {
            "id": uid("msg"),
            "threadId": thread_id,
            "role": _assistant_role(turn_dept),
            "authorName": turn_dept["agentName"],
            "text": "",
            "ts": now_ms(),
            "pending": True,
            "status": "sending",
            **reply_overrides,
        }
        await repo.add_message(reply)

    hub.mark_dirty()
    cancel_event = chat_streams.start(thread_id, reply["id"])
    sink = ChatMessageStreamSink(
        thread_id=thread_id,
        msg_id=reply["id"],
        message=reply,
        cancel_event=cancel_event,
    )
    await sink.start()

    async def on_stream_event(event) -> None:
        if await request.is_disconnected():
            cancel_event.set()
        await sink.handle(event)

    stopped = False
    stream_error: str | None = None
    try:
        result = await _complete_with_provider(
            turn_dept,
            history,
            user_msg,
            memory_context,
            departments=departments,
            on_stream_event=on_stream_event,
            stream_msg_id=sink.msg_id,
        )
    except ChatStreamCancelled:
        stopped = True
        result = _partial_chat_result(turn_dept, sink.text, stop_reason="cancelled")
    except Exception as exc:
        error_type, stream_error = provider_exception_detail(exc)
        result = _partial_chat_result(
            turn_dept,
            sink.text,
            stop_reason="error",
            error_type=error_type,
            error_detail=stream_error,
        )
    finally:
        chat_streams.finish(thread_id, reply["id"])

    result.meta["ragHitIds"] = [hit["id"] for hit in memory_hits]
    result.meta["compactEnqueued"] = compact_enqueued
    reply = await sink.finish(result=result, stopped=stopped, error=stream_error)
    runtime_dependency = bool(result.meta.get("runtimeDependency"))
    status = "cancelled" if stopped else "failed" if stream_error else "blocked" if runtime_dependency else "sent"
    reply = {**reply, "status": status, **reply_overrides}
    if runtime_dependency:
        reply["error"] = _message_error("runtime_dependency", _runtime_dependency_detail(result))
    runtime_meta = _message_runtime_payload(result, turn_dept)
    if runtime_meta:
        reply["runtime"] = runtime_meta
    citations = citation_chips(memory_hits, department_id=turn_dept["id"])
    usage = _message_usage_payload(
        result,
        turn_dept,
        rag_hits=result.meta.get("ragHitIds", []),
        compact_enqueued=bool(result.meta.get("compactEnqueued")),
    )
    reply = ensure_rendering_metadata(
        reply,
        usage=usage,
        citations=citations,
        notices=["runtime_dependency"] if runtime_dependency else None,
        severity="warn" if runtime_dependency else None,
    )

    tool_messages: list[dict[str, Any]] = []
    tool_activities: list[dict[str, Any]] = []
    async with session_scope() as s:
        repo = Repo(s)
        reply, cost_summary = await _apply_thread_cost(repo, thread_id, reply)
        usage["threadUsd"] = cost_summary["totalUsd"]
        await repo.update_message(reply)
        await update_agent_state(repo, dept, "idle", mood_delta=0.01)
        if not result.meta.get("cancelled") and not runtime_dependency:
            await repo.add_cost(
                uid("cost"),
                now_ms(),
                turn_dept["id"],
                "chat",
                result.usd,
                detail=f"{result.provider_id}:{result.model}:{turn_dept.get('thinkingEffort', 'high')}:{result.speed}",
                provider_id=result.provider_id,
                model=result.model,
                speed=result.speed,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
        await _record_budget_exhaustion(repo, turn_dept, now_ms())
        if memory_hits:
            await repo.add_activity(_activity(
                f"ดึง RAG {len(memory_hits)} รายการเข้าบริบทของ{turn_dept['agentName']}",
                type_="system",
                department_id=turn_dept["id"],
                severity="info",
            ))
        await repo.add_activity(_activity(
            f"{turn_dept['agentName']} ตอบกลับในแชต",
            type_="message",
            department_id=turn_dept["id"],
        ))
    hub.pulse({"kind": "spend", "departmentId": turn_dept["id"]})
    hub.mark_dirty()
    return _chat_response(reply, usage=usage)


@app.get("/health", response_model=HealthResponse)
async def health() -> dict[str, Any]:
    settings = get_settings()
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        departments_count = await repo.count_departments()
        tasks_count = await repo.count_tasks()
        approvals_count = await repo.count_approvals()
        memory = await repo.memory_runtime_health(resolve_embeddings=False)
        jobs = await repo.job_runtime_summary(now, stale_after_ms=_job_stale_after_ms(settings))
    engine = engine_runtime_snapshot(settings)
    provider = provider_health(settings, probe_accounts=False)
    graph = graph_health()
    memory["graphHealth"] = graph
    ok = bool(provider.get("ready")) and not engine.get("stale") and not jobs.get("staleRunning")
    return {
        "ok": ok,
        "provider": provider,
        "engine": engine,
        "jobs": jobs,
        "graph": graph,
        "memory": memory,
        "counts": {
            "departments": departments_count,
            "tasks": tasks_count,
            "approvals": approvals_count,
        },
    }


@app.get("/api/runtime")
async def runtime_status() -> dict[str, Any]:
    from .eval.harness import EvalHarness
    from .memory.embeddings import ollama_reachable, resolve_embedder
    from .runtime import agent_runtime_health
    from .runtime.provisioning import runtime_agent_provisioning_status
    from .tools import HostBridge, build_default_tool_registry, load_custom_tools
    from .org.capabilities import build_capability_registry
    settings = get_settings()
    now = now_ms()
    registry = build_default_tool_registry()
    custom_tool_count = 0
    capability_stats: dict[str, Any] = {}
    async with session_scope() as s:
        repo = Repo(s)
        company = await repo.get_company()
        jobs = await repo.job_runtime_summary(now, stale_after_ms=_job_stale_after_ms(settings))
        memory = await repo.memory_runtime_health()
        departments = await repo.list_departments()
        capability_stats = (await build_capability_registry(repo)).stats()
        custom_tool_count = await load_custom_tools(repo, registry)
    engine = engine_runtime_snapshot(settings)
    provider = await asyncio.to_thread(provider_health, settings, probe_accounts=False)
    agent_runtime = await agent_runtime_health(settings)
    embedder = await resolve_embedder(settings)
    host_bridge = HostBridge(settings).status().to_dict()
    eval_status = EvalHarness(settings).status()
    graph = graph_health()
    memory["graphHealth"] = graph
    v2_ok = True
    if settings.use_letta_runtime and not agent_runtime.get("ok"):
        v2_ok = False
    return {
        "ok": (
            bool(provider.get("ready"))
            and not engine.get("stale")
            and not jobs.get("staleRunning")
            and v2_ok
        ),
        "now": now,
        "running": bool(company.running if company else False),
        "engine": engine,
        "jobs": jobs,
        "provider": provider,
        "wsClients": hub.client_count,
        "v2": {
            "agentBackend": settings.agent_backend,
            "agentRuntime": agent_runtime,
            "agentProvisioning": runtime_agent_provisioning_status(departments),
            "embeddings": {
                "provider": embedder.name,
                "dim": embedder.dim,
                "ollamaReachable": await ollama_reachable(settings),
                "model": settings.ollama_embedding_model,
            },
            "memory": memory,
            "database": _database_fingerprint(settings),
            "objectStoreEnabled": settings.object_store_enabled,
            "backup": _backup_runtime_status(settings, now),
            "toolRegistryCount": len(registry.list()),
            "customToolCount": custom_tool_count,
            "hostBridge": host_bridge,
            "credentialReadiness": _credential_readiness_status(settings),
            "eval": eval_status,
            "learning": {
                "reflectionEnabled": settings.reflection_enabled,
                "consolidationEnabled": settings.consolidation_enabled,
                "consolidationIntervalHours": settings.consolidation_interval_hours,
            },
            "org": {
                "dynamicRoutingEnabled": settings.dynamic_routing_enabled,
                "dynamicRouteMinScore": settings.dynamic_route_min_score,
                "lifecycleEnabled": settings.org_lifecycle_enabled,
                "autospawnEnabled": settings.org_autospawn_enabled,
                "automergeEnabled": settings.org_automerge_enabled,
                "idleRetireHours": settings.org_idle_retire_hours,
                "capabilityRegistry": capability_stats,
            },
            "entitlements": {
                "hostShell": settings.entitlement_host_shell,
                "hostFilesystem": settings.entitlement_host_filesystem,
                "browserAutomation": settings.entitlement_browser_automation,
                "desktopAutomation": settings.entitlement_desktop_automation,
                "externalSend": settings.entitlement_external_send,
                "credentials": settings.entitlement_credentials,
            },
        },
    }


@app.get("/api/provider-auth/status")
async def provider_auth_status(probe: bool = False) -> dict[str, Any]:
    from .provider.chatgpt_oauth import chatgpt_oauth_login_state, chatgpt_oauth_status

    settings = get_settings()
    if probe:
        from .provider.claude_code_provider import claude_code_auth_status

        claude_status = await asyncio.to_thread(claude_code_auth_status, settings.claude_code_command)
    else:
        claude_status = provider_health(settings, probe_accounts=False).get("claudeCodeAuth") or {}
    return {
        "chatgptAccount": {
            **chatgpt_oauth_status(settings),
            "login": chatgpt_oauth_login_state(),
        },
        "claudeCode": claude_status,
    }


@app.get("/api/provider-auth/reference")
async def provider_auth_reference() -> dict[str, Any]:
    from .provider.reference import provider_credential_reference

    return await asyncio.to_thread(provider_credential_reference, get_settings())


@app.get("/api/provider-auth/env")
async def provider_auth_env_settings() -> dict[str, Any]:
    from .provider.env_settings import provider_env_settings

    return provider_env_settings(get_settings())


@app.patch("/api/provider-auth/env")
async def update_provider_auth_env_settings(input: ProviderEnvUpdateInput) -> dict[str, Any]:
    from .provider.env_settings import provider_env_settings, update_provider_env_settings
    from .provider.registry import reset_providers

    try:
        result = update_provider_env_settings([item.dump() for item in input.updates], apply_to_process=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_settings.cache_clear()
    reset_providers()
    hub.mark_dirty()
    settings = get_settings()
    return {
        **provider_env_settings(settings),
        "update": result,
    }


@app.post("/api/provider-auth/chatgpt/start")
async def start_chatgpt_provider_auth(input: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    from .provider.chatgpt_oauth import ChatGPTAccountOAuthError, start_chatgpt_oauth_login_session

    try:
        timeout_s = float((input or {}).get("timeoutS") or 300)
    except (TypeError, ValueError):
        timeout_s = 300.0
    try:
        return start_chatgpt_oauth_login_session(get_settings(), timeout_s=timeout_s)
    except ChatGPTAccountOAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/provider-auth/claude-code/start")
async def start_claude_code_provider_auth() -> dict[str, Any]:
    from .provider.claude_code_provider import start_claude_code_login

    return start_claude_code_login(get_settings().claude_code_command)


@app.post("/api/runtime/checkpoints/{department_id}")
async def create_runtime_agent_checkpoint(
    department_id: str,
    reason: str = Body("manual runtime checkpoint"),
    actor: str = Body("executive"),
) -> dict[str, Any]:
    from .runtime.checkpoints import create_runtime_checkpoint

    async with session_scope() as s:
        repo = Repo(s)
        dept = await repo.get_department(department_id)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        checkpoint = await create_runtime_checkpoint(repo, dept, reason=reason, actor=actor)
    hub.mark_dirty()
    return checkpoint


@app.get("/api/catalog", response_model=CatalogResponse)
async def get_catalog() -> dict[str, Any]:
    return catalog_payload()


def _route_category(path: str) -> str:
    parts = [part for part in path.split("/") if part and not part.startswith("{")]
    if len(parts) >= 2 and parts[0] == "api":
        return parts[1].replace("-", "_")
    return parts[0].replace("-", "_") if parts else "root"


CAPABILITY_CATEGORY_ALIASES: dict[str, set[str]] = {
    "artifact": {"artifacts"},
    "audit": {"audit", "logs"},
    "schedule": {"triggers"},
    "scheduler": {"triggers"},
    "scheduling": {"triggers"},
    "cron": {"triggers"},
    "timer": {"triggers"},
    "trigger": {"triggers"},
    "objective": {"objectives"},
    "standing_goal": {"objectives"},
    "standing_goals": {"objectives"},
    "goal": {"objectives", "projects"},
    "goals": {"objectives", "projects"},
    "autonomy": {"departments", "objectives", "triggers"},
    "autonomous": {"departments", "objectives", "triggers"},
    "pause": {"running", "tools"},
    "resume": {"running", "tools"},
    "running": {"running"},
    "kill_switch": {"running", "tools"},
    "stop_all": {"running", "tools"},
    "state": {"state"},
    "snapshot": {"state"},
    "activity": {"activity"},
    "feed": {"activity"},
    "runtime": {"runtime"},
    "health": {"health", "runtime", "graph"},
    "status": {"runtime", "state"},
    "graph": {"graph"},
    "knowledge_graph": {"graph"},
    "knowledge_debt": {"knowledge_debt"},
    "knowledge_health": {"knowledge_debt", "graph"},
    "memory": {"departments", "knowledge_debt", "graph"},
    "knowledge": {"departments", "knowledge_debt", "graph"},
    "rag": {"departments", "knowledge_debt"},
    "catalog": {"catalog", "tools"},
    "model": {"catalog", "departments"},
    "models": {"catalog", "departments"},
    "provider": {"catalog", "departments"},
    "providers": {"catalog", "departments"},
    "thinking": {"catalog", "departments"},
    "connector": {"connectors"},
    "connectors": {"connectors"},
    "integration": {"connectors"},
    "integrations": {"connectors"},
    "mcp": {"connectors", "tools"},
    "permission": {"permissions", "policy"},
    "permissions": {"permissions", "policy"},
    "policy": {"policy", "permissions"},
    "owner_mode": {"policy", "permissions", "tools"},
    "cost": {"budget", "cost_report"},
    "costs": {"budget", "cost_report"},
    "finance": {"budget", "cost_report"},
    "finances": {"budget", "cost_report"},
    "spend": {"budget", "cost_report"},
    "spending": {"budget", "cost_report"},
    "money": {"budget", "cost_report"},
    "log": {"logs", "audit"},
    "logging": {"logs", "audit"},
    "audit_log": {"audit", "logs"},
    "audit_logs": {"audit", "logs"},
    "api_log": {"audit", "logs"},
    "api_logs": {"audit", "logs"},
    "decision": {"decisions"},
    "evidence": {"evidence_packs", "critique_reports"},
    "evidence_pack": {"evidence_packs"},
    "critique": {"critique_reports"},
    "critique_report": {"critique_reports"},
    "quality": {"artifacts", "evidence_packs", "critique_reports"},
    "quality_loop": {"artifacts", "evidence_packs", "critique_reports"},
    "meeting": {"meetings"},
    "notification": {"notifications", "notification_preferences"},
    "notification_preference": {"notification_preferences"},
    "notification_pref": {"notification_preferences"},
    "notification_prefs": {"notification_preferences"},
    "preference": {"preferences", "owner_profile"},
    "owner": {"owner_profile", "preferences"},
    "owner_preference": {"owner_profile", "preferences"},
    "owner_preferences": {"owner_profile", "preferences"},
    "executive": {"executive"},
    "ceo": {"executive"},
    "org": {"onboarding", "departments"},
    "org_plan": {"onboarding"},
    "org_plans": {"onboarding"},
    "onboard": {"onboarding"},
    "organization": {"onboarding", "departments"},
    "organisation": {"onboarding", "departments"},
    "project": {"projects"},
    "skill": {"skills"},
    "tool": {"tools"},
    "task": {"tasks"},
    "handoff": {"handoffs", "tasks"},
    "handoffs": {"handoffs", "tasks"},
    "thread": {"threads", "messages"},
    "message": {"messages", "threads"},
    "chat_history": {"threads", "messages"},
    "conversation": {"threads", "messages"},
    "team": {"departments"},
    "teams": {"departments"},
    "dept": {"departments"},
    "department": {"departments"},
    "workspace": {"departments", "artifacts"},
    "workspaces": {"departments", "artifacts"},
    "file": {"import", "artifacts", "departments"},
    "import": {"import", "artifacts"},
    "file_import": {"import", "artifacts"},
    "upload": {"import", "artifacts"},
    "attachment": {"import", "artifacts", "threads"},
    "attachments": {"import", "artifacts", "threads"},
    "war_room": {"war_rooms"},
    "warroom": {"war_rooms"},
    "playbook": {"playbooks"},
    "lesson": {"lessons"},
    "approval": {"approvals", "tools"},
    "approvals": {"approvals", "tools"},
}


def _normalize_discovery_token(value: str | None) -> str | None:
    if not value:
        return None
    token = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return token or None


def _capability_category_filter(category: str | None) -> set[str] | None:
    token = _normalize_discovery_token(category)
    if not token:
        return None
    aliases = {token, *CAPABILITY_CATEGORY_ALIASES.get(token, set())}
    if token.endswith("ies") and len(token) > 3:
        aliases.add(f"{token[:-3]}y")
    if token.endswith("s") and len(token) > 1:
        aliases.add(token[:-1])
    else:
        aliases.add(f"{token}s")
        if token.endswith("y") and len(token) > 1:
            aliases.add(f"{token[:-1]}ies")
    for alias in list(aliases):
        aliases.update(CAPABILITY_CATEGORY_ALIASES.get(alias, set()))
    return aliases


def _operation_json_schema(operation: dict[str, Any], status: str = "200") -> dict[str, Any] | None:
    content = (
        (operation.get("responses") or {})
        .get(status, {})
        .get("content", {})
        .get("application/json", {})
    )
    schema = content.get("schema")
    return schema if isinstance(schema, dict) else None


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    content = (
        (operation.get("requestBody") or {})
        .get("content", {})
        .get("application/json", {})
    )
    schema = content.get("schema")
    return schema if isinstance(schema, dict) else None


def _capability_for_route(route: APIRoute, method: str, operation: dict[str, Any] | None) -> dict[str, Any]:
    operation = operation or {}
    return ApiCapability(
        method=method,
        path=route.path,
        name=route.name,
        category=_route_category(route.path),
        mutates=method in AUDITED_API_METHODS,
        summary=operation.get("summary") or route.summary or route.description,
        operation_id=operation.get("operationId"),
        parameters=[
            item
            for item in operation.get("parameters", [])
            if isinstance(item, dict) and item.get("in") in {"path", "query"}
        ],
        request_body={
            "required": bool((operation.get("requestBody") or {}).get("required")),
            "schema": _request_body_schema(operation),
        } if operation.get("requestBody") else None,
        response_schema=_operation_json_schema(operation),
    ).dump()


def _api_capabilities(
    *,
    category: str | None = None,
    method: str | None = None,
    path: str | None = None,
    mutates: bool | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    openapi = app.openapi()
    paths = openapi.get("paths") or {}
    method_filter = method.upper() if method else None
    category_filter = _capability_category_filter(category)
    path_filter = path.rstrip("/") if path else None
    capabilities: list[dict[str, Any]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not (route.path.startswith("/api/") or route.path == "/health"):
            continue
        if not route.include_in_schema:
            continue
        methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        for method in methods:
            if method_filter and method != method_filter:
                continue
            if path_filter and route.path.rstrip("/") != path_filter and not route.path.startswith(f"{path_filter}/"):
                continue
            route_category = _route_category(route.path)
            if category_filter and route_category not in category_filter:
                continue
            method_mutates = method in AUDITED_API_METHODS
            if mutates is not None and method_mutates != mutates:
                continue
            operation = (paths.get(route.path) or {}).get(method.lower())
            capabilities.append(_capability_for_route(route, method, operation))
    capabilities.sort(key=lambda item: (item["category"], item["path"], item["method"]))
    return capabilities[: max(1, min(limit, 500))]


def _schema_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            refs.add(ref.rsplit("/", 1)[-1])
        for child in value.values():
            refs.update(_schema_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_schema_refs(child))
    return refs


def _schemas_for_capabilities(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    components = (app.openapi().get("components") or {}).get("schemas") or {}
    queue: list[str] = []
    seen: set[str] = set()
    for capability in capabilities:
        for name in _schema_refs(capability):
            if name not in seen:
                queue.append(name)
    schemas: dict[str, Any] = {}
    while queue:
        name = queue.pop(0)
        if name in seen or name not in components:
            continue
        seen.add(name)
        schema = components[name]
        schemas[name] = schema
        for nested in _schema_refs(schema):
            if nested not in seen:
                queue.append(nested)
    return schemas


@app.get("/api/capabilities", response_model=ApiCapabilityResponse)
async def get_api_capabilities(
    category: str | None = Query(None),
    method: str | None = Query(None),
    path: str | None = Query(None),
    mutates: bool | None = Query(None),
    include_schemas: bool = Query(False, alias="includeSchemas"),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    endpoints = _api_capabilities(
        category=category,
        method=method,
        path=path,
        mutates=mutates,
        limit=limit,
    )
    return {
        "endpoints": endpoints,
        "guidance": [
            "Chat agents can call these endpoints with call_atrium_api.",
            "Filter by category, path, or method before asking for schemas to keep tool results small.",
            "Use includeSchemas=true to receive the request/response component schemas for the filtered endpoints.",
            "Use GET endpoints to inspect current state before mutating.",
            "Every POST/PATCH/PUT/DELETE /api request is recorded as append-only audit activity.",
        ],
        "schemas": _schemas_for_capabilities(endpoints) if include_schemas else {},
    }


@app.get("/api/executive", response_model=Executive)
async def get_executive() -> dict[str, Any]:
    async with session_scope() as s:
        dept = await Repo(s).get_department(EXEC_ID)
    if not dept:
        raise HTTPException(status_code=404, detail="executive not found")
    return _executive_from_department(dept)


@app.get("/api/policy", response_model=PermissionPolicy)
async def get_policy() -> dict[str, Any]:
    async with session_scope() as s:
        return await Repo(s).get_permission_policy()


@app.get("/api/permissions/mode", response_model=PermissionPolicy)
async def get_permission_mode() -> dict[str, Any]:
    """Owner Mode compatibility endpoint for the global permission policy."""
    return await get_policy()


@app.patch("/api/policy", response_model=PermissionPolicy)
async def update_policy(input: PermissionPolicyInput) -> dict[str, Any]:
    requested_mode = _canonical_permission_mode(input.mode)
    now = now_ms()
    updated_by = input.updated_by or "owner"
    async with session_scope() as s:
        repo = Repo(s)
        update_fields = input.model_dump(by_alias=True, exclude_none=True, exclude={"mode", "updatedBy", "updated_by"})
        policy = await repo.set_permission_policy(requested_mode, updated_by, **update_fields)
        mode = policy["mode"]
        note = (
            f" (คำขอ {requested_mode} ถูกบังคับเป็น {mode})"
            if requested_mode != mode else ""
        )
        await repo.add_activity(_activity(
            f"ยืนยันโหมดสิทธิ์เป็น {mode}{note}",
            type_="approval",
            severity="warn",
            ts=now,
        ))
        await s.commit()
        if _permission_mode_is_full(mode):
            await _approve_pending_approvals_for_full_auto(repo, approved_by=updated_by, now=now)
    hub.mark_dirty()
    return policy


@app.patch("/api/permissions/mode", response_model=PermissionPolicy)
async def update_permission_mode(input: PermissionPolicyInput) -> dict[str, Any]:
    """Owner Mode compatibility endpoint for changing the global policy mode."""
    return await update_policy(input)


@app.get("/api/graph/health", response_model=GraphHealthResponse)
async def get_graph_health() -> dict[str, Any]:
    return graph_health()


@app.get("/api/state", response_model=CompanyState)
async def get_state() -> JSONResponse:
    return JSONResponse(content=await _snapshot())


@app.get("/api/departments", response_model=list[Department])
async def list_departments(
    q: str | None = Query(default=None),
    state: str | None = Query(default=None),
    autonomy: bool | None = Query(default=None),
    include_executive: bool = Query(default=True, alias="includeExecutive"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        departments = await Repo(s).list_departments()
    if not include_executive:
        departments = [dept for dept in departments if not is_exec(str(dept.get("id") or ""))]
    if state:
        wanted_state = state.strip().lower()
        departments = [dept for dept in departments if str(dept.get("state") or "").lower() == wanted_state]
    if autonomy is not None:
        departments = [dept for dept in departments if bool(dept.get("autonomy")) == autonomy]
    if q:
        needle = q.strip().lower()
        departments = [
            dept for dept in departments
            if needle in " ".join(str(dept.get(key) or "") for key in ("id", "name", "role", "agentName", "charter")).lower()
        ]
    return departments[:limit]


@app.get("/api/departments/{dept_id}", response_model=Department)
async def get_department(dept_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        dept = await Repo(s).get_department(dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="department not found")
    return dept


@app.get("/api/activity", response_model=list[ActivityEvent])
async def get_activity(
    since: int | None = Query(default=None),
    after: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Replay durable activity events after a REST snapshot or reconnect."""
    async with session_scope() as s:
        return await Repo(s).activity_since(since=since, after_id=after, limit=limit)


@app.get("/api/threads/{thread_id}/messages", response_model=list[ChatMessage])
async def get_thread_messages(
    thread_id: str,
    after: int | None = Query(default=None),
    after_id: str | None = Query(default=None, alias="afterId"),
    before: int | None = Query(default=None),
    before_id: str | None = Query(default=None, alias="beforeId"),
    limit: int = Query(default=200, ge=1, le=1000),
    all_: bool = Query(default=False, alias="all"),
) -> list[dict[str, Any]]:
    """Replay durable chat messages so reconnecting clients can fill transcript gaps."""
    if before is not None and after is not None:
        raise HTTPException(status_code=400, detail="use either before or after, not both")
    async with session_scope() as s:
        repo = Repo(s)
        if all_ and after is None and before is None:
            return await repo.all_thread_messages(thread_id)
        if before is not None:
            return await repo.thread_messages_before(thread_id, before_ts=before, before_id=before_id, limit=limit)
        if after is None:
            return await repo.thread_messages(thread_id, limit=limit)
        return await repo.thread_messages_after(thread_id, after_ts=after, after_id=after_id, limit=limit)


@app.get("/api/threads/{thread_id}/cost", response_model=ThreadCostSummary)
async def get_thread_cost(thread_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        messages = await Repo(s).thread_messages(thread_id, limit=1000)
    return thread_cost_summary(thread_id, messages)


@app.get("/api/chat/input/commands", response_model=list[SlashCommandSpec])
async def list_chat_input_commands(q: str | None = Query(default=None)) -> list[dict[str, Any]]:
    return _command_specs_payload(q)


@app.get("/api/chat/tool-surface")
async def get_chat_tool_surface(
    department_id: str | None = Query(None, alias="departmentId"),
    text: str = Query("status"),
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        departments = await repo.list_departments()
        dept = await repo.get_department(department_id or EXEC_ID)
    if not dept:
        raise HTTPException(status_code=404, detail="department not found")
    enabled = should_enable_chat_tools(text, dept)
    summary = chat_tool_surface_summary(departments, dept) if enabled else {
        "enabled": False,
        "departmentId": dept["id"],
        "toolCount": 0,
        "tools": [],
        "hasCallAtriumApi": False,
        "hasRunOwnerTool": False,
        "ownerToolCount": 0,
        "ownerTools": [],
    }
    summary["textWouldEnableTools"] = enabled
    return summary


@app.get("/api/threads/{thread_id}/mentions", response_model=list[MessageMentionTarget])
async def autocomplete_thread_mentions(
    thread_id: str,
    q: str = Query(default=""),
    limit: int = Query(default=8, ge=1, le=20),
) -> list[dict[str, Any]]:
    del thread_id
    async with session_scope() as s:
        departments = await Repo(s).list_departments()
    return autocomplete_mentions(q, departments, limit=limit)


@app.get("/api/threads/{thread_id}/prompt-starters", response_model=list[PromptStarter])
async def get_thread_prompt_starters(thread_id: str) -> list[dict[str, Any]]:
    async with session_scope() as s:
        repo = Repo(s)
        departments = await repo.list_departments()
        dept = await repo.get_department(dept_id_from_thread(thread_id)) or await repo.get_department(EXEC_ID)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
    return prompt_starters_for_thread(thread_id, dept, departments)


@app.post("/api/threads/{thread_id}/input/estimate", response_model=InputEstimate)
async def estimate_thread_input(thread_id: str, input: InputEstimateInput) -> dict[str, Any]:
    del thread_id
    async with session_scope() as s:
        attachments = await _normalize_chat_attachments(Repo(s), input)
    return estimate_input(input.text, attachments)


@app.get("/api/threads/{thread_id}/draft", response_model=ThreadDraft)
async def get_thread_draft(thread_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        draft = await Repo(s).get_entity("thread_draft", thread_id)
    return draft or _draft_payload(thread_id, "", [])


@app.put("/api/threads/{thread_id}/draft", response_model=ThreadDraft)
async def put_thread_draft(thread_id: str, input: ThreadDraftInput) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        attachments = await _normalize_chat_attachments(repo, input)
        draft = _draft_payload(thread_id, input.text, attachments)
        await repo.put_entity("thread_draft", draft, status="active", ts=draft["updatedAt"])
    hub.pulse({"kind": "input_draft", "threadId": thread_id, "updatedAt": draft["updatedAt"]})
    return draft


@app.delete("/api/threads/{thread_id}/draft", response_model=OkResponse)
async def delete_thread_draft(thread_id: str) -> dict[str, bool]:
    async with session_scope() as s:
        await _delete_thread_draft(Repo(s), thread_id)
    hub.pulse({"kind": "input_draft", "threadId": thread_id, "cleared": True})
    return {"ok": True}


@app.get("/api/threads/{thread_id}/latest-user-message", response_model=ChatMessage)
async def get_latest_user_message(thread_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        message = await Repo(s).latest_user_message(thread_id)
    if not message:
        raise HTTPException(status_code=404, detail="no user message in this thread")
    return message


@app.get("/api/tools/runs", response_model=list[ToolRun])
async def list_tool_runs(
    dept_id: str | None = Query(default=None, alias="deptId"),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        runs = await Repo(s).list_entities("tool_run", dept=dept_id, status=status, limit=limit)
    return [_public_tool_run(run) for run in runs]


@app.get("/api/tools/catalog", response_model=list[ToolCatalogItem])
async def get_tool_catalog(
    tool: str | None = Query(None),
    executor: str | None = Query(None),
    risk_class: str | None = Query(None, alias="riskClass"),
    mutates: bool | None = Query(None),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        repo = Repo(s)
        policy = await repo.get_permission_policy()
        custom_tools = await repo.list_entities("custom_tool", status="active", limit=500)
    rows = [item for item in policy.get("toolCatalog", []) if isinstance(item, dict)]
    for custom_tool in custom_tools:
        catalog_row = custom_tool.get("catalogRow")
        if isinstance(catalog_row, dict):
            rows.append(catalog_row)
    if tool:
        wanted = str(tool).strip().lower()
        rows = [item for item in rows if str(item.get("tool") or "").lower() == wanted]
    if executor:
        wanted = str(executor).strip().lower()
        rows = [item for item in rows if str(item.get("executor") or "").lower() == wanted]
    if risk_class:
        wanted = str(risk_class).strip().lower()
        rows = [item for item in rows if str(item.get("riskClass") or "").lower() == wanted]
    if mutates is not None:
        rows = [item for item in rows if bool(item.get("mutatesState")) == mutates]
    return rows


@app.get("/api/tools/route")
async def route_tool_execution(tool: str = Query(...), args: str | None = Query(None)) -> dict[str, Any]:
    canonical = _canonical_tool(tool)
    route_args: dict[str, Any] | None = None
    if args:
        try:
            parsed_args = json.loads(args)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="args must be a JSON object") from exc
        if not isinstance(parsed_args, dict):
            raise HTTPException(status_code=400, detail="args must be a JSON object")
        route_args = parsed_args
    async with session_scope() as s:
        repo = Repo(s)
        catalog_item = _tool_catalog_item(canonical)
        custom_catalog = None
        if not catalog_item:
            from .tools.foundry import custom_tool_catalog_row

            custom_catalog = await custom_tool_catalog_row(repo, canonical)
        if not catalog_item and not custom_catalog:
            route = _build_tool_route(canonical, args=route_args)
            return {**route, "supported": False, "catalog": None}
    route = _build_tool_route(canonical, custom_catalog, args=route_args)
    return {
        **route,
        "supported": True,
        "catalog": custom_catalog or catalog_item,
    }


@app.get("/api/connectors", response_model=list[Connector])
async def list_connectors() -> list[dict[str, Any]]:
    return _connector_catalog()


@app.get("/api/host-bridge/parity", response_model=HostBridgeParityStatusResponse)
async def get_host_bridge_parity() -> dict[str, Any]:
    return _host_bridge_parity_status_payload()


@app.get("/api/tools/runs/{run_id}", response_model=ToolRun)
async def get_tool_run(run_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        run = await Repo(s).get_entity("tool_run", run_id)
    if not run:
        raise HTTPException(status_code=404, detail="tool run not found")
    return _public_tool_run(run)


@app.post("/api/tools/runs/{run_id}/cancel", response_model=ToolRun)
async def cancel_tool_run(run_id: str) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        run = await repo.get_entity("tool_run", run_id)
        if not run:
            raise HTTPException(status_code=404, detail="tool run not found")
        if run.get("status") in {"completed", "succeeded"}:
            raise HTTPException(status_code=409, detail="completed tool run cannot be cancelled")
        if run.get("status") != "cancelled":
            await _owner_process_tool(repo, {"action": "kill", "runId": run_id}, run.get("departmentId") or EXEC_ID)
            run = await repo.get_entity("tool_run", run_id) or run
            if run.get("status") != "cancelled":
                run["status"] = "cancelled"
                run["error"] = "cancelled by owner"
                run["completedAt"] = now
                await _save_tool_run(repo, run)
            approval_id = run.get("approvalId")
            if approval_id:
                approval = await repo.get_approval(approval_id)
                if approval:
                    if approval.get("status") == "pending":
                        approval["status"] = "rejected"
                        action = approval.get("action")
                        if action:
                            action["executedAt"] = now
                        await repo.save_approval(approval)
                    await _upsert_approval_chat_message(repo, approval, run=run)
            await repo.add_activity(_activity(
                f"cancelled tool {run['tool']}",
                type_="approval",
                department_id=run.get("departmentId"),
                severity="warn",
            ))
    hub.mark_dirty()
    return _public_tool_run(run)


@app.get("/api/tools/approvals", response_model=list[Approval])
async def list_tool_approvals(
    status: ApprovalStatus | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        query_limit = limit if status is not None else min(1000, max(limit * 5, limit))
        approvals = await Repo(s).list_approvals(status=status, limit=query_limit)
    out = [
        approval
        for approval in approvals
        if approval.get("kind") == "external_action"
        and (approval.get("action") or {}).get("action") == "run_tool"
    ]
    return out[:limit]


@app.get("/api/approvals", response_model=list[Approval])
async def list_approvals(
    status: ApprovalStatus | None = Query(default=None),
    kind: ApprovalKind | None = Query(default=None),
    department_id: str | None = Query(default=None, alias="departmentId"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        query_limit = limit if kind is None and department_id is None else min(1000, max(limit * 5, limit))
        approvals = await Repo(s).list_approvals(status=status, limit=query_limit)
    out = [
        approval for approval in approvals
        if (kind is None or approval.get("kind") == kind)
        and (department_id is None or approval.get("departmentId") == department_id)
    ]
    return out[:limit]


@app.get("/api/approvals/{approval_id}", response_model=Approval)
async def get_approval(approval_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        approval = await Repo(s).get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


@app.post("/api/tools/run", response_model=ToolRunResponse)
async def run_tool(input: ToolRunInput) -> dict[str, Any]:
    now = now_ms()
    tool = _canonical_tool(input.tool)
    args = dict(input.args or {})
    thread_id = str(input.thread_id or args.get("threadId") or args.get("thread_id") or "").strip() or None
    if thread_id and not (args.get("threadId") or args.get("thread_id")):
        args["threadId"] = thread_id
    run = ToolRun(
        id=uid("tool"),
        tool=tool,
        department_id=input.department_id,
        thread_id=thread_id,
        task_id=input.task_id,
        requested_by=input.requested_by,
        args=args,
        status="queued",
        created_at=now,
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        company = await repo.get_company()
        if not await repo.get_department(input.department_id):
            raise HTTPException(status_code=404, detail="department not found")
        if input.task_id and not await repo.get_task(input.task_id):
            raise HTTPException(status_code=404, detail="task not found")
        from .tools.foundry import custom_tool_catalog_row

        catalog_item = _tool_catalog_item(tool)
        custom_catalog = None if catalog_item else await custom_tool_catalog_row(repo, tool)
        if custom_catalog:
            run["customTool"] = True
            run["customCatalogRow"] = custom_catalog
        elif not catalog_item:
            raise HTTPException(status_code=400, detail=f"unsupported tool: {tool}")
        route = _build_tool_route(tool, custom_catalog, args=args)
        run["executor"] = str((custom_catalog or catalog_item or {}).get("executor") or route.get("executor") or _tool_executor(tool))
        run["riskClass"] = _tool_risk_class(run)
        run["executorRoute"] = {
            **route,
            "catalogRiskClass": route.get("riskClass"),
            "riskClass": run["riskClass"],
            "executor": run["executor"],
            "checkpointBefore": _checkpoint_required_for_run(run),
        }
        policy = await repo.get_permission_policy()
        decision = _tool_policy_decision(
            run,
            require_approval=input.require_approval,
            policy=policy,
            running=bool(company.running if company else True),
        )
        if decision == "auto_approved":
            runtime_block = _tool_runtime_block_reason(run)
            if runtime_block:
                decision = "blocked_by_runtime"
                run["policyReason"] = runtime_block
        run["policyDecision"] = decision
        if decision in {"blocked_by_policy", "blocked_by_runtime"}:
            run["status"] = "blocked"
            run["error"] = run.get("policyReason") or "tool execution is blocked"
            run["completedAt"] = now_ms()
            await _save_tool_run(repo, run)
            await repo.add_activity(_activity(
                f"blocked tool {run['tool']}: {run['error']}",
                type_="approval",
                department_id=run["departmentId"],
                severity="alert",
            ))
            hub.mark_dirty()
            return {"run": _public_tool_run(run), "approval": None, "executed": False}
        if decision == "approval_required":
            approval = await _request_tool_approval(repo, run)
            hub.mark_dirty()
            return {"run": _public_tool_run(run), "approval": approval, "executed": False}
        run = await _execute_tool_run_record(repo, run)
    hub.mark_dirty()
    return {"run": _public_tool_run(run), "approval": None, "executed": True}


@app.get("/api/audit/logs", response_model=list[AuditLogEntry])
async def get_audit_logs(
    dept_id: str | None = Query(default=None, alias="deptId"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await _collect_audit_logs(Repo(s), dept_id, kind, limit)


@app.post("/api/audit/notes", response_model=AuditLogEntry)
async def create_audit_note(input: CreateAuditNoteInput) -> dict[str, Any]:
    note = {
        "id": uid("aud"),
        "ts": now_ms(),
        "departmentId": input.department_id,
        "body": _clip_text(input.body, 10_000),
        "author": input.author,
        "links": input.links,
        "severity": input.severity,
    }
    async with session_scope() as s:
        repo = Repo(s)
        if input.department_id and not await repo.get_department(input.department_id):
            raise HTTPException(status_code=404, detail="department not found")
        await repo.put_entity(
            "audit_note",
            note,
            dept=input.department_id,
            status=input.severity,
            ts=note["ts"],
        )
        await repo.add_activity(_activity(
            f"เพิ่ม audit note โดย {input.author}",
            type_="system",
            department_id=input.department_id,
            severity=input.severity,
        ))
    hub.mark_dirty()
    return _audit_from_note(note)


@app.get("/api/logs", response_model=list[AuditLogEntry])
async def get_logs(
    dept_id: str | None = Query(default=None, alias="deptId"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Owner Mode compatibility alias for read-only audit log queries."""
    return await get_audit_logs(dept_id=dept_id, kind=kind, limit=limit)


@app.get("/api/audit/logs/export", response_model=AuditLogExportResponse)
@app.get("/api/logs/export", response_model=AuditLogExportResponse)
async def export_audit_logs(
    dept_id: str | None = Query(default=None, alias="deptId"),
    kind: str | None = Query(default=None),
    format: Literal["md", "json", "jsonl"] = Query(default="json"),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    exported_at = now_ms()
    async with session_scope() as s:
        rows = await _collect_audit_logs(Repo(s), dept_id, kind, limit)
    redacted_fields = sorted({field for row in rows for field in (row.get("redactedFields") or [])})
    meta = {
        "exportedAt": exported_at,
        "rowCount": len(rows),
        "deptId": dept_id,
        "kind": kind,
        "redacted": True,
        "redactedFields": redacted_fields,
    }
    if format == "jsonl":
        content = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        if content:
            content += "\n"
        content_type = "application/x-ndjson; charset=utf-8"
    elif format == "md":
        content = _audit_export_markdown(rows, exported_at=exported_at, dept_id=dept_id, kind=kind)
        content_type = "text/markdown; charset=utf-8"
    else:
        content = json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2, sort_keys=True)
        content_type = "application/json"
    scope = "_".join(part for part in [dept_id or "all", kind or "all"] if part)
    return {
        "format": format,
        "contentType": content_type,
        "filename": f"audit_{scope}.{format if format != 'jsonl' else 'jsonl'}",
        "content": content,
        "rowCount": len(rows),
        "exportedAt": exported_at,
        "deptId": dept_id,
        "kind": kind,
        "redacted": True,
        "redactedFields": redacted_fields,
    }


@app.get("/api/logs/{log_id}", response_model=AuditLogEntry)
async def get_log(log_id: str) -> dict[str, Any]:
    logs = await get_audit_logs(dept_id=None, kind=None, limit=1000)
    found = next((log for log in logs if log["id"] == log_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="log not found")
    return found


@app.post("/api/logs/{log_id}/notes", response_model=AuditLogEntry)
async def create_log_note(log_id: str, input: CreateAuditNoteInput) -> dict[str, Any]:
    log = await get_log(log_id)
    links = [*input.links, f"atrium://audit/{log_id}"]
    note_input = CreateAuditNoteInput(
        body=input.body,
        author=input.author,
        department_id=input.department_id or log.get("departmentId"),
        links=links,
        severity=input.severity,
    )
    return await create_audit_note(note_input)


async def _actor_display_name(repo: Repo, actor_id: str) -> str:
    if actor_id == "user":
        return "คุณ"
    dept = await repo.get_department(actor_id)
    if dept:
        return dept.get("agentName") or dept.get("name") or actor_id
    return actor_id


def _find_task_handoff(task: dict[str, Any], handoff_id: str) -> dict[str, Any] | None:
    return next((h for h in task.get("handoffs", []) if h.get("id") == handoff_id), None)


async def _persist_handoff_message(
    repo: Repo,
    handoff: dict[str, Any],
    *,
    from_actor: str,
    act: str,
    text: str,
    now: int,
    task_id: str,
) -> dict[str, Any]:
    message = make_handoff_message(
        handoff,
        from_actor=from_actor,
        act=act,
        text=_clip_text(text, 20_000) or "",
        now=now,
        task_id=task_id,
    )
    await repo.put_entity("handoff_message", message, dept=from_actor, status=handoff["id"], ts=now)
    await repo.add_message(handoff_chat_message(message, await _actor_display_name(repo, from_actor)))
    return message


async def _escalate_handoff_rejects(
    repo: Repo,
    handoff: dict[str, Any],
    *,
    reject_count: int,
    now: int,
) -> None:
    decision = {
        "id": uid("dec"),
        "title": f"Escalate rejected handoff {handoff['id']}",
        "proposedBy": handoff.get("toDept"),
        "approvedBy": "executive",
        "rationale": f"handoff rejected {reject_count} times; executive routing required",
        "alternatives": ["revise request", "assign another department", "cancel handoff"],
        "impact": "prevents autonomous reject loops",
        "linkedTask": handoff.get("sourceTaskId"),
        "linkedArtifacts": [],
        "status": "approved",
        "supersedes": None,
        "ts": now,
    }
    await repo.put_entity("decision", decision, dept=handoff.get("fromDept"), status="approved", ts=now)


async def _apply_handoff_message_to_tasks(
    repo: Repo,
    handoff_id: str,
    message: dict[str, Any],
    *,
    status: str,
    now: int,
) -> None:
    settings = get_settings()
    tasks = await repo.list_active_tasks(limit=1000)
    touched = False
    reject_count = 0
    escalated = False
    for task in tasks:
        updated_handoffs = []
        task_changed = False
        for handoff in task.get("handoffs", []):
            if handoff.get("id") != handoff_id:
                updated_handoffs.append(handoff)
                continue
            current_status = normalize_handoff_status(status)
            reject_count = sum(
                1 for m in [*handoff.get("messages", []), message] if m.get("act") == "reject"
            )
            if message["act"] == "reject" and reject_count >= settings.max_handoff_rejects:
                current_status = "escalated"
                escalated = True
            updated = append_handoff_message(handoff, message, status=current_status)
            updated["lastActionAt"] = now
            if current_status in {"rejected", "escalated", "closed", "cancelled"}:
                updated["closedAt"] = updated.get("closedAt") or now
                updated["closedBy"] = updated.get("closedBy") or message["from"]
            updated_handoffs.append(updated)
            task_changed = True

            if task["id"] == updated.get("sourceTaskId"):
                if message["act"] == "clarify":
                    task["status"] = "waiting"
                    task["waitingOn"] = {"dept": message["from"], "handoffId": handoff_id, "reason": "clarification"}
                elif message["act"] == "reply":
                    task.pop("waitingOn", None)
                    if task.get("status") in {"blocked", "waiting"}:
                        task["status"] = "in_progress"
                elif message["act"] in {"deliver", "return"}:
                    task.pop("waitingOn", None)
                    if task.get("status") in {"blocked", "waiting"}:
                        task["status"] = "review" if message["act"] == "deliver" else "in_progress"
                elif message["act"] == "reject":
                    task["status"] = "blocked" if escalated else "revising"
                    task["waitingOn"] = {
                        "dept": "executive" if escalated else message["from"],
                        "handoffId": handoff_id,
                        "reason": "executive_decision" if escalated else "clarification",
                    }

            if task["id"] == updated.get("targetTaskId"):
                if message["act"] in {"deliver", "return"}:
                    task["status"] = "done"
                    task["progress"] = 1
                    updated["deliverableArtifactIds"] = list(dict.fromkeys([
                        *updated.get("deliverableArtifactIds", []),
                        *[str(item) for item in task.get("deliverables", []) if str(item).strip()],
                    ]))
                    dept = await repo.get_department(task.get("departmentId"))
                    if dept and dept.get("currentTaskId") == task["id"]:
                        dept["state"] = "idle"
                        dept["currentTaskId"] = None
                        await repo.save_department(dept)
                elif message["act"] == "reject":
                    task["status"] = "cancelled"
                    dept = await repo.get_department(task.get("departmentId"))
                    if dept and dept.get("currentTaskId") == task["id"]:
                        dept["state"] = "idle"
                        dept["currentTaskId"] = None
                        await repo.save_department(dept)

        if task_changed:
            task["handoffs"] = updated_handoffs
            task["updatedAt"] = now
            task["log"] = [*task.get("log", []), f"handoff {handoff_id}: {message['act']}"]
            await repo.save_task(task)
            if task.get("status") in {"assigned", "backlog", "revising", "in_progress"} and not task.get("waitingOn"):
                dept = await repo.get_department(str(task.get("departmentId") or ""))
                if dept:
                    await _wake_department_for_assigned_task(
                        repo,
                        dept,
                        task,
                        now,
                        reason="หลัง handoff ตอบกลับ",
                    )
            touched = True
    if not touched:
        raise HTTPException(status_code=404, detail="handoff not found")
    if escalated:
        # Every matching task now carries the escalated handoff status; one decision is enough.
        handoff = next(
            h
            for task in tasks
            for h in task.get("handoffs", [])
            if h.get("id") == handoff_id
        )
        await _escalate_handoff_rejects(repo, handoff, reject_count=reject_count, now=now)
        source_dept = await repo.get_department(str(handoff.get("fromDept") or ""))
        target_dept = await repo.get_department(str(handoff.get("toDept") or ""))
        await emit_work_status_notice(
            repo,
            event="handoff_escalated",
            summary=f"handoff {handoff_id} ถูกส่งต่อผู้บริหารหลังถูกปฏิเสธ {reject_count} ครั้ง",
            source_dept=source_dept or handoff.get("fromDept"),
            target_dept=target_dept or handoff.get("toDept"),
            task_id=handoff.get("sourceTaskId"),
            handoff_id=handoff_id,
            severity="warn",
            now=now,
            dedupe_key=f"handoff_escalated:{handoff_id}:{reject_count}",
        )


@app.post("/api/engine/tick", response_model=EngineTickResponse)
async def tick_engine(
    compact: bool = Query(False),
    dept_id: str | None = Query(default=None, alias="deptId"),
) -> dict[str, Any]:
    """Run one engine tick manually for CLI/test drivers."""
    return await run_engine_tick(force_compact=compact, compact_department_id=dept_id)


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await ws.accept()
    q = hub.subscribe()
    try:
        await ws.send_json({"type": "state", "state": await _snapshot()})
        while True:
            queue_task = asyncio.create_task(q.get())
            receive_task = asyncio.create_task(ws.receive_text())
            done, pending = await asyncio.wait(
                {queue_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if receive_task in done:
                # We do not need client messages, but reading is required so
                # websocket disconnect/shutdown events can break the loop.
                raw = receive_task.result()
                with contextlib.suppress(json.JSONDecodeError, TypeError, AttributeError):
                    payload = json.loads(raw)
                    action = payload.get("action") or payload.get("type")
                    if action in {"stop_generation", "stop_chat_generation"}:
                        stopped, msg_id = chat_streams.stop(
                            thread_id=payload.get("threadId"),
                            msg_id=payload.get("msgId") or payload.get("messageId"),
                        )
                        await ws.send_json({
                            "type": "pulse",
                            "event": {
                                "kind": "msg_stop_requested",
                                "threadId": payload.get("threadId"),
                                "msgId": msg_id,
                                "stopped": stopped,
                            },
                        })
                continue
            await ws.send_json(queue_task.result())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        hub.unsubscribe(q)


@app.post("/api/running", response_model=RunningResponse)
async def set_running(input: SetRunningInput) -> dict[str, bool]:
    async with session_scope() as s:
        repo = Repo(s)
        await repo.set_running(input.running)
        cancelled = None
        if not input.running:
            cancelled = await _cancel_pending_tool_work(repo, "global kill switch engaged")
        await repo.add_activity(_activity(
            (
                "เริ่มเดินระบบอีกครั้ง"
                if input.running
                else (
                    "หยุดระบบชั่วคราว (สวิตช์ฉุกเฉิน)"
                    f" - cancelled tool runs={cancelled['toolRuns']}, approvals={cancelled['approvals']}, jobs={cancelled['jobs']}"
                )
            ),
            severity="good" if input.running else "alert",
        ))
    hub.mark_dirty()
    return {"running": input.running}


@app.post("/api/tools/kill-switch", response_model=RunningResponse)
async def tool_kill_switch() -> dict[str, bool]:
    return await set_running(SetRunningInput(running=False))


@app.post("/api/tools/resume", response_model=RunningResponse)
async def tool_resume() -> dict[str, bool]:
    return await set_running(SetRunningInput(running=True))


@app.post("/api/departments", response_model=Department)
async def create_department(input: CreateDepartmentInput) -> dict[str, Any]:
    async with session_scope() as s:
        from .org.checkpoints import create_org_checkpoint, mark_org_checkpoint_applied, rollback_endpoint

        repo = Repo(s)
        departments = await repo.list_departments()
        provider_id, model, effort = normalize_ai_config(
            input.provider_id,
            input.model,
            input.thinking_effort or "high",
        )
        speed = coerce_model_speed(model, input.speed or "standard")
        dept_id = input.id or uid("dept")
        actor = "executive" if input.by_executive else "user"
        checkpoint = await create_org_checkpoint(
            repo,
            reason=f"create department {input.name}",
            actor=actor,
            action="create_department",
            metadata={"departmentId": dept_id, "name": input.name, "surface": "api/departments"},
        )
        created_at = now_ms()
        dept = {
            "id": dept_id,
            "name": input.name,
            "role": input.role,
            "charter": input.charter or input.role,
            "emoji": input.emoji or "🟣",
            "accent": input.accent or ACCENTS[len(departments) % len(ACCENTS)],
            "providerId": provider_id,
            "model": model,
            "thinkingEffort": effort,
            "speed": speed,
            "agentName": input.agent_name,
            "state": "idle",
            "mood": 0.85,
            "currentTaskId": None,
            "autonomy": input.autonomy if input.autonomy is not None else False,
            "createdAt": created_at,
            "room": _room_for_new_department(departments),
            "memory": {
                "archiveChunks": 0,
                "ragEntries": 0,
                "graphNodes": 0,
                "graphEdges": 0,
                "lastCompactionAt": None,
                "tokensSaved": 0,
            },
            "skills": input.skills or [],
            "tools": input.tools or [],
            "workspacePath": _provision_workspace(dept_id),
            "visibilityPolicy": _visibility_policy(dept_id),
            "lifecycle": {
                "spawnReason": f"manual create: {input.name}",
                "spawnedBy": actor,
                "checkpointId": checkpoint["id"],
                "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
            },
        }
        await repo.save_department(dept)
        from .org.capabilities import sync_department_capabilities

        await sync_department_capabilities(repo, dept, source="department_create")
        from .runtime.provisioning import ensure_department_runtime_agent_safely

        await ensure_department_runtime_agent_safely(repo, dept)
        dept = await repo.get_department(dept_id) or dept
        decision = Decision(
            id=uid("dec"),
            title=f"สร้างแผนก {dept['name']}",
            proposed_by=actor,
            approved_by="executive",
            rationale="สร้างแผนกแบบ auto ตามนโยบาย v0.4 พร้อม provision workspace และ visibility policy",
            alternatives=[],
            impact=(
                f"เพิ่ม agent {dept['agentName']} และพื้นที่งาน {dept['workspacePath']}; "
                f"checkpoint={checkpoint['id']}; rollback={rollback_endpoint(checkpoint['id'])}"
            ),
            linked_task=None,
            linked_artifacts=[],
            status="approved",
            supersedes=None,
            ts=now_ms(),
        ).dump()
        await repo.put_entity("decision", decision, dept=dept["id"], status="approved", ts=decision["ts"])
        await mark_org_checkpoint_applied(
            repo,
            checkpoint["id"],
            metadata={
                "spawnedDepartmentIds": [dept_id],
                "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
            },
        )
        await repo.add_activity(_activity(
            f"เปิดแผนกใหม่: {dept['name']} ({dept['agentName']})",
            type_="system",
            department_id=dept["id"],
            severity="good",
        ))
    hub.pulse({"kind": "state", "departmentId": dept["id"]})
    hub.mark_dirty()
    return dept


@app.patch("/api/departments/{dept_id}", response_model=Department)
async def edit_department(dept_id: str, patch: EditDepartmentInput) -> dict[str, Any]:
    patch_data = patch.model_dump(by_alias=True, exclude_unset=True)
    async with session_scope() as s:
        repo = Repo(s)
        dept = await _patch_department(repo, dept_id, patch_data)
        await repo.add_activity(_activity(
            f"อัปเดตการตั้งค่าฝ่าย{dept['name']}",
            department_id=dept_id,
        ))
    hub.mark_dirty()
    return dept


@app.post("/api/departments/{dept_id}/provider", response_model=Department)
async def set_department_provider(dept_id: str, input: ProviderInput) -> dict[str, Any]:
    async with session_scope() as s:
        dept = await _patch_department(Repo(s), dept_id, {"providerId": input.provider_id})
    hub.mark_dirty()
    return dept


@app.post("/api/departments/{dept_id}/model", response_model=Department)
async def set_department_model(dept_id: str, input: ModelInput) -> dict[str, Any]:
    async with session_scope() as s:
        dept = await _patch_department(Repo(s), dept_id, {"model": input.model})
    hub.mark_dirty()
    return dept


@app.post("/api/departments/{dept_id}/thinking", response_model=Department)
async def set_department_thinking(dept_id: str, input: ThinkingInput) -> dict[str, Any]:
    async with session_scope() as s:
        dept = await _patch_department(Repo(s), dept_id, {"thinkingEffort": input.thinking_effort})
    hub.mark_dirty()
    return dept


@app.post("/api/departments/{dept_id}/speed", response_model=Department)
async def set_department_speed(dept_id: str, input: SpeedInput) -> dict[str, Any]:
    async with session_scope() as s:
        dept = await _patch_department(Repo(s), dept_id, {"speed": input.speed})
    hub.mark_dirty()
    return dept


@app.post("/api/departments/{dept_id}/autonomy", response_model=Department)
async def set_department_autonomy(dept_id: str, input: ToggleInput) -> dict[str, Any]:
    async with session_scope() as s:
        dept = await _patch_department(Repo(s), dept_id, {"autonomy": input.enabled})
    hub.mark_dirty()
    return dept


@app.delete("/api/departments/{dept_id}", response_model=GuardedActionResponse)
async def close_department(dept_id: str) -> dict[str, Any]:
    if is_exec(dept_id):
        raise HTTPException(status_code=400, detail="executive department cannot be closed")
    async with session_scope() as s:
        repo = Repo(s)
        dept = await repo.get_department(dept_id)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        approval = await _request_destructive_approval(
            repo,
            title=f"ปิดแผนก {dept['name']}",
            detail=f"การปิดแผนกจะย้ายงานที่ค้างกลับ backlog และลบ memory/cost ของ {dept['agentName']} แบบถาวร",
            department_id=dept_id,
            action={"action": "delete_department", "departmentId": dept_id, "requestedBy": "user"},
        )
    hub.mark_dirty()
    return {"ok": True, "approval": approval, "executed": True}


@app.post("/api/tasks", response_model=Task)
async def assign_task(input: AssignTaskInput) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        dept = await repo.get_department(input.department_id)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        if input.project_id:
            project = await repo.get_entity("project", input.project_id)
            if not project:
                raise HTTPException(status_code=404, detail="project not found")
            allowed = set(project.get("departments", []))
            if input.department_id not in allowed:
                raise HTTPException(status_code=400, detail="department is not a member of the project")
        task = {
            "id": input.id or uid("task"),
            "title": input.title,
            "detail": input.detail or "",
            "status": "assigned",
            "priority": input.priority or "normal",
            "departmentId": input.department_id,
            "origin": {"kind": "executive"} if input.by_executive else {"kind": "user"},
            "progress": 0,
            "createdAt": now,
            "updatedAt": now,
            "handoffs": [],
            "log": ["ผู้บริหารมอบหมาย" if input.by_executive else "ผู้ใช้มอบหมายโดยตรง"],
            "projectId": input.project_id,
            "deliverables": [],
            "watchers": input.watchers,
            "parentTaskId": input.parent_task_id,
            "subTaskIds": [],
            "deadlineAt": input.deadline_at,
            "result": None,
        }
        interval_ms = normalize_review_interval_ms(input.review_interval_ms)
        apply_task_review_schedule(task, interval_ms, now)
        if interval_ms:
            task["log"].append(f"ตั้งรอบปลุกผู้บริหารตรวจงานทุก {review_interval_label(interval_ms)}")
        await _link_child_task(repo, input.parent_task_id, task["id"])
        await repo.save_task(task)
        woke = await _wake_department_for_assigned_task(repo, dept, task, now)
        await enqueue_task_review_reminder(repo, task, now=now)
        await repo.add_activity(_activity(
            f"งานใหม่ “{task['title']}” → ฝ่าย{dept['name']}" + (" และเริ่มทำทันที" if woke else ""),
            type_="task_created",
            department_id=input.department_id,
        ))
    hub.pulse({"kind": "state", "departmentId": input.department_id})
    hub.mark_dirty()
    return task


@app.get("/api/tasks", response_model=list[Task])
async def list_tasks(
    status: TaskStatus | None = Query(default=None),
    department_id: str | None = Query(default=None, alias="departmentId"),
    project_id: str | None = Query(default=None, alias="projectId"),
    include_details: bool = Query(default=False, alias="includeDetails"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        tasks = await Repo(s).list_tasks(
            status=status,
            department_id=department_id,
            project_id=project_id,
            limit=limit,
            newest_first=True,
        )
    if include_details:
        return tasks
    return [_task_list_summary(task) for task in tasks]


def _task_list_summary(task: dict[str, Any]) -> dict[str, Any]:
    out = dict(task)
    out["log"] = [str(line)[:500] for line in list(task.get("log") or [])[-5:]]
    out["draftDeliverableMarkdown"] = None
    return out


@app.get("/api/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        task = await Repo(s).get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.patch("/api/tasks/{task_id}/review-schedule", response_model=Task)
async def update_task_review_schedule(task_id: str, input: UpdateTaskReviewScheduleInput) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        task = await repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.get("status") in {"done", "cancelled"}:
            raise HTTPException(status_code=409, detail="completed task cannot schedule review reminders")
        interval_ms = normalize_review_interval_ms(input.review_interval_ms)
        apply_task_review_schedule(task, interval_ms, now)
        task["updatedAt"] = now
        label = review_interval_label(interval_ms)
        task.setdefault("log", []).append(
            f"ผู้บริหารแก้รอบปลุกตรวจงานเป็น {label}" if interval_ms else "ผู้บริหารปิดรอบปลุกตรวจงาน"
        )
        await repo.save_task(task)
        await enqueue_task_review_reminder(repo, task, now=now)
        dept = await repo.get_department(task.get("departmentId")) if task.get("departmentId") else None
        await repo.add_activity(_activity(
            (
                f"ตั้งรอบตรวจงาน “{task['title']}” ทุก {label}"
                if interval_ms
                else f"ปิดรอบตรวจงาน “{task['title']}”"
            ),
            type_="task_progress",
            department_id=(dept or {}).get("id") or task.get("departmentId"),
            severity="info",
            ts=now,
        ))
    hub.pulse({"kind": "state", "departmentId": task.get("departmentId"), "taskId": task["id"]})
    hub.mark_dirty()
    return task


@app.post("/api/tasks/{task_id}/request-close", response_model=Approval)
async def request_task_close(task_id: str, input: RequestTaskClosureInput) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        task = await repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.get("status") in {"done", "cancelled"}:
            raise HTTPException(status_code=409, detail="completed task cannot request closure")
        if input.department_id and input.department_id != task.get("departmentId"):
            raise HTTPException(status_code=400, detail="departmentId does not own this task")
        dept = await repo.get_department(task.get("departmentId"))
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        content = (
            input.detail
            or input.summary
            or task.get("draftDeliverableMarkdown")
            or (task.get("result") or {}).get("summary")
            or "\n".join(str(line) for line in task.get("log", [])[-8:])
        )
        approval = await request_task_close_approval(
            repo,
            dept,
            task,
            now,
            content=str(content or ""),
            decision={
                "rationale": input.summary or "แผนกขอปิดงานผ่าน API",
                "impact": f"requestedBy={input.requested_by}; task={task_id}",
            },
            source=f"api:{input.requested_by}",
        )
    hub.mark_dirty()
    return approval


@app.post("/api/tasks/{task_id}/reassign", response_model=Task)
async def reassign_task(task_id: str, input: ReassignTaskInput) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        task = await repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.get("status") in {"done", "cancelled"}:
            raise HTTPException(status_code=409, detail="completed task cannot be reassigned")
        target = await repo.get_department(input.department_id)
        if not target or is_exec(target["id"]):
            raise HTTPException(status_code=404, detail="target department not found")
        old_dept_id = task.get("departmentId")
        if old_dept_id == target["id"]:
            return task
        if task.get("projectId"):
            project = await repo.get_entity("project", task["projectId"])
            allowed = set(project.get("departments", [])) if project else set()
            if allowed and target["id"] not in allowed:
                raise HTTPException(status_code=400, detail="target department is not a project member")

        old_dept = await repo.get_department(old_dept_id) if old_dept_id else None
        task["departmentId"] = target["id"]
        if task.get("status") in {"backlog", "waiting"}:
            task["status"] = "assigned"
        task["waitingOn"] = None
        task["updatedAt"] = now
        handoff = {
            "id": uid("handoff"),
            "fromDept": old_dept_id or "backlog",
            "toDept": target["id"],
            "ts": now,
            "reason": input.reason or "drag-and-drop reassignment",
            "kind": "delegate",
            "status": "accepted",
            "depth": 0,
            "sourceTaskId": task["id"],
            "targetTaskId": task["id"],
            "messages": [],
        }
        task.setdefault("handoffs", []).append(handoff)
        task.setdefault("log", []).append(
            f"{input.requested_by} ย้ายงานจาก {old_dept['name'] if old_dept else 'backlog'} ไป {target['name']}"
        )

        if old_dept and old_dept.get("currentTaskId") == task["id"]:
            old_dept["currentTaskId"] = None
            await repo.save_department(old_dept)
        if not target.get("currentTaskId"):
            target["currentTaskId"] = task["id"]
            await repo.save_department(target)
        await repo.save_task(task)
        await repo.add_activity(_activity(
            f"ย้ายงาน “{task['title']}” → ฝ่าย{target['name']}",
            type_="handoff" if old_dept_id else "task_assigned",
            department_id=old_dept_id or target["id"],
        ))
    hub.pulse({"kind": "handoff" if old_dept_id else "state", "departmentId": old_dept_id or target["id"], "toDepartmentId": target["id"]})
    hub.mark_dirty()
    return task


@app.post("/api/messages/{thread_id}/stop", response_model=StopGenerationResponse)
async def stop_generation(
    thread_id: str,
    input: StopGenerationInput | None = Body(default=None),
) -> dict[str, Any]:
    message_id = input.message_id if input else None
    stopped, stopped_msg_id = chat_streams.stop(thread_id=thread_id, msg_id=message_id)
    queued_cancelled = 0
    cancelled_messages: list[dict[str, Any]] = []

    async with session_scope() as s:
        repo = Repo(s)
        messages = await repo.thread_messages(thread_id, limit=500)
        targets = [
            msg
            for msg in messages
            if _is_active_chat_reply(msg)
            and (
                (message_id is not None and msg.get("id") == message_id)
                or (message_id is None and (stopped_msg_id is None or msg.get("id") == stopped_msg_id))
            )
        ]
        if message_id is None:
            # The composer exposes one stop control for the whole busy thread.
            # If older queued placeholders exist, cancel them too so the UI
            # leaves the busy state after one click.
            target_ids = {msg.get("id") for msg in targets}
            targets.extend(
                msg
                for msg in messages
                if _is_active_chat_reply(msg) and msg.get("id") not in target_ids
            )
        if targets:
            stopped_msg_id = stopped_msg_id or str(targets[-1].get("id") or "")
            queued_cancelled = await repo.cancel_chat_reply_jobs(
                thread_id,
                message_id if message_id is not None else None,
                "chat generation stopped by user",
            )
            for target in targets:
                cancelled = _cancelled_chat_reply(target, detail="generation stopped by user")
                await repo.update_message(cancelled)
                cancelled_messages.append(cancelled)
            stopped = True

    if stopped:
        hub.pulse({"kind": "msg_stop_requested", "threadId": thread_id, "msgId": stopped_msg_id, "stopped": True})
        for msg in cancelled_messages:
            hub.pulse({
                "kind": "msg_done",
                "threadId": thread_id,
                "msgId": msg["id"],
                "text": msg.get("text") or "หยุดการตอบแล้วก่อนเริ่มสร้างคำตอบ",
                "stopped": True,
            })
        if cancelled_messages:
            hub.mark_dirty()

    return {"stopped": stopped, "messageId": stopped_msg_id, "queuedCancelled": queued_cancelled}


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, Any]:
    settings = get_settings()
    secret = telegram_webhook_secret(settings)
    if secret:
        supplied = str(request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
        if not hmac.compare_digest(supplied, secret):
            raise HTTPException(status_code=401, detail="invalid Telegram webhook secret")
    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid Telegram update JSON") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Telegram update must be an object")
    async with session_scope() as s:
        repo = Repo(s)
        result = await handle_telegram_update(repo, update, settings=settings, store_file=_store_file_artifact)
    if result.get("status") in {"queued", "blocked", "sent", "retry_queued"}:
        hub.mark_dirty()
    return result


@app.post("/api/messages/{thread_id}", response_model=SendMessageResponse)
async def send_message(thread_id: str, input: SendMessageInput, request: Request) -> dict[str, Any]:
    settings = get_settings()
    now = now_ms()
    if is_meeting_thread(thread_id):
        user_msg = {
            "id": input.client_message_id or uid("msg"),
            "threadId": thread_id,
            "role": "user",
            "authorName": "คุณ",
            "text": input.text,
            "ts": now,
            "clientMessageId": input.client_message_id,
            "retryOfMessageId": input.retry_of_message_id,
        }
        return await _send_meeting_message(thread_id, input, request, user_msg)
    if is_war_room_thread(thread_id):
        user_msg = {
            "id": input.client_message_id or uid("msg"),
            "threadId": thread_id,
            "role": "user",
            "authorName": "คุณ",
            "text": input.text,
            "ts": now,
            "clientMessageId": input.client_message_id,
            "retryOfMessageId": input.retry_of_message_id,
        }
        return await _send_war_room_message(thread_id, input, request, user_msg)
    requested_thread_id = thread_id
    async with session_scope() as s:
        repo = Repo(s)
        dept_id = dept_id_from_thread(requested_thread_id)
        thread_id = thread_id_for(dept_id)
        legacy_department_thread = requested_thread_id != thread_id and not is_exec(dept_id)
        current_dept = await repo.get_department(dept_id) or await repo.get_department(EXEC_ID)
        if not current_dept:
            raise HTTPException(status_code=404, detail="no department available for this thread")
        departments = await repo.list_departments()
        attachments = await _normalize_chat_attachments(repo, input)
        if not str(input.text or "").strip() and not attachments:
            raise HTTPException(status_code=400, detail="message text or attachments are required")
        estimate = estimate_input(input.text, attachments)
        if input_character_limit_exceeded(estimate):
            raise HTTPException(status_code=413, detail=input_character_limit_detail())
        mentions = resolve_department_mentions(input.text, departments)
        explicit_target_id = str(input.target_department_id or "").strip()
        explicit_target_dept = None
        if explicit_target_id:
            explicit_target_dept = next((dept for dept in departments if dept.get("id") == explicit_target_id), None)
            if not explicit_target_dept:
                raise HTTPException(status_code=404, detail="target department not found")
        responder_dept = explicit_target_dept or choose_mentioned_responder(current_dept, mentions, departments)
        command = parse_slash_command(input.text)
        routed_department_id = responder_dept["id"] if responder_dept["id"] != current_dept["id"] else None
        if legacy_department_thread and routed_department_id is None:
            routed_department_id = current_dept["id"]
        history = await _thread_messages_for_live_prompt(repo, thread_id)
        existing_user_msg = await repo.get_message(input.client_message_id) if input.client_message_id else None
        if existing_user_msg:
            if existing_user_msg.get("threadId") not in {thread_id, requested_thread_id} or existing_user_msg.get("role") != "user":
                raise HTTPException(status_code=409, detail="clientMessageId already belongs to another message")
            existing_reply = next(
                (
                    msg
                    for msg in history
                    if msg.get("role") != "user"
                    and msg.get("replyToMessageId") == existing_user_msg["id"]
                    and msg.get("status") in {"queued", "sending", "sent", "failed", "cancelled", "blocked"}
                ),
                None,
            )
            if existing_reply and not input.retry_of_message_id:
                return _chat_response(
                    existing_reply,
                    usage={
                        "usd": 0.0,
                        "ragHits": [],
                        "compactEnqueued": False,
                        "toolRuns": [],
                        "warnings": [{
                            "code": "duplicate_request",
                            "message": "คืนผลลัพธ์เดิมจาก clientMessageId เพื่อกันข้อความซ้ำ",
                            "severity": "info",
                        }],
                    },
                    mentions=existing_user_msg.get("mentions", []),
                    suggestions=existing_reply.get("suggestedFollowUps", []),
                    estimate=estimate,
                    draft_cleared=False,
                )
        active_reply = _active_chat_reply(history, ignore_reply_to=input.retry_of_message_id)
        if active_reply and (not settings.engine_enabled or not input.queue_if_busy):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "thread_busy",
                    "message": "thread already has an active reply; retry after it finishes or enable queueIfBusy",
                    "activeMessageId": active_reply.get("id"),
                },
            )
        rate_limit, rate_warnings, rate_exceeded = _rate_limit_state(
            history,
            now,
            int(settings.chat_rate_limit_per_minute),
        )
        user_msg = {
            "id": existing_user_msg["id"] if existing_user_msg else (input.client_message_id or uid("msg")),
            "threadId": thread_id,
            "role": "user",
            "authorName": "คุณ",
            "text": input.text,
            "ts": now,
            "clientMessageId": input.client_message_id,
            "retryOfMessageId": input.retry_of_message_id,
            "status": "sent",
            "attachments": attachments,
            "mentions": mentions,
            "input": _input_metadata(
                estimate,
                status="sent",
                command=command,
                routed_department_id=routed_department_id,
            ),
        }
        await _attach_message_refs(repo, thread_id, user_msg, input)
        draft_cleared = await _delete_thread_draft(repo, thread_id)
        if command:
            response = await _execute_slash_command(
                repo,
                thread_id=thread_id,
                command=command,
                user_msg=user_msg,
                current_dept=current_dept,
                responder_dept=responder_dept,
                departments=departments,
                history=history,
                mentions=mentions,
                estimate=estimate,
                draft_cleared=draft_cleared,
            )
            hub.mark_dirty()
            return response

        dept = responder_dept
        turn_dept = _with_turn_speed(_with_turn_thinking_effort(dept, input.thinking_effort), input.speed)
        estimated_usd = _chat_cost_estimate(turn_dept)
        budget = await _budget_state(repo, estimated_usd)
        safety_warnings = [*rate_warnings, *_budget_warnings(budget)]
        if rate_exceeded:
            user_msg["status"] = "blocked"
            user_msg["error"] = _message_error("rate_limited", "chat rate limit exceeded")
            user_msg["input"] = _input_metadata(
                estimate,
                status="failed",
                command=command,
                routed_department_id=routed_department_id,
            )
            if not existing_user_msg:
                await repo.add_message(user_msg)
            else:
                await repo.update_message({**existing_user_msg, **user_msg})
            reply = {
                "id": uid("msg"),
                "threadId": thread_id,
                "role": "system",
                "authorName": "Trust & Safety",
                "text": safety_warnings[0]["message"] if safety_warnings else "ส่งข้อความถี่เกิน rate limit",
                "ts": now_ms(),
                "status": "blocked",
                "replyToMessageId": user_msg["id"],
                "error": _message_error("rate_limited", "chat rate limit exceeded"),
            }
            usage = {
                "usd": 0.0,
                "ragHits": [],
                "compactEnqueued": False,
                "toolRuns": [],
                "warnings": safety_warnings,
                "rateLimit": rate_limit,
                "budget": budget,
            }
            reply = ensure_rendering_metadata(
                reply,
                usage=usage,
                notices=_warning_notices(safety_warnings),
                severity="warn",
            )
            await repo.add_message(reply)
            await repo.add_activity(_activity(
                f"บล็อกแชตของ{dept['agentName']}ตาม rate limit",
                type_="system",
                department_id=dept["id"],
                severity="warn",
            ))
            hub.pulse({"kind": "chat_safety_warning", "threadId": thread_id, "warnings": safety_warnings})
            hub.mark_dirty()
            return _chat_response(
                reply,
                usage=usage,
                mentions=mentions,
                estimate=estimate,
                draft_cleared=draft_cleared,
            )

        if not existing_user_msg:
            await repo.add_message(user_msg)
        else:
            await repo.update_message({**existing_user_msg, **user_msg})
        await _record_message_mentions(repo, thread_id=thread_id, user_msg=user_msg, mentions=mentions, current_dept=current_dept)
        if routed_department_id:
            await repo.add_activity(_activity(
                f"route ข้อความจาก executive ไปยังฝ่าย{responder_dept['name']}ด้วย @mention",
                type_="message",
                department_id=responder_dept["id"],
            ))
        blocked = await _budget_block_reason(repo, turn_dept, estimated_usd)
        if blocked:
            blocked_warnings = [
                *safety_warnings,
                {"code": "budget_guardrail", "message": blocked, "severity": "alert"},
            ]
            usage = {
                "usd": 0.0,
                "ragHits": [],
                "compactEnqueued": False,
                "toolRuns": [],
                "thinkingEffort": turn_dept.get("thinkingEffort", "high"),
                "speed": turn_dept.get("speed", "standard"),
                "warnings": blocked_warnings,
                "rateLimit": rate_limit,
                "budget": budget,
            }
            suggestions = suggested_followups_for_response(dept, input.text, blocked, mentions=mentions)
            reply = {
                "id": uid("msg"),
                "threadId": thread_id,
                "role": "executive" if is_exec(dept["id"]) else "agent",
                "authorName": dept["agentName"],
                "text": f"ยังไม่เรียกโมเดลเพราะ runtime dependency: {blocked}",
                "ts": now_ms(),
                "status": "blocked",
                "replyToMessageId": user_msg["id"],
                "error": _message_error("runtime_dependency", blocked),
                "suggestedFollowUps": suggestions,
                **agent_message_metadata(dept),
            }
            reply = ensure_rendering_metadata(
                reply,
                usage=usage,
                notices=["runtime_dependency", *_warning_notices(safety_warnings)],
                severity="warn",
            )
            cost_summary = thread_cost_summary(thread_id, [*history, user_msg, reply])
            reply["threadCost"] = cost_summary
            usage["threadUsd"] = cost_summary["totalUsd"]
            await repo.add_message(reply)
            await repo.add_activity(_activity(
                f"runtime dependency บล็อกแชตของ{dept['agentName']}: {blocked}",
                type_="budget",
                department_id=dept["id"],
                severity="alert",
            ))
            hub.pulse({"kind": "spend", "departmentId": dept["id"]})
            hub.mark_dirty()
            return _chat_response(
                reply,
                usage=usage,
                mentions=mentions,
                suggestions=suggestions,
                estimate=estimate,
                draft_cleared=draft_cleared,
            )
        if settings.engine_enabled:
            dept = await update_agent_state(repo, dept, "thinking", mood_delta=-0.01)
            turn_dept = _with_turn_speed(_with_turn_thinking_effort(dept, input.thinking_effort), input.speed)
            queued_usage = {
                "usd": 0.0,
                "ragHits": [],
                "compactEnqueued": False,
                "toolRuns": [],
                "thinkingEffort": turn_dept.get("thinkingEffort", "high"),
                "speed": turn_dept.get("speed", "standard"),
                "warnings": safety_warnings,
                "rateLimit": rate_limit,
                "budget": budget,
            }
            reply = {
                "id": uid("msg"),
                "threadId": thread_id,
                "role": "executive" if is_exec(dept["id"]) else "agent",
                "authorName": dept["agentName"],
                "text": "กำลังคิดและทำงานต่อในคิวเบื้องหลัง...",
                "ts": now_ms(),
                "pending": True,
                "status": "queued",
                "replyToMessageId": user_msg["id"],
                **agent_message_metadata(dept),
            }
            cost_summary = thread_cost_summary(thread_id, [*history, user_msg, reply])
            queued_usage["threadUsd"] = cost_summary["totalUsd"]
            reply["threadCost"] = cost_summary
            reply = ensure_rendering_metadata(
                reply,
                usage=queued_usage,
                notices=_warning_notices(safety_warnings),
                severity="warn" if safety_warnings else None,
            )
            await repo.add_message(reply)
            job_id = uid("job")
            await repo.enqueue(
                job_id,
                "chat_reply",
                {
                    "threadId": thread_id,
                    "departmentId": dept["id"],
                    "userMessageId": user_msg["id"],
                    "replyMessageId": reply["id"],
                    "text": input.text,
                    "userTs": user_msg["ts"],
                    "replyTs": reply["ts"],
                    "thinkingEffort": input.thinking_effort,
                    "speed": input.speed,
                    "attachments": attachments,
                    "mentions": mentions,
                    "tokenEstimate": estimate,
                    "safetyWarnings": safety_warnings,
                    "rateLimit": rate_limit,
                    "budget": budget,
                },
                now_ms(),
                priority=1,
            )
            await repo.add_activity(_activity(
                f"คิวแชตระยะยาวของ{dept['agentName']} ({job_id})",
                type_="message",
                department_id=dept["id"],
            ))
            hub.mark_dirty()
            return _chat_response(
                reply,
                usage=queued_usage,
                mentions=mentions,
                estimate=estimate,
                draft_cleared=draft_cleared,
            )
        memory_context, memory_hits = await _retrieval_context(repo, dept, input.text)
        tool_memory_context = await recent_tool_run_context(repo, dept, thread_id)
        if tool_memory_context:
            memory_context = (memory_context + "\n\n" if memory_context else "") + tool_memory_context
        attached_context = await attachment_context(repo, attachments)
        if attached_context:
            memory_context = (memory_context + "\n\n" if memory_context else "") + "Attached user context:\n" + attached_context
        context_tokens, context_token_source = await _context_tokens_for_turn(
            dept,
            history,
            user_msg,
            memory_context=memory_context,
            departments=departments,
        )
        compact_enqueued = await _maybe_enqueue_context_compaction(
            repo,
            thread_id=thread_id,
            dept=dept,
            message_count=len(history) + 2,
            estimated_context_tokens=context_tokens,
            context_token_source=context_token_source,
        )
        dept = await update_agent_state(repo, dept, "thinking", mood_delta=-0.01)
        turn_dept = _with_turn_speed(_with_turn_thinking_effort(dept, input.thinking_effort), input.speed)
        author_role = "executive" if is_exec(dept["id"]) else "agent"
        reply = {
            "id": uid("msg"),
            "threadId": thread_id,
            "role": author_role,
            "authorName": dept["agentName"],
            "text": "",
            "ts": now_ms(),
            "pending": True,
            "status": "sending",
            "replyToMessageId": user_msg["id"],
            **agent_message_metadata(dept),
        }
        await repo.add_message(reply)

    hub.mark_dirty()
    cancel_event = chat_streams.start(thread_id, reply["id"])
    sink = ChatMessageStreamSink(
        thread_id=thread_id,
        msg_id=reply["id"],
        message=reply,
        cancel_event=cancel_event,
    )
    await sink.start()

    async def on_stream_event(event) -> None:
        if await request.is_disconnected():
            cancel_event.set()
        await sink.handle(event)

    stopped = False
    stream_error: str | None = None
    try:
        result = await _complete_with_provider(
            turn_dept,
            history,
            user_msg,
            memory_context,
            departments=departments,
            on_stream_event=on_stream_event,
            stream_msg_id=sink.msg_id,
        )
    except ChatStreamCancelled:
        stopped = True
        result = _partial_chat_result(turn_dept, sink.text, stop_reason="cancelled")
    except Exception as exc:
        error_type, stream_error = provider_exception_detail(exc)
        result = _partial_chat_result(
            turn_dept,
            sink.text,
            stop_reason="error",
            error_type=error_type,
            error_detail=stream_error,
        )
    finally:
        chat_streams.finish(thread_id, reply["id"])

    result.meta["ragHitIds"] = [hit["id"] for hit in memory_hits]
    result.meta["compactEnqueued"] = compact_enqueued
    reply = await sink.finish(result=result, stopped=stopped, error=stream_error)
    runtime_dependency = bool(result.meta.get("runtimeDependency"))
    if runtime_dependency:
        reply["status"] = "blocked"
        reply["error"] = _message_error("runtime_dependency", _runtime_dependency_detail(result))
    if result.meta.get("toolRuns"):
        reply["toolRuns"] = result.meta["toolRuns"]
        reply = _attach_tool_artifacts_to_message(reply, result.meta["toolRuns"])
    runtime_meta = _message_runtime_payload(result, turn_dept)
    if runtime_meta:
        reply["runtime"] = runtime_meta
    suggestions = suggested_followups_for_response(dept, input.text, result.text, mentions=mentions)
    reply["suggestedFollowUps"] = suggestions
    citations = citation_chips(memory_hits, department_id=dept["id"])
    usage = _message_usage_payload(
        result,
        turn_dept,
        rag_hits=result.meta.get("ragHitIds", []),
        compact_enqueued=bool(result.meta.get("compactEnqueued")),
        warnings=safety_warnings,
        rate_limit=rate_limit,
        budget=budget,
    )
    reply = ensure_rendering_metadata(
        reply,
        usage=usage,
        citations=citations,
        notices=[
            *_warning_notices(safety_warnings),
            *(["message_failed"] if stream_error else []),
            *(["runtime_dependency"] if runtime_dependency else []),
        ],
        severity="warn" if safety_warnings or stream_error or runtime_dependency else None,
    )

    async with session_scope() as s:
        repo = Repo(s)
        await repo.update_message(reply)
        if not result.meta.get("cancelled") and not runtime_dependency:
            await repo.add_cost(
                uid("cost"),
                now_ms(),
                dept["id"],
                "chat",
                result.usd,
                detail=f"{result.provider_id}:{result.model}:{turn_dept.get('thinkingEffort', 'high')}:{result.speed}",
                provider_id=result.provider_id,
                model=result.model,
                speed=result.speed,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
        await _record_budget_exhaustion(repo, dept, now_ms())
        if memory_hits:
            await repo.add_activity(_activity(
                f"ดึง RAG {len(memory_hits)} รายการเข้าบริบทของ{dept['agentName']}",
                type_="system",
                department_id=dept["id"],
                severity="info",
            ))
        await repo.add_activity(_activity(
            f"{dept['agentName']} ตอบกลับในแชต",
            type_="message",
            department_id=dept["id"],
        ))
        tool_messages, tool_activities = await _tool_activity_lines(
            repo,
            thread_id,
            dept,
            result.meta.get("toolRuns", []),
        )
    hub.pulse({"kind": "spend", "departmentId": dept["id"]})
    hub.mark_dirty()
    return _chat_response(
        reply,
        usage=usage,
        messages=tool_messages,
        activity=tool_activities,
        mentions=mentions,
        suggestions=suggestions,
        estimate=estimate,
        draft_cleared=draft_cleared,
    )


@app.post("/api/messages/{thread_id}/retry", response_model=SendMessageResponse)
async def retry_message(thread_id: str, input: RetryMessageInput, request: Request) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        messages = await repo.thread_messages(thread_id, limit=500)
        target = next((msg for msg in messages if msg.get("id") == input.message_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="message not found")
        source = target if target.get("role") == "user" else None
        if source is None and target.get("replyToMessageId"):
            source = next((msg for msg in messages if msg.get("id") == target.get("replyToMessageId")), None)
        if source is None:
            target_ts = int(target.get("ts") or 0)
            source = next(
                (
                    msg
                    for msg in reversed(messages)
                    if msg.get("role") == "user" and int(msg.get("ts") or 0) <= target_ts
                ),
                None,
            )
        if not source:
            raise HTTPException(status_code=400, detail="no user message available to retry")

    retry_input = SendMessageInput(
        text=str(source.get("text") or ""),
        attachments=source.get("attachments") or [],
        target_department_id=_message_turn_department_id(source, thread_id),
        thinking_effort=input.thinking_effort,
        speed=input.speed,
        client_message_id=input.client_message_id,
        retry_of_message_id=input.message_id,
        queue_if_busy=True,
    )
    return await send_message(thread_id, retry_input, request)


@app.post("/api/messages/{thread_id}/regenerate", response_model=SendMessageResponse)
async def regenerate_message(
    thread_id: str,
    input: RegenerateMessageInput,
    request: Request,
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        messages = await repo.thread_messages(thread_id, limit=500)
        active = _active_chat_reply(messages)
        if active:
            raise HTTPException(status_code=409, detail={"code": "thread_busy", "activeMessageId": active.get("id")})
        target = (
            next((msg for msg in messages if msg.get("id") == input.message_id), None)
            if input.message_id
            else _latest_assistant_message(messages)
        )
        if not target:
            raise HTTPException(status_code=404, detail="assistant message not found")
        if target.get("role") == "user":
            raise HTTPException(status_code=400, detail="regenerate target must be an assistant/system reply")
        source = None
        if target.get("replyToMessageId"):
            source = next((msg for msg in messages if msg.get("id") == target.get("replyToMessageId")), None)
        source = source or _previous_user_message(messages, target["id"])
        if not source:
            raise HTTPException(status_code=400, detail="no user message available for regeneration")
        dept = await repo.get_department(_message_turn_department_id(target, thread_id)) or await repo.get_department(EXEC_ID)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        history = _messages_before(messages, source["id"])

    return await _complete_chat_turn(
        thread_id=thread_id,
        user_msg=source,
        history=history,
        dept=dept,
        request=request,
        reply_overrides={
            "replyToMessageId": source["id"],
            "parentMessageId": source["id"],
            "retryOfMessageId": target["id"],
            "regeneratedFromMessageId": target["id"],
        },
        thinking_effort=input.thinking_effort,
        speed=input.speed,
    )


@app.post("/api/messages/{thread_id}/{message_id}/edit", response_model=BranchConversationResponse)
async def edit_message_and_branch(
    thread_id: str,
    message_id: str,
    input: EditMessageInput,
    request: Request,
) -> dict[str, Any]:
    if not input.text.strip():
        raise HTTPException(status_code=400, detail="edited text is required")
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        messages = await repo.thread_messages(thread_id, limit=500)
        target = next((msg for msg in messages if msg.get("id") == message_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="message not found")
        if target.get("role") != "user":
            raise HTTPException(status_code=400, detail="only user messages can be edited and resent")
        branch_thread_id = _branch_thread_id(thread_id, input.branch_id)
        source_history = _messages_before(messages, message_id)
        copied_history: list[dict[str, Any]] = []
        for msg in source_history:
            copied = _copy_message_for_branch(msg, branch_thread_id, thread_id)
            await repo.add_message(copied)
            copied_history.append(copied)
        edited_message = {
            **target,
            "id": uid("msg"),
            "threadId": branch_thread_id,
            "text": input.text,
            "ts": now,
            "status": "sent",
            "editedFromMessageId": target["id"],
            "branchFromThreadId": thread_id,
            "branchPointMessageId": target["id"],
            "replyToMessageId": target.get("replyToMessageId"),
        }
        edited_message.pop("pending", None)
        dept = await repo.get_department(_message_turn_department_id(edited_message, thread_id)) or await repo.get_department(EXEC_ID)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        await repo.add_message(edited_message)
        await repo.add_activity(_activity(
            f"แตก branch แชตจากข้อความ {target['id']}",
            type_="message",
            department_id=dept["id"],
        ))

    response = await _complete_chat_turn(
        thread_id=branch_thread_id,
        user_msg=edited_message,
        history=copied_history,
        dept=dept,
        request=request,
        reply_overrides={
            "replyToMessageId": edited_message["id"],
            "parentMessageId": edited_message["id"],
            "branchFromThreadId": thread_id,
            "branchPointMessageId": target["id"],
        },
        thinking_effort=input.thinking_effort,
        speed=input.speed,
    )
    return {
        "branchThreadId": branch_thread_id,
        "editedMessage": edited_message,
        "response": response,
        "copiedCount": len(copied_history),
    }


@app.post("/api/messages/{thread_id}/{message_id}/actions", response_model=MessageActionResponse)
async def apply_message_action(
    thread_id: str,
    message_id: str,
    input: MessageActionInput,
) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        message = await repo.get_message(message_id, thread_id=thread_id)
        if not message:
            raise HTTPException(status_code=404, detail="message not found")
        mutated = input.action != "copy"
        if input.action == "pin":
            message["pinned"] = True
            message["pinnedAt"] = message.get("pinnedAt") or now
        elif input.action == "unpin":
            message["pinned"] = False
            message.pop("pinnedAt", None)
        elif input.action in {"react", "unreact"}:
            reaction = (input.reaction or "").strip()
            if not reaction:
                raise HTTPException(status_code=400, detail="reaction is required")
            reactions = list(message.get("reactions") or [])
            reactions = [
                item
                for item in reactions
                if not (item.get("emoji") == reaction and item.get("actor") == input.actor)
            ]
            if input.action == "react":
                reactions.append({"emoji": reaction, "actor": input.actor, "ts": now})
            message["reactions"] = reactions
        if mutated:
            await repo.update_message(message)
            await repo.add_activity(_activity(
                f"message action {input.action}: {message_id}",
                type_="message",
                department_id=dept_id_from_thread(thread_id),
            ))
    if mutated:
        hub.pulse({"kind": "message_action", "threadId": thread_id, "messageId": message_id, "action": input.action})
        hub.mark_dirty()
    return {"action": input.action, "mutated": mutated, "message": message}


@app.post("/api/messages/{thread_id}/{message_id}/promote", response_model=PromoteMessageResponse)
async def promote_message_to_memory(
    thread_id: str,
    message_id: str,
    input: PromoteMessageInput,
) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        dept_id = dept_id_from_thread(thread_id)
        dept = await repo.get_department(dept_id) or await repo.get_department(EXEC_ID)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        message = await repo.get_message(message_id, thread_id=thread_id)
        if not message:
            raise HTTPException(status_code=404, detail="message not found")
        text = str(message.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="cannot promote an empty message")
        title = (input.title or text.splitlines()[0] or message_id).strip()[:120]
        tags = []
        for tag in [*input.tags, "pinned", "chat", f"thread:{thread_id}", f"message:{message_id}"]:
            if tag and tag not in tags:
                tags.append(tag)
        knowledge = {
            "id": uid("kn"),
            "title": title,
            "ts": now,
            "score": 0.92,
            "text": text,
            "tags": tags,
            "source": f"message:{message_id}",
        }
        embedder = await resolve_embedder()
        vecs = await embedder.embed([text])
        vector = vecs[0] if vecs else None
        await repo.add_knowledge(
            dept["id"],
            knowledge,
            embedding=vector,
            source=knowledge["source"],
            embedding_meta=embedding_metadata(embedder, vector),
        )
        message["pinned"] = True
        message["pinnedAt"] = message.get("pinnedAt") or now
        message["memoryPromotedId"] = knowledge["id"]
        await repo.update_message(message)
        await _refresh_memory_stats(repo, dept["id"])
        await repo.add_activity(_activity(
            f"promote message เป็น knowledge: {title}",
            type_="system",
            department_id=dept["id"],
            severity="good",
        ))
    hub.pulse({"kind": "message_promoted", "threadId": thread_id, "messageId": message_id, "knowledgeId": knowledge["id"]})
    hub.mark_dirty()
    return {"message": message, "knowledge": knowledge}


@app.get("/api/threads/{thread_id}/search", response_model=ThreadSearchResponse)
async def search_thread_messages(
    thread_id: str,
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    query = q.strip()
    terms = [term for term in query.lower().split() if term]
    async with session_scope() as s:
        messages = await Repo(s).thread_messages(thread_id, limit=1000)
    hits: list[dict[str, Any]] = []
    for msg in messages:
        haystack = f"{msg.get('authorName', '')} {msg.get('text', '')}".lower()
        if query.lower() not in haystack and not all(term in haystack for term in terms):
            continue
        score = float(haystack.count(query.lower()) * 3 + sum(haystack.count(term) for term in terms) or 1)
        hits.append({"message": msg, "snippet": _message_snippet(str(msg.get("text") or ""), query), "score": score})
    hits.sort(key=lambda item: (item["score"], item["message"].get("ts", 0)), reverse=True)
    return {"threadId": thread_id, "query": query, "hits": hits[:limit]}


@app.get("/api/threads/{thread_id}/export", response_model=ThreadExportResponse)
async def export_thread(
    thread_id: str,
    format: Literal["md", "json"] = Query(default="md"),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    exported_at = now_ms()
    async with session_scope() as s:
        messages = await Repo(s).thread_messages(thread_id, limit=limit)
    if format == "json":
        content = json.dumps({"threadId": thread_id, "exportedAt": exported_at, "messages": messages}, ensure_ascii=False, indent=2)
        content_type = "application/json"
        filename = f"{thread_id.replace(':', '_')}.json"
    else:
        content = _thread_export_markdown(thread_id, messages, exported_at)
        content_type = "text/markdown; charset=utf-8"
        filename = f"{thread_id.replace(':', '_')}.md"
    return {
        "threadId": thread_id,
        "format": format,
        "contentType": content_type,
        "filename": filename,
        "content": content,
        "messageCount": len(messages),
        "exportedAt": exported_at,
    }


@app.get("/api/threads/{thread_id}/stats", response_model=ThreadStatsResponse)
async def get_thread_stats(
    thread_id: str,
    after: int | None = Query(default=None),
) -> dict[str, Any]:
    async with session_scope() as s:
        return await Repo(s).thread_stats(thread_id, after_ts=after)


@app.get("/api/threads/{thread_id}/ledger")
async def get_thread_ledger(
    thread_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Append-only conversation ledger for a thread (messages, tools, compaction refs)."""
    async with session_scope() as s:
        rows = await Repo(s).list_entities("conversation_ledger", limit=max(limit * 10, 1000))
    filtered = [
        row
        for row in rows
        if row.get("threadId") == thread_id or (row.get("payload") or {}).get("threadId") == thread_id
    ]
    filtered.sort(key=lambda row: (int(row.get("ts") or 0), str(row.get("id") or "")))
    return filtered[-limit:]


@app.get("/api/ledger")
async def search_conversation_ledger(
    thread_id: str | None = Query(default=None, alias="threadId"),
    department_id: str | None = Query(default=None, alias="departmentId"),
    project_id: str | None = Query(default=None, alias="projectId"),
    task_id: str | None = Query(default=None, alias="taskId"),
    artifact_id: str | None = Query(default=None, alias="artifactId"),
    decision_id: str | None = Query(default=None, alias="decisionId"),
    tool_run_id: str | None = Query(default=None, alias="toolRunId"),
    message_id: str | None = Query(default=None, alias="messageId"),
    branch_thread_id: str | None = Query(default=None, alias="branchThreadId"),
    source_thread_id: str | None = Query(default=None, alias="sourceThreadId"),
    citation_source: str | None = Query(default=None, alias="citationSource"),
    event_type: str | None = Query(default=None, alias="eventType"),
    q: str | None = Query(default=None),
    after: int | None = Query(default=None),
    before: int | None = Query(default=None),
    semantic: bool = Query(default=True),
    semantic_limit: int = Query(default=25, ge=0, le=200, alias="semanticLimit"),
    limit: int = Query(default=100, ge=1, le=1000),
    scan_limit: int = Query(default=5000, ge=100, le=20000, alias="scanLimit"),
) -> dict[str, Any]:
    """Search the append-only ledger by timeline refs plus optional semantic text."""
    from .memory.ledger import ledger_row_matches, rank_ledger_rows

    async with session_scope() as s:
        rows = await Repo(s).list_entities("conversation_ledger", limit=scan_limit)
    filtered = [
        row
        for row in rows
        if ledger_row_matches(
            row,
            thread_id=thread_id,
            department_id=department_id,
            project_id=project_id,
            task_id=task_id,
            artifact_id=artifact_id,
            decision_id=decision_id,
            tool_run_id=tool_run_id,
            message_id=message_id,
            branch_thread_id=branch_thread_id,
            source_thread_id=source_thread_id,
            citation_source=citation_source,
            event_type=event_type,
            after=after,
            before=before,
        )
    ]
    ranked = await rank_ledger_rows(filtered, q, semantic=semantic and semantic_limit > 0, max_semantic_rows=semantic_limit)
    if not q:
        ranked = ranked[-limit:]
    else:
        ranked = ranked[:limit]
    return {
        "ok": True,
        "count": len(ranked),
        "scanned": len(rows),
        "matched": len(filtered),
        "filters": {
            "threadId": thread_id,
            "departmentId": department_id,
            "projectId": project_id,
            "taskId": task_id,
            "artifactId": artifact_id,
            "decisionId": decision_id,
            "toolRunId": tool_run_id,
            "messageId": message_id,
            "branchThreadId": branch_thread_id,
            "sourceThreadId": source_thread_id,
            "citationSource": citation_source,
            "eventType": event_type,
            "q": q,
            "after": after,
            "before": before,
            "semantic": semantic,
            "semanticLimit": semantic_limit,
            "limit": limit,
            "scanLimit": scan_limit,
        },
        "rows": ranked,
    }


@app.post("/api/approvals/{approval_id}/resolve", response_model=Approval)
async def resolve_approval(approval_id: str, input: ResolveApprovalInput) -> dict[str, Any]:
    if input.decision == "pending":
        raise HTTPException(status_code=400, detail="decision must be approved or rejected")
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        approval = await repo.get_approval(approval_id)
        if not approval:
            raise HTTPException(status_code=404, detail="approval not found")
        if approval.get("status") != "pending":
            raise HTTPException(status_code=400, detail="approval is already resolved")
        approval["status"] = input.decision
        executed = False
        if input.decision == "approved":
            executed = await _execute_approval_action(repo, approval)
        elif (approval.get("action") or {}).get("action") == "close_task":
            action = approval["action"]
            await reject_task_close_request(
                repo,
                approval,
                rejected_by=action.get("rejectedBy") or "executive",
                reason=approval.get("detail"),
                now=now,
            )
            action["executedAt"] = now
            executed = True
        elif (approval.get("action") or {}).get("action") == "resolve_project":
            action = approval["action"]
            await _resolve_project_review_action(
                repo,
                project_id=action.get("projectId"),
                artifact_id=action.get("artifactId"),
                task_id=action.get("taskId"),
                approved=False,
                resolved_by="user",
                approval_id=approval["id"],
            )
            executed = True
        elif (approval.get("action") or {}).get("action") == "approve_org_plan":
            action = approval["action"]
            plan = await repo.get_entity("org_plan", action.get("orgPlanId"))
            if plan:
                plan.update({"status": "rejected", "rejectedBy": "user", "updatedAt": now})
                await repo.put_entity("org_plan", plan, status="rejected", ts=now)
                await _record_learning_signal(
                    repo,
                    source="reject",
                    what_went_wrong="ผู้ใช้ไม่อนุมัติ org chart ที่ผู้บริหารเสนอ",
                    lesson_text="ก่อนสร้างแผนกจริง ผู้บริหารต้องสรุปโจทย์ ผังองค์กร เหตุผล และขออนุมัติให้ชัดเจน",
                    applied_to=["preference", "playbook"],
                )
            action["executedAt"] = now
            executed = True
        elif (approval.get("action") or {}).get("action") == "run_tool":
            action = approval["action"]
            run = await repo.get_entity("tool_run", action.get("toolRunId"))
            if run and run.get("status") == "pending_approval":
                run["status"] = "cancelled"
                run["error"] = "approval rejected"
                run["completedAt"] = now
                await _save_tool_run(repo, run)
            action["executedAt"] = now
        if executed and approval.get("action") and not approval["action"].get("executedAt"):
            approval["action"]["executedAt"] = now
        await repo.save_approval(approval)
        await _upsert_approval_chat_message(repo, approval)
        await repo.add_activity(_activity(
            f"{'อนุมัติและดำเนินการ' if executed else 'อนุมัติ' if input.decision == 'approved' else 'ปฏิเสธ'}: {approval['title']}",
            type_="approval",
            department_id=approval.get("departmentId"),
            severity="good" if input.decision == "approved" else "warn",
        ))
    hub.mark_dirty()
    return approval


@app.post("/api/tools/approvals/{approval_id}/resolve", response_model=Approval)
async def resolve_tool_approval(approval_id: str, input: ResolveApprovalInput) -> dict[str, Any]:
    return await resolve_approval(approval_id, input)


def _normalize_objective(obj: dict[str, Any]) -> dict[str, Any]:
    data = dict(obj)
    if "department_id" in data and "departmentId" not in data:
        data["departmentId"] = data.pop("department_id")
    if "last_run_at" in data and "lastRunAt" not in data:
        data["lastRunAt"] = data.pop("last_run_at")
    if "next_run_at" in data and "nextRunAt" not in data:
        data["nextRunAt"] = data.pop("next_run_at")
    data.setdefault("enabled", True)
    data.setdefault("lastRunAt", None)
    if not data.get("nextRunAt") and data.get("cadence"):
        data["nextRunAt"] = next_run_for_cadence(str(data.get("cadence")), now=now_ms())
    return data


def _objective_next_run(cadence: str, raw_next_run_at: int | None, now: int) -> int:
    if raw_next_run_at is not None:
        try:
            return int(raw_next_run_at)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="nextRunAt must be a unix millisecond timestamp") from exc
    next_run = next_run_for_cadence(cadence, now=now)
    if next_run is None:
        raise HTTPException(status_code=400, detail="cadence is required for scheduled objective")
    return next_run


async def _require_objective_department(repo: Repo, department_id: str) -> dict[str, Any]:
    dept = await repo.get_department(department_id)
    if not dept:
        raise HTTPException(status_code=404, detail="department not found")
    return dept


@app.get("/api/objectives", response_model=list[ScheduledObjective])
async def list_objectives(
    dept_id: str | None = Query(default=None, alias="deptId"),
    enabled: bool | None = Query(default=None),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = [_normalize_objective(row) for row in await Repo(s).list_objectives()]
    if dept_id:
        rows = [row for row in rows if row.get("departmentId") == dept_id]
    if enabled is not None:
        rows = [row for row in rows if bool(row.get("enabled")) is enabled]
    rows.sort(key=lambda row: (int(row.get("nextRunAt") or 0), str(row.get("title") or "")))
    return rows


@app.post("/api/objectives", response_model=ScheduledObjective)
async def create_objective(input: CreateObjectiveInput) -> dict[str, Any]:
    now = now_ms()
    title = _clip_text(input.title.strip(), 240)
    cadence = input.cadence.strip()
    department_id = input.department_id.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title cannot be empty")
    if not cadence:
        raise HTTPException(status_code=400, detail="cadence cannot be empty")
    if not department_id:
        raise HTTPException(status_code=400, detail="departmentId cannot be empty")
    objective_id = (input.id or uid("obj")).strip()
    if not objective_id:
        raise HTTPException(status_code=400, detail="objective id cannot be empty")
    async with session_scope() as s:
        repo = Repo(s)
        if await repo.get_objective(objective_id):
            raise HTTPException(status_code=409, detail="objective already exists")
        await _require_objective_department(repo, department_id)
        objective = {
            "id": objective_id,
            "title": title,
            "cadence": cadence,
            "departmentId": department_id,
            "enabled": input.enabled,
            "lastRunAt": None,
            "nextRunAt": _objective_next_run(cadence, input.next_run_at, now),
        }
        await repo.add_objective(objective)
        await repo.add_activity(_activity(
            f"สร้าง objective: {title}",
            type_="autonomous",
            department_id=department_id,
            severity="good",
        ))
    hub.mark_dirty()
    return objective


@app.get("/api/objectives/{objective_id}", response_model=ScheduledObjective)
async def get_objective(objective_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        objective = await Repo(s).get_objective(objective_id)
        if not objective:
            raise HTTPException(status_code=404, detail="objective not found")
        return _normalize_objective(objective)


@app.patch("/api/objectives/{objective_id}", response_model=ScheduledObjective)
async def update_objective(objective_id: str, input: UpdateObjectiveInput) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        objective = await repo.get_objective(objective_id)
        if not objective:
            raise HTTPException(status_code=404, detail="objective not found")
        objective = _normalize_objective(objective)
        if input.title is not None:
            title = _clip_text(input.title.strip(), 240)
            if not title:
                raise HTTPException(status_code=400, detail="title cannot be empty")
            objective["title"] = title
        if input.department_id is not None:
            department_id = input.department_id.strip()
            if not department_id:
                raise HTTPException(status_code=400, detail="departmentId cannot be empty")
            await _require_objective_department(repo, department_id)
            objective["departmentId"] = department_id
        if input.cadence is not None:
            cadence = input.cadence.strip()
            if not cadence:
                raise HTTPException(status_code=400, detail="cadence cannot be empty")
            objective["cadence"] = cadence
            if input.next_run_at is None:
                objective["nextRunAt"] = _objective_next_run(cadence, None, now)
        if input.next_run_at is not None:
            objective["nextRunAt"] = _objective_next_run(objective["cadence"], input.next_run_at, now)
        if input.enabled is not None:
            objective["enabled"] = input.enabled
            if input.enabled and not objective.get("nextRunAt"):
                objective["nextRunAt"] = _objective_next_run(objective["cadence"], None, now)
        await repo.save_objective(objective)
        await repo.add_activity(_activity(
            f"แก้ objective: {objective['title']}",
            type_="autonomous",
            department_id=objective.get("departmentId"),
            severity="info",
        ))
    hub.mark_dirty()
    return objective


@app.post("/api/objectives/{objective_id}/toggle", response_model=ScheduledObjective)
async def toggle_objective(objective_id: str, input: ToggleInput) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        objective = await repo.get_objective(objective_id)
        if not objective:
            raise HTTPException(status_code=404, detail="objective not found")
        objective = _normalize_objective(objective)
        objective["enabled"] = input.enabled
        if input.enabled and not objective.get("nextRunAt"):
            objective["nextRunAt"] = _objective_next_run(objective["cadence"], None, now_ms())
        await repo.save_objective(objective)
        await repo.add_activity(_activity(
            f"{'เปิด' if input.enabled else 'ปิด'} objective: {objective['title']}",
            type_="autonomous",
            department_id=objective.get("departmentId"),
            severity="good" if input.enabled else "warn",
        ))
    hub.mark_dirty()
    return objective


@app.get("/api/cost-report", response_model=CostReport)
async def get_cost_report(
    scope: CostReportScope = Query("day"),
    id: str | None = Query(default=None),
    dept_id: str | None = Query(default=None, alias="deptId"),
) -> dict[str, Any]:
    dept_filter = dept_id or (id if scope in {"agent", "dept"} else None)
    async with session_scope() as s:
        return await Repo(s).cost_report(scope, dept_filter)


@app.get("/api/cost-analytics")
async def get_cost_analytics(
    dept_id: str | None = Query(default=None, alias="deptId"),
) -> dict[str, Any]:
    from .learning.cost_analytics import cost_analytics

    async with session_scope() as s:
        return await cost_analytics(Repo(s), dept_id=dept_id)


@app.post("/api/knowledge/import")
async def import_knowledge(
    department_id: str = Body(..., alias="departmentId"),
    source_kind: str = Body("text", alias="sourceKind"),
    title: str = Body(...),
    text: str | None = Body(default=None),
    url: str | None = Body(default=None),
    path: str | None = Body(default=None),
) -> dict[str, Any]:
    from pathlib import Path

    from .memory.warehouse import import_local_file, import_text_source, import_url_snapshot

    async with session_scope() as s:
        repo = Repo(s)
        if not await repo.get_department(department_id):
            raise HTTPException(status_code=404, detail="department not found")
        if source_kind == "url" and url:
            row = await import_url_snapshot(repo, department_id=department_id, url=url, title=title)
        elif source_kind == "file" and path:
            row = await import_local_file(repo, department_id=department_id, path=Path(path), title=title)
        elif text:
            row = await import_text_source(
                repo,
                department_id=department_id,
                title=title,
                text=text,
                source_uri=path or url or f"inline:{title}",
                source_kind=source_kind,
            )
        else:
            raise HTTPException(status_code=400, detail="provide text, url, or path")
    hub.mark_dirty()
    return row


@app.get("/api/knowledge/search")
async def search_knowledge_warehouse(
    department_id: str = Query(..., alias="departmentId"),
    q: str = Query(..., min_length=1),
    limit: int = Query(default=8, ge=1, le=50),
) -> dict[str, Any]:
    from .memory.warehouse import hybrid_search

    async with session_scope() as s:
        repo = Repo(s)
        if not await repo.get_department(department_id):
            raise HTTPException(status_code=404, detail="department not found")
        hits = await hybrid_search(repo, department_id, q, k=limit)
    return {
        "ok": True,
        "departmentId": department_id,
        "query": q,
        "count": len(hits),
        "hits": hits,
    }


@app.get("/api/eval/summary/{department_id}")
async def get_eval_summary(department_id: str) -> dict[str, Any]:
    from .eval.scoring import department_eval_summary

    async with session_scope() as s:
        if not await Repo(s).get_department(department_id):
            raise HTTPException(status_code=404, detail="department not found")
        return await department_eval_summary(Repo(s), department_id)


@app.get("/api/eval/golden")
async def list_eval_golden_tasks() -> dict[str, Any]:
    from .eval.harness import EvalHarness

    harness = EvalHarness(get_settings())
    return {**harness.status(), "tasks": harness.list_tasks()}


@app.post("/api/eval/golden/{task_id}/judge")
async def judge_eval_golden_task(
    task_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    from .eval.harness import EvalHarness

    response_text = str(body.get("responseText") or body.get("response") or "").strip()
    if not response_text:
        raise HTTPException(status_code=400, detail="responseText is required")
    raw_tools = body.get("toolsUsed") or []
    if not isinstance(raw_tools, list):
        raise HTTPException(status_code=400, detail="toolsUsed must be a list")
    harness = EvalHarness(get_settings())
    async with session_scope() as s:
        repo = Repo(s)
        try:
            row = await harness.judge_and_record(
                repo,
                task_id=task_id,
                response_text=response_text,
                tools_used=[str(tool) for tool in raw_tools if str(tool).strip()],
                department_id=str(body.get("departmentId") or "").strip() or None,
                actor=str(body.get("actor") or "api"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    hub.mark_dirty()
    return row


@app.get("/api/org/capabilities")
async def get_org_capabilities() -> list[dict[str, Any]]:
    from .org.capabilities import build_capability_registry

    async with session_scope() as s:
        registry = await build_capability_registry(Repo(s))
        return registry.catalog()


@app.post("/api/org/capabilities/sync")
async def sync_org_capabilities(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    from .org.capabilities import sync_all_department_capabilities, sync_department_capabilities

    dept_id = str((body or {}).get("departmentId") or "").strip()
    async with session_scope() as s:
        repo = Repo(s)
        if dept_id:
            dept = await repo.get_department(dept_id)
            if not dept:
                raise HTTPException(status_code=404, detail="department not found")
            entity = await sync_department_capabilities(repo, dept, source="api_sync")
            return {"count": 1 if entity else 0, "synced": [entity["id"]] if entity else [], "skipped": []}
        return await sync_all_department_capabilities(repo)


@app.post("/api/org/route")
async def route_task(
    title: str = Body(...),
    detail: str = Body(""),
) -> dict[str, Any]:
    from .org.router import route_task_to_department

    async with session_scope() as s:
        dept, score, note = await route_task_to_department(Repo(s), title=title, detail=detail)
    return {"department": dept, "score": score, "note": note}


@app.post("/api/org/lifecycle/run")
async def run_org_lifecycle() -> dict[str, Any]:
    from .org.lifecycle import process_org_lifecycle

    async with session_scope() as s:
        result = await process_org_lifecycle(Repo(s), now_ms())
    hub.mark_dirty()
    return result


@app.post("/api/org/checkpoints/{checkpoint_id}/rollback")
async def rollback_org(checkpoint_id: str) -> dict[str, Any]:
    from .org.checkpoints import rollback_org_checkpoint

    async with session_scope() as s:
        result = await rollback_org_checkpoint(Repo(s), checkpoint_id)
    hub.mark_dirty()
    return result


@app.post("/api/tools/foundry/design")
async def foundry_design(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from .tools import build_default_tool_registry
    from .tools.foundry import ToolDraft, ToolFoundry

    draft = ToolDraft.from_dict(body)
    return ToolFoundry(build_default_tool_registry()).design(draft)


@app.post("/api/tools/foundry/test")
async def foundry_test(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from .tools import build_default_tool_registry
    from .tools.foundry import ToolDraft, ToolFoundry

    draft = ToolDraft.from_dict(body)
    async with session_scope() as s:
        return await ToolFoundry(build_default_tool_registry()).run_tests(draft)


@app.post("/api/tools/foundry/register")
async def foundry_register(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from .org.checkpoints import create_org_checkpoint, mark_org_checkpoint_applied
    from .tools import build_default_tool_registry
    from .tools.foundry import ToolDraft, ToolFoundry

    draft = ToolDraft.from_dict(body)
    async with session_scope() as s:
        repo = Repo(s)
        checkpoint = await create_org_checkpoint(
            repo,
            reason=f"register tool {draft.name}",
            action="register_tool",
            metadata={"tool": draft.name},
        )
        foundry = ToolFoundry(build_default_tool_registry())
        test = await foundry.run_tests(draft)
        if not test.get("ok"):
            raise HTTPException(status_code=400, detail=test)
        record = await foundry.register(repo, draft, checkpoint_id=checkpoint["id"])
        try:
            from .runtime.factory import get_agent_runtime

            runtime_registration = await get_agent_runtime(get_settings()).register_tool(
                draft.name,
                record.get("catalogRow") if isinstance(record.get("catalogRow"), dict) else {},
            )
        except Exception as exc:
            runtime_registration = {
                "ok": False,
                "tool": draft.name,
                "registered": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        record["runtimeRegistration"] = runtime_registration
        record["history"] = [
            *list(record.get("history") or []),
            {
                "event": "runtime_register",
                "registeredAt": now_ms(),
                "backend": runtime_registration.get("backend"),
                "ok": bool(runtime_registration.get("ok")),
                "registered": bool(runtime_registration.get("registered")),
            },
        ]
        await repo.put_entity("custom_tool", record, status=str(record.get("status") or "active"), ts=now_ms())
        checkpoint = await mark_org_checkpoint_applied(
            repo,
            checkpoint["id"],
            metadata={
                "tool": draft.name,
                "version": record.get("version"),
                "customToolId": record.get("id"),
                "runtimeRegistered": bool(runtime_registration.get("registered")),
            },
        )
    hub.mark_dirty()
    return {"checkpoint": checkpoint, "tool": record, "tests": test}


@app.post("/api/tools/foundry/{tool_name}/rollback")
async def foundry_rollback(tool_name: str, reason: str = Body("deprecated")) -> dict[str, Any]:
    from .tools import build_default_tool_registry
    from .tools.foundry import ToolFoundry

    async with session_scope() as s:
        repo = Repo(s)
        result = await ToolFoundry(build_default_tool_registry()).rollback(repo, tool_name, reason=reason)
        if result.get("ok") and result.get("status") == "active":
            record = await repo.get_entity("custom_tool", tool_name)
            catalog = record.get("catalogRow") if isinstance(record, dict) and isinstance(record.get("catalogRow"), dict) else None
            if record and catalog:
                try:
                    from .runtime.factory import get_agent_runtime

                    runtime_registration = await get_agent_runtime(get_settings()).register_tool(tool_name, catalog)
                except Exception as exc:
                    runtime_registration = {
                        "ok": False,
                        "tool": tool_name,
                        "registered": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                record["runtimeRegistration"] = runtime_registration
                record["history"] = [
                    *list(record.get("history") or []),
                    {
                        "event": "runtime_rollback_register",
                        "registeredAt": now_ms(),
                        "backend": runtime_registration.get("backend"),
                        "ok": bool(runtime_registration.get("ok")),
                        "registered": bool(runtime_registration.get("registered")),
                        "version": record.get("version"),
                    },
                ]
                await repo.put_entity("custom_tool", record, status="active", ts=now_ms())
                result["runtimeRegistration"] = runtime_registration
    hub.mark_dirty()
    return result


@app.get("/api/budget", response_model=Budget)
async def get_budget() -> dict[str, Any]:
    async with session_scope() as s:
        return await Repo(s).get_budget()


@app.patch("/api/budget/company", response_model=Budget)
async def set_company_budget(input: BudgetCapInput) -> dict[str, Any]:
    if input.daily_cap_usd < 0:
        raise HTTPException(status_code=400, detail="daily budget must be non-negative")
    async with session_scope() as s:
        repo = Repo(s)
        await repo.set_cap(input.daily_cap_usd)
        await repo.add_activity(_activity(
            f"ปรับเพดานงบรวมรายวันเป็น ${input.daily_cap_usd:.2f}",
            type_="budget",
            severity="warn",
        ))
        budget = await repo.get_budget()
    hub.mark_dirty()
    return budget


@app.patch("/api/departments/{dept_id}/budget", response_model=Department, include_in_schema=False)
async def set_department_budget(dept_id: str, input: BudgetCapInput) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="per-department budget caps were removed by v0.4 §28; use /api/budget/company and /api/cost-report",
    )


@app.get("/api/departments/{dept_id}/memory", response_model=DepartmentMemory)
async def get_department_memory(dept_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        return await Repo(s).department_memory(dept_id)


@app.patch("/api/departments/{dept_id}/knowledge/{knowledge_id}", response_model=OkResponse)
async def edit_knowledge(dept_id: str, knowledge_id: str, input: EditKnowledgeInput) -> dict[str, bool]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    async with session_scope() as s:
        repo = Repo(s)
        ok = await repo.edit_knowledge(dept_id, knowledge_id, patch)
        if not ok:
            raise HTTPException(status_code=404, detail="knowledge entry not found")
        await _refresh_memory_stats(repo, dept_id)
    hub.mark_dirty()
    return {"ok": True}


@app.delete("/api/departments/{dept_id}/knowledge/{knowledge_id}", response_model=GuardedActionResponse)
async def delete_knowledge(dept_id: str, knowledge_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        dept = await repo.get_department(dept_id)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        memory = await repo.department_memory(dept_id)
        if not any(k["id"] == knowledge_id for k in memory.get("knowledge", [])):
            raise HTTPException(status_code=404, detail="knowledge entry not found")
        approval = await _request_destructive_approval(
            repo,
            title=f"ลบ knowledge {knowledge_id}",
            detail=f"ลบ knowledge entry ของฝ่าย{dept['name']}แบบถาวร",
            department_id=dept_id,
            action={
                "action": "delete_knowledge",
                "departmentId": dept_id,
                "knowledgeId": knowledge_id,
                "requestedBy": "user",
            },
        )
    hub.mark_dirty()
    return {"ok": True, "approval": approval, "executed": True}


@app.get("/api/departments/{dept_id}/workspace/audit", response_model=WorkspaceAuditResponse)
async def audit_department_workspace(dept_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        dept = await Repo(s).get_department(dept_id)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
    path = Path(dept.get("workspacePath") or _provision_workspace(dept_id)).resolve()
    if not _path_inside(path, get_settings().workspace_dir):
        raise HTTPException(status_code=400, detail="workspace path is outside ATRIUM data dir")
    audit = _ensure_git_repo(path)
    return {"departmentId": dept_id, **audit}


@app.get("/api/artifacts", response_model=list[Artifact])
async def list_artifacts(
    dept: str | None = None,
    project: str | None = None,
    status: str | None = None,
    task_id: str | None = Query(default=None, alias="taskId"),
    limit: int = 500,
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        artifacts = await Repo(s).list_entities("artifact", dept=dept, project=project, status=status, limit=limit)
    if task_id:
        artifacts = [a for a in artifacts if task_id in a.get("taskIds", [])]
    return artifacts


@app.post("/api/artifacts", response_model=Artifact)
async def create_artifact(input: CreateArtifactInput) -> dict[str, Any]:
    now = now_ms()
    artifact_id = uid("art")
    created_by = input.created_by or input.owner_dept
    data = input.model_dump(by_alias=True)
    artifact = Artifact(
        id=artifact_id,
        name=input.name,
        kind=input.kind,
        mime=input.mime,
        owner_dept=input.owner_dept,
        task_ids=input.task_ids,
        project_id=input.project_id,
        version=1,
        status="draft",
        uri=input.uri or _entity_uri("artifact", artifact_id),
        tags=input.tags,
        links=input.links,
        preview=input.preview,
        created_at=now,
        created_by=created_by,
        updated_at=now,
        updated_by=created_by,
    ).dump()
    # Preserve explicitly supplied nullable fields from the input model.
    artifact.update({k: v for k, v in data.items() if k in {"mime", "preview"} and v is not None})
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=1,
        author=created_by,
        ts=now,
        note="initial version",
        uri=artifact["uri"],
        preview=artifact.get("preview"),
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        if not await repo.get_department(input.owner_dept):
            raise HTTPException(status_code=404, detail="owner department not found")
        if input.project_id and not await repo.get_entity("project", input.project_id):
            raise HTTPException(status_code=404, detail="project not found")
        await repo.put_entity("artifact", artifact, dept=input.owner_dept, project=input.project_id, status="draft", ts=now)
        await repo.put_entity(
            "artifact_version",
            {**version, "id": f"{artifact_id}:1"},
            dept=input.owner_dept,
            project=input.project_id,
            status="draft",
            ts=now,
        )
        await _link_artifact_to_tasks(repo, artifact_id, input.task_ids)
        await repo.add_activity(_activity(
            f"สร้าง artifact “{artifact['name']}”",
            type_="task_done",
            department_id=input.owner_dept,
            severity="good",
        ))
    hub.mark_dirty()
    return artifact


@app.get("/api/artifacts/{artifact_id}", response_model=Artifact)
async def get_artifact(artifact_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        artifact = await Repo(s).get_entity("artifact", artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        return artifact


@app.get("/api/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    version: int | None = Query(default=None),
    inline: bool = Query(default=False),
) -> Response:
    async with session_scope() as s:
        repo = Repo(s)
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        selected_version = version or int(artifact.get("version") or 1)
        version_row = await _get_artifact_version(repo, artifact_id, selected_version)
    uri = version_row.get("uri") or artifact.get("uri")
    data = bytes_from_uri(uri)
    if data is None:
        raise HTTPException(status_code=404, detail="artifact bytes are not available")
    filename = safe_filename(str(artifact.get("name") or f"{artifact_id}.bin"))
    mime = artifact.get("contentMime") or artifact.get("mime") or "application/octet-stream"
    disposition = "inline" if inline else "attachment"
    return Response(
        data,
        media_type=mime,
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}"},
    )


@app.post("/api/artifacts/{artifact_id}/review-gate", response_model=ArtifactQualityReview)
async def run_artifact_review_gate(
    artifact_id: str,
    reason: str = Query(default="manual-quality-loop", min_length=1, max_length=120),
) -> dict[str, Any]:
    async with session_scope() as s:
        payload = await _artifact_quality_review_payload(Repo(s), artifact_id, reason=reason)
    hub.mark_dirty()
    return payload


@app.patch("/api/artifacts/{artifact_id}", response_model=Artifact)
async def update_artifact(artifact_id: str, input: UpdateArtifactInput) -> dict[str, Any]:
    now = now_ms()
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    async with session_scope() as s:
        repo = Repo(s)
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        updated_by = patch.pop("updatedBy", None) or artifact.get("updatedBy") or artifact.get("ownerDept")
        artifact.update(patch)
        artifact["version"] = int(artifact.get("version", 1)) + 1
        artifact["updatedAt"] = now
        artifact["updatedBy"] = updated_by
        await repo.put_entity(
            "artifact",
            artifact,
            dept=artifact.get("ownerDept"),
            project=artifact.get("projectId"),
            status=artifact.get("status"),
            ts=now,
        )
        version = ArtifactVersion(
            artifact_id=artifact_id,
            version=artifact["version"],
            author=updated_by,
            ts=now,
            note=f"updated {', '.join(sorted(patch.keys())) or 'metadata'}",
            parent=artifact["version"] - 1,
            uri=artifact["uri"],
            preview=artifact.get("preview"),
        ).dump()
        await repo.put_entity(
            "artifact_version",
            {**version, "id": f"{artifact_id}:{artifact['version']}"},
            dept=artifact.get("ownerDept"),
            project=artifact.get("projectId"),
            status=artifact.get("status"),
            ts=now,
        )
        if artifact.get("status") == "in_review" and artifact.get("projectId"):
            project = await repo.get_entity("project", artifact["projectId"])
            if project:
                await _submit_project_artifact_for_user_review(
                    repo,
                    project=project,
                    artifact=artifact,
                    task_id=(artifact.get("taskIds") or [None])[0],
                    requested_by=updated_by,
                )
    hub.mark_dirty()
    return artifact


@app.get("/api/artifacts/{artifact_id}/versions", response_model=list[ArtifactVersion])
async def list_artifact_versions(artifact_id: str) -> list[dict[str, Any]]:
    async with session_scope() as s:
        versions = await Repo(s).list_entities("artifact_version", limit=500)
    return [v for v in versions if v.get("artifactId") == artifact_id]


@app.get("/api/artifacts/{artifact_id}/content", response_model=ArtifactContentResponse)
async def get_artifact_content(
    artifact_id: str,
    version: int | None = Query(default=None),
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        selected_version = version or int(artifact.get("version") or 1)
        version_row = await _get_artifact_version(repo, artifact_id, selected_version)
    preview = version_row.get("preview") or artifact.get("preview")
    preview_text = _preview_content(preview) if isinstance(preview, dict) else None
    return {
        "artifact": artifact,
        "version": version_row,
        "content": preview_text
        if preview_text is not None
        else _read_artifact_text(
            version_row.get("uri") or artifact.get("uri"),
            filename=str(artifact.get("name") or artifact_id),
            mime=artifact.get("contentMime") or artifact.get("mime"),
        ),
    }


@app.get("/api/artifacts/{artifact_id}/preview", response_model=ArtifactPreviewResponse)
async def get_artifact_preview(
    artifact_id: str,
    version: int | None = Query(default=None),
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        selected_version = version or int(artifact.get("version") or 1)
        version_row = await _get_artifact_version(repo, artifact_id, selected_version)
    preview = version_row.get("preview")
    if not preview and selected_version == int(artifact.get("version") or 1):
        preview = artifact.get("preview")
    if not preview:
        raise HTTPException(status_code=404, detail="artifact preview is not available")
    return ArtifactPreviewResponse(
        artifact_id=artifact_id,
        version=selected_version,
        preview=preview,
        content=_preview_content(preview),
    ).dump()


@app.put("/api/artifacts/{artifact_id}/content", response_model=ArtifactContentResponse)
async def put_artifact_content(artifact_id: str, input: ArtifactContentInput) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        author = input.author or artifact.get("updatedBy") or artifact.get("ownerDept")
        before_text = ""
        with contextlib.suppress(Exception):
            before_text = _read_artifact_text(artifact.get("uri"))
        result = await _write_artifact_content_version(
            repo,
            artifact,
            text=input.text,
            author=author,
            note=input.note,
            status=input.status,
            preview_kind=input.preview_kind,
        )
        if str(author).lower() in {"user", "owner"} and before_text:
            ratio = difflib.SequenceMatcher(None, before_text, input.text).ratio()
            size_delta = abs(len(before_text) - len(input.text))
            if ratio < 0.72 or size_delta > 1000:
                await _record_learning_signal(
                    repo,
                    source="heavy_edit",
                    task_id=(artifact.get("taskIds") or [None])[0],
                    artifact_id=artifact_id,
                    what_went_wrong="ผู้ใช้แก้ artifact มากหลังส่งตรวจ",
                    lesson_text="ก่อนส่ง deliverable ต้องเทียบกับ preference ล่าสุด ตรวจ preview และแนบ evidence/assumption ให้ครบ",
                    applied_to=["knowledge", "playbook", "preference"],
                )
        if result["artifact"].get("status") == "in_review" and result["artifact"].get("projectId"):
            project = await repo.get_entity("project", result["artifact"]["projectId"])
            if project:
                await _submit_project_artifact_for_user_review(
                    repo,
                    project=project,
                    artifact=result["artifact"],
                    task_id=(result["artifact"].get("taskIds") or [None])[0],
                    requested_by=author,
                )
    hub.mark_dirty()
    return result


@app.get("/api/artifacts/{artifact_id}/diff", response_model=ArtifactDiffResponse)
async def diff_artifact_versions(
    artifact_id: str,
    from_version: int = Query(alias="fromVersion"),
    to_version: int = Query(alias="toVersion"),
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        if not await repo.get_entity("artifact", artifact_id):
            raise HTTPException(status_code=404, detail="artifact not found")
        from_row = await _get_artifact_version(repo, artifact_id, from_version)
        to_row = await _get_artifact_version(repo, artifact_id, to_version)
    before = _read_artifact_text(from_row.get("uri"))
    after = _read_artifact_text(to_row.get("uri"))
    diff = "\n".join(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"{artifact_id}@v{from_version}",
        tofile=f"{artifact_id}@v{to_version}",
        lineterm="",
    ))
    return {"artifactId": artifact_id, "fromVersion": from_version, "toVersion": to_version, "diff": diff}


@app.post("/api/artifacts/{artifact_id}/rollback", response_model=ArtifactContentResponse)
async def rollback_artifact(artifact_id: str, input: RollbackArtifactInput) -> dict[str, Any]:
    if input.version < 1:
        raise HTTPException(status_code=400, detail="version must be positive")
    async with session_scope() as s:
        repo = Repo(s)
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        version_row = await _get_artifact_version(repo, artifact_id, input.version)
        content = _read_artifact_text(version_row.get("uri"))
        author = input.author or artifact.get("updatedBy") or artifact.get("ownerDept")
        result = await _write_artifact_content_version(
            repo,
            artifact,
            text=content,
            author=author,
            note=f"{input.note} to v{input.version}",
            status=artifact.get("status"),
            preview_kind=(artifact.get("preview") or {}).get("kind", "md"),
        )
    hub.mark_dirty()
    return result


def _decision_dept(decision: dict[str, Any]) -> str | None:
    return decision.get("proposedBy")


def _decision_actor(decision: dict[str, Any]) -> str:
    return decision.get("approvedBy") or decision.get("proposedBy") or "system"


async def _record_decision_event(
    repo: Repo,
    *,
    event: str,
    decision: dict[str, Any],
    related_decision_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ts: int | None = None,
) -> dict[str, Any]:
    stamp = ts or now_ms()
    row: dict[str, Any] = {
        "id": uid("decevt"),
        "decisionId": decision["id"],
        "event": event,
        "actor": _decision_actor(decision),
        "relatedDecisionId": related_decision_id,
        "title": decision.get("title"),
        "status": decision.get("status"),
        "ts": stamp,
        "snapshot": after or decision,
    }
    if before is not None:
        row["before"] = before
    if after is not None:
        row["after"] = after
    return await repo.put_entity(
        "decision_event",
        row,
        dept=_decision_dept(decision),
        status=event,
        ts=stamp,
    )


async def _supersede_prior_decision(repo: Repo, successor: dict[str, Any], *, ts: int) -> None:
    previous_id = successor.get("supersedes")
    if not previous_id:
        return
    if previous_id == successor.get("id"):
        raise HTTPException(status_code=400, detail="decision cannot supersede itself")
    previous = await repo.get_entity("decision", previous_id)
    if not previous:
        raise HTTPException(status_code=404, detail="superseded decision not found")
    if previous.get("status") == "superseded":
        return
    before = dict(previous)
    previous = {**previous, "status": "superseded"}
    await repo.put_entity(
        "decision",
        previous,
        dept=_decision_dept(previous),
        status="superseded",
        ts=previous.get("ts") or ts,
    )
    await _record_decision_event(
        repo,
        event="superseded",
        decision=previous,
        related_decision_id=successor["id"],
        before=before,
        after=previous,
        ts=ts,
    )


@app.get("/api/decisions", response_model=list[Decision])
async def list_decisions(
    dept: str | None = None,
    project: str | None = None,
    status: str | None = None,
    task_id: str | None = Query(default=None, alias="taskId"),
    limit: int = 500,
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        decisions = await Repo(s).list_entities("decision", dept=dept, project=project, status=status, limit=limit)
    if task_id:
        decisions = [d for d in decisions if d.get("linkedTask") == task_id]
    return decisions


@app.post("/api/decisions", response_model=Decision)
async def create_decision(input: CreateDecisionInput) -> dict[str, Any]:
    now = now_ms()
    approved_by = input.approved_by or ("executive" if input.status == "approved" else None)
    decision = Decision(
        id=uid("dec"),
        title=input.title,
        proposed_by=input.proposed_by,
        approved_by=approved_by,
        rationale=_clip_text(input.rationale.strip(), 1000) if input.rationale and input.rationale.strip() else None,
        alternatives=input.alternatives,
        impact=input.impact,
        linked_task=input.linked_task,
        linked_artifacts=input.linked_artifacts,
        status=input.status,
        supersedes=input.supersedes,
        ts=now,
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        await _supersede_prior_decision(repo, decision, ts=now)
        await repo.put_entity(
            "decision",
            decision,
            dept=input.proposed_by,
            status=input.status,
            ts=now,
        )
        await _record_decision_event(
            repo,
            event="created",
            decision=decision,
            related_decision_id=decision.get("supersedes"),
            ts=now,
        )
    hub.mark_dirty()
    return decision


@app.patch("/api/decisions/{decision_id}", response_model=Decision)
async def update_decision(decision_id: str, input: UpdateDecisionInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    async with session_scope() as s:
        repo = Repo(s)
        decision = await repo.get_entity("decision", decision_id)
        if not decision:
            raise HTTPException(status_code=404, detail="decision not found")
        before = dict(decision)
        decision.update(patch)
        decision["ts"] = now_ms()
        await _supersede_prior_decision(repo, decision, ts=decision["ts"])
        await repo.put_entity(
            "decision",
            decision,
            dept=decision.get("proposedBy"),
            status=decision.get("status"),
            ts=decision["ts"],
        )
        await _record_decision_event(
            repo,
            event="updated",
            decision=decision,
            related_decision_id=decision.get("supersedes"),
            before=before,
            after=decision,
            ts=decision["ts"],
        )
    hub.mark_dirty()
    return decision


@app.get("/api/notifications", response_model=list[Notification])
async def list_notifications(
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    limit: int = 500,
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        notifications = await Repo(s).list_entities("notification", limit=limit)
    if unread_only:
        notifications = [n for n in notifications if not n.get("read")]
    return notifications


@app.post("/api/notifications", response_model=Notification)
async def create_notification(input: CreateNotificationInput) -> dict[str, Any]:
    now = now_ms()
    notification = Notification(
        id=uid("notif"),
        type=input.type,
        severity=input.severity,
        title=input.title,
        body=input.body,
        ts=now,
        read=False,
        links=input.links,
    ).dump()
    async with session_scope() as s:
        await Repo(s).put_entity("notification", notification, status="unread", ts=now)
    hub.mark_dirty()
    return notification


@app.patch("/api/notifications/{notification_id}/read", response_model=Notification)
async def set_notification_read(notification_id: str, input: NotificationReadInput) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        notification = await repo.get_entity("notification", notification_id)
        if not notification:
            raise HTTPException(status_code=404, detail="notification not found")
        notification["read"] = input.read
        await repo.put_entity(
            "notification",
            notification,
            status="read" if input.read else "unread",
            ts=notification.get("ts") or now_ms(),
        )
    hub.mark_dirty()
    return notification


@app.post("/api/notifications/read-all", response_model=dict[str, int])
async def mark_all_notifications_read() -> dict[str, int]:
    count = 0
    async with session_scope() as s:
        repo = Repo(s)
        notifications = await repo.list_entities("notification", limit=2000)
        for notification in notifications:
            if notification.get("read"):
                continue
            notification["read"] = True
            await repo.put_entity("notification", notification, status="read", ts=notification.get("ts") or now_ms())
            count += 1
    hub.mark_dirty()
    return {"updated": count}


@app.get("/api/notification-preferences", response_model=NotificationPreferences)
async def get_notification_preferences() -> dict[str, Any]:
    async with session_scope() as s:
        prefs = await Repo(s).get_entity("notification_preferences", NOTIFICATION_PREFS_ID)
    return _normalize_notification_preferences(prefs)


@app.patch("/api/notification-preferences", response_model=NotificationPreferences)
async def set_notification_preferences(input: UpdateNotificationPreferencesInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        prefs = _normalize_notification_preferences(
            await repo.get_entity("notification_preferences", NOTIFICATION_PREFS_ID)
        )
        by_type_patch = patch.get("byType") or {}
        for notif_type, mode in by_type_patch.items():
            if notif_type not in NOTIFICATION_TYPES:
                raise HTTPException(status_code=400, detail=f"unknown notification type: {notif_type}")
            prefs["byType"][notif_type] = mode
        quiet_patch = patch.get("quietHours") or {}
        if "start" in quiet_patch:
            _validate_quiet_time(quiet_patch["start"])
        if "end" in quiet_patch:
            _validate_quiet_time(quiet_patch["end"])
        prefs["quietHours"].update({
            key: value
            for key, value in quiet_patch.items()
            if key in {"enabled", "start", "end", "timezone"} and value is not None
        })
        prefs["updatedAt"] = now
        await repo.put_entity("notification_preferences", prefs, status="active", ts=now)
    hub.mark_dirty()
    return prefs


@app.get("/api/handoffs/{handoff_id}/messages", response_model=list[HandoffMessage])
async def list_handoff_messages(handoff_id: str, limit: int = 200) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = await Repo(s).list_entities("handoff_message", status=handoff_id, limit=limit)
    return list(reversed(rows))


@app.post("/api/handoffs/{handoff_id}/messages", response_model=HandoffMessage)
async def create_handoff_message(handoff_id: str, input: CreateHandoffMessageInput) -> dict[str, Any]:
    now = now_ms()
    if not input.task_id:
        raise HTTPException(status_code=400, detail="taskId is required for handoff messages")
    async with session_scope() as s:
        repo = Repo(s)
        task = await repo.get_task(input.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        handoff = _find_task_handoff(task, handoff_id)
        if not handoff:
            raise HTTPException(status_code=404, detail="handoff not found for task")
        if input.from_ not in handoff_participants(handoff):
            raise HTTPException(status_code=400, detail="actor is not a handoff participant")
        msg = await _persist_handoff_message(
            repo,
            handoff,
            from_actor=input.from_,
            act=input.act,
            text=input.text,
            now=now,
            task_id=input.task_id,
        )
        await _apply_handoff_message_to_tasks(
            repo,
            handoff_id,
            msg,
            status=handoff_status_for_act(input.act),
            now=now,
        )
        source_dept = await repo.get_department(str(handoff.get("fromDept") or ""))
        target_dept = await repo.get_department(str(handoff.get("toDept") or ""))
        event = {
            "request": "handoff_requested",
            "accept": "handoff_accepted",
            "clarify": "handoff_clarify",
            "reply": "handoff_reply",
            "deliver": "handoff_delivered",
            "return": "handoff_returned",
            "reject": "handoff_rejected",
        }.get(input.act, f"handoff_{input.act}")
        actor_name = await _actor_display_name(repo, input.from_)
        await emit_work_status_notice(
            repo,
            event=event,
            summary=f"{actor_name} {visibility_event_label(event)}: {_clip_text(input.text, 260) or '-'}",
            source_dept=source_dept or handoff.get("fromDept"),
            target_dept=target_dept or handoff.get("toDept"),
            task=task,
            handoff_id=handoff_id,
            severity="warn" if input.act in {"reject", "clarify"} else "good" if input.act in {"accept", "deliver"} else "info",
            now=now,
            dedupe_key=f"{event}:{handoff_id}:{msg['id']}",
        )
        await repo.add_activity(_activity(
            f"handoff {handoff_id}: {input.act}",
            type_="handoff",
            department_id=input.from_,
        ))
    hub.mark_dirty()
    return msg


@app.get("/api/bulletins", response_model=list[Bulletin])
async def list_bulletins(limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await Repo(s).list_entities("bulletin", limit=limit)


@app.post("/api/bulletins", response_model=Bulletin)
async def create_bulletin(input: CreateBulletinInput) -> dict[str, Any]:
    now = now_ms()
    bulletin = Bulletin(
        id=uid("bul"),
        title=input.title,
        body=input.body,
        scope=input.scope,
        author=input.author,
        approved_by=input.approved_by,
        ts=now,
        pinned=input.pinned,
        expires_at=input.expires_at,
        links=input.links,
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        await repo.put_entity("bulletin", bulletin, dept=input.author, status="pinned" if input.pinned else "active", ts=now)
        await repo.add_activity(_activity(
            f"ประกาศ company bulletin: {input.title}",
            type_="system",
            department_id=input.author,
            severity="good",
        ))
    hub.mark_dirty()
    return bulletin


@app.get("/api/departments/{dept_id}/peek", response_model=PeekDepartmentResponse)
async def peek_department(
    dept_id: str,
    viewer: str = Query("executive"),
    include_knowledge: bool = Query(False, alias="includeKnowledge"),
) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        dept = await repo.get_department(dept_id)
        if not dept:
            raise HTTPException(status_code=404, detail="department not found")
        tasks = await repo.tasks_for_dept(dept_id)
        artifacts = await repo.list_entities("artifact", dept=dept_id, limit=200)
        knowledge = await repo.list_knowledge(dept_id, limit=20) if include_knowledge else []
        log = {
            "id": uid("peek"),
            "viewer": viewer,
            "departmentId": dept_id,
            "includeKnowledge": include_knowledge,
            "ts": now,
        }
        await repo.put_entity("peek_log", log, dept=dept_id, status="knowledge" if include_knowledge else "public", ts=now)
        await repo.add_activity(_activity(
            f"{viewer} peeked ฝ่าย{dept.get('name', dept_id)}",
            type_="system",
            department_id=dept_id,
        ))
    return {"department": dept, "tasks": tasks, "artifacts": artifacts, "knowledge": knowledge, "logId": log["id"]}


@app.get("/api/onboarding/org-plans", response_model=list[OrgPlan])
async def list_org_plans(
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await Repo(s).list_entities("org_plan", status=status, limit=limit)


@app.post("/api/onboarding/org-plans", response_model=OrgPlan)
async def create_org_plan(input: CreateOrgPlanInput) -> dict[str, Any]:
    if not input.departments:
        raise HTTPException(status_code=400, detail="org plan requires at least one department")
    now = now_ms()
    departments = [_org_department_spec(item.model_dump(by_alias=True)) for item in input.departments]
    plan = OrgPlan(
        id=uid("org"),
        objective=_clip_text(input.objective, 4000),
        interview_summary=_clip_text(input.interview_summary, 6000),
        status="approved",
        departments=departments,
        created_by=input.created_by,
        created_at=now,
        updated_at=now,
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        decision = Decision(
            id=uid("dec"),
            title=f"สร้างผังองค์กรแบบ Full Auto: {plan['objective'][:120]}",
            proposed_by=input.created_by,
            approved_by=input.created_by,
            rationale="ผู้บริหารสรุปโจทย์จาก onboarding interview แล้ว apply org chart ทันทีตาม Full Auto",
            alternatives=[f"{item['name']}: {item['role']}" for item in departments],
            impact=f"orgPlan={plan['id']}; departments={len(departments)}",
            linked_task=None,
            linked_artifacts=[],
            status="approved",
            ts=now,
        ).dump()
        plan["decisionId"] = decision["id"]
        await repo.put_entity("org_plan", plan, status="approved", ts=now)
        await repo.put_entity("decision", decision, status=decision["status"], ts=now)
        await repo.add_activity(_activity(
            f"สร้าง org chart {len(departments)} แผนกแบบ Full Auto",
            type_="system",
            severity="good",
        ))
        plan = await _apply_org_plan(repo, plan, approved_by=input.created_by)
    hub.mark_dirty()
    return plan


@app.get("/api/onboarding/org-plans/{plan_id}", response_model=OrgPlan)
async def get_org_plan(plan_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        plan = await Repo(s).get_entity("org_plan", plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="org plan not found")
    return plan


@app.patch("/api/onboarding/org-plans/{plan_id}", response_model=OrgPlan)
async def update_org_plan(plan_id: str, input: UpdateOrgPlanInput) -> dict[str, Any]:
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        plan = await repo.get_entity("org_plan", plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="org plan not found")
        if plan.get("status") != "proposed":
            raise HTTPException(status_code=409, detail="only proposed org plans can be edited")

        if input.objective is not None:
            objective = input.objective.strip()
            if not objective:
                raise HTTPException(status_code=400, detail="objective cannot be empty")
            plan["objective"] = _clip_text(objective, 4000)
        if input.interview_summary is not None:
            plan["interviewSummary"] = _clip_text(input.interview_summary.strip(), 6000)
        if input.departments is not None:
            if not input.departments:
                raise HTTPException(status_code=400, detail="org plan requires at least one department")
            plan["departments"] = [_org_department_spec(item.model_dump(by_alias=True)) for item in input.departments]
        if not plan.get("departments"):
            raise HTTPException(status_code=400, detail="org plan requires at least one department")

        plan["updatedAt"] = now
        await repo.put_entity("org_plan", plan, status="proposed", ts=now)

        approval_id = plan.get("approvalId")
        approval = await repo.get_approval(approval_id) if approval_id else None
        if approval and approval.get("status") == "pending":
            approval["title"] = f"อนุมัติ org chart: {plan['objective'][:90]}"
            approval["detail"] = (
                f"ผู้ใช้ปรับผังองค์กรล่าสุดเป็น {len(plan['departments'])} แผนก "
                "เมื่ออนุมัติแล้วระบบจะสร้างทุกแผนกตาม spec ปัจจุบัน"
            )
            action = approval.get("action") or {}
            action["updatedBy"] = input.updated_by
            action["updatedAt"] = now
            approval["action"] = action
            await repo.save_approval(approval)
            await _upsert_approval_chat_message(repo, approval)

        decision_id = plan.get("decisionId")
        decision = await repo.get_entity("decision", decision_id) if decision_id else None
        if decision and decision.get("status") == "proposed":
            decision["title"] = f"เสนอผังองค์กร: {plan['objective'][:120]}"
            decision["alternatives"] = [f"{item['name']}: {item['role']}" for item in plan["departments"]]
            decision["impact"] = f"orgPlan={plan['id']}; departments={len(plan['departments'])}; revisedBy={input.updated_by}"
            decision["ts"] = now
            await repo.put_entity("decision", decision, status="proposed", ts=now)

        await repo.add_activity(_activity(
            f"ปรับ org chart {plan_id} เป็น {len(plan['departments'])} แผนก",
            type_="approval",
            severity="info",
        ))
    hub.mark_dirty()
    return plan


@app.post("/api/onboarding/org-plans/{plan_id}/resolve", response_model=OrgPlan)
async def resolve_org_plan(plan_id: str, input: ResolveOrgPlanInput) -> dict[str, Any]:
    if input.decision == "pending":
        raise HTTPException(status_code=400, detail="decision must be approved or rejected")
    now = now_ms()
    async with session_scope() as s:
        repo = Repo(s)
        plan = await repo.get_entity("org_plan", plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="org plan not found")
        approval_id = plan.get("approvalId")
        if approval_id:
            approval = await repo.get_approval(approval_id)
            if approval and approval.get("status") == "pending":
                approval["status"] = input.decision
                approval["action"] = {
                    **(approval.get("action") or {}),
                    "approvedBy": input.resolved_by if input.decision == "approved" else None,
                    "executedAt": now,
                }
                await repo.save_approval(approval)
                await _upsert_approval_chat_message(repo, approval)
        if input.decision == "approved":
            plan = await _apply_org_plan(repo, plan, approved_by=input.resolved_by, approval_id=approval_id)
        else:
            plan.update({"status": "rejected", "rejectedBy": input.resolved_by, "updatedAt": now})
            await repo.put_entity("org_plan", plan, status="rejected", ts=now)
            await _record_learning_signal(
                repo,
                source="reject",
                what_went_wrong="ผู้ใช้ปฏิเสธ org chart ใน onboarding",
                lesson_text="เสนอ org chart ต้องมีเหตุผลรายแผนกและถามยืนยันก่อนสร้างแผนกจริง",
                applied_to=["preference", "playbook"],
            )
        await repo.add_activity(_activity(
            f"{'อนุมัติ' if input.decision == 'approved' else 'ปฏิเสธ'} org chart {plan_id}",
            type_="approval",
            severity="good" if input.decision == "approved" else "warn",
        ))
    hub.mark_dirty()
    return plan


@app.get("/api/projects", response_model=list[Project])
async def list_projects(status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await Repo(s).list_entities("project", status=status, limit=limit)


@app.post("/api/projects", response_model=Project)
async def create_project(input: CreateProjectInput) -> dict[str, Any]:
    now = now_ms()
    project_id = input.id or uid("proj")
    project = Project(
        id=project_id,
        name=input.name,
        goal=input.goal,
        brief=input.brief,
        shared_notes=input.shared_notes,
        status="active",
        departments=input.departments,
        lead=input.lead,
        workspace_path=_provision_project_workspace(project_id),
        created_at=now,
        deliverable_artifact_id=None,
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        await _validate_departments(repo, input.departments + ([input.lead] if input.lead else []))
        await repo.put_entity("project", project, project=project_id, status="active", ts=now)
        decision = Decision(
            id=uid("dec"),
            title=f"เปิดโปรเจกต์ {input.name}",
            proposed_by=input.lead or "executive",
            approved_by="executive",
            rationale="เปิด project-scoped workspace สำหรับงานข้ามแผนก",
            alternatives=[],
            impact=f"workspace: {project['workspacePath']}",
            linked_task=None,
            linked_artifacts=[],
            status="approved",
            ts=now,
        ).dump()
        await repo.put_entity("decision", decision, project=project_id, status="approved", ts=now)
        await repo.add_activity(_activity(f"เปิดโปรเจกต์ {input.name}", type_="system", severity="good"))
    hub.mark_dirty()
    return project


@app.get("/api/projects/{project_id}", response_model=Project)
async def get_project(project_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        project = await Repo(s).get_entity("project", project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        return project


@app.patch("/api/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, input: UpdateProjectInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    async with session_scope() as s:
        repo = Repo(s)
        project = await repo.get_entity("project", project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        if "departments" in patch:
            await _validate_departments(repo, patch["departments"])
        if patch.get("lead"):
            await _validate_departments(repo, [patch["lead"]])
        if patch.get("status") == "done":
            await _assert_project_can_be_marked_done(repo, project, patch.get("deliverableArtifactId"))
        project.update(patch)
        await repo.put_entity("project", project, project=project_id, status=project.get("status"), ts=now_ms())
    hub.mark_dirty()
    return project


@app.post("/api/projects/{project_id}/resolve", response_model=Project)
async def resolve_project(project_id: str, input: ResolveProjectInput) -> dict[str, Any]:
    if input.decision == "pending":
        raise HTTPException(status_code=400, detail="decision must be approved or rejected")
    async with session_scope() as s:
        repo = Repo(s)
        project = await repo.get_entity("project", project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        resolved = await _resolve_project_review_action(
            repo,
            project_id=project_id,
            artifact_id=input.deliverable_artifact_id or project.get("deliverableArtifactId"),
            task_id=input.task_id,
            approved=input.decision == "approved",
            resolved_by=input.resolved_by,
            approval_id=project.get("finalApprovalId"),
        )
    hub.mark_dirty()
    return resolved


def _normalize_war_room(war_room: dict[str, Any]) -> dict[str, Any]:
    out = dict(war_room)
    room_id = str(out.get("id") or "")
    if room_id and not out.get("threadId"):
        out["threadId"] = war_room_thread_id(room_id)
    return out


async def _seed_war_room_thread(
    repo: Repo,
    war_room: dict[str, Any],
    departments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    room = _normalize_war_room(war_room)
    thread_id = str(room.get("threadId") or "")
    if not thread_id:
        return None
    if await repo.thread_messages(thread_id, limit=1):
        return None
    participants = war_room_participants(room, departments)
    names = [str(dept.get("agentName") or dept.get("name") or dept.get("id")) for dept in participants]
    roster = ", ".join(names) if names else "ยังไม่มีสมาชิกที่พร้อมตอบ"
    msg = system_chat_message(
        thread_id,
        f"War room “{room.get('title', room.get('id'))}” พร้อมทำงานร่วมกับ {roster}",
        flow=war_room_flow(room, participants),
        war_room_id=str(room.get("id") or ""),
        severity="good" if participants else "warn",
        ts=int(room.get("createdAt") or now_ms()),
    )
    await repo.add_message(msg)
    return msg


async def _war_room_collaboration_payload(
    repo: Repo,
    war_room: dict[str, Any],
    *,
    limit: int = 200,
) -> dict[str, Any]:
    room = _normalize_war_room(war_room)
    departments = await repo.list_departments()
    participants = war_room_participants(room, departments)
    thread_id = str(room.get("threadId") or war_room_thread_id(str(room.get("id") or "")))
    messages = await repo.thread_messages(thread_id, limit=limit)
    return {
        "warRoom": room,
        "threadId": thread_id,
        "participants": [agent_snapshot(dept) for dept in participants],
        "flow": war_room_flow(room, participants),
        "context": war_room_context(room, participants),
        "messages": messages,
    }


@app.get("/api/war-rooms", response_model=list[WarRoom])
async def list_war_rooms(project: str | None = None, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rooms = await Repo(s).list_entities("war_room", project=project, status=status, limit=limit)
    return [_normalize_war_room(room) for room in rooms]


@app.get("/api/war-rooms/{war_room_id}", response_model=WarRoom)
async def get_war_room(war_room_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        war_room = await Repo(s).get_entity("war_room", war_room_id)
    if not war_room:
        raise HTTPException(status_code=404, detail="war room not found")
    return _normalize_war_room(war_room)


@app.get("/api/war-rooms/{war_room_id}/collaboration", response_model=WarRoomCollaboration)
async def get_war_room_collaboration(
    war_room_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        war_room = await repo.get_entity("war_room", war_room_id)
        if not war_room:
            raise HTTPException(status_code=404, detail="war room not found")
        return await _war_room_collaboration_payload(repo, war_room, limit=limit)


@app.post("/api/war-rooms", response_model=WarRoom)
async def create_war_room(input: CreateWarRoomInput) -> dict[str, Any]:
    now = now_ms()
    war_room_id = uid("war")
    war_room = WarRoom(
        id=war_room_id,
        title=input.title,
        goal=input.goal,
        thread_id=war_room_thread_id(war_room_id),
        lead=input.lead,
        members=input.members,
        task_id=input.task_id,
        project_id=input.project_id,
        status="active",
        scratchpad=input.scratchpad,
        decisions=[],
        artifacts=[],
        created_at=now,
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        await _validate_departments(repo, input.members + ([input.lead] if input.lead else []))
        departments = await repo.list_departments()
        await repo.put_entity("war_room", war_room, project=input.project_id, status="active", ts=now)
        intro = await _seed_war_room_thread(repo, war_room, departments)
        await repo.add_activity(_activity(f"เปิด war room: {input.title}", type_="system", severity="good"))
        if intro:
            hub.pulse({
                "kind": "chat_activity",
                "threadId": war_room["threadId"],
                "msgId": intro["id"],
                "message": intro,
            })
    hub.mark_dirty()
    return war_room


@app.patch("/api/war-rooms/{war_room_id}", response_model=WarRoom)
async def update_war_room(war_room_id: str, input: UpdateWarRoomInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    for list_field in ("members", "decisions", "artifacts"):
        if list_field in patch and patch[list_field] is None:
            patch[list_field] = []
    if patch.get("scratchpad") is None and "scratchpad" in patch:
        patch["scratchpad"] = ""
    if patch.get("status") is None and "status" in patch:
        patch.pop("status")
    async with session_scope() as s:
        repo = Repo(s)
        war_room = await repo.get_entity("war_room", war_room_id)
        if not war_room:
            raise HTTPException(status_code=404, detail="war room not found")
        if "members" in patch:
            await _validate_departments(repo, patch["members"])
        if patch.get("lead"):
            await _validate_departments(repo, [patch["lead"]])
        participants_changed = "members" in patch or "lead" in patch
        war_room = _normalize_war_room(war_room)
        war_room.update(patch)
        await repo.put_entity(
            "war_room",
            war_room,
            project=war_room.get("projectId"),
            status=war_room.get("status"),
            ts=now_ms(),
        )
        if participants_changed:
            departments = await repo.list_departments()
            participants = war_room_participants(war_room, departments)
            names = ", ".join(str(dept.get("agentName") or dept.get("name") or dept.get("id")) for dept in participants)
            await _add_chat_system_line(
                repo,
                str(war_room.get("threadId") or war_room_thread_id(war_room_id)),
                f"War room “{war_room.get('title', war_room_id)}” อัปเดตสมาชิก: {names or 'ไม่มีสมาชิกที่พร้อมตอบ'}",
                flow=war_room_flow(war_room, participants),
                war_room_id=war_room_id,
                severity="good" if participants else "warn",
            )
    hub.mark_dirty()
    return war_room


@app.get("/api/skills", response_model=list[Skill])
async def list_skills(limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await Repo(s).list_entities("skill", limit=limit)


@app.post("/api/skills", response_model=Skill)
async def create_skill(input: CreateSkillInput) -> dict[str, Any]:
    skill = Skill(
        id=input.id or uid("skill"),
        name=input.name,
        description=input.description,
        tools=input.tools,
        suggested_model=input.suggested_model,
        cost_profile=input.cost_profile,
    ).dump()
    async with session_scope() as s:
        await Repo(s).put_entity("skill", skill, ts=now_ms())
    hub.mark_dirty()
    return skill


@app.get("/api/preferences", response_model=list[Preference])
async def list_preferences(category: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        prefs = await Repo(s).list_entities("preference", status=category, limit=limit)
    return prefs


@app.post("/api/preferences", response_model=Preference)
async def create_preference(input: CreatePreferenceInput) -> dict[str, Any]:
    pref = Preference(
        id=input.id or uid("pref"),
        category=input.category,
        text=input.text,
        confidence=input.confidence,
        source=input.source,
        ts=now_ms(),
    ).dump()
    async with session_scope() as s:
        await Repo(s).put_entity("preference", pref, status=input.category, ts=pref["ts"])
    hub.mark_dirty()
    return pref


@app.patch("/api/preferences/{preference_id}", response_model=Preference)
async def update_preference(preference_id: str, input: UpdatePreferenceInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    async with session_scope() as s:
        repo = Repo(s)
        pref = await repo.get_entity("preference", preference_id)
        if not pref:
            raise HTTPException(status_code=404, detail="preference not found")
        pref.update({k: v for k, v in patch.items() if v is not None})
        pref["ts"] = now_ms()
        await repo.put_entity("preference", pref, status=pref.get("category"), ts=pref["ts"])
    hub.mark_dirty()
    return pref


@app.delete("/api/preferences/{preference_id}", response_model=OkResponse)
async def delete_preference(preference_id: str) -> dict[str, bool]:
    async with session_scope() as s:
        repo = Repo(s)
        pref = await repo.get_entity("preference", preference_id)
        if not pref:
            raise HTTPException(status_code=404, detail="preference not found")
        await repo.delete_entity("preference", preference_id)
    hub.mark_dirty()
    return {"ok": True}


@app.get("/api/owner-profile", response_model=OwnerProfile)
async def get_owner_profile() -> dict[str, Any]:
    async with session_scope() as s:
        prefs = await Repo(s).list_entities("preference", limit=1000)
    return _owner_profile_from_preferences(prefs)


@app.post("/api/owner-profile/preferences", response_model=Preference)
async def upsert_owner_profile_preference(input: CreatePreferenceInput) -> dict[str, Any]:
    return await create_preference(input)


@app.get("/api/triggers", response_model=list[Trigger])
async def list_triggers(kind: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await Repo(s).list_entities("trigger", status=kind, limit=limit)


@app.post("/api/triggers", response_model=Trigger)
async def create_trigger(input: CreateTriggerInput) -> dict[str, Any]:
    cadence = resolve_trigger_cadence(input.cadence or cadence_from_schedule_object(input.schedule), input.title)
    if input.kind == "cron" and not cadence and not input.one_shot_at:
        raise HTTPException(status_code=400, detail="cron trigger requires cadence or oneShotAt")
    if input.kind == "event" and not input.event:
        raise HTTPException(status_code=400, detail="event trigger requires event")
    now = now_ms()
    trigger = Trigger(
        id=input.id or uid("trig"),
        title=input.title,
        kind=input.kind,
        cadence=cadence,
        one_shot_at=input.one_shot_at,
        event=input.event,
        target=input.target,
        enabled=input.enabled,
        last_run_at=None,
        next_run_at=_next_run_for(cadence, input.one_shot_at),
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        dept_id = _dept_id_from_target(input.target)
        if dept_id:
            await _validate_departments(repo, [dept_id])
        await repo.put_entity("trigger", trigger, status=input.kind, ts=now)
    hub.mark_dirty()
    return trigger


@app.patch("/api/triggers/{trigger_id}", response_model=Trigger)
async def update_trigger(trigger_id: str, input: UpdateTriggerInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    schedule = patch.pop("schedule", None)
    async with session_scope() as s:
        repo = Repo(s)
        trigger = await repo.get_entity("trigger", trigger_id)
        if not trigger:
            raise HTTPException(status_code=404, detail="trigger not found")
        trigger.update(patch)
        if schedule is not None:
            cadence = cadence_from_schedule_object(schedule)
            if cadence:
                trigger["cadence"] = cadence
        if trigger.get("kind") == "cron":
            cadence = resolve_trigger_cadence(trigger.get("cadence"), trigger.get("title"))
            if cadence:
                trigger["cadence"] = cadence
            if not cadence and not trigger.get("oneShotAt"):
                raise HTTPException(status_code=400, detail="cron trigger requires cadence or oneShotAt")
        if trigger.get("kind") == "event" and not trigger.get("event"):
            raise HTTPException(status_code=400, detail="event trigger requires event")
        if "target" in patch:
            dept_id = _dept_id_from_target(trigger["target"])
            if dept_id:
                await _validate_departments(repo, [dept_id])
        if ("cadence" in patch or schedule is not None or "oneShotAt" in patch or "title" in patch) and "nextRunAt" not in patch:
            trigger["nextRunAt"] = _next_run_for(trigger.get("cadence"), trigger.get("oneShotAt"))
        await repo.put_entity("trigger", trigger, status=trigger.get("kind"), ts=now_ms())
    hub.mark_dirty()
    return trigger


@app.get("/api/meetings", response_model=list[Meeting])
async def list_meetings(project: str | None = None, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = await Repo(s).list_entities("meeting", project=project, status=status, limit=limit)
    return [_normalize_meeting(row) for row in rows]


def _normalize_meeting(meeting: dict[str, Any]) -> dict[str, Any]:
    out = dict(meeting)
    meeting_id = str(out.get("id") or "")
    if meeting_id and not out.get("threadId"):
        out["threadId"] = meeting_thread_id(meeting_id)
    return out


async def _seed_meeting_thread(
    repo: Repo,
    meeting: dict[str, Any],
    departments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    item = _normalize_meeting(meeting)
    thread_id = str(item.get("threadId") or "")
    if not thread_id:
        return None
    if await repo.thread_messages(thread_id, limit=1):
        return None
    participants = meeting_participants(item, departments)
    roster = ", ".join(str(dept.get("agentName") or dept.get("name") or dept.get("id")) for dept in participants)
    msg = system_chat_message(
        thread_id,
        f"Meeting “{item.get('title', item.get('id'))}” พร้อมประชุมกับ {roster or 'ยังไม่มีผู้เข้าร่วมที่พร้อมตอบ'}",
        flow=meeting_flow(item, participants),
        meeting_id=str(item.get("id") or ""),
        severity="good" if participants else "warn",
        ts=int(item.get("ts") or now_ms()),
    )
    await repo.add_message(msg)
    return msg


async def _meeting_collaboration_payload(
    repo: Repo,
    meeting: dict[str, Any],
    *,
    limit: int = 200,
) -> dict[str, Any]:
    item = _normalize_meeting(meeting)
    departments = await repo.list_departments()
    participants = meeting_participants(item, departments)
    thread_id = str(item.get("threadId") or meeting_thread_id(str(item.get("id") or "")))
    return {
        "meeting": item,
        "threadId": thread_id,
        "participants": [agent_snapshot(dept) for dept in participants],
        "flow": meeting_flow(item, participants),
        "context": meeting_context(item, participants),
        "messages": await repo.thread_messages(thread_id, limit=limit),
    }


@app.get("/api/meetings/{meeting_id}", response_model=Meeting)
async def get_meeting(meeting_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        meeting = await Repo(s).get_entity("meeting", meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="meeting not found")
    return _normalize_meeting(meeting)


@app.get("/api/meetings/{meeting_id}/collaboration", response_model=MeetingCollaboration)
async def get_meeting_collaboration(
    meeting_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        meeting = await repo.get_entity("meeting", meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="meeting not found")
        return await _meeting_collaboration_payload(repo, meeting, limit=limit)


@app.post("/api/meetings", response_model=Meeting)
async def create_meeting(input: CreateMeetingInput) -> dict[str, Any]:
    now = now_ms()
    action_ids: list[str] = []
    async with session_scope() as s:
        repo = Repo(s)
        await _validate_departments(repo, [p for p in input.participants if p not in {"executive", "devil_advocate"}])
        assignee = next((p for p in input.participants if p not in {"executive", "devil_advocate"}), None)
        for title in input.action_items:
            if not assignee:
                continue
            task_id = uid("task")
            task = {
                "id": task_id,
                "title": title[:80],
                "detail": f"action item from meeting: {input.title}",
                "status": "assigned",
                "priority": "normal",
                "departmentId": assignee,
                "origin": {"kind": "executive"},
                "progress": 0,
                "createdAt": now,
                "updatedAt": now,
                "handoffs": [],
                "log": ["สร้างจาก meeting action item"],
                "projectId": input.project_id,
                "deliverables": [],
                "watchers": [*input.participants],
                "parentTaskId": None,
                "subTaskIds": [],
                "deadlineAt": None,
                "result": None,
            }
            await repo.save_task(task)
            action_ids.append(task_id)
        meeting_id = uid("meet")
        meeting = Meeting(
            id=meeting_id,
            title=input.title,
            thread_id=meeting_thread_id(meeting_id),
            project_id=input.project_id,
            agenda=input.agenda,
            participants=input.participants,
            notes=input.notes,
            decisions=[],
            action_items=action_ids,
            status=input.status,
            ts=now,
        ).dump()
        departments = await repo.list_departments()
        await repo.put_entity("meeting", meeting, project=input.project_id, status=input.status, ts=now)
        intro = await _seed_meeting_thread(repo, meeting, departments)
        await repo.add_activity(_activity(f"สร้าง meeting: {input.title}", type_="system", severity="good"))
        if intro:
            hub.pulse({
                "kind": "chat_activity",
                "threadId": meeting["threadId"],
                "msgId": intro["id"],
                "message": intro,
            })
    hub.mark_dirty()
    return meeting


@app.patch("/api/meetings/{meeting_id}", response_model=Meeting)
async def update_meeting(meeting_id: str, input: UpdateMeetingInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    for list_field in ("decisions", "actionItems"):
        if list_field in patch and patch[list_field] is None:
            patch[list_field] = []
    if patch.get("notes") is None and "notes" in patch:
        patch["notes"] = ""
    if patch.get("status") is None and "status" in patch:
        patch.pop("status")
    async with session_scope() as s:
        repo = Repo(s)
        meeting = await repo.get_entity("meeting", meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="meeting not found")
        meeting = _normalize_meeting(meeting)
        meeting.update(patch)
        await repo.put_entity("meeting", meeting, project=meeting.get("projectId"), status=meeting.get("status"), ts=now_ms())
        if patch:
            departments = await repo.list_departments()
            participants = meeting_participants(meeting, departments)
            await _add_chat_system_line(
                repo,
                str(meeting.get("threadId") or meeting_thread_id(meeting_id)),
                f"Meeting “{meeting.get('title', meeting_id)}” อัปเดต: {', '.join(sorted(patch.keys()))}",
                flow=meeting_flow(meeting, participants),
                meeting_id=meeting_id,
                severity="good",
            )
    hub.mark_dirty()
    return meeting


@app.get("/api/playbooks", response_model=list[Playbook])
async def list_playbooks(limit: int = 500) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await Repo(s).list_entities("playbook", limit=limit)


@app.post("/api/playbooks", response_model=Playbook)
async def create_playbook(input: CreatePlaybookInput) -> dict[str, Any]:
    playbook = Playbook(
        id=input.id or uid("play"),
        name=input.name,
        when_to_use=input.when_to_use,
        steps=input.steps,
        required_skills=input.required_skills,
        deliverable_spec=input.deliverable_spec,
        quality_checklist=input.quality_checklist,
        examples=input.examples,
        version=1,
    ).dump()
    async with session_scope() as s:
        await Repo(s).put_entity("playbook", playbook, ts=now_ms())
    hub.mark_dirty()
    return playbook


@app.patch("/api/playbooks/{playbook_id}", response_model=Playbook)
async def update_playbook(playbook_id: str, input: UpdatePlaybookInput) -> dict[str, Any]:
    patch = input.model_dump(by_alias=True, exclude_unset=True)
    async with session_scope() as s:
        repo = Repo(s)
        playbook = await repo.get_entity("playbook", playbook_id)
        if not playbook:
            raise HTTPException(status_code=404, detail="playbook not found")
        playbook.update(patch)
        playbook["version"] = int(playbook.get("version", 1)) + 1
        await repo.put_entity("playbook", playbook, ts=now_ms())
    hub.mark_dirty()
    return playbook


@app.post("/api/lessons", response_model=Lesson)
async def create_lesson(input: CreateLessonInput) -> dict[str, Any]:
    async with session_scope() as s:
        lesson = await _record_learning_signal(
            Repo(s),
            source=input.source,
            task_id=input.task_id,
            artifact_id=input.artifact_id,
            what_went_wrong=input.what_went_wrong,
            lesson_text=input.lesson,
            applied_to=input.applied_to,
        )
    hub.mark_dirty()
    return lesson


@app.get("/api/evidence-packs/{artifact_id}", response_model=EvidencePack)
async def get_evidence_pack(artifact_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        rows = await repo.list_entities("evidence_pack", status=artifact_id, limit=1)
        if not rows:
            rows = await repo.list_entities("evidence_pack", status=f"artifact:{artifact_id}", limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="evidence pack not found")
    return rows[0]


@app.post("/api/evidence-packs", response_model=EvidencePack)
async def create_evidence_pack(input: CreateEvidencePackInput) -> dict[str, Any]:
    pack = EvidencePack(
        id=uid("evp"),
        artifact_id=input.artifact_id,
        citations=input.citations,
        raw_notes=input.raw_notes,
        confidence=input.confidence,
        gaps=input.gaps,
        assumptions=input.assumptions,
        methodology=input.methodology,
    ).dump()
    async with session_scope() as s:
        repo = Repo(s)
        if not await repo.get_entity("artifact", input.artifact_id):
            raise HTTPException(status_code=404, detail="artifact not found")
        await repo.put_entity("evidence_pack", pack, status=input.artifact_id, ts=now_ms())
    hub.mark_dirty()
    return pack


@app.get("/api/critique-reports", response_model=list[CritiqueReport])
async def list_critique_reports(
    target_type: Literal["task", "project", "decision", "artifact"] | None = Query(default=None, alias="targetType"),
    target_id: str | None = Query(default=None, alias="targetId"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return await _list_critique_reports(Repo(s), target_type=target_type, target_id=target_id, limit=limit)


@app.post("/api/critique-reports", response_model=CritiqueReport)
async def create_critique_report(input: CreateCritiqueInput) -> dict[str, Any]:
    now = now_ms()
    report = CritiqueReport(
        id=uid("crit"),
        target_type=input.target_type,
        target_id=input.target_id,
        risks=[
            "หลักฐานหรือ acceptance criteria อาจยังไม่พอ",
            "ผลลัพธ์อาจผูกกับ assumption ที่ยังไม่ได้ทดสอบ",
        ],
        untested_assumptions=[input.focus or "ยังไม่ได้ระบุขอบเขตการทดสอบครบ"],
        missed_alternatives=["ลองทางเลือกที่ง่ายกว่า/ต้นทุนต่ำกว่า", "ขอข้อมูลเพิ่มก่อน commit ทางใหญ่"],
        open_questions=["ผู้ใช้ต้องอนุมัติ deliverable ระดับไหน", "ต้องมี evidence pack หรือ preview เพิ่มหรือไม่"],
        ts=now,
    ).dump()
    async with session_scope() as s:
        await Repo(s).put_entity("critique_report", report, status=input.target_type, ts=now)
    hub.mark_dirty()
    return report


@app.get("/api/images/status")
async def get_image_generation_status() -> dict[str, Any]:
    settings = get_settings()
    auth = image_generation_auth_status(settings)
    return {
        "configured": bool(auth.get("configured")),
        "provider": auth.get("primaryProvider"),
        "baseUrl": auth.get("primaryBaseUrl"),
        "primaryProvider": auth.get("primaryProvider"),
        "primaryConfigured": bool(auth.get("primaryConfigured")),
        "primaryBaseUrl": auth.get("primaryBaseUrl"),
        "fallbackProvider": auth.get("fallbackProvider"),
        "fallbackConfigured": bool(auth.get("fallbackConfigured")),
        "fallbackBaseUrl": auth.get("fallbackBaseUrl"),
        "usesDedicatedImageKey": bool(auth.get("usesDedicatedImageKey")),
        "timeoutS": auth.get("timeoutS"),
        "defaultModel": PRIMARY_IMAGE_MODEL,
        "fallbackModel": FALLBACK_IMAGE_MODEL,
        "modelPolicy": {
            "primary": PRIMARY_IMAGE_MODEL,
            "fallback": FALLBACK_IMAGE_MODEL,
            "supported": sorted(GPT_IMAGE_MODELS),
            "guidance": (
                "Use gpt-image-2 by default. Non-primary GPT image models require explicit user approval "
                "or a prior gpt-image-2 failure. Model, size, quality, format, compression, background, "
                "and moderation are per-request tool parameters; environment values are only fallback configuration."
            ),
        },
        "requestDefaults": {
            "size": "auto",
            "quality": "auto",
            "outputFormat": "png",
            "background": "auto",
            "moderation": "auto",
            "n": 1,
            "wakeOnComplete": True,
        },
        "supportedOptions": {
            "size": {
                "gptImage2": "auto or WIDTHxHEIGHT; both edges divisible by 16, aspect ratio between 1:3 and 3:1, edge <= 3840, pixels 655360..8294400",
                "otherGptImageModels": ["auto", "1024x1024", "1536x1024", "1024x1536"],
            },
            "aspectRatio": ["1:1", "16:9", "9:16", "4:5", "3:2", "portrait", "landscape", "square"],
            "resolution": ["hd", "2k", "4k"],
            "quality": ["low", "medium", "high", "auto"],
            "clarityAliases": {"draft": "low", "standard": "medium", "final": "high", "sharp": "high"},
            "outputFormat": ["png", "jpeg", "webp"],
            "outputCompression": "0..100, only with jpeg or webp",
            "background": ["auto", "opaque", "transparent"],
            "moderation": ["auto", "low"],
            "n": "1..10",
        },
        "timeoutSeconds": settings.image_generation_timeout_s,
        "supportsAsync": True,
        "backgroundWorker": {
            "kind": "image_generation",
            "concurrency": settings.image_generation_worker_concurrency,
            "jobTimeoutSeconds": settings.image_generation_timeout_s,
            "statusEndpoint": "/api/images/jobs/{jobId}",
        },
        "supportsTextToImage": True,
        "supportsImageReferences": True,
        "supportsMasks": True,
        "storesArtifacts": True,
        "wakesAiOnCompletion": True,
        "modelVisionContext": {
            "maxImages": settings.image_context_max_images,
            "maxBytesPerImage": settings.image_context_max_bytes,
        },
    }


@app.get("/api/images/jobs/{job_id}")
async def get_image_generation_job(job_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        runs = await repo.list_entities("image_generation_run", limit=1000)
    run = next(
        (
            item for item in runs
            if str(item.get("jobId") or "") == job_id or str(item.get("id") or "") == job_id
        ),
        None,
    )
    if not run:
        raise HTTPException(status_code=404, detail="image generation job not found")
    return {"ok": True, "now": now_ms(), "job": run}


@app.get("/api/images/jobs")
async def list_image_generation_jobs(
    thread_id: str | None = Query(default=None, alias="threadId"),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    async with session_scope() as s:
        repo = Repo(s)
        runs = await repo.list_entities("image_generation_run", status=status, limit=max(limit * 4, limit))
    if thread_id:
        runs = [run for run in runs if run.get("threadId") == thread_id]
    return {"ok": True, "now": now_ms(), "jobs": runs[:limit]}


@app.post("/api/images/generate", response_model=GenerateImageResponse)
async def generate_image(input: GenerateImageInput) -> dict[str, Any]:
    raw = input.model_dump(by_alias=True, exclude_none=True)
    owner_dept = input.owner_dept or EXEC_ID
    async with session_scope() as s:
        try:
            repo = Repo(s)
            if input.async_mode and not input.wait_for_result:
                result = await queue_image_generation_assets(
                    repo,
                    raw,
                    fallback_owner_dept=owner_dept,
                    requested_by=input.requested_by or input.requester_name or input.created_by or "owner-ui",
                    thread_id=input.thread_id,
                )
            else:
                result = await generate_image_assets(
                    repo,
                    raw,
                    fallback_owner_dept=owner_dept,
                    requested_by=input.requested_by or input.requester_name or input.created_by or "owner-ui",
                )
        except ImageGenerationError as exc:
            detail = str(exc)
            status = 403 if "not enabled" in detail.lower() or "permission" in detail.lower() else 502
            raise HTTPException(status_code=status, detail=detail) from exc
    hub.mark_dirty()
    return result


@app.post("/api/import/file", response_model=ImportFileResponse)
async def import_file(input: ImportFileInput) -> dict[str, Any]:
    source = Path(input.source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=404, detail="source file not found")
    async with session_scope() as s:
        repo = Repo(s)
        if not await repo.get_department(input.target_dept):
            raise HTTPException(status_code=404, detail="target department not found")
        result = await _store_file_artifact(
            repo,
            data=source.read_bytes(),
            filename=source.name,
            owner_dept=input.target_dept,
            project_id=input.project_id,
            artifact_name=input.artifact_name,
            tags=input.tags,
            links=[str(source)],
            created_by="importer",
            status="approved",
        )
    hub.mark_dirty()
    return {"artifact": result["artifact"], "knowledge": result.get("knowledge")}


@app.post("/api/attachments/upload", response_model=ImportFileResponse)
async def upload_attachment(
    file: UploadFile = File(...),
    thread_id: str | None = Form(default=None, alias="threadId"),
    target_dept: str | None = Form(default=None, alias="targetDept"),
    project_id: str | None = Form(default=None, alias="projectId"),
    artifact_name: str | None = Form(default=None, alias="artifactName"),
    tags: str = Form(default="attachment,upload"),
) -> dict[str, Any]:
    data = await _read_upload_bytes(file)
    filename = safe_filename(file.filename)
    dept_id = target_dept or (dept_id_from_thread(thread_id) if thread_id else EXEC_ID)
    async with session_scope() as s:
        repo = Repo(s)
        if not await repo.get_department(dept_id):
            raise HTTPException(status_code=404, detail="target department not found")
        if project_id and not await repo.get_entity("project", project_id):
            raise HTTPException(status_code=404, detail="project not found")
        result = await _store_file_artifact(
            repo,
            data=data,
            filename=filename,
            owner_dept=dept_id,
            project_id=project_id,
            artifact_name=artifact_name,
            tags=_parse_upload_tags(tags),
            links=[],
            created_by="owner-ui",
            status="approved",
            mime=file.content_type,
        )
    await file.close()
    hub.mark_dirty()
    return {"artifact": result["artifact"], "knowledge": result.get("knowledge")}


@app.get("/api/audio/status")
async def get_audio_transcription_status() -> dict[str, Any]:
    return audio_transcription_status(get_settings())


@app.post("/api/audio/transcribe")
async def transcribe_audio_upload(
    file: UploadFile = File(...),
    thread_id: str | None = Form(default=None, alias="threadId"),
    target_dept: str | None = Form(default=None, alias="targetDept"),
    project_id: str | None = Form(default=None, alias="projectId"),
    artifact_name: str | None = Form(default=None, alias="artifactName"),
    tags: str = Form(default="audio,transcription,upload"),
    model: str | None = Form(default=None),
    language: str | None = Form(default=None),
    prompt: str | None = Form(default=None),
    persist: bool = Form(default=True),
) -> dict[str, Any]:
    data = await _read_upload_bytes(file)
    filename = safe_filename(file.filename)
    mime = guess_mime(filename, file.content_type)
    try:
        if not is_audio_file(filename, mime):
            raise HTTPException(status_code=415, detail="uploaded file is not an audio file")
        if persist:
            dept_id = target_dept or (dept_id_from_thread(thread_id) if thread_id else EXEC_ID)
            async with session_scope() as s:
                repo = Repo(s)
                if not await repo.get_department(dept_id):
                    raise HTTPException(status_code=404, detail="target department not found")
                if project_id and not await repo.get_entity("project", project_id):
                    raise HTTPException(status_code=404, detail="project not found")
                result = await _store_file_artifact(
                    repo,
                    data=data,
                    filename=filename,
                    owner_dept=dept_id,
                    project_id=project_id,
                    artifact_name=artifact_name,
                    tags=_parse_upload_tags(tags),
                    links=[],
                    created_by="owner-ui",
                    status="approved",
                    mime=mime,
                    force_audio_transcription=True,
                    audio_transcription_model=model,
                    audio_transcription_language=language,
                    audio_transcription_prompt=prompt,
                )
            hub.mark_dirty()
            transcription = result["artifact"].get("audioTranscription") or {"status": "not_attempted"}
            if transcription.get("status") != "succeeded":
                failure_detail = (
                    transcription.get("error")
                    or transcription.get("reason")
                    or "audio transcription did not succeed"
                )
                raise HTTPException(
                    status_code=409 if transcription.get("status") == "skipped" else 502,
                    detail={
                        "message": str(failure_detail),
                        "transcription": transcription,
                        "artifact": result["artifact"],
                        "knowledge": result.get("knowledge"),
                    },
                )
            return {
                "ok": transcription.get("status") == "succeeded",
                "transcription": transcription,
                "artifact": result["artifact"],
                "knowledge": result.get("knowledge"),
            }
        transcribed = await transcribe_audio_bytes(
            data,
            filename=filename,
            mime=mime,
            model=model,
            language=language,
            prompt=prompt,
        )
        return {"ok": True, "transcription": transcribed.public_dict()}
    except AudioTranscriptionNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AudioTranscriptionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await file.close()


@app.get("/api/knowledge-debt", response_model=KnowledgeDebtReport)
async def get_knowledge_debt() -> dict[str, Any]:
    now = now_ms()
    rows = []
    async with session_scope() as s:
        repo = Repo(s)
        for dept in await repo.list_departments():
            knowledge = await repo.list_knowledge(dept["id"], limit=1000)
            graph = await repo.graph(dept["id"])
            rows.append(compute_department_knowledge_debt(dept["id"], knowledge, graph, now))
    return {"departments": rows, "generatedAt": now}


@app.get("/api/entities/{entity_type}")
async def list_entities(
    entity_type: str,
    dept: str | None = None,
    project: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        entities = await Repo(s).list_entities(entity_type, dept=dept, project=project, status=status, limit=limit)
    if entity_type == "tool_run":
        return [_public_tool_run(entity) for entity in entities]
    return entities


@app.get("/api/entities/{entity_type}/{entity_id}")
async def get_entity(entity_type: str, entity_id: str) -> dict[str, Any]:
    async with session_scope() as s:
        entity = await Repo(s).get_entity(entity_type, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="entity not found")
        if entity_type == "tool_run":
            return _public_tool_run(entity)
        return entity


@app.put("/api/entities/{entity_type}/{entity_id}")
async def put_entity(entity_type: str, entity_id: str, input: EntityInput = Body(...)) -> dict[str, Any]:
    _assert_generic_entity_mutable(entity_type)
    data = {**input.data, "id": entity_id}
    async with session_scope() as s:
        entity = await Repo(s).put_entity(
            entity_type,
            data,
            dept=data.get("departmentId") or data.get("dept") or data.get("ownerDept"),
            project=data.get("projectId"),
            status=data.get("status"),
            ts=data.get("ts") or data.get("createdAt") or now_ms(),
        )
    hub.mark_dirty()
    return entity


@app.delete("/api/entities/{entity_type}/{entity_id}", response_model=GuardedActionResponse)
async def delete_entity(entity_type: str, entity_id: str) -> dict[str, Any]:
    _assert_generic_entity_mutable(entity_type)
    async with session_scope() as s:
        repo = Repo(s)
        entity = await repo.get_entity(entity_type, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="entity not found")
        dept_id = entity.get("departmentId") or entity.get("dept") or entity.get("ownerDept")
        approval = await _request_destructive_approval(
            repo,
            title=f"ลบ entity {entity_type}/{entity_id}",
            detail="การลบ generic entity เป็น action ถาวร จึงรันผ่าน Full Auto audit record",
            department_id=dept_id,
            action={
                "action": "delete_entity",
                "entityType": entity_type,
                "entityId": entity_id,
                "requestedBy": "user",
            },
        )
    hub.mark_dirty()
    return {"ok": True, "approval": approval, "executed": True}
