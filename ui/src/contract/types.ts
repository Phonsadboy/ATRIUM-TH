/* ============================================================
   ATRIUM — domain contract
   These types ARE the seam between this UI and the future
   backend. The real API/WebSocket layer must speak this shape.
   ============================================================ */

export type ID = string

/* ---------- Agents & departments ---------- */

export type AgentState =
  | 'idle'
  | 'thinking'
  | 'working'
  | 'review'
  | 'handoff'
  | 'blocked'
  | 'offline'

export type AccentName =
  | 'amber'
  | 'teal'
  | 'coral'
  | 'lavender'
  | 'sky'
  | 'honey'

export type AiProviderId = 'anthropic' | 'openai' | 'chatgpt_account' | 'claude_code'

export type ModelId =
  | 'claude-sonnet-4-6'
  | 'claude-opus-4-6'
  | 'claude-opus-4-7'
  | 'claude-sonnet-4-7'
  | 'claude-opus-4-8'
  | 'gpt-5.5'
  | 'gpt-5.4-mini'
  | 'gpt-5.3-codex'

export type ThinkingEffort = 'off' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'

/** Claude Fast Mode switch. Only legal on selected Opus models; the backend is
 *  the final arbiter and coerces an unsupported request back to 'standard'. */
export type ModelSpeed = 'standard' | 'fast'

/** Office floor placement, in tile units. */
export interface RoomRect {
  x: number
  y: number
  w: number
  h: number
}

export interface MemoryStats {
  archiveChunks: number
  ragEntries: number
  graphNodes: number
  graphEdges: number
  lastCompactionAt: number | null
  tokensSaved: number
  workingSummary?: string | null
  workingArchiveId?: string | null
  workingThreadId?: string | null
  workingMessageCount?: number | null
  workingUpdatedAt?: number | null
}

export interface VisibilityPolicy {
  dept: ID
  archive: 'private'
  knowledge: 'private' | 'on_request'
  tasks: 'company' | 'on_request'
  artifacts: 'company'
}

export interface Department {
  id: ID
  name: string
  role: string
  charter: string
  emoji: string
  accent: AccentName
  providerId: AiProviderId
  model: ModelId
  thinkingEffort: ThinkingEffort
  /** standard response path, or Claude Fast Mode (Opus-only). */
  speed: ModelSpeed
  agentName: string
  state: AgentState
  /** 0..1 wellbeing / load — drives the character's expression. */
  mood: number
  currentTaskId: ID | null
  autonomy: boolean
  createdAt: number
  room: RoomRect
  memory: MemoryStats
  skills: string[]
  tools: string[]
  workspacePath?: string | null
  visibilityPolicy?: VisibilityPolicy | null
}

/* ---------- Memory ("Godly Compact") ---------- */

export type MemoryKind = 'archive' | 'knowledge' | 'graph'

export interface MemoryArchiveEntry {
  id: ID
  title: string
  ts: number
  tokens: number
  summary: string
}

export interface MemoryKnowledgeEntry {
  id: ID
  title: string
  ts: number
  /** retrieval relevance, 0..1 */
  score: number
  text: string
  tags: string[]
  source?: string | null
}

export type GraphNodeType =
  | 'concept'
  | 'entity'
  | 'task'
  | 'person'
  | 'artifact'

export interface GraphNode {
  id: ID
  label: string
  type: GraphNodeType
  /** normalized layout position 0..1 */
  x: number
  y: number
}

export interface GraphEdge {
  from: ID
  to: ID
  rel: string
}

export interface MemoryGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface DepartmentMemory {
  departmentId: ID
  archive: MemoryArchiveEntry[]
  knowledge: MemoryKnowledgeEntry[]
  graph: MemoryGraph
}

/* ---------- Tasks ---------- */

export type TaskStatus =
  | 'backlog'
  | 'assigned'
  | 'in_progress'
  | 'review'
  | 'revising'
  | 'done'
  | 'blocked'
  | 'cancelled'

export type Priority = 'low' | 'normal' | 'high' | 'urgent'

export type Originator =
  | { kind: 'user' }
  | { kind: 'executive' }
  | { kind: 'department'; id: ID }

export type HandoffKind = 'delegate' | 'consult' | 'collaborate' | 'return'

export interface Handoff {
  id: ID
  fromDept: ID
  toDept: ID
  ts: number
  reason: string
  kind: HandoffKind
  status?: string
}

export interface Task {
  id: ID
  title: string
  detail: string
  status: TaskStatus
  priority: Priority
  /** current owning department, or null while in backlog */
  departmentId: ID | null
  origin: Originator
  /** 0..1 */
  progress: number
  createdAt: number
  updatedAt: number
  handoffs: Handoff[]
  log: string[]
  /** set while this task is parked pending another department's handoff */
  waitingOn?: { dept: ID; handoffId: ID }
  /** v0.4: optional project grouping and durable outputs. */
  projectId?: ID | null
  deliverables?: ID[]
  watchers?: ID[]
  parentTaskId?: ID | null
  subTaskIds?: ID[]
  deadlineAt?: number | null
  result?: Record<string, unknown> | null
}

/* ---------- Chat ---------- */

export type ThreadId = string // 'executive' | `dept:${ID}` | `war:${ID}`

export type ChatRole = 'user' | 'executive' | 'agent' | 'system'

export type MessageRenderFormat = 'plain' | 'markdown'
export type MessageRenderNotice =
  | 'budget_guardrail'
  | 'context_compacted'
  | 'rate_limited'
  | 'rate_limit_warning'
  | 'budget_warning'
  | 'approval_required'
  | 'message_failed'
  | 'runtime_dependency'

export type MessageStatus = 'queued' | 'sending' | 'sent' | 'failed' | 'cancelled' | 'blocked' | 'pending_approval'

export interface MessageCodeBlock {
  index: number
  language?: string | null
  text: string
}

export interface MessageLink {
  url: string
  label?: string | null
}

export interface MessageMention {
  raw: string
  target?: string | null
}

export interface MessageCitation {
  id: ID
  kind: 'knowledge' | 'archive' | 'graph' | 'memory'
  label: string
  title?: string | null
  source?: string | null
  score?: number | null
  snippet?: string | null
  departmentId?: ID | null
}

