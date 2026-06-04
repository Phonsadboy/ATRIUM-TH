"""Pydantic models that ARE the wire contract.

The core section is a faithful port of `ui/src/contract/types.ts` — same field
names, same camelCase on the wire — so the React client can point a `WsClient`
at this service and render unchanged. The v0.4 section adds the extension types
from REQUIREMENTS.v0.4-draft.md (§31). To keep `CompanyState` byte-compatible
with the shipped UI, v0.4 entities are NOT folded into the snapshot; each gets
its own REST endpoints and WebSocket event types instead.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def to_camel(s: str) -> str:
    head, *tail = s.split("_")
    return head + "".join(p.capitalize() for p in tail)


class Schema(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )

    def dump(self) -> dict[str, Any]:
        """Wire form: camelCase aliases, omitting unset Optionals that the UI treats as absent."""
        return self.model_dump(by_alias=True, exclude_none=False)


# ============================================================
# Core contract (mirror of types.ts)
# ============================================================

AgentState = Literal["idle", "thinking", "working", "review", "handoff", "blocked", "offline"]
AccentName = Literal["amber", "teal", "coral", "lavender", "sky", "honey"]
AiProviderId = Literal["anthropic", "openai", "chatgpt_account", "claude_code"]
ModelId = Literal[
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-sonnet-4-7",
    "claude-opus-4-8",
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
]
ThinkingEffort = Literal["off", "low", "medium", "high", "xhigh", "max"]
ModelSpeed = Literal["standard", "fast"]

TaskStatus = Literal[
    "backlog", "assigned", "in_progress", "review", "revising", "done", "blocked", "cancelled"
]
Priority = Literal["low", "normal", "high", "urgent"]
HandoffKind = Literal["delegate", "consult", "collaborate", "return"]
ChatRole = Literal["user", "executive", "agent", "system"]
MessageRenderFormat = Literal["plain", "markdown"]
MessageRenderNotice = Literal[
    "budget_guardrail",
    "context_compacted",
    "rate_limited",
    "rate_limit_warning",
    "budget_warning",
    "approval_required",
    "message_failed",
    "runtime_dependency",
]
MessageStatus = Literal["queued", "sending", "sent", "failed", "cancelled", "blocked", "pending_approval"]
ReasoningStatus = Literal["available", "redacted", "omitted", "disabled", "unavailable"]
Severity = Literal["info", "good", "warn", "alert"]
ActivityType = Literal[
    "task_created", "task_assigned", "task_progress", "task_done", "handoff",
    "state_change", "compaction", "spend", "approval", "budget", "autonomous", "message", "system", "api_mutation",
]
ApprovalKind = Literal["external_action", "publish", "destructive_action", "task_close"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
CostCategory = Literal["work", "chat", "meeting", "autonomous", "memory", "tool"]
CostReportScope = Literal["day", "month", "project", "dept", "agent"]
GraphNodeType = Literal["concept", "entity", "task", "person", "artifact"]
MemoryKind = Literal["archive", "knowledge", "graph"]
BUILTIN_TOOL_NAMES = (
    # legacy v0.3 names kept for the existing UI/client surface
    "read_file",
    "write_file",
    "copy_file",
    "move_file",
    "http_get",
    "import_url",
    "run_command",
    # Owner Mode tool catalog names from REQUIREMENTS.owner-mode-draft.md
    "fs.list",
    "fs.read",
    "fs.write",
    "fs.patch",
    "fs.copy",
    "fs.move",
    "fs.delete",
    "shell.exec",
    "sandbox.exec",
    "git.status",
    "git.diff",
    "git.commit",
    "git.push",
    "browser.profiles",
    "browser.open",
    "browser.snapshot",
    "browser.act",
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.screenshot",
    "desktop.apps",
    "desktop.snapshot",
    "desktop.act",
    "desktop.open_app",
    "desktop.activate_app",
    "desktop.quit_app",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "web.search",
    "web.fetch",
    "http.get",
    "http.post",
    "import.url",
    "mcp.call",
    "notify.send",
    "scheduler.create",
    "logs.query",
    "logs.note",
)
# Tool Foundry can register arbitrary names at runtime, so the wire type must
# accept more than the built-in catalog while BUILTIN_TOOL_NAMES preserves docs.
ToolName = str
ToolRunStatus = Literal[
    "queued",
    "pending_approval",
    "running",
    "succeeded",
    "completed",  # legacy alias retained for older clients
    "failed",
    "cancelled",
    "blocked",
]
# Legacy ATRIUM mode names remain accepted; OpenClaw-style modes are the
# canonical policy surface for tool execution.
PermissionMode = Literal["deny", "allowlist", "ask", "auto", "full", "full_auto", "approve_everything", "approve_all", "critical_only"]
ToolRiskClass = Literal[
    "safe_read",
    "local_write",
    "host_write",
    "command",
    "network",
    "desktop",
    "credential",
    "external_send",
    "destructive",
    "privileged",
]
PolicyDecision = Literal["auto_approved", "approval_required", "blocked_by_policy", "blocked_by_runtime"]
AuditLogKind = Literal["activity", "approval", "tool_run", "note"]


class RoomRect(Schema):
    x: float
    y: float
    w: float
    h: float


class MemoryStats(Schema):
    archive_chunks: int = 0
    rag_entries: int = 0
    graph_nodes: int = 0
    graph_edges: int = 0
    last_compaction_at: Optional[int] = None
    tokens_saved: int = 0
    working_summary: Optional[str] = None
    working_archive_id: Optional[str] = None
    working_thread_id: Optional[str] = None
    working_message_count: Optional[int] = None
    working_updated_at: Optional[int] = None


class VisibilityPolicy(Schema):
    dept: str
    archive: Literal["private"] = "private"
    knowledge: Literal["private", "on_request"] = "on_request"
    tasks: Literal["company", "on_request"] = "company"
    artifacts: Literal["company"] = "company"


class Department(Schema):
    id: str
    name: str
    role: str
    charter: str
    emoji: str
    accent: AccentName
    provider_id: AiProviderId
    model: ModelId
    thinking_effort: ThinkingEffort
    speed: ModelSpeed = "standard"
    agent_name: str
    state: AgentState
    mood: float
    current_task_id: Optional[str] = None
    autonomy: bool
    created_at: int
    room: RoomRect
    memory: MemoryStats
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    workspace_path: Optional[str] = None
    visibility_policy: Optional[VisibilityPolicy] = None
    runtime: Optional[dict[str, Any]] = None


class Executive(Schema):
    id: str
    agent_name: str
    provider_id: AiProviderId
    model: ModelId
    thinking_effort: ThinkingEffort
    speed: ModelSpeed = "standard"
    system_prompt: str
    daily_budget_usd: float
    workspace_path: Optional[str] = None
    tools: list[str] = Field(default_factory=list)
    autonomy: bool = True


class MemoryArchiveEntry(Schema):
    id: str
    title: str
    ts: int
    tokens: int
    summary: str
    thread_id: Optional[str] = None
    message_count: Optional[int] = None
    transcript: Optional[str] = None


class MemoryKnowledgeEntry(Schema):
    id: str
    title: str
    ts: int
    score: float
    text: str
    tags: list[str] = Field(default_factory=list)
    source: Optional[str] = None


class GraphNode(Schema):
    id: str
    label: str
    type: GraphNodeType
    x: float
    y: float
    valid_from: Optional[int] = Field(default=None, alias="validFrom")
    valid_to: Optional[int] = Field(default=None, alias="validTo")
    confidence: float = 0.7
    source: Optional[str] = None


class GraphEdge(Schema):
    from_: str = Field(alias="from")
    to: str
    rel: str
    valid_from: Optional[int] = Field(default=None, alias="validFrom")
    valid_to: Optional[int] = Field(default=None, alias="validTo")
    confidence: float = 0.7
    source: Optional[str] = None


class MemoryGraph(Schema):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class DepartmentMemory(Schema):
    department_id: str
    archive: list[MemoryArchiveEntry] = Field(default_factory=list)
    knowledge: list[MemoryKnowledgeEntry] = Field(default_factory=list)
    graph: MemoryGraph = Field(default_factory=MemoryGraph)


class Handoff(Schema):
    id: str
    from_dept: str
    to_dept: str
    ts: int
    reason: str
    kind: HandoffKind
    status: Optional[str] = None
    depth: int = 0
    context_packet_ref: Optional[str] = None
    source_task_id: Optional[str] = None
    target_task_id: Optional[str] = None
    war_room_id: Optional[str] = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


class Task(Schema):
    id: str
    title: str
    detail: str
    status: TaskStatus
    priority: Priority
    department_id: Optional[str] = None
    origin: dict[str, Any]  # {kind:'user'} | {kind:'executive'} | {kind:'department', id}
    progress: float
    created_at: int
    updated_at: int
    handoffs: list[Handoff] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)
    waiting_on: Optional[dict[str, Any]] = None  # {dept, handoffId}
    # v0.4 extension fields (additive; UI ignores unknown keys)
    project_id: Optional[str] = None
    deliverables: list[str] = Field(default_factory=list)
    draft_deliverable_markdown: Optional[str] = None
    watchers: list[str] = Field(default_factory=list)
    parent_task_id: Optional[str] = None
    sub_task_ids: list[str] = Field(default_factory=list)
    deadline_at: Optional[int] = None
    result: Optional[dict[str, Any]] = None
    review_interval_ms: Optional[int] = None
    next_review_at: Optional[int] = None
    last_review_reminder_at: Optional[int] = None
    review_reminder_count: int = 0
    review_schedule_token: Optional[str] = None


class MessageCodeBlock(Schema):
    index: int
    language: Optional[str] = None
    text: str


class MessageLink(Schema):
    url: str
    label: Optional[str] = None


class MessageMention(Schema):
    raw: str
    target: Optional[str] = None


class MessageCitation(Schema):
    id: str
    kind: Literal["knowledge", "archive", "graph", "memory"] = "knowledge"
    label: str
    title: Optional[str] = None
    source: Optional[str] = None
    score: Optional[float] = None
    snippet: Optional[str] = None
    department_id: Optional[str] = None


class MessageRenderMetadata(Schema):
    format: MessageRenderFormat = "plain"
    code_blocks: list[MessageCodeBlock] = Field(default_factory=list)
    links: list[MessageLink] = Field(default_factory=list)
    mentions: list[MessageMention] = Field(default_factory=list)
    citations: list[MessageCitation] = Field(default_factory=list)
    cost_usd: Optional[float] = None
    compact_enqueued: bool = False
    notices: list[MessageRenderNotice] = Field(default_factory=list)
    severity: Optional[Severity] = None


class ChatParticipant(Schema):
    department_id: Optional[str] = None
    department_name: Optional[str] = None
    agent_name: Optional[str] = None
    role: Optional[str] = None
    state: Optional[AgentState] = None
    mood: Optional[float] = None
    current_task_id: Optional[str] = None


class ChatFlowStep(Schema):
    kind: str
    label: Optional[str] = None
    department_id: Optional[str] = None
    task_id: Optional[str] = None
    war_room_id: Optional[str] = None
    meeting_id: Optional[str] = None


class ChatFlow(Schema):
    kind: Literal["activity", "handoff", "war_room", "meeting", "cost", "status", "department_work"]
    title: Optional[str] = None
    steps: list[ChatFlowStep] = Field(default_factory=list)
    refs: dict[str, Any] = Field(default_factory=dict)


class ThreadCostSummary(Schema):
    thread_id: str
    total_usd: float = 0.0
    by_agent: dict[str, float] = Field(default_factory=dict)
    costed_messages: int = 0
    message_count: int = 0
    updated_at: int


class MessageAttachment(Schema):
    artifact_id: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[str] = None
    mime: Optional[str] = None
    uri: Optional[str] = None
    size_bytes: Optional[int] = None
    project_id: Optional[str] = None
    video_project_id: Optional[str] = None
    asset_id: Optional[str] = None
    timeline_id: Optional[str] = None
    timeline_version: Optional[int] = None
    render_id: Optional[str] = None
    media_handle: Optional[str] = None
    context_tool: Optional[str] = None
    context_args: Optional[dict[str, Any]] = None
    review_status: Optional[str] = None
    video_review_id: Optional[str] = None
    approval_id: Optional[str] = None


class MessageMentionTarget(Schema):
    raw: str
    department_id: Optional[str] = None
    thread_id: Optional[str] = None
    display_name: Optional[str] = None
    agent_name: Optional[str] = None
    matched_by: Optional[str] = None


class SuggestedFollowUp(Schema):
    id: str
    label: str
    text: str


MessageInputStatus = Literal[
    "queued",
    "sending",
    "sent",
    "executed",
    "failed",
    "draft",
    "cancelled",
    "blocked",
    "pending_approval",
]


class MessageInputMetadata(Schema):
    status: Optional[MessageInputStatus] = None
    command: Optional[str] = None
    command_args: Optional[str] = None
    routed_department_id: Optional[str] = None
    character_count: int = 0
    estimated_tokens: int = 0
    attachment_count: int = 0


class MessageError(Schema):
    code: str
    detail: str
    retryable: bool = True


class MessageReaction(Schema):
    emoji: str
    actor: str = "user"
    ts: int


class ChatMessage(Schema):
    id: str
    thread_id: str
    role: ChatRole
    author_name: str
    text: str
    ts: int
    pending: Optional[bool] = None
    status: Optional[MessageStatus] = None
    error: Optional[MessageError] = None
    reply_to_message_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    quote_message_id: Optional[str] = None
    quote_text: Optional[str] = None
    quote_author_name: Optional[str] = None
    retry_of_message_id: Optional[str] = None
    regenerated_from_message_id: Optional[str] = None
    edited_from_message_id: Optional[str] = None
    branch_from_thread_id: Optional[str] = None
    branch_point_message_id: Optional[str] = None
    branch_copy_of_message_id: Optional[str] = None
    pinned: Optional[bool] = None
    pinned_at: Optional[int] = None
    memory_promoted_id: Optional[str] = None
    reactions: list[MessageReaction] = Field(default_factory=list)
    approval_id: Optional[str] = None
    approval_status: Optional[ApprovalStatus] = None
    tool_run_id: Optional[str] = None
    tool_runs: Optional[list[dict[str, Any]]] = None
    runtime: Optional[dict[str, Any]] = None
    content_format: Optional[MessageRenderFormat] = None
    render: Optional[MessageRenderMetadata] = None
    reasoning: Optional[str] = None
    reasoning_summary: Optional[str] = None
    reasoning_status: Optional[ReasoningStatus] = None
    reasoning_redacted: Optional[bool] = None
    thinking_tokens: Optional[int] = None
    generation_ms: Optional[int] = None
    attachments: list[MessageAttachment] = Field(default_factory=list)
    mentions: list[MessageMentionTarget] = Field(default_factory=list)
    suggested_follow_ups: list[SuggestedFollowUp] = Field(default_factory=list)
    input: Optional[MessageInputMetadata] = None
    department_id: Optional[str] = None
    agent_state: Optional[AgentState] = None
    agent_mood: Optional[float] = None
    participant: Optional[ChatParticipant] = None
    activity_id: Optional[str] = None
    activity_type: Optional[ActivityType] = None
    flow: Optional[ChatFlow] = None
    war_room_id: Optional[str] = None
    meeting_id: Optional[str] = None
    thread_cost: Optional[ThreadCostSummary] = None


class SlashCommandSpec(Schema):
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    usage: str
    examples: list[str] = Field(default_factory=list)


class SlashCommandResult(Schema):
    name: str
    ok: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class InputEstimate(Schema):
    characters: int
    words: int
    estimated_tokens: int
    max_recommended_tokens: int
    within_limit: bool
    warnings: list[str] = Field(default_factory=list)
    attachment_count: int = 0


class InputEstimateInput(Schema):
    text: str = ""
    attachments: list[MessageAttachment] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)


class PromptStarter(Schema):
    id: str
    thread_id: str
    department_id: Optional[str] = None
    label: str
    text: str
    category: str


class ThreadDraftInput(Schema):
    text: str = ""
    attachments: list[MessageAttachment] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)


class ThreadDraft(Schema):
    id: str
    thread_id: str
    text: str = ""
    attachments: list[MessageAttachment] = Field(default_factory=list)
    updated_at: int


class ActivityEvent(Schema):
    id: str
    ts: int
    type: ActivityType
    department_id: Optional[str] = None
    text: str
    severity: Severity


class ApprovalAction(Schema):
    action: Literal[
        "delete_department",
        "delete_knowledge",
        "delete_entity",
        "run_tool",
        "resolve_project",
        "approve_org_plan",
        "close_task",
    ]
    department_id: Optional[str] = None
    knowledge_id: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    tool_run_id: Optional[str] = None
    project_id: Optional[str] = None
    artifact_id: Optional[str] = None
    task_id: Optional[str] = None
    org_plan_id: Optional[str] = None
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    executed_at: Optional[int] = None


class Approval(Schema):
    id: str
    ts: int
    kind: ApprovalKind
    title: str
    detail: str
    department_id: Optional[str] = None
    cost_usd: Optional[float] = None
    status: ApprovalStatus
    action: Optional[ApprovalAction] = None


class ToolCatalogItem(Schema):
    tool: ToolName
    risk_class: ToolRiskClass
    mutates_state: bool
    external_system: bool
    description: str
    executor: str = "host"
    default_timeout_ms: int = 10_000
    output_limit_bytes: int = 60_000
    supports_checkpoint: bool = False
    can_use_credentials: bool = False
    rollback_capable: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    redaction_rules: list[str] = Field(default_factory=list)


class PermissionPolicy(Schema):
    mode: PermissionMode = "full_auto"
    agent_full_access: bool = True
    requested_mode: Optional[str] = None
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    allowed_risk_classes: list[str] = Field(default_factory=list)
    denied_risk_classes: list[str] = Field(default_factory=list)
    command_allowlist: list[str] = Field(default_factory=list)
    command_denylist: list[str] = Field(default_factory=list)
    ask_fallback: Optional[str] = None
    strict_inline_eval: bool = True
    updated_at: Optional[int] = None
    updated_by: Optional[str] = None
    tool_catalog: list[ToolCatalogItem] = Field(default_factory=list)


class AuditLogEntry(Schema):
    id: str
    ts: int
    kind: AuditLogKind
    department_id: Optional[str] = None
    severity: Severity = "info"
    title: str
    detail: str = ""
    author: Optional[str] = None
    refs: dict[str, Any] = Field(default_factory=dict)
    redacted: bool = False
    redacted_fields: list[str] = Field(default_factory=list)


class CreateAuditNoteInput(Schema):
    body: str
    author: str = "user"
    department_id: Optional[str] = None
    links: list[str] = Field(default_factory=list)
    severity: Severity = "info"


class GuardedActionResponse(Schema):
    ok: bool
    approval: Optional[Approval] = None
    executed: bool = False


class ToolRun(Schema):
    id: str
    tool: ToolName
    department_id: str
    thread_id: Optional[str] = None
    task_id: Optional[str] = None
    requested_by: str
    args: dict[str, Any] = Field(default_factory=dict)
    args_redacted: bool = False
    redacted_fields: list[str] = Field(default_factory=list)
    status: ToolRunStatus
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    approval_id: Optional[str] = None
    risk_class: Optional[ToolRiskClass] = None
    policy_decision: Optional[PolicyDecision] = None
    policy_reason: Optional[str] = None
    executor: Optional[str] = None
    executor_route: Optional[dict[str, Any]] = None
    checkpoint_id: Optional[str] = None
    checkpoint: Optional[dict[str, Any]] = None
    created_at: int
    started_at: Optional[int] = None
    completed_at: Optional[int] = None


class ToolRunInput(Schema):
    tool: ToolName
    department_id: str
    thread_id: Optional[str] = None
    task_id: Optional[str] = None
    requested_by: str = "user"
    args: dict[str, Any] = Field(default_factory=dict)
    require_approval: Optional[bool] = None


class ToolRunResponse(Schema):
    run: ToolRun
    approval: Optional[Approval] = None
    executed: bool = False


class ScheduledObjective(Schema):
    id: str
    title: str
    cadence: str
    department_id: str
    enabled: bool
    last_run_at: Optional[int] = None
    next_run_at: int


class CreateObjectiveInput(Schema):
    id: Optional[str] = None
    title: str
    cadence: str
    department_id: str
    enabled: bool = True
    next_run_at: Optional[int] = None


class UpdateObjectiveInput(Schema):
    title: Optional[str] = None
    cadence: Optional[str] = None
    department_id: Optional[str] = None
    enabled: Optional[bool] = None
    next_run_at: Optional[int] = None


class CostRecord(Schema):
    id: str
    ts: int
    department_id: Optional[str] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    provider_id: Optional[AiProviderId] = None
    model: Optional[ModelId] = None
    thinking_effort: Optional[ThinkingEffort] = None
    speed: Optional[ModelSpeed] = None
    kind: Literal["model", "tool", "api"] = "model"
    category: CostCategory
    usd: float
    detail: str = ""
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    thinking_tokens: Optional[int] = None


class CostSeriesPoint(Schema):
    day: str
    usd: float


class CostReport(Schema):
    scope: CostReportScope
    spent_usd: float
    forecast_usd: float
    by_category: dict[str, float]
    by_agent: dict[str, float]
    series: list[CostSeriesPoint]
    anomalies: list[str]


class Budget(Schema):
    daily_cap_usd: float
    spent_today_usd: float


class ExecutiveQueueItem(Schema):
    id: str
    kind: str
    status: str
    title: str
    detail: Optional[str] = None
    department_id: Optional[str] = None
    thread_id: Optional[str] = None
    task_id: Optional[str] = None
    user_message_id: Optional[str] = None
    reply_message_id: Optional[str] = None
    run_after: int
    priority: int
    attempts: int = 0
    last_error: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0


class CompanyState(Schema):
    company_name: str
    now: int
    running: bool
    departments: list[Department]
    tasks: list[Task]
    threads: dict[str, list[ChatMessage]]
    activity: list[ActivityEvent]
    approvals: list[Approval]
    objectives: list[ScheduledObjective]
    executive_queue: list[ExecutiveQueueItem] = Field(default_factory=list)
    budget: Budget
    permission_policy: PermissionPolicy


class PulseEvent(Schema):
    kind: Literal["handoff", "compaction", "spend", "state", "autonomous", "done"]
    department_id: str
    to_department_id: Optional[str] = None


# ============================================================
# Inputs (mirror of client.ts)
# ============================================================

class CreateDepartmentInput(Schema):
    id: Optional[str] = None
    name: str
    role: str
    charter: Optional[str] = None
    provider_id: AiProviderId
    model: ModelId
    thinking_effort: Optional[ThinkingEffort] = None
    speed: Optional[ModelSpeed] = None
    agent_name: str
    accent: Optional[AccentName] = None
    emoji: Optional[str] = None
    autonomy: Optional[bool] = None
    skills: Optional[list[str]] = None
    tools: Optional[list[str]] = None
    by_executive: Optional[bool] = None


class EditDepartmentInput(Schema):
    name: Optional[str] = None
    role: Optional[str] = None
    charter: Optional[str] = None
    emoji: Optional[str] = None
    accent: Optional[AccentName] = None
    provider_id: Optional[AiProviderId] = None
    model: Optional[ModelId] = None
    thinking_effort: Optional[ThinkingEffort] = None
    speed: Optional[ModelSpeed] = None
    agent_name: Optional[str] = None
    autonomy: Optional[bool] = None
    skills: Optional[list[str]] = None
    tools: Optional[list[str]] = None


class AssignTaskInput(Schema):
    id: Optional[str] = None
    title: str
    detail: Optional[str] = None
    department_id: str
    priority: Optional[Priority] = None
    by_executive: Optional[bool] = None
    project_id: Optional[str] = None
    watchers: list[str] = Field(default_factory=list)
    parent_task_id: Optional[str] = None
    deadline_at: Optional[int] = None
    review_interval_ms: Optional[int] = None


class UpdateTaskReviewScheduleInput(Schema):
    review_interval_ms: Optional[int] = None


class ReassignTaskInput(Schema):
    department_id: str
    requested_by: str = "user"
    reason: Optional[str] = None


class RequestTaskClosureInput(Schema):
    department_id: Optional[str] = None
    requested_by: str = "department"
    summary: Optional[str] = None
    detail: Optional[str] = None


class SendMessageInput(Schema):
    text: str = ""
    attachments: list[MessageAttachment] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    target_department_id: Optional[str] = None
    thinking_effort: Optional[ThinkingEffort] = None
    speed: Optional[ModelSpeed] = None
    client_message_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    quote_message_id: Optional[str] = None
    retry_of_message_id: Optional[str] = None
    queue_if_busy: bool = True


class SetRunningInput(Schema):
    running: bool


class ResolveApprovalInput(Schema):
    decision: ApprovalStatus


class ToggleInput(Schema):
    enabled: bool


class BudgetCapInput(Schema):
    daily_cap_usd: float


class PermissionPolicyInput(Schema):
    mode: PermissionMode
    agent_full_access: Optional[bool] = None
    allowed_tools: Optional[list[str]] = None
    denied_tools: Optional[list[str]] = None
    allowed_risk_classes: Optional[list[str]] = None
    denied_risk_classes: Optional[list[str]] = None
    command_allowlist: Optional[list[str]] = None
    command_denylist: Optional[list[str]] = None
    ask_fallback: Optional[str] = None
    strict_inline_eval: Optional[bool] = None
    updated_by: Optional[str] = None


class EditKnowledgeInput(Schema):
    title: Optional[str] = None
    text: Optional[str] = None
    tags: Optional[list[str]] = None


# ============================================================
# v0.4 extension types (REQUIREMENTS.v0.4-draft.md §31)
# Exposed via dedicated REST endpoints + WS events, not in CompanyState.
# ============================================================

ArtifactKind = Literal["file", "doc", "code", "report", "link", "memo", "dataset", "image"]
ArtifactStatus = Literal["draft", "in_review", "approved", "published", "superseded", "archived"]
HandoffStatus = Literal["open", "accepted", "rejected", "clarifying", "delivered", "escalated"]
HandoffAct = Literal["request", "accept", "reject", "clarify", "reply", "deliver"]
DecisionStatus = Literal["proposed", "approved", "rejected", "superseded"]
NotifType = Literal[
    "approval", "budget", "blocked", "task_done", "digest", "crash", "knowledge_debt", "security"
]
NotificationDeliveryMode = Literal["off", "inbox", "push"]
ProjectStatus = Literal["active", "paused", "done", "archived"]
WarRoomStatus = Literal["active", "resolved", "archived"]
MeetingStatus = Literal["scheduled", "active", "done"]
TriggerKind = Literal["cron", "event"]
TriggerEvent = Literal["dept_done", "blocked", "idle", "budget", "escalate"]
PrefCategory = Literal["comm", "code", "design", "risk", "budget", "general"]
LessonSource = Literal["reject", "heavy_edit", "low_rating"]
PreviewKind = Literal["md", "diff", "image", "screenshot", "sheet", "pdf"]
ConnectorStatus = Literal["available", "configured", "blocked_by_runtime"]
ConnectorKind = Literal["local_file", "git", "http", "web", "browser", "desktop", "sandbox", "mcp"]
ConnectorProofStatus = Literal["not_required", "local_blocked", "cross_os_unverified", "cross_os_verified"]
HostBridgeParityStatus = Literal["local_blocked", "cross_os_unverified", "cross_os_verified"]
OrgPlanStatus = Literal["proposed", "approved", "rejected", "applied"]


class OrgPlanDepartment(Schema):
    id: Optional[str] = None
    name: str
    role: str
    charter: Optional[str] = None
    agent_name: Optional[str] = None
    provider_id: AiProviderId
    model: Optional[ModelId] = "claude-sonnet-4-7"
    thinking_effort: Optional[ThinkingEffort] = "high"
    speed: Optional[ModelSpeed] = "standard"
    emoji: Optional[str] = None
    accent: Optional[AccentName] = None
    autonomy: bool = False
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class OrgPlan(Schema):
    id: str
    objective: str
    interview_summary: str = ""
    status: OrgPlanStatus
    departments: list[OrgPlanDepartment] = Field(default_factory=list)
    created_by: str = "executive"
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
    approval_id: Optional[str] = None
    applied_department_ids: list[str] = Field(default_factory=list)
    decision_id: Optional[str] = None
    created_at: int
    updated_at: int


class HandoffMessage(Schema):
    id: str
    handoff_id: str
    from_: str = Field(alias="from")
    act: HandoffAct
    text: str
    ts: int
    task_id: Optional[str] = None
    thread_id: Optional[str] = None


class ArtifactPreview(Schema):
    kind: PreviewKind
    uri: str


class Artifact(Schema):
    id: str
    name: str
    kind: ArtifactKind
    mime: Optional[str] = None
    owner_dept: str
    task_ids: list[str] = Field(default_factory=list)
    project_id: Optional[str] = None
    version: int
    status: ArtifactStatus
    uri: str
    storage: Optional[Literal["object_store", "filesystem", "external"]] = None
    content_hash: Optional[str] = None
    content_size_bytes: Optional[int] = None
    content_mime: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    preview: Optional[ArtifactPreview] = None
    created_at: int
    created_by: str
    updated_at: int
    updated_by: str
    approval_tier: Optional[Literal["department", "user"]] = None
    approved_by: Optional[str] = None
    approved_at: Optional[int] = None
    review_gate: Optional[dict[str, Any]] = None
    video_project_id: Optional[str] = None
    asset_id: Optional[str] = None
    timeline_id: Optional[str] = None
    timeline_version: Optional[int] = None
    render_id: Optional[str] = None
    media_handle: Optional[str] = None
    context_tool: Optional[str] = None
    context_args: Optional[dict[str, Any]] = None
    review_status: Optional[str] = None
    video_review_id: Optional[str] = None
    approval_id: Optional[str] = None


class ArtifactVersion(Schema):
    artifact_id: str
    version: int
    author: str
    ts: int
    note: str
    parent: Optional[int] = None
    uri: str
    storage: Optional[Literal["object_store", "filesystem", "external"]] = None
    content_hash: Optional[str] = None
    content_size_bytes: Optional[int] = None
    content_mime: Optional[str] = None
    preview: Optional[ArtifactPreview] = None


class Decision(Schema):
    id: str
    title: str
    proposed_by: str
    approved_by: Optional[str] = None
    rationale: Optional[str] = None
    alternatives: list[str] = Field(default_factory=list)
    impact: str = ""
    linked_task: Optional[str] = None
    linked_artifacts: list[str] = Field(default_factory=list)
    status: DecisionStatus
    supersedes: Optional[str] = None
    ts: int


class Bulletin(Schema):
    id: str
    title: str
    body: str
    scope: Any  # 'all' | list[str]
    author: str
    approved_by: Optional[str] = None
    ts: int
    pinned: bool = False
    expires_at: Optional[int] = None
    links: list[str] = Field(default_factory=list)


class Notification(Schema):
    id: str
    type: NotifType
    severity: Severity
    title: str
    body: str
    ts: int
    read: bool = False
    links: list[str] = Field(default_factory=list)


class CreateArtifactInput(Schema):
    name: str
    kind: ArtifactKind
    owner_dept: str
    task_ids: list[str] = Field(default_factory=list)
    project_id: Optional[str] = None
    uri: Optional[str] = None
    mime: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    preview: Optional[ArtifactPreview] = None
    created_by: Optional[str] = None


class UpdateArtifactInput(Schema):
    name: Optional[str] = None
    status: Optional[ArtifactStatus] = None
    uri: Optional[str] = None
    mime: Optional[str] = None
    tags: Optional[list[str]] = None
    links: Optional[list[str]] = None
    preview: Optional[ArtifactPreview] = None
    updated_by: Optional[str] = None


class ArtifactContentInput(Schema):
    text: str
    note: str = "content update"
    author: Optional[str] = None
    status: Optional[ArtifactStatus] = None
    preview_kind: PreviewKind = "md"


class ArtifactContentResponse(Schema):
    artifact: Artifact
    version: ArtifactVersion
    content: str


class ArtifactPreviewResponse(Schema):
    artifact_id: str
    version: int
    preview: ArtifactPreview
    content: Optional[str] = None


class ArtifactDiffResponse(Schema):
    artifact_id: str
    from_version: int
    to_version: int
    diff: str


class RollbackArtifactInput(Schema):
    version: int
    author: Optional[str] = None
    note: str = "rollback"


class WorkspaceAuditResponse(Schema):
    department_id: str
    workspace_path: str
    git_enabled: bool
    head: Optional[str] = None
    dirty: bool = False
    error: Optional[str] = None


class CreateDecisionInput(Schema):
    title: str
    proposed_by: str
    rationale: Optional[str] = None
    alternatives: list[str] = Field(default_factory=list)
    impact: str = ""
    linked_task: Optional[str] = None
    linked_artifacts: list[str] = Field(default_factory=list)
    status: DecisionStatus = "approved"
    approved_by: Optional[str] = None
    supersedes: Optional[str] = None


class UpdateDecisionInput(Schema):
    status: Optional[DecisionStatus] = None
    approved_by: Optional[str] = None
    rationale: Optional[str] = None
    alternatives: Optional[list[str]] = None
    impact: Optional[str] = None
    linked_artifacts: Optional[list[str]] = None
    supersedes: Optional[str] = None


class CreateNotificationInput(Schema):
    type: NotifType
    severity: Severity
    title: str
    body: str
    links: list[str] = Field(default_factory=list)


class NotificationReadInput(Schema):
    read: bool = True


class NotificationQuietHours(Schema):
    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"
    timezone: str = "Asia/Bangkok"


class UpdateNotificationQuietHoursInput(Schema):
    enabled: Optional[bool] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone: Optional[str] = None


class NotificationPreferences(Schema):
    by_type: dict[str, NotificationDeliveryMode]
    quiet_hours: NotificationQuietHours = Field(default_factory=NotificationQuietHours)
    updated_at: int


class UpdateNotificationPreferencesInput(Schema):
    by_type: Optional[dict[str, NotificationDeliveryMode]] = None
    quiet_hours: Optional[UpdateNotificationQuietHoursInput] = None


class WarRoom(Schema):
    id: str
    title: str
    goal: str
    thread_id: str
    lead: Optional[str] = None
    members: list[str] = Field(default_factory=list)
    task_id: Optional[str] = None
    project_id: Optional[str] = None
    status: WarRoomStatus
    scratchpad: str = ""
    decisions: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    created_at: int


class WarRoomCollaboration(Schema):
    war_room: WarRoom
    thread_id: str
    participants: list[ChatParticipant] = Field(default_factory=list)
    flow: ChatFlow
    context: str
    messages: list[ChatMessage] = Field(default_factory=list)


class Project(Schema):
    id: str
    name: str
    goal: str
    brief: str = ""
    shared_notes: str = ""
    status: ProjectStatus
    departments: list[str] = Field(default_factory=list)
    lead: Optional[str] = None
    workspace_path: str
    created_at: int
    deliverable_artifact_id: Optional[str] = None
    final_approval_id: Optional[str] = None
    review_status: Optional[Literal[
        "draft",
        "pending_user",
        "approved_by_user",
        "rejected_by_user",
        "approved_by_full_auto",
        "rejected_by_full_auto",
    ]] = None
    completed_at: Optional[int] = None
    resolved_by: Optional[str] = None


class Skill(Schema):
    id: str
    name: str
    description: str
    tools: list[str] = Field(default_factory=list)
    suggested_model: Optional[str] = None
    cost_profile: Optional[str] = None


class Preference(Schema):
    id: str
    category: PrefCategory
    text: str
    confidence: float
    source: Optional[str] = None
    ts: int


class OwnerProfile(Schema):
    id: str = "owner"
    scope: Literal["company-global"] = "company-global"
    preferences: list[Preference] = Field(default_factory=list)
    by_category: dict[str, list[Preference]] = Field(default_factory=dict)
    updated_at: Optional[int] = None


class Trigger(Schema):
    id: str
    title: str
    kind: TriggerKind
    cadence: Optional[str] = None
    one_shot_at: Optional[int] = None
    event: Optional[TriggerEvent] = None
    target: str
    enabled: bool = True
    last_run_at: Optional[int] = None
    next_run_at: Optional[int] = None


class CritiqueReport(Schema):
    id: str
    target_type: Literal["task", "project", "decision", "artifact"]
    target_id: str
    risks: list[str] = Field(default_factory=list)
    untested_assumptions: list[str] = Field(default_factory=list)
    missed_alternatives: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    ts: int


class Meeting(Schema):
    id: str
    title: str
    thread_id: str
    project_id: Optional[str] = None
    agenda: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    notes: str = ""
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    status: MeetingStatus
    ts: int


class MeetingCollaboration(Schema):
    meeting: Meeting
    thread_id: str
    participants: list[ChatParticipant] = Field(default_factory=list)
    flow: ChatFlow
    context: str
    messages: list[ChatMessage] = Field(default_factory=list)


class Playbook(Schema):
    id: str
    name: str
    when_to_use: str
    steps: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    deliverable_spec: str = ""
    quality_checklist: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    version: int = 1


class Lesson(Schema):
    id: str
    source: LessonSource
    task_id: Optional[str] = None
    artifact_id: Optional[str] = None
    what_went_wrong: str
    lesson: str
    applied_to: list[str] = Field(default_factory=list)
    ts: int


class EvidenceCitation(Schema):
    source: str
    url: Optional[str] = None
    quote: str


class EvidencePack(Schema):
    id: str
    artifact_id: str
    citations: list[EvidenceCitation] = Field(default_factory=list)
    raw_notes: str = ""
    confidence: float = 0.0
    gaps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    methodology: Optional[str] = None


class ArtifactReviewGate(Schema):
    required: bool
    reason: str
    evidence_pack_id: str
    critique_report_id: str
    preview_available: bool
    checked_at: int
    confidence: float = 0.0
    gaps: list[str] = Field(default_factory=list)
    risk_count: int = 0


class ArtifactQualityReview(Schema):
    artifact: Artifact
    evidence_pack: EvidencePack
    critique_report: CritiqueReport
    gate: ArtifactReviewGate


class Connector(Schema):
    id: str
    name: str
    kind: ConnectorKind
    status: ConnectorStatus
    description: str
    tools: list[ToolName] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    runtime_status: Optional[str] = None
    read_ready: bool = True
    write_ready: bool = False
    local_fallback: bool = False
    external_write_requires: list[str] = Field(default_factory=list)
    proof_status: ConnectorProofStatus = "not_required"
    proof_summary: Optional[str] = None
    proof_gaps: list[str] = Field(default_factory=list)
    proof_details: dict[str, Any] = Field(default_factory=dict)


class HostBridgeParityConnectorProof(Schema):
    id: str
    proof_status: ConnectorProofStatus
    proof_summary: Optional[str] = None
    proof_gaps: list[str] = Field(default_factory=list)
    proof_details: dict[str, Any] = Field(default_factory=dict)


class HostBridgeParityStatusResponse(Schema):
    ok: bool
    status: HostBridgeParityStatus
    summary: str
    gaps: list[str] = Field(default_factory=list)
    report: dict[str, Any] = Field(default_factory=dict)
    local: dict[str, Any] = Field(default_factory=dict)
    connectors: list[HostBridgeParityConnectorProof] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)


class CreateHandoffMessageInput(Schema):
    from_: str = Field(alias="from")
    act: HandoffAct
    text: str
    task_id: Optional[str] = None


class CreateBulletinInput(Schema):
    title: str
    body: str
    scope: Any = "all"
    author: str
    approved_by: Optional[str] = "executive"
    pinned: bool = False
    expires_at: Optional[int] = None
    links: list[str] = Field(default_factory=list)


class CreateProjectInput(Schema):
    id: Optional[str] = None
    name: str
    goal: str
    brief: str = ""
    shared_notes: str = ""
    departments: list[str] = Field(default_factory=list)
    lead: Optional[str] = None


class UpdateProjectInput(Schema):
    name: Optional[str] = None
    goal: Optional[str] = None
    brief: Optional[str] = None
    shared_notes: Optional[str] = None
    status: Optional[ProjectStatus] = None
    departments: Optional[list[str]] = None
    lead: Optional[str] = None
    deliverable_artifact_id: Optional[str] = None


class ResolveProjectInput(Schema):
    decision: ApprovalStatus
    deliverable_artifact_id: Optional[str] = None
    task_id: Optional[str] = None
    resolved_by: str = "user"


class CreateOrgPlanInput(Schema):
    objective: str
    interview_summary: str = ""
    departments: list[OrgPlanDepartment] = Field(default_factory=list)
    created_by: str = "executive"
    # Compatibility input only. Full Auto applies org plans immediately.
    require_approval: bool = False


class UpdateOrgPlanInput(Schema):
    objective: Optional[str] = None
    interview_summary: Optional[str] = None
    departments: Optional[list[OrgPlanDepartment]] = None
    updated_by: str = "user"


class ResolveOrgPlanInput(Schema):
    decision: ApprovalStatus
    resolved_by: str = "user"


class CreateWarRoomInput(Schema):
    title: str
    goal: str
    lead: Optional[str] = None
    members: list[str] = Field(default_factory=list)
    task_id: Optional[str] = None
    project_id: Optional[str] = None
    scratchpad: str = ""


class UpdateWarRoomInput(Schema):
    status: Optional[WarRoomStatus] = None
    scratchpad: Optional[str] = None
    lead: Optional[str] = None
    members: Optional[list[str]] = None
    decisions: Optional[list[str]] = None
    artifacts: Optional[list[str]] = None


class CreateSkillInput(Schema):
    id: Optional[str] = None
    name: str
    description: str
    tools: list[str] = Field(default_factory=list)
    suggested_model: Optional[str] = None
    cost_profile: Optional[str] = None


class CreatePreferenceInput(Schema):
    id: Optional[str] = None
    category: PrefCategory
    text: str
    confidence: float = 0.7
    source: Optional[str] = None


class UpdatePreferenceInput(Schema):
    category: Optional[PrefCategory] = None
    text: Optional[str] = None
    confidence: Optional[float] = None
    source: Optional[str] = None


class PeekDepartmentResponse(Schema):
    department: Department
    tasks: list[Task] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    knowledge: list[MemoryKnowledgeEntry] = Field(default_factory=list)
    log_id: str


class CreateTriggerInput(Schema):
    id: Optional[str] = None
    title: str
    kind: TriggerKind
    target: str
    cadence: Optional[str] = None
    schedule: Optional[dict[str, Any]] = None
    one_shot_at: Optional[int] = None
    event: Optional[TriggerEvent] = None
    enabled: bool = True


class UpdateTriggerInput(Schema):
    title: Optional[str] = None
    enabled: Optional[bool] = None
    cadence: Optional[str] = None
    schedule: Optional[dict[str, Any]] = None
    one_shot_at: Optional[int] = None
    event: Optional[TriggerEvent] = None
    target: Optional[str] = None
    next_run_at: Optional[int] = None


class CreateMeetingInput(Schema):
    title: str
    project_id: Optional[str] = None
    agenda: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    notes: str = ""
    action_items: list[str] = Field(default_factory=list)
    status: MeetingStatus = "scheduled"


class UpdateMeetingInput(Schema):
    notes: Optional[str] = None
    decisions: Optional[list[str]] = None
    action_items: Optional[list[str]] = None
    status: Optional[MeetingStatus] = None


class CreatePlaybookInput(Schema):
    id: Optional[str] = None
    name: str
    when_to_use: str
    steps: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    deliverable_spec: str = ""
    quality_checklist: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class UpdatePlaybookInput(Schema):
    name: Optional[str] = None
    when_to_use: Optional[str] = None
    steps: Optional[list[str]] = None
    required_skills: Optional[list[str]] = None
    deliverable_spec: Optional[str] = None
    quality_checklist: Optional[list[str]] = None
    examples: Optional[list[str]] = None


class CreateLessonInput(Schema):
    source: LessonSource
    task_id: Optional[str] = None
    artifact_id: Optional[str] = None
    what_went_wrong: str
    lesson: str
    applied_to: list[str] = Field(default_factory=list)


class CreateEvidencePackInput(Schema):
    artifact_id: str
    citations: list[EvidenceCitation] = Field(default_factory=list)
    raw_notes: str = ""
    confidence: float = 0.0
    gaps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    methodology: Optional[str] = None


class CreateCritiqueInput(Schema):
    target_type: Literal["task", "project", "decision", "artifact"]
    target_id: str
    focus: Optional[str] = None


class ImportFileInput(Schema):
    source_path: str
    target_dept: str
    project_id: Optional[str] = None
    artifact_name: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    copy_to_workspace: bool = True


class ImportFileResponse(Schema):
    artifact: Artifact
    knowledge: Optional[MemoryKnowledgeEntry] = None


ImageQuality = Literal["low", "medium", "high", "auto"]
ImageOutputFormat = Literal["png", "jpeg", "webp"]
ImageBackground = Literal["auto", "opaque", "transparent"]
ImageModeration = Literal["auto", "low"]


class ImageArtifactLocation(Schema):
    artifact_id: str
    name: str
    mime: Optional[str] = None
    uri: str
    storage: Optional[str] = None
    content_hash: Optional[str] = None
    content_size_bytes: Optional[int] = None
    download_url: str
    preview_url: str
    local_path: Optional[str] = None


class GenerateImageInput(Schema):
    prompt: str = Field(min_length=1, max_length=32000)
    owner_dept: Optional[str] = None
    thread_id: Optional[str] = None
    project_id: Optional[str] = None
    task_ids: list[str] = Field(default_factory=list)
    artifact_name: Optional[str] = None
    reference_artifact_ids: list[str] = Field(default_factory=list)
    mask_artifact_id: Optional[str] = None
    model: Optional[str] = None
    model_override_approved: bool = False
    model_override_reason: Optional[str] = None
    fallback_from_model: Optional[str] = None
    primary_model_error: Optional[str] = None
    n: int = Field(default=1, ge=1, le=10)
    size: Optional[str] = None
    width: Optional[int] = Field(default=None, ge=16, le=3840)
    height: Optional[int] = Field(default=None, ge=16, le=3840)
    aspect_ratio: Optional[str] = None
    resolution: Optional[str] = None
    clarity: Optional[str] = None
    quality: Optional[ImageQuality] = None
    output_format: Optional[ImageOutputFormat] = None
    output_compression: Optional[int] = Field(default=None, ge=0, le=100)
    background: Optional[ImageBackground] = None
    moderation: Optional[ImageModeration] = None
    async_mode: bool = False
    wait_for_result: bool = False
    wake_on_complete: bool = True
    requested_by: Optional[str] = None
    requester_name: Optional[str] = None
    created_by: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class GenerateImageResponse(Schema):
    ok: bool
    status: Optional[str] = None
    async_mode: bool = False
    job_id: Optional[str] = None
    run_id: Optional[str] = None
    message_id: Optional[str] = None
    queued_at: Optional[int] = None
    status_url: Optional[str] = None
    mode: Literal["generate", "edit"]
    summary: str
    model: str
    provider: str
    artifacts: list[Artifact] = Field(default_factory=list)
    artifact: Optional[Artifact] = None
    locations: list[ImageArtifactLocation] = Field(default_factory=list)
    usage: Optional[dict[str, Any]] = None
    request_id: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)
    reference_artifact_ids: list[str] = Field(default_factory=list)
    mask_artifact_id: Optional[str] = None
    model_policy: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    job: Optional[dict[str, Any]] = None
    jobs: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeDebtDepartment(Schema):
    department_id: str
    stale: int
    conflicting: int
    unsourced: int
    orphaned: int
    duplicate: int
    health: float


class KnowledgeDebtReport(Schema):
    departments: list[KnowledgeDebtDepartment]
    generated_at: int