export interface MessageRenderMetadata {
  format: MessageRenderFormat
  codeBlocks: MessageCodeBlock[]
  links: MessageLink[]
  mentions: MessageMention[]
  citations: MessageCitation[]
  costUsd?: number | null
  compactEnqueued: boolean
  notices: MessageRenderNotice[]
  severity?: Severity | null
}

export type ReasoningStatus =
  | 'available'
  | 'redacted'
  | 'omitted'
  | 'disabled'
  | 'unavailable'

export interface MessageReaction {
  emoji: string
  actor: string
  ts: number
}

export interface MessageError {
  code: string
  detail: string
  retryable: boolean
}

export interface ChatMessage {
  id: ID
  threadId: ThreadId
  role: ChatRole
  authorName: string
  text: string
  ts: number
  pending?: boolean
  status?: MessageStatus | null
  error?: MessageError | null
  replyToMessageId?: ID | null
  parentMessageId?: ID | null
  quoteMessageId?: ID | null
  quoteText?: string | null
  quoteAuthorName?: string | null
  retryOfMessageId?: ID | null
  regeneratedFromMessageId?: ID | null
  editedFromMessageId?: ID | null
  branchFromThreadId?: ThreadId | null
  branchPointMessageId?: ID | null
  branchCopyOfMessageId?: ID | null
  pinned?: boolean | null
  pinnedAt?: number | null
  memoryPromotedId?: ID | null
  reactions?: MessageReaction[]
  approvalId?: ID | null
  approvalStatus?: ApprovalStatus | null
  toolRunId?: ID | null
  contentFormat?: MessageRenderFormat | null
  render?: MessageRenderMetadata | null
  /** adaptive-thinking transparency (present from the FastAPI backend) */
  reasoning?: string | null
  reasoningSummary?: string | null
  reasoningStatus?: ReasoningStatus | null
  reasoningRedacted?: boolean | null
  thinkingTokens?: number | null
  generationMs?: number | null
  attachments?: MessageAttachment[]
  mentions?: MessageMention[]
  suggestedFollowUps?: SuggestedFollowUp[]
  input?: MessageInputMetadata | null
  /** ATRIUM domain context the backend tags onto messages */
  departmentId?: ID | null
  agentState?: AgentState | null
  agentMood?: number | null
  participant?: ChatParticipant | null
  activityId?: ID | null
  activityType?: ActivityType | null
  flow?: ChatFlow | null
  warRoomId?: ID | null
  meetingId?: ID | null
  threadCost?: ThreadCostSummary | null
  toolRuns?: ChatToolRun[] | null
  /** UI-only: client-minted id used for optimistic insert + idempotent retry */
  clientMessageId?: ID | null
  /** UI-only: this reply is mid-stream (live token / thinking / tool overlay). */
  streaming?: boolean | null
  /** UI-only: chronological breakdown of one assistant turn (thinking ↔ tools ↔
   *  text in the real order they happened), built live from the stream pulses.
   *  Absent for history loaded without a live stream — render the flat fallback. */
  segments?: TurnSegment[] | null
}

/** UI-only: one ordered step of an assistant turn. Lets the chat render the turn
 *  as a timeline (think → use tools → think again → answer) instead of collapsing
 *  all thinking into one block above all tools. */
export type TurnSegment =
  | { kind: 'thinking'; text: string }
  | { kind: 'tools'; runs: ChatToolRun[] }
  | { kind: 'text'; text: string }

/* ---------- Chat: rich message sub-shapes ---------- */

export interface MessageAttachment {
  artifactId?: ID | null
  kind?: string | null
  mime?: string | null
  name?: string | null
  sizeBytes?: number | null
  uri?: string | null
  projectId?: ID | null
  videoProjectId?: ID | null
  assetId?: ID | null
  timelineId?: ID | null
  timelineVersion?: number | null
  renderId?: ID | null
  mediaHandle?: string | null
  contextTool?: string | null
  contextArgs?: Record<string, unknown> | null
  reviewStatus?: string | null
  videoReviewId?: ID | null
  approvalId?: ID | null
}

export interface MessageMentionTarget {
  raw: string
  departmentId?: ID | null
  agentName?: string | null
  displayName?: string | null
  threadId?: ThreadId | null
  matchedBy?: string | null
}

export interface SuggestedFollowUp {
  id: ID
  label: string
  text: string
}

export interface MessageInputMetadata {
  characterCount?: number
  estimatedTokens?: number
  attachmentCount?: number
  command?: string | null
  commandArgs?: string | null
  routedDepartmentId?: ID | null
  status?: string | null
}

export interface ChatParticipant {
  departmentId?: ID | null
  departmentName?: string | null
  agentName?: string | null
  role?: string | null
  state?: AgentState | string | null
  mood?: number | null
  currentTaskId?: ID | null
}

export interface ChatFlowStep {
  kind: string
  label?: string | null
  departmentId?: ID | null
  taskId?: ID | null
  warRoomId?: ID | null
  meetingId?: ID | null
}

export interface ChatFlow {
  kind: string
  title?: string | null
  steps?: ChatFlowStep[]
  refs?: Record<string, unknown>
}

/** A tool invocation carried inline on a chat message (chat tool loop). */
export interface ChatToolRun {
  id: ID
  /** the model's tool_use id — stable key for live streaming upserts */
  toolUseId?: ID | null
  tool: string
  status: string
  departmentId?: ID | null
  requestedBy?: string | null
  args?: Record<string, unknown>
  argsRedacted?: boolean
  redactedFields?: string[]
  result?: Record<string, unknown> | null
  error?: string | null
  approvalId?: ID | null
  riskClass?: ToolRiskClass | string | null
  policyDecision?: PolicyDecision | string | null
  policyReason?: string | null
  label?: string | null
  summary?: string | null
  createdAt?: number
  startedAt?: number | null
  completedAt?: number | null
}

export interface VideoJobRecord {
  id: ID
  kind?: string | null
  tool?: string | null
  status?: string | null
  projectId?: ID | null
  ownerDept?: ID | null
  progress?: {
    phase?: string | null
    percent?: number | null
    message?: string | null
  } | null
  result?: Record<string, unknown> | null
  error?: string | null
  manifestPath?: string | null
  logPath?: string | null
  statusUrl?: string | null
  logs?: string[] | null
  events?: Record<string, unknown>[] | null
  [key: string]: unknown
}

export interface ThreadCostSummary {
  threadId: ThreadId
  totalUsd?: number
  messageCount?: number
  costedMessages?: number
  byAgent?: Record<string, number>
  updatedAt: number
}

export interface SlashCommandSpec {
  name: string
  aliases?: string[]
  description: string
  usage: string
  examples?: string[]
}

export interface SlashCommandResult {
  name: string
  ok: boolean
  message: string
  data?: Record<string, unknown>
}

export interface ChatRateLimitState {
  limit: number
  remaining: number
  resetAt: number
  windowMs: number
  exceeded?: boolean
}

export interface ChatBudgetState {
  dailyCapUsd: number
  spentTodayUsd: number
  estimatedUsd: number
  remainingUsd: number
  warningRatio: number
  wouldExceed?: boolean
}

export interface SafetyWarning {
  code: string
  message: string
  severity?: Severity | string
  retryAfterMs?: number | null
}

/* ---------- Chat: send / usage envelope ---------- */

export interface MessageUsage {
  usd: number
  threadUsd?: number
  ragHits?: string[]
  compactEnqueued?: boolean
  generationMs?: number | null
  model?: string | null
  providerId?: string | null
  thinkingEffort?: ThinkingEffort | string | null
  thinkingTokens?: number
  reasoningStatus?: ReasoningStatus | string | null
  reasoningChars?: number
  redactedThinking?: boolean
  stopReason?: string
  tokensIn?: number
  tokensOut?: number
  toolRuns?: ChatToolRun[]
  rateLimit?: ChatRateLimitState | null
  budget?: ChatBudgetState | null
  warnings?: SafetyWarning[]
}

export interface SendMessageResponse {
  message: ChatMessage
  usage: MessageUsage
  messages?: ChatMessage[]
  activity?: ActivityEvent[]
  command?: SlashCommandResult | null
  draftCleared?: boolean
  mentions?: MessageMentionTarget[]
  suggestedFollowUps?: SuggestedFollowUp[]
  tokenEstimate?: InputEstimate | null
}

/** Client-side options for ApiClient.sendMessage (camelCase wire shape). */
export interface SendMessageOptions {
  attachments?: MessageAttachment[]
  attachmentIds?: ID[]
  targetDepartmentId?: ID | null
  thinkingEffort?: ThinkingEffort | null
  clientMessageId?: ID | null
  parentMessageId?: ID | null
  quoteMessageId?: ID | null
  retryOfMessageId?: ID | null
  queueIfBusy?: boolean
}

export interface RegenerateMessageInput {
  messageId?: ID | null
  thinkingEffort?: ThinkingEffort | null
}

export interface RetryMessageInput {
  messageId: ID
  clientMessageId?: ID | null
  thinkingEffort?: ThinkingEffort | null
}

export type MessageActionName = 'copy' | 'pin' | 'unpin' | 'react' | 'unreact'

export interface MessageActionInput {
  action: MessageActionName | string
  actor?: string
  reaction?: string | null
}

export interface MessageActionResponse {
  action: string
  message: ChatMessage
  mutated: boolean
}

export interface EditMessageInput {
  text: string
  branchId?: ID | null
  thinkingEffort?: ThinkingEffort | null
}

export interface BranchConversationResponse {
  branchThreadId: ThreadId
  editedMessage: ChatMessage
  response: SendMessageResponse
  copiedCount?: number
}

export interface PromoteMessageInput {
  title?: string | null
  tags?: string[]
  actor?: string
}

export interface PromoteMessageResponse {
  message: ChatMessage
  knowledge: Record<string, unknown>
}

/* ---------- Chat: thread utilities ---------- */

export interface ThreadDraft {
  id: ID
  threadId: ThreadId
  text?: string
  attachments?: MessageAttachment[]
  updatedAt: number
}

export interface ThreadDraftInput {
  text?: string
  attachments?: MessageAttachment[]
  attachmentIds?: ID[]
}

export interface ThreadExportResponse {
  threadId: ThreadId
  format: string
  content: string
  contentType: string
  filename: string
  messageCount: number
  exportedAt: number
}

export interface InputEstimate {
  characters: number
  words: number
  estimatedTokens: number
  maxRecommendedTokens: number
  withinLimit: boolean
  warnings?: string[]
  attachmentCount?: number
}

export interface InputEstimateInput {
  text?: string
  attachments?: MessageAttachment[]
  attachmentIds?: ID[]
}

export interface ThreadSearchHit {
  message: ChatMessage
  score: number
  snippet: string
}

export interface ThreadSearchResponse {
  threadId: ThreadId
  query: string
  hits?: ThreadSearchHit[]
}

export interface ThreadStatsResponse {
  threadId: ThreadId
  total: number
  newCount?: number
  latestMessageId?: ID | null
  latestTs?: number | null
}

export interface PromptStarter {
  id: ID
  threadId: ThreadId
  departmentId?: ID | null
  label: string
  text: string
  category: string
}

/* ---------- Activity feed ---------- */

export type ActivityType =
  | 'task_created'
  | 'task_assigned'
  | 'task_progress'
  | 'task_done'
  | 'handoff'
  | 'state_change'
  | 'compaction'
  | 'spend'
  | 'approval'
  | 'autonomous'
  | 'message'
  | 'system'

export type Severity = 'info' | 'good' | 'warn' | 'alert'

export interface ActivityEvent {
  id: ID
  ts: number
  type: ActivityType
  departmentId: ID | null
  text: string
  severity: Severity
}

/* ---------- Approvals ---------- */

export type ApprovalKind = 'external_action' | 'publish' | 'destructive_action' | 'task_close'

export type ApprovalStatus = 'pending' | 'approved' | 'rejected'

export interface Approval {
  id: ID
  ts: number
  kind: ApprovalKind
  title: string
  detail: string
  departmentId: ID | null
  costUsd?: number
  status: ApprovalStatus
  /** structured action the backend executes once a guarded approval is approved */
  action?: Record<string, unknown> | null
}

/* ---------- v0.4 durable work records ---------- */

export type ArtifactKind = 'file' | 'doc' | 'code' | 'report' | 'link' | 'memo' | 'dataset' | 'image'
export type ArtifactStatus = 'draft' | 'in_review' | 'approved' | 'published' | 'superseded' | 'archived'
export type PreviewKind = 'md' | 'diff' | 'image' | 'screenshot' | 'sheet' | 'pdf'

export interface ArtifactPreview {
  kind: PreviewKind
  uri: string
}

export type ArtifactApprovalTier = 'department' | 'user'

export interface Artifact {
  id: ID
  name: string
  kind: ArtifactKind
  mime?: string | null
  ownerDept: ID
  taskIds: ID[]
  projectId?: ID | null
  version: number
  status: ArtifactStatus
  uri: string
  storage?: 'object_store' | 'filesystem' | 'external' | null
  contentHash?: string | null
  contentMime?: string | null
  contentSizeBytes?: number | null
  tags: string[]
  links: string[]
  preview?: ArtifactPreview | null
  approvalTier?: ArtifactApprovalTier | null
  approvedAt?: number | null
  approvedBy?: string | null
  reviewStatus?: string | null
  videoProjectId?: ID | null
  assetId?: ID | null
  timelineId?: ID | null
  timelineVersion?: number | null
  renderId?: ID | null
  mediaHandle?: string | null
  contextTool?: string | null
  contextArgs?: Record<string, unknown> | null
  videoReviewId?: ID | null
  approvalId?: ID | null
  reviewGate?: {
    required?: boolean
    reason?: string
    evidencePackId?: ID
    critiqueReportId?: ID
    previewAvailable?: boolean
    checkedAt?: number
    confidence?: number
    gaps?: string[]
    riskCount?: number
  } | null
  createdAt: number
  createdBy: string
  updatedAt: number
  updatedBy: string
}

export interface ArtifactVersion {
  artifactId: ID
  version: number
  author: string
  ts: number
  note: string
  parent?: number | null
  preview?: ArtifactPreview | null
  uri: string
  storage?: 'object_store' | 'filesystem' | 'external' | null
  contentHash?: string | null
  contentMime?: string | null
  contentSizeBytes?: number | null
}

export type DecisionStatus = 'proposed' | 'approved' | 'rejected' | 'superseded'

export interface Decision {
  id: ID
  title: string
  proposedBy: ID
  approvedBy?: ID | null
  rationale?: string | null
  alternatives: string[]
  impact: string
  linkedTask?: ID | null
  linkedArtifacts: ID[]
  status: DecisionStatus
  supersedes?: ID | null
  ts: number
}

export type NotifType =
  | 'approval'
  | 'budget'
  | 'blocked'
  | 'task_done'
  | 'digest'
  | 'crash'
  | 'knowledge_debt'
  | 'security'

export interface Notification {
  id: ID
  type: NotifType
  severity: Severity
  title: string
  body: string
  ts: number
  read: boolean
  links: string[]
}

/* ---------- Scheduler / objectives ---------- */

export interface ScheduledObjective {
  id: ID
  title: string
  cadence: string
  departmentId: ID
  enabled: boolean
  lastRunAt: number | null
  nextRunAt: number
}

export interface CreateObjectiveInput {
  id?: ID | null
  title: string
  cadence: string
  departmentId: ID
  enabled?: boolean
  nextRunAt?: number | null
}

export interface UpdateObjectiveInput {
  title?: string | null
  cadence?: string | null
  departmentId?: ID | null
  enabled?: boolean | null
  nextRunAt?: number | null
}

/* ---------- Cost tracking ---------- */

export type CostCategory =
  | 'work'
  | 'chat'
  | 'meeting'
  | 'autonomous'
  | 'memory'
  | 'tool'

export interface CostRecord {
  id: ID
  ts: number
  departmentId: ID
  category: CostCategory
  usd: number
  detail?: string
}

export type CostReportScope = 'day' | 'month' | 'project' | 'dept' | 'agent'

export interface CostReport {
  scope: CostReportScope
  spentUsd: number
  forecastUsd: number
  byCategory: Record<CostCategory, number>
  byAgent: Record<string, number>
  series: { day: string; usd: number }[]
  anomalies: string[]
}

/* ---------- Budget ---------- */

export interface Budget {
  dailyCapUsd: number
  spentTodayUsd: number
}

/* ---------- Owner Mode / permission policy ---------- */

/** Runtime permission mode. ATRIUM forces this to full_auto for the owner-operated profile. */
export type PolicyMode = 'approve_everything' | 'critical_only' | 'full_auto'

export interface ToolCatalogItem {
  tool: ToolName
  riskClass: ToolRiskClass
  mutatesState: boolean
  externalSystem: boolean
  description: string
  executor?: string
  defaultTimeoutMs?: number
  outputLimitBytes?: number
  supportsCheckpoint?: boolean
  canUseCredentials?: boolean
  rollbackCapable?: boolean
  inputSchema?: Record<string, unknown>
  outputSchema?: Record<string, unknown>
  redactionRules?: string[]
}

export interface PermissionPolicy {
  mode: PolicyMode
  updatedAt?: number | null
  updatedBy?: string | null
  toolCatalog: ToolCatalogItem[]
}

export type ToolRiskClass =
  | 'safe_read'
  | 'local_write'
  | 'host_write'
  | 'command'
  | 'network'
  | 'desktop'
  | 'credential'
  | 'external_send'
  | 'destructive'
  | 'privileged'

export type ToolName =
  | 'read_file'
  | 'write_file'
  | 'copy_file'
  | 'move_file'
  | 'http_get'
  | 'import_url'
  | 'run_command'
  | 'fs.list'
  | 'fs.read'
  | 'fs.write'
  | 'fs.patch'
  | 'fs.copy'
  | 'fs.move'
  | 'fs.delete'
  | 'shell.exec'
  | 'sandbox.exec'
  | 'git.status'
  | 'git.diff'
  | 'git.commit'
  | 'git.push'
  | 'browser.open'
  | 'browser.click'
  | 'browser.type'
  | 'browser.keypress'
  | 'browser.paste_text'
  | 'desktop.screenshot'
  | 'desktop.click'
  | 'desktop.type'
  | 'desktop.keypress'
  | 'desktop.paste_text'
  | 'http.get'
  | 'http.post'
  | 'import.url'
  | 'mcp.call'
  | 'notify.send'
  | 'scheduler.create'
  | 'logs.query'
  | 'logs.note'

export type ToolRunStatus =
  | 'queued'
  | 'pending_approval'
  | 'running'
  | 'succeeded'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'blocked'
export type PolicyDecision =
  | 'auto_approved'
  | 'approval_required'
  | 'blocked_by_policy'
  | 'blocked_by_runtime'

export interface ToolRun {
  id: ID
  tool: ToolName
  departmentId: ID
  threadId?: ThreadId | null
  requestedBy: string
  status: ToolRunStatus
  createdAt: number
  args: Record<string, unknown>
  argsRedacted?: boolean
  redactedFields?: string[]
  riskClass?: ToolRiskClass | null
  policyDecision?: PolicyDecision | null
  taskId?: ID | null
  approvalId?: ID | null
  result?: Record<string, unknown> | null
  error?: string | null
  completedAt?: number | null
}

export type PreferenceCategory = 'comm' | 'code' | 'design' | 'risk' | 'budget' | 'general'

export interface Preference {
  id: ID
  category: PreferenceCategory
  text: string
  confidence: number
  ts: number
  source?: string | null
}

export interface OwnerProfile {
  id: 'owner'
  scope: 'company-global'
  preferences: Preference[]
  byCategory: Partial<Record<PreferenceCategory, Preference[]>>
  updatedAt?: number | null
}

export interface Executive {
  id: ID
  agentName: string
  providerId: AiProviderId
  model: ModelId
  thinkingEffort: ThinkingEffort
  systemPrompt: string
  dailyBudgetUsd: number
  workspacePath?: string | null
  tools: string[]
  autonomy: boolean
}

/* ---------- Company snapshot ---------- */

export interface CompanyState {
  companyName: string
  /** server wall clock (ms) */
  now: number
  /** master switch — false means the kill switch is engaged */
  running: boolean
  departments: Department[]
  tasks: Task[]
  threads: Record<ThreadId, ChatMessage[]>
  activity: ActivityEvent[]
  approvals: Approval[]
  objectives: ScheduledObjective[]
  budget: Budget
  /** Owner Mode guardrail policy (present from the FastAPI backend). */
  permissionPolicy?: PermissionPolicy
}

/* ============================================================
   Extended backend surface ("Console")
   Entities the backend exposes via dedicated REST endpoints,
   not carried in the CompanyState snapshot. Wire shape mirrors
   contract/openapi.json (camelCase on the wire). Loaded on demand.
   ============================================================ */

/* ---------- Projects (two-tier review) ---------- */

export type ProjectStatus = 'active' | 'paused' | 'done' | 'archived'
export type ProjectReviewStatus =
  | 'draft'
  | 'pending_user'
  | 'approved_by_user'
  | 'rejected_by_user'

export interface Project {
  id: ID
  name: string
  goal: string
  status: ProjectStatus
  workspacePath: string
  departments: ID[]
  lead?: ID | null
  deliverableArtifactId?: ID | null
  finalApprovalId?: ID | null
  reviewStatus?: ProjectReviewStatus | null
  resolvedBy?: ID | null
  createdAt: number
  completedAt?: number | null
}

export interface CreateProjectInput {
  name: string
  goal: string
  departments?: ID[]
  lead?: ID | null
  id?: ID | null
}

export interface UpdateProjectInput {
  name?: string | null
  goal?: string | null
  status?: ProjectStatus | null
  departments?: ID[] | null
  lead?: ID | null
  deliverableArtifactId?: ID | null
}

export type ProjectResolveDecision = 'pending' | 'approved' | 'rejected'

export interface ResolveProjectInput {
  decision: ProjectResolveDecision
  deliverableArtifactId?: ID | null
  resolvedBy?: string
  taskId?: ID | null
}

/* ---------- Executive onboarding org plans ---------- */

export type OrgPlanStatus = 'proposed' | 'approved' | 'rejected' | 'applied'

export interface OrgPlanDepartment {
  id?: ID | null
  name: string
  role: string
  charter?: string | null
  agentName?: string | null
  providerId: AiProviderId
  model?: ModelId | null
  thinkingEffort?: ThinkingEffort | null
  emoji?: string | null
  accent?: AccentName | null
  autonomy?: boolean
  skills?: string[]
  tools?: string[]
}

export interface OrgPlan {
  id: ID
  objective: string
  interviewSummary: string
  status: OrgPlanStatus
  departments: OrgPlanDepartment[]
  createdBy: string
  approvedBy?: string | null
  rejectedBy?: string | null
  approvalId?: ID | null
  appliedDepartmentIds: ID[]
  decisionId?: ID | null
  createdAt: number
  updatedAt: number
}

export interface CreateOrgPlanInput {
  objective: string
  interviewSummary?: string
  departments: OrgPlanDepartment[]
  createdBy?: string
  requireApproval?: boolean
}

export interface UpdateOrgPlanInput {
  objective?: string | null
  interviewSummary?: string | null
  departments?: OrgPlanDepartment[] | null
  updatedBy?: string
}

export interface ResolveOrgPlanInput {
  decision: ApprovalStatus
  resolvedBy?: string
}

/* ---------- Artifacts (versioned outputs) ---------- */

export interface ArtifactContentResponse {
  artifact: Artifact
  content: string
  version: ArtifactVersion
}

export interface ArtifactDiffResponse {
  artifactId: ID
  fromVersion: number
  toVersion: number
  diff: string
}

export interface CreateArtifactInput {
  name: string
  kind: ArtifactKind
  ownerDept: ID
  uri?: string | null
  mime?: string | null
  projectId?: ID | null
  taskIds?: ID[]
  tags?: string[]
  links?: string[]
  preview?: ArtifactPreview | null
  createdBy?: string | null
}

export interface UpdateArtifactInput {
  name?: string | null
  status?: ArtifactStatus | null
  uri?: string | null
  mime?: string | null
  tags?: string[] | null
  links?: string[] | null
  preview?: ArtifactPreview | null
  updatedBy?: string | null
}

export interface ArtifactContentInput {
  text: string
  note?: string
  author?: string | null
  status?: ArtifactStatus | null
  previewKind?: PreviewKind
}

export interface RollbackArtifactInput {
  version: number
  note?: string
  author?: string | null
}

/* ---------- Tool runs (guarded execution) ---------- */

export interface ToolRunInput {
  tool: ToolName
  departmentId: ID
  threadId?: ThreadId | null
  args?: Record<string, unknown>
  requestedBy?: string
  taskId?: ID | null
  requireApproval?: boolean | null
}

export interface ToolRunResponse {
  run: ToolRun
  approval?: Approval | null
  executed?: boolean
}

/* ---------- Meetings ---------- */

export type MeetingStatus = 'scheduled' | 'active' | 'done'

export interface Meeting {
  id: ID
  title: string
  threadId: ThreadId
  ts: number
  status: MeetingStatus
  agenda: string[]
  participants: ID[]
  notes: string
  decisions: string[]
  actionItems: string[]
  projectId?: ID | null
}

export interface CreateMeetingInput {
  title: string
  agenda?: string[]
  participants?: ID[]
  notes?: string
  actionItems?: string[]
  status?: MeetingStatus
  projectId?: ID | null
}

export interface UpdateMeetingInput {
  status?: MeetingStatus | null
  notes?: string | null
  decisions?: string[] | null
  actionItems?: string[] | null
}

export interface MeetingCollaboration {
  meeting: Meeting
  threadId: ThreadId
  participants: ChatParticipant[]
  flow: ChatFlow
  context: string
  messages: ChatMessage[]
}

/* ---------- War rooms ---------- */

export type WarRoomStatus = 'active' | 'resolved' | 'archived'

export interface WarRoom {
  id: ID
  title: string
  goal: string
  threadId: ThreadId
  status: WarRoomStatus
  members: ID[]
  lead?: ID | null
  scratchpad: string
  decisions: string[]
  artifacts: ID[]
  projectId?: ID | null
  taskId?: ID | null
  createdAt: number
}

export interface CreateWarRoomInput {
  title: string
  goal: string
  members?: ID[]
  lead?: ID | null
  scratchpad?: string
  projectId?: ID | null
  taskId?: ID | null
}

export interface UpdateWarRoomInput {
  status?: WarRoomStatus | null
  lead?: ID | null
  members?: ID[] | null
  scratchpad?: string | null
  decisions?: string[] | null
  artifacts?: ID[] | null
}

export interface WarRoomCollaboration {
  warRoom: WarRoom
  threadId: ThreadId
  participants: ChatParticipant[]
  flow: ChatFlow
  context: string
  messages: ChatMessage[]
}

/* ---------- Playbooks (reusable how-to) ---------- */

export interface Playbook {
  id: ID
  name: string
  whenToUse: string
  steps: string[]
  requiredSkills: string[]
  deliverableSpec: string
  qualityChecklist: string[]
  examples: string[]
  version: number
}

export interface CreatePlaybookInput {
  name: string
  whenToUse: string
  steps?: string[]
  requiredSkills?: string[]
  deliverableSpec?: string
  qualityChecklist?: string[]
  examples?: string[]
  id?: ID | null
}

export interface UpdatePlaybookInput {
  name?: string | null
  whenToUse?: string | null
  steps?: string[] | null
  requiredSkills?: string[] | null
  deliverableSpec?: string | null
  qualityChecklist?: string[] | null
  examples?: string[] | null
}

/* ---------- Lessons learned ---------- */

export type LessonSource = 'reject' | 'heavy_edit' | 'low_rating'

export interface Lesson {
  id: ID
  whatWentWrong: string
  lesson: string
  source: LessonSource
  ts: number
  taskId?: ID | null
  artifactId?: ID | null
  appliedTo: string[]
}

export interface CreateLessonInput {
  whatWentWrong: string
  lesson: string
  source: LessonSource
  taskId?: ID | null
  artifactId?: ID | null
  appliedTo?: string[]
}

/* ---------- Bulletins (company board) ---------- */

export interface Bulletin {
  id: ID
  title: string
  body: string
  author: string
  ts: number
  scope: string
  pinned: boolean
  links: string[]
  expiresAt?: number | null
  approvedBy?: string | null
}

export interface CreateBulletinInput {
  title: string
  body: string
  author: string
  scope?: string
  pinned?: boolean
  links?: string[]
  expiresAt?: number | null
  approvedBy?: string | null
}

/* ---------- Triggers (scheduler) ---------- */

export type TriggerKind = 'cron' | 'event'
export type TriggerEvent = 'dept_done' | 'blocked' | 'idle' | 'budget' | 'escalate'

export interface Trigger {
  id: ID
  kind: TriggerKind
  title: string
  target: ID
  cadence?: string | null
  event?: TriggerEvent | null
  enabled: boolean
  oneShotAt?: number | null
  lastRunAt?: number | null
  nextRunAt?: number | null
}

export interface CreateTriggerInput {
  kind: TriggerKind
  title: string
  target: ID
  cadence?: string | null
  schedule?: { every?: number | null; unit?: string | null } | null
  event?: TriggerEvent | null
  enabled?: boolean
  oneShotAt?: number | null
  id?: ID | null
}

export interface UpdateTriggerInput {
  title?: string | null
  target?: ID | null
  cadence?: string | null
  schedule?: { every?: number | null; unit?: string | null } | null
  event?: TriggerEvent | null
  enabled?: boolean | null
  oneShotAt?: number | null
  nextRunAt?: number | null
}

/* ---------- Audit trail ---------- */

export type AuditKind = 'activity' | 'approval' | 'tool_run' | 'note'

export interface AuditLogEntry {
  id: ID
  kind: AuditKind
  title: string
  detail: string
  ts: number
  severity: Severity
  departmentId?: ID | null
  author?: string | null
  refs: Record<string, unknown>
  redacted: boolean
  redactedFields: string[]
}

export interface AuditLogExportResponse {
  format: 'md' | 'json' | 'jsonl'
  contentType: string
  filename: string
  content: string
  rowCount: number
  exportedAt: number
  deptId?: ID | null
  kind?: string | null
  redacted: boolean
  redactedFields: string[]
}

export interface CreateAuditNoteInput {
  body: string
  severity?: Severity
  departmentId?: ID | null
  author?: string
  links?: string[]
}

/* ---------- Skills registry ---------- */

export interface Skill {
  id: ID
  name: string
  description: string
  tools: string[]
  suggestedModel?: string | null
  costProfile?: string | null
}

export interface CreateSkillInput {
  name: string
  description: string
  tools?: string[]
  suggestedModel?: string | null
  costProfile?: string | null
  id?: ID | null
}

/* ---------- Handoff conversation ---------- */

export type HandoffAct = 'request' | 'accept' | 'reject' | 'clarify' | 'reply' | 'deliver'

export interface HandoffMessage {
  id: ID
  handoffId: ID
  from: ID
  act: HandoffAct
  text: string
  ts: number
  taskId?: ID | null
  threadId?: ThreadId | null
}

export interface CreateHandoffMessageInput {
  from: ID
  act: HandoffAct
  text: string
  taskId?: ID | null
}

/* ---------- Notification preferences ---------- */

export type NotifChannel = 'off' | 'inbox' | 'push'

export interface NotificationQuietHours {
  enabled?: boolean
  start?: string
  end?: string
  timezone?: string
}

export interface NotificationPreferences {
  byType: Partial<Record<NotifType, NotifChannel>>
  quietHours: NotificationQuietHours
  updatedAt: number
}

export interface UpdateNotificationPreferencesInput {
  byType?: Partial<Record<NotifType, NotifChannel>> | null
  quietHours?: NotificationQuietHours | null
}

/* ---------- Knowledge debt ---------- */

export interface KnowledgeDebtDepartment {
  departmentId: ID
  stale: number
  duplicate: number
  conflicting: number
  orphaned: number
  unsourced: number
  health: number
}

export interface KnowledgeDebtReport {
  departments: KnowledgeDebtDepartment[]
  generatedAt: number
}

/* ---------- Model/provider catalog ---------- */

export interface CatalogResponse {
  models: Record<string, unknown>[]
  providers: Record<string, unknown>[]
  thinkingEfforts: Record<string, unknown>[]
  speedModes: Record<string, unknown>[]
}

export interface ProviderAuthStatusResponse {
  chatgptAccount: Record<string, unknown>
  claudeCode: Record<string, unknown>
}

export interface ProviderCredentialReference {
  id: string
  label?: string
  kind?: string
  env?: string[]
  usedBy?: string[]
  enables?: string[]
  notes?: string[]
  configured?: boolean | null
}

export interface ProviderRouteReference {
  id: AiProviderId | string
  label?: string
  shortLabel?: string
  routeType?: string
  wireApi?: string | null
  baseUrl?: string
  baseUrlEnv?: string
  credentialId?: string
  credentialHint?: string
  supportsChat?: boolean
  subsystems?: string[]
  defaultModel?: ModelId | string
  models?: string[]
  purpose?: string
  blurb?: string
  preferredWhen?: string
  cautions?: string[]
  configured?: boolean | null
}

export interface ProviderAuthReferenceResponse {
  version: number
  purpose?: string
  agentUsage?: Record<string, unknown>
  providers: ProviderRouteReference[]
  credentials: ProviderCredentialReference[]
  subsystems?: Record<string, unknown>[]
  recommendedConnectedProviderIds?: string[]
  statusWarnings?: string[]
}

export interface ProviderAuthStartResponse {
  id?: string
  status?: string | Record<string, unknown>
  authorizationUrl?: string
  redirectUri?: string
  startedAt?: number
  expiresAt?: number
  error?: string
  started?: boolean
  mode?: string
  command?: string
  statusDetail?: Record<string, unknown>
}

export type ProviderEnvFieldKind = 'text' | 'secret' | 'number' | 'boolean'

export interface ProviderEnvField {
  key: string
  label: string
  kind: ProviderEnvFieldKind
  setting?: string
  aliases: string[]
  placeholder?: string
  configured: boolean
  source: 'dotenv' | 'process' | 'default' | 'empty' | string
  sourceKey?: string
  processOverride?: boolean
  value: string
  maskedValue?: string
  valueRedacted?: boolean
  restartRecommended?: boolean
  impact?: string
}

export interface ProviderEnvGroup {
  id: string
  label: string
  credentialId?: string
  providerIds?: AiProviderId[]
  subsystemId?: string
  fields: ProviderEnvField[]
}

export interface ProviderEnvUpdate {
  key: string
  value?: string | null
  unset?: boolean
}

export interface ProviderEnvSettingsResponse {
  version: number
  envPath: string
  groups: ProviderEnvGroup[]
  reference?: Record<string, unknown>
  processOverrides: string[]
  restartRecommended?: boolean
  update?: {
    updatedKeys: string[]
    unsetKeys: string[]
    runtimeAppliedKeys?: string[]
    runtimeUnsetKeys?: string[]
    restartRecommended?: boolean
  }
}

/* ---------- Decisions (create/edit) ---------- */

export interface CreateDecisionInput {
  title: string
  proposedBy: ID
  rationale?: string | null
  impact?: string
  alternatives?: string[]
  linkedTask?: ID | null
  linkedArtifacts?: ID[]
  status?: DecisionStatus
  approvedBy?: ID | null
  supersedes?: ID | null
}

export interface UpdateDecisionInput {
  rationale?: string | null
  impact?: string | null
  alternatives?: string[] | null
  linkedArtifacts?: ID[] | null
  status?: DecisionStatus | null
  approvedBy?: ID | null
  supersedes?: ID | null
}

/* ---------- Preferences (owner profile) ---------- */

export interface CreatePreferenceInput {
  category: PreferenceCategory
  text: string
  confidence?: number
  source?: string | null
  id?: ID | null
}

export interface UpdatePreferenceInput {
  category?: PreferenceCategory | null
  text?: string | null
  confidence?: number | null
  source?: string | null
}

/* ---------- Evidence packs (artifact provenance) ---------- */

export interface EvidenceCitation {
  source: string
  url?: string | null
  quote: string
}

export interface EvidencePack {
  id: ID
  artifactId: ID
  citations: EvidenceCitation[]
  rawNotes: string
  confidence: number
  gaps: string[]
  assumptions: string[]
  methodology?: string | null
}

export interface CreateEvidencePackInput {
  artifactId: ID
  citations?: EvidenceCitation[]
  rawNotes?: string
  confidence?: number
  gaps?: string[]
  assumptions?: string[]
  methodology?: string | null
}

export interface ArtifactReviewGate {
  required: boolean
  reason: string
  evidencePackId: ID
  critiqueReportId: ID
  previewAvailable: boolean
  checkedAt: number
  confidence: number
  gaps: string[]
  riskCount: number
}

/* ---------- Critique reports (red-team a work item) ---------- */

export type CritiqueTargetType = 'task' | 'project' | 'decision' | 'artifact'

export interface CritiqueReport {
  id: ID
  targetType: CritiqueTargetType
  targetId: ID
  risks: string[]
  untestedAssumptions: string[]
  missedAlternatives: string[]
  openQuestions: string[]
  ts: number
}

export interface CreateCritiqueInput {
  targetType: CritiqueTargetType
  targetId: ID
  focus?: string | null
}

export interface ArtifactQualityReview {
  artifact: Artifact
  evidencePack: EvidencePack
  critiqueReport: CritiqueReport
  gate: ArtifactReviewGate
}

/* ---------- File import ---------- */

export interface ImportFileInput {
  sourcePath: string
  targetDept: ID
  projectId?: ID | null
  artifactName?: string | null
  tags?: string[]
  copyToWorkspace?: boolean
}

export interface ImportFileResponse {
  artifact: Artifact
  knowledge?: MemoryKnowledgeEntry | null
}

export type ImageQuality = 'low' | 'medium' | 'high' | 'auto'
export type ImageOutputFormat = 'png' | 'jpeg' | 'webp'
export type ImageBackground = 'auto' | 'opaque' | 'transparent'
export type ImageModeration = 'auto' | 'low'

export interface ImageArtifactLocation {
  artifactId: ID
  name: string
  mime?: string | null
  uri: string
  storage?: string | null
  contentHash?: string | null
  contentSizeBytes?: number | null
  downloadUrl: string
  previewUrl: string
  localPath?: string | null
}

export interface GenerateImageInput {
  prompt: string
  ownerDept?: ID | null
  threadId?: ThreadId | null
  projectId?: ID | null
  taskIds?: ID[]
  artifactName?: string | null
  referenceArtifactIds?: ID[]
  maskArtifactId?: ID | null
  model?: string | null
  modelOverrideApproved?: boolean
  modelOverrideReason?: string | null
  fallbackFromModel?: string | null
  primaryModelError?: string | null
  n?: number
  size?: string | null
  width?: number | null
  height?: number | null
  aspectRatio?: string | null
  resolution?: string | null
  clarity?: string | null
  quality?: ImageQuality | null
  outputFormat?: ImageOutputFormat | null
  outputCompression?: number | null
  background?: ImageBackground | null
  moderation?: ImageModeration | null
  asyncMode?: boolean
  waitForResult?: boolean
  wakeOnComplete?: boolean
  requestedBy?: string | null
  requesterName?: string | null
  createdBy?: string | null
  tags?: string[]
}

export interface GenerateImageResponse {
  ok: boolean
  status?: string | null
  asyncMode?: boolean
  jobId?: string | null
  runId?: string | null
  messageId?: string | null
  queuedAt?: number | null
  statusUrl?: string | null
  mode: 'generate' | 'edit'
  summary: string
  model: string
  provider: string
  artifacts: Artifact[]
  artifact?: Artifact | null
  locations: ImageArtifactLocation[]
  usage?: Record<string, unknown> | null
  requestId?: string | null
  options: Record<string, unknown>
  referenceArtifactIds: ID[]
  maskArtifactId?: ID | null
  modelPolicy: Record<string, unknown>
  warnings: string[]
  job?: Record<string, unknown> | null
  jobs?: Record<string, unknown>[]
}

/* ---------- Knowledge graph ---------- */

export interface GraphHealthResponse {
  configured: string
  backend: string
  enabled: boolean
  path?: string | null
  error?: string | null
}

export interface MemoryRuntimeHealth {
  storage: string
  vectorSearch: string
  pgvector: {
    extension: boolean
    column: boolean
  }
  embeddings: {
    provider: string
    model: string
    fallback: boolean
  }
  graph: string
}

export interface HealthResponse {
  ok: boolean
  provider: Record<string, unknown>
  graph: GraphHealthResponse
  memory: MemoryRuntimeHealth
  counts: Record<string, number>
}

export interface EntityInput {
  data: Record<string, unknown>
}

/* ---------- Connectors (capability surface) ---------- */

export type ConnectorKind =
  | 'local_file'
  | 'git'
  | 'http'
  | 'browser'
  | 'desktop'
  | 'sandbox'
  | 'mcp'

export type ConnectorStatus = 'available' | 'configured' | 'blocked_by_runtime'

export interface Connector {
  id: ID
  name: string
  kind: ConnectorKind
  status: ConnectorStatus
  description: string
  capabilities: string[]
  requires: string[]
  tools: string[]
  runtimeStatus?: string | null
  readReady: boolean
  writeReady: boolean
  localFallback: boolean
  externalWriteRequires: string[]
}

/* ---------- Chat generation control ---------- */

export interface StopGenerationInput {
  messageId?: ID | null
}

export interface StopGenerationResponse {
  stopped: boolean
  messageId?: ID | null
  queuedCancelled?: number
}

/* ---------- Artifact preview ---------- */

export interface ArtifactPreviewResponse {
  artifactId: ID
  version: number
  preview: ArtifactPreview
  content?: string | null
}

/* ---------- Shared response envelopes ---------- */

export interface GuardedActionResponse {
  ok: boolean
  executed?: boolean
  approval?: Approval | null
}

export interface RunningResponse {
  running: boolean
}

export interface WorkspaceAuditResponse {
  departmentId: ID
  workspacePath: string
  gitEnabled: boolean
  dirty?: boolean
  head?: string | null
  error?: string | null
}

export interface PeekDepartmentResponse {
  department: Department
  logId: string
  tasks: Task[]
  artifacts: Artifact[]
  knowledge: MemoryKnowledgeEntry[]
}
