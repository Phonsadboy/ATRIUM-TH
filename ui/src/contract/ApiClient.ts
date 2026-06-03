import type {
  AssignTaskInput,
  CompanyClient,
  CreateDepartmentInput,
  EditDepartmentInput,
  PulseEvent,
  ReassignTaskInput,
  Unsubscribe,
} from './client'
import type {
  AiProviderId,
  Approval,
  ApprovalStatus,
  Artifact,
  ArtifactContentInput,
  ArtifactContentResponse,
  ArtifactDiffResponse,
  ArtifactQualityReview,
  ArtifactVersion,
  AuditLogExportResponse,
  AuditLogEntry,
  Bulletin,
  CatalogResponse,
  ArtifactPreviewResponse,
  ChatMessage,
  ChatRole,
  ChatToolRun,
  TurnSegment,
  VideoJobRecord,
  CompanyState,
  Connector,
  CostCategory,
  CostReport,
  CostReportScope,
  CreateArtifactInput,
  CreateAuditNoteInput,
  CreateBulletinInput,
  CreateCritiqueInput,
  CreateDecisionInput,
  CreateEvidencePackInput,
  CreateHandoffMessageInput,
  CreateLessonInput,
  CreateMeetingInput,
  CreateObjectiveInput,
  CreateOrgPlanInput,
  CreatePlaybookInput,
  CreatePreferenceInput,
  CreateProjectInput,
  CreateSkillInput,
  CreateTriggerInput,
  CreateWarRoomInput,
  CritiqueReport,
  CritiqueTargetType,
  Decision,
  Department,
  DepartmentMemory,
  EvidencePack,
  Executive,
  GenerateImageInput,
  GenerateImageResponse,
  GraphHealthResponse,
  HealthResponse,
  HandoffMessage,
  ImportFileInput,
  ImportFileResponse,
  KnowledgeDebtReport,
  Lesson,
  Meeting,
  MeetingCollaboration,
  ModelId,
  ModelSpeed,
  Notification,
  NotificationPreferences,
  OrgPlan,
  OwnerProfile,
  PeekDepartmentResponse,
  Playbook,
  PolicyMode,
  Preference,
  Project,
  ProviderAuthReferenceResponse,
  ProviderAuthStartResponse,
  ProviderEnvSettingsResponse,
  ProviderEnvUpdate,
  ProviderAuthStatusResponse,
  ResolveProjectInput,
  ResolveOrgPlanInput,
  RollbackArtifactInput,
  ScheduledObjective,
  Skill,
  StopGenerationResponse,
  Task,
  TaskStatus,
  ThinkingEffort,
  ThreadId,
  ToolCatalogItem,
  ToolRun,
  ToolRunInput,
  ToolRunResponse,
  Trigger,
  UpdateArtifactInput,
  UpdateDecisionInput,
  UpdateMeetingInput,
  UpdateNotificationPreferencesInput,
  UpdateObjectiveInput,
  UpdateOrgPlanInput,
  UpdatePlaybookInput,
  UpdatePreferenceInput,
  UpdateProjectInput,
  UpdateTriggerInput,
  UpdateWarRoomInput,
  WarRoom,
  WarRoomCollaboration,
  WorkspaceAuditResponse,
  SendMessageOptions,
  SendMessageResponse,
  MessageActionName,
  MessageActionResponse,
  RegenerateMessageInput,
  RetryMessageInput,
  EditMessageInput,
  BranchConversationResponse,
  PromoteMessageInput,
  PromoteMessageResponse,
  ThreadDraft,
  ThreadDraftInput,
  ThreadExportResponse,
  InputEstimate,
  InputEstimateInput,
  MessageMentionTarget,
  PromptStarter,
  ThreadSearchResponse,
  ThreadStatsResponse,
  ThreadCostSummary,
} from './types'
import {
  coerceModelSpeed,
  coerceThinkingEffort,
  defaultThinkingEffortForModel,
  defaultModelForProvider,
  isModelAvailableForProvider,
} from './models'
import { uid } from '../lib/id'
import { isExec, deptIdFromThread } from '../lib/threads'

const ACCENTS: Department['accent'][] = ['amber', 'teal', 'coral', 'lavender', 'sky', 'honey']
const DAY_MS = 86_400_000
const COST_CATEGORIES: CostCategory[] = ['work', 'chat', 'meeting', 'autonomous', 'memory', 'tool']

const EMPTY_STATE: CompanyState = {
  companyName: 'ATRIUM',
  now: Date.now(),
  running: true,
  departments: [],
  tasks: [],
  threads: {},
  activity: [],
  approvals: [],
  objectives: [],
  budget: { dailyCapUsd: 500, spentTodayUsd: 0 },
}

function emptyMemory(departmentId: string): DepartmentMemory {
  return { departmentId, archive: [], knowledge: [], graph: { nodes: [], edges: [] } }
}

function emptyReport(scope: CostReportScope): CostReport {
  const today = Date.now() - (Date.now() % DAY_MS)
  const series = Array.from({ length: 7 }, (_, i) => ({
    day: new Date(today - (6 - i) * DAY_MS).toISOString().slice(0, 10),
    usd: 0,
  }))
  return {
    scope,
    spentUsd: 0,
    forecastUsd: 0,
    byCategory: Object.fromEntries(COST_CATEGORIES.map((c) => [c, 0])) as Record<CostCategory, number>,
    byAgent: {},
    series,
    anomalies: [],
  }
}

function wsUrlFor(baseUrl: string): string {
  const url = new URL(baseUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = '/ws'
  url.search = ''
  url.hash = ''
  return url.toString()
}

/** Live overlay for a reply that is streaming over the WebSocket. */
interface StreamState {
  threadId: ThreadId
  msgId: string
  text: string
  thinking: string
  toolRuns: Map<string, ChatToolRun>
  /** Ordered timeline of this turn (thinking ↔ tools ↔ text as they arrive). */
  segments: TurnSegment[]
  base?: ChatMessage
  done: boolean
  stopped?: boolean
  error?: string | null
}

export class ApiClient implements CompanyClient {
  private state: CompanyState = EMPTY_STATE
  /** DEV-only: while true, server snapshots are ignored so the office canvas
   *  can be driven from a local demo seed (see office/devOffice.ts). */
  private devHold = false
  private listeners = new Set<(state: CompanyState) => void>()
  private pulseListeners = new Set<(event: PulseEvent) => void>()
  private memory = new Map<string, DepartmentMemory>()
  private reports = new Map<string, CostReport>()
  private notifications: Notification[] = []
  private decisions: Decision[] = []
  private decisionsLoaded = false
  private ws: WebSocket | null = null
  private reconnectTimer: number | null = null
  private readonly baseUrl: string
  /** Last server-truth threads (without optimistic overlays applied). */
  private serverThreads: Record<string, ChatMessage[]> = {}
  /** Optimistic chat messages (sending user msgs, pending placeholders, failed). */
  private optimistic: ChatMessage[] = []
  /** Placeholder ids that are mid-generation (keep the typing bubble visible). */
  private inflight = new Set<string>()
  /** Chronological turn segments kept past the live overlay so a completed reply
   *  (after the server snapshot prunes the overlay) still renders as a timeline. */
  private streamSegments = new Map<string, TurnSegment[]>()
  /** Live token-stream overlays keyed by server message id (msg_start..msg_done). */
  private streaming = new Map<string, StreamState>()
  private streamRepaintQueued = false

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    // best-effort first load; the WS reconnect loop recovers if the backend
    // is not up yet (no local fallback masks an absent backend)
    void this.refresh().catch(() => undefined)
    void this.refreshNotifications()
    this.connect()
  }

  getState = (): CompanyState => this.state

  subscribe = (listener: (state: CompanyState) => void): Unsubscribe => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  onPulse = (listener: (event: PulseEvent) => void): Unsubscribe => {
    this.pulseListeners.add(listener)
    return () => this.pulseListeners.delete(listener)
  }

  /* ---------- DEV-only office iteration hooks ----------
     These let the office canvas be exercised (characters, walking,
     meetings, speech bubbles) without a live backend. They are inert in
     production builds — `import.meta.env.DEV` is statically false there,
     so the bodies are dropped. See office/devOffice.ts. */

  /** Replace the live snapshot and hold off further server pushes. */
  devReplaceState = (next: CompanyState): void => {
    if (!import.meta.env.DEV) return
    this.devHold = true
    this.setState(next)
  }

  /** Emit a synthetic pulse (handoff/done/spend/…) to trigger canvas FX. */
  devEmitPulse = (event: PulseEvent): void => {
    if (!import.meta.env.DEV) return
    this.emitPulse(event)
  }

  /** Stop holding; the next server snapshot re-adopts real company state. */
  devRelease = (): void => {
    if (!import.meta.env.DEV) return
    this.devHold = false
    this.setState({ ...this.state, now: Date.now() })
  }

  /**
   * Send a chat message. Optimistically inserts the user bubble (status
   * `sending`) plus a pending reply placeholder, POSTs, then reconciles with
   * server truth. On failure the user bubble flips to `failed` with a retry
   * affordance. Returns the full envelope (usage, follow-ups, command result).
   */
  sendMessage = async (
    threadId: ThreadId,
    text: string,
    opts: SendMessageOptions = {},
  ): Promise<SendMessageResponse | null> => {
    const clientMessageId = opts.clientMessageId ?? uid('cmsg')
    const ts = Date.now()
    const userMsg: ChatMessage = {
      id: uid('msg'),
      threadId,
      role: 'user',
      authorName: 'คุณ',
      text,
      ts,
      status: 'sending',
      clientMessageId,
      attachments: opts.attachments,
      quoteMessageId: opts.quoteMessageId ?? null,
      input: {
        status: 'sent',
        routedDepartmentId: opts.targetDepartmentId ?? undefined,
        characterCount: text.length,
        estimatedTokens: 0,
        attachmentCount: opts.attachments?.length ?? opts.attachmentIds?.length ?? 0,
      },
    }
    const placeholderId = uid('pending')
    this.optimistic.push(userMsg, this.pendingBubble(threadId, placeholderId, ts + 1, opts.targetDepartmentId))
    this.inflight.add(placeholderId)
    this.repaintThreads(ts)
    try {
      const res = await this.request<SendMessageResponse>(this.threadEndpoint(threadId), 'POST', {
        text,
        attachments: opts.attachments ?? [],
        attachmentIds: opts.attachmentIds ?? [],
        targetDepartmentId: opts.targetDepartmentId ?? null,
        thinkingEffort: opts.thinkingEffort ?? null,
        clientMessageId,
        parentMessageId: opts.parentMessageId ?? null,
        quoteMessageId: opts.quoteMessageId ?? null,
        retryOfMessageId: opts.retryOfMessageId ?? null,
        queueIfBusy: opts.queueIfBusy ?? true,
      })
      this.inflight.delete(placeholderId)
      this.optimistic = this.optimistic.filter(
        (m) => m.id !== placeholderId && m.clientMessageId !== clientMessageId,
      )
      await this.refresh()
      return res
    } catch (err) {
      console.error('[ATRIUM API] sendMessage', err)
      this.inflight.delete(placeholderId)
      this.optimistic = this.optimistic
        .filter((m) => m.id !== placeholderId)
        .map((m) =>
          m.clientMessageId === clientMessageId
            ? {
                ...m,
                status: 'failed' as const,
                error: { code: 'send_failed', detail: 'ส่งข้อความไม่สำเร็จ', retryable: true },
              }
            : m,
        )
      this.repaintThreads()
      return null
    }
  }

  /** Re-send a locally-failed optimistic message (idempotent via clientMessageId). */
  retryFailed = (threadId: ThreadId, clientMessageId: string): Promise<SendMessageResponse | null> => {
    const failed = this.optimistic.find(
      (m) => m.clientMessageId === clientMessageId && m.status === 'failed',
    )
    if (!failed) return Promise.resolve(null)
    this.optimistic = this.optimistic.filter((m) => m.clientMessageId !== clientMessageId)
    this.repaintThreads()
    return this.sendMessage(threadId, failed.text, {
      clientMessageId,
      attachments: failed.attachments ?? undefined,
      targetDepartmentId: failed.input?.routedDepartmentId ?? undefined,
      quoteMessageId: failed.quoteMessageId ?? undefined,
    })
  }

  /** Retry a server-side message that failed (by server message id). */
  retryMessage = (
    threadId: ThreadId,
    messageId: string,
    thinkingEffort?: ThinkingEffort,
  ): Promise<SendMessageResponse> =>
    this.withPending(threadId, () =>
      this.request<SendMessageResponse>(`${this.threadEndpoint(threadId)}/retry`, 'POST', {
        messageId,
        thinkingEffort: thinkingEffort ?? null,
      } satisfies RetryMessageInput),
    )

  /** Regenerate the latest (or a specific) assistant reply, optionally at a new effort. */
  regenerateMessage = (
    threadId: ThreadId,
    messageId?: string,
    thinkingEffort?: ThinkingEffort,
  ): Promise<SendMessageResponse> =>
    this.withPending(threadId, () =>
      this.request<SendMessageResponse>(`${this.threadEndpoint(threadId)}/regenerate`, 'POST', {
        messageId: messageId ?? null,
        thinkingEffort: thinkingEffort ?? null,
      } satisfies RegenerateMessageInput),
    )

  /** Edit a user message and branch the conversation from that point. */
  editMessage = (
    threadId: ThreadId,
    messageId: string,
    text: string,
    thinkingEffort?: ThinkingEffort,
  ): Promise<BranchConversationResponse> =>
    this.withPending(threadId, () =>
      this.request<BranchConversationResponse>(
        `${this.threadEndpoint(threadId)}/${encodeURIComponent(messageId)}/edit`,
        'POST',
        { text, thinkingEffort: thinkingEffort ?? null } satisfies EditMessageInput,
      ),
    )

  /** Pin / unpin / react / unreact / copy a message. */
  messageAction = (
    threadId: ThreadId,
    messageId: string,
    action: MessageActionName,
    reaction?: string,
  ): Promise<MessageActionResponse> =>
    this.afterMutation(
      this.request<MessageActionResponse>(
        `${this.threadEndpoint(threadId)}/${encodeURIComponent(messageId)}/actions`,
        'POST',
        { action, reaction: reaction ?? null, actor: 'owner' },
      ),
    )

  pinMessage = (threadId: ThreadId, messageId: string, pinned: boolean): Promise<MessageActionResponse> =>
    this.messageAction(threadId, messageId, pinned ? 'pin' : 'unpin')
  toggleReaction = (
    threadId: ThreadId,
    messageId: string,
    emoji: string,
    on: boolean,
  ): Promise<MessageActionResponse> =>
    this.messageAction(threadId, messageId, on ? 'react' : 'unreact', emoji)

  /** Promote a message into the department's RAG knowledge base. */
  promoteMessage = (
    threadId: ThreadId,
    messageId: string,
    input: PromoteMessageInput = {},
  ): Promise<PromoteMessageResponse> =>
    this.afterMutation(
      this.request<PromoteMessageResponse>(
        `${this.threadEndpoint(threadId)}/${encodeURIComponent(messageId)}/promote`,
        'POST',
        { title: input.title ?? null, tags: input.tags ?? [], actor: input.actor ?? 'owner' },
      ),
    )

  // ----- Thread utilities (drafts, cost, export, search, estimate, mentions) -----
  getThreadCost = (threadId: ThreadId): Promise<ThreadCostSummary> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/cost`, 'GET')
  getDraft = (threadId: ThreadId): Promise<ThreadDraft> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/draft`, 'GET')
  putDraft = (threadId: ThreadId, input: ThreadDraftInput): Promise<ThreadDraft> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/draft`, 'PUT', input)
  deleteDraft = (threadId: ThreadId): Promise<unknown> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/draft`, 'DELETE')
  exportThread = (
    threadId: ThreadId,
    format: 'md' | 'json' = 'md',
    limit?: number,
  ): Promise<ThreadExportResponse> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/export${this.qs({ format, limit })}`, 'GET')
  getThreadMessages = async (threadId: ThreadId, all = false, limit?: number): Promise<ChatMessage[]> => {
    try {
      return await this.request(`/api/threads/${encodeURIComponent(threadId)}/messages${this.qs({ all, limit })}`, 'GET')
    } catch (err) {
      if (!all) throw err
      const exported = await this.exportThread(threadId, 'json', 5000)
      const parsed = JSON.parse(exported.content) as { messages?: ChatMessage[] }
      return Array.isArray(parsed.messages) ? parsed.messages : []
    }
  }
  estimateInput = (threadId: ThreadId, input: InputEstimateInput): Promise<InputEstimate> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/input/estimate`, 'POST', input)
  getMentions = (threadId: ThreadId, q?: string, limit?: number): Promise<MessageMentionTarget[]> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/mentions${this.qs({ q, limit })}`, 'GET')
  getPromptStarters = (threadId: ThreadId): Promise<PromptStarter[]> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/prompt-starters`, 'GET')
  searchThread = (threadId: ThreadId, q: string, limit?: number): Promise<ThreadSearchResponse> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/search${this.qs({ q, limit })}`, 'GET')
  getThreadStats = (threadId: ThreadId, after?: number): Promise<ThreadStatsResponse> =>
    this.request(`/api/threads/${encodeURIComponent(threadId)}/stats${this.qs({ after })}`, 'GET')

  createDepartment = (input: CreateDepartmentInput): Department => {
    const id = input.id ?? uid('dept')
    const providerId = input.providerId
    const model = isModelAvailableForProvider(input.model, providerId)
      ? input.model
      : defaultModelForProvider(providerId)
    const index = this.state.departments.length
    const placed = this.state.departments.filter((d) => !isExec(d.id)).length
    const dept: Department = {
      id,
      name: input.name,
      role: input.role,
      charter: input.charter ?? input.role,
      emoji: input.emoji ?? '🟣',
      accent: input.accent ?? ACCENTS[index % ACCENTS.length],
      providerId,
      model,
      thinkingEffort: coerceThinkingEffort(
        model,
        input.thinkingEffort ?? defaultThinkingEffortForModel(model),
      ),
      speed: coerceModelSpeed(model, input.speed ?? 'standard'),
      agentName: input.agentName,
      state: 'idle',
      mood: 0.85,
      currentTaskId: null,
      autonomy: input.autonomy ?? false,
      createdAt: Date.now(),
      room: { x: 1 + (placed % 3) * 6, y: 6 + Math.floor(placed / 3) * 5, w: 5, h: 4 },
      memory: {
        archiveChunks: 0,
        ragEntries: 0,
        graphNodes: 0,
        graphEdges: 0,
        lastCompactionAt: null,
        tokensSaved: 0,
      },
      skills: input.skills ?? [],
      tools: input.tools ?? [],
    }
    this.memory.set(id, emptyMemory(id))
    this.setState({ ...this.state, departments: [...this.state.departments, dept], now: dept.createdAt })
    this.command(() => this.request('/api/departments', 'POST', { ...input, id }))
    return dept
  }

  assignTask = (input: AssignTaskInput): Task => {
    const id = input.id ?? uid('task')
    const now = Date.now()
    const task: Task = {
      id,
      title: input.title,
      detail: input.detail ?? '',
      status: 'assigned',
      priority: input.priority ?? 'normal',
      departmentId: input.departmentId,
      origin: input.byExecutive ? { kind: 'executive' } : { kind: 'user' },
      progress: 0,
      createdAt: now,
      updatedAt: now,
      handoffs: [],
      log: [input.byExecutive ? 'ผู้บริหารมอบหมาย' : 'ผู้ใช้มอบหมายโดยตรง'],
      projectId: input.projectId ?? null,
      deliverables: [],
      watchers: input.watchers ?? [],
      parentTaskId: input.parentTaskId ?? null,
      subTaskIds: [],
      deadlineAt: input.deadlineAt ?? null,
      result: null,
    }
    this.setState({ ...this.state, tasks: [...this.state.tasks, task], now })
    this.command(() => this.request('/api/tasks', 'POST', { ...input, id }))
    return task
  }

  listTasks = (
    p: { status?: TaskStatus; departmentId?: string; projectId?: string; includeDetails?: boolean; limit?: number } = {},
  ): Promise<Task[]> => this.request(`/api/tasks${this.qs(p)}`, 'GET')

  reassignTask = (taskId: string, input: ReassignTaskInput): Promise<Task> =>
    this.afterMutation(this.request(`/api/tasks/${encodeURIComponent(taskId)}/reassign`, 'POST', input))

  setRunning = (running: boolean): void => {
    this.setState({ ...this.state, running, now: Date.now() })
    this.command(() => this.request('/api/running', 'POST', { running }))
  }

  setDepartmentProvider = (departmentId: string, providerId: AiProviderId): void => {
    this.patchDeptLocal(departmentId, { providerId })
    this.command(() => this.request(`/api/departments/${departmentId}/provider`, 'POST', { providerId }))
  }

  setDepartmentModel = (departmentId: string, model: ModelId): void => {
    this.patchDeptLocal(departmentId, { model })
    this.command(() => this.request(`/api/departments/${departmentId}/model`, 'POST', { model }))
  }

  setDepartmentThinkingEffort = (departmentId: string, effort: ThinkingEffort): void => {
    this.patchDeptLocal(departmentId, { thinkingEffort: effort })
    this.command(() =>
      this.request(`/api/departments/${departmentId}/thinking`, 'POST', { thinkingEffort: effort }),
    )
  }

  setDepartmentSpeed = (departmentId: string, speed: ModelSpeed): void => {
    // Optimistic flip, then reconcile to the backend's coerced value — it is the
    // arbiter of whether the dept's model can actually run Fast Mode, so a 'fast'
    // request on an unsupported model comes back 'standard' and the UI follows.
    this.patchDeptLocal(departmentId, { speed })
    this.command(
      () => this.request<Department>(`/api/departments/${departmentId}/speed`, 'POST', { speed }),
      (dept) => this.patchDeptLocal(departmentId, { speed: dept.speed }),
    )
  }

  setDepartmentAutonomy = (departmentId: string, autonomy: boolean): void => {
    this.patchDeptLocal(departmentId, { autonomy })
    this.command(() =>
      this.request(`/api/departments/${departmentId}/autonomy`, 'POST', { enabled: autonomy }),
    )
  }

  setCompanyBudget = (dailyCapUsd: number): void => {
    this.setState({
      ...this.state,
      now: Date.now(),
      budget: { ...this.state.budget, dailyCapUsd },
    })
    this.command(() => this.request('/api/budget/company', 'PATCH', { dailyCapUsd }))
  }

  editDepartment = (departmentId: string, patch: EditDepartmentInput): void => {
    this.patchDeptLocal(departmentId, patch)
    this.command(() => this.request(`/api/departments/${departmentId}`, 'PATCH', patch))
  }

  closeDepartment = (departmentId: string): void => {
    this.setState({
      ...this.state,
      now: Date.now(),
      departments: this.state.departments.filter((d) => d.id !== departmentId),
      tasks: this.state.tasks.map((t) =>
        t.departmentId === departmentId
          ? { ...t, departmentId: null, status: 'backlog', waitingOn: undefined }
          : t,
      ),
    })
    this.memory.delete(departmentId)
    this.command(() => this.request(`/api/departments/${departmentId}`, 'DELETE'))
  }

  resolveApproval = (approvalId: string, decision: ApprovalStatus): void => {
    this.setState({
      ...this.state,
      now: Date.now(),
      approvals: this.state.approvals.map((a) =>
        a.id === approvalId ? { ...a, status: decision } : a,
      ),
    })
    this.command(() =>
      this.request(`/api/approvals/${approvalId}/resolve`, 'POST', { decision }),
    )
  }

  setPermissionPolicy = (mode: PolicyMode): void => {
    const prev = this.state.permissionPolicy
    this.setState({
      ...this.state,
      now: Date.now(),
      permissionPolicy: prev
        ? { ...prev, mode }
        : { mode, updatedAt: null, updatedBy: 'owner', toolCatalog: [] },
    })
    this.command(() => this.request('/api/policy', 'PATCH', { mode, updatedBy: 'owner' }))
  }

  toggleObjective = (objectiveId: string, enabled: boolean): void => {
    this.setState({
      ...this.state,
      now: Date.now(),
      objectives: this.state.objectives.map((o) =>
        o.id === objectiveId ? { ...o, enabled } : o,
      ),
    })
    this.command(() =>
      this.request(`/api/objectives/${objectiveId}/toggle`, 'POST', { enabled }),
    )
  }

  getCostReport = (scope: CostReportScope, deptId?: string): CostReport => {
    const key = `${scope}:${deptId ?? ''}`
    const cached = this.reports.get(key)
    if (!cached) {
      this.reports.set(key, emptyReport(scope))
      const suffix = new URLSearchParams({ scope })
      if (deptId) suffix.set('deptId', deptId)
      this.command(
        () => this.request<CostReport>(`/api/cost-report?${suffix.toString()}`, 'GET'),
        (report) => {
          this.reports.set(key, report)
          this.setState({ ...this.state, now: Date.now() })
        },
      )
    }
    return this.reports.get(key) ?? emptyReport(scope)
  }

  getMemory = (departmentId: string): DepartmentMemory => {
    const cached = this.memory.get(departmentId)
    if (!cached) {
      this.memory.set(departmentId, emptyMemory(departmentId))
      this.command(
        () => this.request<DepartmentMemory>(`/api/departments/${departmentId}/memory`, 'GET'),
        (memory) => {
          this.memory.set(departmentId, memory)
          this.updateMemoryStats(departmentId, memory)
        },
      )
    }
    return this.memory.get(departmentId) ?? emptyMemory(departmentId)
  }

  deleteKnowledge = (departmentId: string, id: string): void => {
    const mem = this.getMemory(departmentId)
    const next = { ...mem, knowledge: mem.knowledge.filter((k) => k.id !== id) }
    this.memory.set(departmentId, next)
    this.updateMemoryStats(departmentId, next)
    this.command(() => this.request(`/api/departments/${departmentId}/knowledge/${id}`, 'DELETE'))
  }

  editKnowledge = (
    departmentId: string,
    id: string,
    patch: { title?: string; text?: string; tags?: string[] },
  ): void => {
    const mem = this.getMemory(departmentId)
    const next = {
      ...mem,
      knowledge: mem.knowledge.map((k) =>
        k.id === id ? { ...k, ...patch, ts: Date.now() } : k,
      ),
    }
    this.memory.set(departmentId, next)
    this.updateMemoryStats(departmentId, next)
    this.command(() =>
      this.request(`/api/departments/${departmentId}/knowledge/${id}`, 'PATCH', patch),
    )
  }

  getNotifications = (): Notification[] => this.notifications

  markNotificationRead = (id: string): void => {
    this.notifications = this.notifications.map((n) => (n.id === id ? { ...n, read: true } : n))
    this.bump()
    this.command(
      () => this.request(`/api/notifications/${id}/read`, 'PATCH', { read: true }),
      () => void this.refreshNotifications(),
    )
  }

  markAllNotificationsRead = (): void => {
    this.notifications = this.notifications.map((n) => ({ ...n, read: true }))
    this.bump()
    this.command(
      () => this.request('/api/notifications/read-all', 'POST'),
      () => void this.refreshNotifications(),
    )
  }

  getDecisions = (): Decision[] => {
    if (!this.decisionsLoaded) {
      this.decisionsLoaded = true
      void this.refreshDecisions()
    }
    return this.decisions
  }

  /* ==========================================================
     Console data layer — entities served by dedicated REST
     endpoints (not in the CompanyState snapshot). These return
     Promises; panels manage their own loading/error state and
     re-fetch after mutations. Mutations that can ripple into the
     live snapshot (approvals, activity, running, …) refresh it.
     ========================================================== */

  // ----- Projects -----
  listProjects = (p: { status?: string; limit?: number } = {}): Promise<Project[]> =>
    this.request(`/api/projects${this.qs(p)}`, 'GET')
  getProject = (id: string): Promise<Project> => this.request(`/api/projects/${id}`, 'GET')
  createProject = (input: CreateProjectInput): Promise<Project> =>
    this.afterMutation(this.request('/api/projects', 'POST', input))
  updateProject = (id: string, patch: UpdateProjectInput): Promise<Project> =>
    this.afterMutation(this.request(`/api/projects/${id}`, 'PATCH', patch))
  resolveProject = (id: string, input: ResolveProjectInput): Promise<Project> =>
    this.afterMutation(this.request(`/api/projects/${id}/resolve`, 'POST', input))

  // ----- Executive onboarding -----
  listOrgPlans = (p: { status?: string; limit?: number } = {}): Promise<OrgPlan[]> =>
    this.request(`/api/onboarding/org-plans${this.qs(p)}`, 'GET')
  getOrgPlan = (id: string): Promise<OrgPlan> =>
    this.request(`/api/onboarding/org-plans/${id}`, 'GET')
  createOrgPlan = (input: CreateOrgPlanInput): Promise<OrgPlan> =>
    this.afterMutation(this.request('/api/onboarding/org-plans', 'POST', input))
  updateOrgPlan = (id: string, input: UpdateOrgPlanInput): Promise<OrgPlan> =>
    this.afterMutation(this.request(`/api/onboarding/org-plans/${id}`, 'PATCH', input))
  resolveOrgPlan = (id: string, input: ResolveOrgPlanInput): Promise<OrgPlan> =>
    this.afterMutation(this.request(`/api/onboarding/org-plans/${id}/resolve`, 'POST', input))

  // ----- Artifacts -----
  listArtifacts = (
    p: { dept?: string; project?: string; status?: string; taskId?: string; limit?: number } = {},
  ): Promise<Artifact[]> => this.request(`/api/artifacts${this.qs(p)}`, 'GET')
  getArtifact = (id: string): Promise<Artifact> => this.request(`/api/artifacts/${id}`, 'GET')
  createArtifact = (input: CreateArtifactInput): Promise<Artifact> =>
    this.afterMutation(this.request('/api/artifacts', 'POST', input))
  runArtifactReviewGate = (id: string, p: { reason?: string } = {}): Promise<ArtifactQualityReview> =>
    this.afterMutation(this.request(`/api/artifacts/${id}/review-gate${this.qs(p)}`, 'POST'))
  updateArtifact = (id: string, patch: UpdateArtifactInput): Promise<Artifact> =>
    this.afterMutation(this.request(`/api/artifacts/${id}`, 'PATCH', patch))
  getArtifactContent = (id: string, version?: number): Promise<ArtifactContentResponse> =>
    this.request(`/api/artifacts/${id}/content${this.qs({ version })}`, 'GET')
  putArtifactContent = (id: string, input: ArtifactContentInput): Promise<ArtifactContentResponse> =>
    this.afterMutation(this.request(`/api/artifacts/${id}/content`, 'PUT', input))
  listArtifactVersions = (id: string): Promise<ArtifactVersion[]> =>
    this.request(`/api/artifacts/${id}/versions`, 'GET')
  getArtifactDiff = (id: string, fromVersion: number, toVersion: number): Promise<ArtifactDiffResponse> =>
    this.request(`/api/artifacts/${id}/diff${this.qs({ fromVersion, toVersion })}`, 'GET')
  rollbackArtifact = (id: string, input: RollbackArtifactInput): Promise<ArtifactContentResponse> =>
    this.afterMutation(this.request(`/api/artifacts/${id}/rollback`, 'POST', input))

  // ----- Tools -----
  getToolCatalog = (): Promise<ToolCatalogItem[]> => this.request('/api/tools/catalog', 'GET')
  listToolRuns = (p: { deptId?: string; status?: string; limit?: number } = {}): Promise<ToolRun[]> =>
    this.request(`/api/tools/runs${this.qs(p)}`, 'GET')
  getToolRun = (id: string): Promise<ToolRun> => this.request(`/api/tools/runs/${id}`, 'GET')
  runTool = (input: ToolRunInput): Promise<ToolRunResponse> =>
    this.afterMutation(this.request('/api/tools/run', 'POST', input))
  cancelToolRun = (id: string): Promise<ToolRun> =>
    this.afterMutation(this.request(`/api/tools/runs/${id}/cancel`, 'POST'))
  listToolApprovals = (p: { status?: string; limit?: number } = {}): Promise<Approval[]> =>
    this.request(`/api/tools/approvals${this.qs(p)}`, 'GET')
  resolveToolApproval = (id: string, decision: ApprovalStatus): Promise<Approval> =>
    this.afterMutation(this.request(`/api/tools/approvals/${id}/resolve`, 'POST', { decision }))
  toolsKillSwitch = (): Promise<unknown> =>
    this.afterMutation(this.request('/api/tools/kill-switch', 'POST'))
  toolsResume = (): Promise<unknown> => this.afterMutation(this.request('/api/tools/resume', 'POST'))

  // ----- Meetings -----
  listMeetings = (p: { project?: string; status?: string; limit?: number } = {}): Promise<Meeting[]> =>
    this.request(`/api/meetings${this.qs(p)}`, 'GET')
  getMeeting = (id: string): Promise<Meeting> =>
    this.request(`/api/meetings/${encodeURIComponent(id)}`, 'GET')
  getMeetingCollaboration = (id: string, p: { limit?: number } = {}): Promise<MeetingCollaboration> =>
    this.request(`/api/meetings/${encodeURIComponent(id)}/collaboration${this.qs(p)}`, 'GET')
  createMeeting = (input: CreateMeetingInput): Promise<Meeting> =>
    this.afterMutation(this.request('/api/meetings', 'POST', input))
  updateMeeting = (id: string, patch: UpdateMeetingInput): Promise<Meeting> =>
    this.afterMutation(this.request(`/api/meetings/${id}`, 'PATCH', patch))

  // ----- War rooms -----
  listWarRooms = (p: { project?: string; status?: string; limit?: number } = {}): Promise<WarRoom[]> =>
    this.request(`/api/war-rooms${this.qs(p)}`, 'GET')
  getWarRoom = (id: string): Promise<WarRoom> =>
    this.request(`/api/war-rooms/${encodeURIComponent(id)}`, 'GET')
  getWarRoomCollaboration = (id: string, p: { limit?: number } = {}): Promise<WarRoomCollaboration> =>
    this.request(`/api/war-rooms/${encodeURIComponent(id)}/collaboration${this.qs(p)}`, 'GET')
  createWarRoom = (input: CreateWarRoomInput): Promise<WarRoom> =>
    this.afterMutation(this.request('/api/war-rooms', 'POST', input))
  updateWarRoom = (id: string, patch: UpdateWarRoomInput): Promise<WarRoom> =>
    this.afterMutation(this.request(`/api/war-rooms/${id}`, 'PATCH', patch))

  // ----- Playbooks -----
  listPlaybooks = (p: { limit?: number } = {}): Promise<Playbook[]> =>
    this.request(`/api/playbooks${this.qs(p)}`, 'GET')
  createPlaybook = (input: CreatePlaybookInput): Promise<Playbook> =>
    this.afterMutation(this.request('/api/playbooks', 'POST', input))
  updatePlaybook = (id: string, patch: UpdatePlaybookInput): Promise<Playbook> =>
    this.afterMutation(this.request(`/api/playbooks/${id}`, 'PATCH', patch))

  // ----- Lessons -----
  createLesson = (input: CreateLessonInput): Promise<Lesson> =>
    this.afterMutation(this.request('/api/lessons', 'POST', input))

  // ----- Bulletins -----
  listBulletins = (p: { limit?: number } = {}): Promise<Bulletin[]> =>
    this.request(`/api/bulletins${this.qs(p)}`, 'GET')
  createBulletin = (input: CreateBulletinInput): Promise<Bulletin> =>
    this.afterMutation(this.request('/api/bulletins', 'POST', input))

  // ----- Triggers (scheduler) -----
  listTriggers = (p: { kind?: string; limit?: number } = {}): Promise<Trigger[]> =>
    this.request(`/api/triggers${this.qs(p)}`, 'GET')
  createTrigger = (input: CreateTriggerInput): Promise<Trigger> =>
    this.afterMutation(this.request('/api/triggers', 'POST', input))
  updateTrigger = (id: string, patch: UpdateTriggerInput): Promise<Trigger> =>
    this.afterMutation(this.request(`/api/triggers/${id}`, 'PATCH', patch))

  // ----- Objectives (standing autonomous goals) -----
  listObjectives = (p: { deptId?: string; enabled?: boolean } = {}): Promise<ScheduledObjective[]> =>
    this.request(`/api/objectives${this.qs(p)}`, 'GET')
  getObjective = (id: string): Promise<ScheduledObjective> =>
    this.request(`/api/objectives/${id}`, 'GET')
  createObjective = (input: CreateObjectiveInput): Promise<ScheduledObjective> =>
    this.afterMutation(this.request('/api/objectives', 'POST', input))
  updateObjective = (id: string, patch: UpdateObjectiveInput): Promise<ScheduledObjective> =>
    this.afterMutation(this.request(`/api/objectives/${id}`, 'PATCH', patch))

  // ----- Audit -----
  listAuditLogs = (p: { deptId?: string; kind?: string; limit?: number } = {}): Promise<AuditLogEntry[]> =>
    this.request(`/api/audit/logs${this.qs(p)}`, 'GET')
  exportAuditLogs = (
    p: { deptId?: string; kind?: string; format?: 'md' | 'json' | 'jsonl'; limit?: number } = {},
  ): Promise<AuditLogExportResponse> =>
    this.request(`/api/audit/logs/export${this.qs(p)}`, 'GET')
  createAuditNote = (input: CreateAuditNoteInput): Promise<AuditLogEntry> =>
    this.afterMutation(this.request('/api/audit/notes', 'POST', input))

  // ----- Skills -----
  listSkills = (p: { limit?: number } = {}): Promise<Skill[]> =>
    this.request(`/api/skills${this.qs(p)}`, 'GET')
  createSkill = (input: CreateSkillInput): Promise<Skill> =>
    this.afterMutation(this.request('/api/skills', 'POST', input))

  // ----- Owner profile / preferences -----
  getOwnerProfile = (): Promise<OwnerProfile> => this.request('/api/owner-profile', 'GET')
  listPreferences = (p: { category?: string; limit?: number } = {}): Promise<Preference[]> =>
    this.request(`/api/preferences${this.qs(p)}`, 'GET')
  createPreference = (input: CreatePreferenceInput): Promise<Preference> =>
    this.afterMutation(this.request('/api/preferences', 'POST', input))
  updatePreference = (id: string, patch: UpdatePreferenceInput): Promise<Preference> =>
    this.afterMutation(this.request(`/api/preferences/${id}`, 'PATCH', patch))
  deletePreference = (id: string): Promise<unknown> =>
    this.afterMutation(this.request(`/api/preferences/${id}`, 'DELETE'))

  // ----- Executive -----
  getExecutive = (): Promise<Executive> => this.request('/api/executive', 'GET')

  // ----- Notification preferences (may be absent on older backends) -----
  getNotificationPreferences = (): Promise<NotificationPreferences> =>
    this.request('/api/notification-preferences', 'GET')
  updateNotificationPreferences = (
    patch: UpdateNotificationPreferencesInput,
  ): Promise<NotificationPreferences> =>
    this.request('/api/notification-preferences', 'PATCH', patch)

  // ----- Knowledge debt & catalog -----
  getKnowledgeDebt = (): Promise<KnowledgeDebtReport> => this.request('/api/knowledge-debt', 'GET')
  getCatalog = (): Promise<CatalogResponse> => this.request('/api/catalog', 'GET')
  getProviderAuthStatus = (): Promise<ProviderAuthStatusResponse> =>
    this.request('/api/provider-auth/status', 'GET')
  getProviderAuthReference = (): Promise<ProviderAuthReferenceResponse> =>
    this.request('/api/provider-auth/reference', 'GET')
  getProviderEnvSettings = (): Promise<ProviderEnvSettingsResponse> =>
    this.request('/api/provider-auth/env', 'GET')
  updateProviderEnvSettings = (updates: ProviderEnvUpdate[]): Promise<ProviderEnvSettingsResponse> =>
    this.request('/api/provider-auth/env', 'PATCH', { updates })
  startChatGPTAccountLogin = (): Promise<ProviderAuthStartResponse> =>
    this.request('/api/provider-auth/chatgpt/start', 'POST', { timeoutS: 300 })
  startClaudeCodeLogin = (): Promise<ProviderAuthStartResponse> =>
    this.request('/api/provider-auth/claude-code/start', 'POST')

  // ----- Decisions (create/edit) -----
  createDecision = (input: CreateDecisionInput): Promise<Decision> =>
    this.afterMutation(this.request('/api/decisions', 'POST', input))
  updateDecision = (id: string, patch: UpdateDecisionInput): Promise<Decision> =>
    this.afterMutation(this.request(`/api/decisions/${id}`, 'PATCH', patch))

  // ----- Handoff conversation -----
  listHandoffMessages = (handoffId: string, p: { limit?: number } = {}): Promise<HandoffMessage[]> =>
    this.request(`/api/handoffs/${handoffId}/messages${this.qs(p)}`, 'GET')
  postHandoffMessage = (handoffId: string, input: CreateHandoffMessageInput): Promise<HandoffMessage> =>
    this.afterMutation(this.request(`/api/handoffs/${handoffId}/messages`, 'POST', input))

  // ----- Department peek / workspace audit -----
  peekDepartment = (
    id: string,
    p: { viewer?: string; includeKnowledge?: boolean } = {},
  ): Promise<PeekDepartmentResponse> =>
    this.request(`/api/departments/${id}/peek${this.qs(p)}`, 'GET')
  getWorkspaceAudit = (id: string): Promise<WorkspaceAuditResponse> =>
    this.request(`/api/departments/${id}/workspace/audit`, 'GET')

  // ----- Evidence packs / critique / import / runtime health -----
  getEvidencePack = (artifactId: string): Promise<EvidencePack> =>
    this.request(`/api/evidence-packs/${artifactId}`, 'GET')
  createEvidencePack = (input: CreateEvidencePackInput): Promise<EvidencePack> =>
    this.afterMutation(this.request('/api/evidence-packs', 'POST', input))
  listCritiques = (
    p: { targetType?: CritiqueTargetType; targetId?: string; limit?: number } = {},
  ): Promise<CritiqueReport[]> => this.request(`/api/critique-reports${this.qs(p)}`, 'GET')
  createCritique = (input: CreateCritiqueInput): Promise<CritiqueReport> =>
    this.afterMutation(this.request('/api/critique-reports', 'POST', input))
  importFile = (input: ImportFileInput): Promise<ImportFileResponse> =>
    this.afterMutation(this.request('/api/import/file', 'POST', input))
  getImageGenerationStatus = (): Promise<Record<string, unknown>> =>
    this.request('/api/images/status', 'GET')
  getImageGenerationJob = (jobId: string): Promise<Record<string, unknown>> =>
    this.request(`/api/images/jobs/${encodeURIComponent(jobId)}`, 'GET')
  listImageGenerationJobs = (
    p: { threadId?: ThreadId; status?: string; limit?: number } = {},
  ): Promise<Record<string, unknown>> => this.request(`/api/images/jobs${this.qs(p)}`, 'GET')
  generateImage = (input: GenerateImageInput): Promise<GenerateImageResponse> =>
    this.afterMutation(this.request('/api/images/generate', 'POST', input))
  uploadAttachment = (
    threadId: ThreadId,
    file: File,
    input: { targetDept?: string | null; projectId?: string | null; artifactName?: string | null; tags?: string[] } = {},
  ): Promise<ImportFileResponse> => {
    const form = new FormData()
    form.append('file', file)
    form.append('threadId', threadId)
    if (input.targetDept) form.append('targetDept', input.targetDept)
    if (input.projectId) form.append('projectId', input.projectId)
    if (input.artifactName) form.append('artifactName', input.artifactName)
    if (input.tags?.length) form.append('tags', input.tags.join(','))
    return this.afterMutation(this.requestForm('/api/attachments/upload', 'POST', form))
  }
  artifactDownloadUrl = (artifactId: string, version?: number): string =>
    `${this.baseUrl}/api/artifacts/${encodeURIComponent(artifactId)}/download${this.qs({ version })}`
  artifactOpenUrl = (artifactId: string, version?: number): string =>
    `${this.baseUrl}/api/artifacts/${encodeURIComponent(artifactId)}/download${this.qs({ version, inline: true })}`
  getHealth = (): Promise<HealthResponse> => this.request('/health', 'GET')
  getGraphHealth = (): Promise<GraphHealthResponse> => this.request('/api/graph/health', 'GET')

  // ----- Connectors -----
  listConnectors = (): Promise<Connector[]> => this.request('/api/connectors', 'GET')

  /* ==========================================================
     Phase 4 — Executive console (memory inspector, config diff,
     project workspace, tool execution history). These read the
     durable REST surface directly; panels own loading/error.
     ========================================================== */

  /** Generic durable-entity list (newest first), e.g. `org_checkpoint`,
   *  `runtime_checkpoint`, `checkpoint`, `lesson`, `tool_run`. */
  listEntities = <T = Record<string, unknown>>(
    kind: string,
    p: { limit?: number; dept?: string; project?: string; status?: string } = {},
  ): Promise<T[]> => this.request(`/api/entities/${encodeURIComponent(kind)}${this.qs(p)}`, 'GET')

  getVideoJob = (jobId: string): Promise<VideoJobRecord> =>
    this.request(`/api/entities/video_job/${encodeURIComponent(jobId)}`, 'GET')

  /** Decisions filtered by scope (the cached `getDecisions()` stays for the live log). */
  listDecisions = (
    p: { dept?: string; project?: string; status?: string; taskId?: string; limit?: number } = {},
  ): Promise<Decision[]> => this.request(`/api/decisions${this.qs(p)}`, 'GET')

  /** Department memory (knowledge + temporal graph) as a fresh promise for console panels. */
  getDepartmentMemory = (departmentId: string): Promise<DepartmentMemory> =>
    this.request(`/api/departments/${encodeURIComponent(departmentId)}/memory`, 'GET')

  /** Full cost analytics surface (forecast + per-dimension attribution; telemetry only). */
  getCostAnalytics = (deptId?: string): Promise<Record<string, unknown>> =>
    this.request(`/api/cost-analytics${this.qs({ deptId })}`, 'GET')

  /** Append-only conversation/runtime ledger lookup (raw transcript backlinks). */
  lookupLedger = (
    p: Record<string, string | number | boolean | undefined | null> = {},
  ): Promise<{ rows?: unknown[]; count?: number } & Record<string, unknown>> =>
    this.request(`/api/ledger${this.qs(p)}`, 'GET')

  /** Roll an org checkpoint back to its captured snapshot (full-auto safety rail). */
  rollbackOrgCheckpoint = (checkpointId: string): Promise<Record<string, unknown>> =>
    this.afterMutation(
      this.request(`/api/org/checkpoints/${encodeURIComponent(checkpointId)}/rollback`, 'POST'),
    )

  // ----- Chat: stop generation + artifact preview -----
  stopGeneration = async (threadId: ThreadId, messageId?: string): Promise<StopGenerationResponse> => {
    const result = await this.request<StopGenerationResponse>(
      `/api/messages/${encodeURIComponent(threadId)}/stop`,
      'POST',
      { messageId: messageId ?? null },
    )
    await this.refresh().catch(() => undefined)
    return result
  }
  getArtifactPreview = (id: string, version?: number): Promise<ArtifactPreviewResponse> =>
    this.request(`/api/artifacts/${id}/preview${this.qs({ version })}`, 'GET')

  /** Build a query string from defined, non-empty params. */
  private qs(params: Record<string, string | number | boolean | undefined | null>): string {
    const sp = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
    }
    const s = sp.toString()
    return s ? `?${s}` : ''
  }

  /** Await a mutation, then refresh the live snapshot (best-effort). */
  private async afterMutation<T>(p: Promise<T>): Promise<T> {
    const value = await p
    void this.refresh().catch(() => undefined)
    return value
  }

  private async refreshDecisions(): Promise<void> {
    try {
      this.decisions = await this.request<Decision[]>('/api/decisions?limit=200', 'GET')
      this.bump()
    } catch {
      /* decisions are best-effort; ignore fetch errors */
    }
  }

  private async refreshNotifications(): Promise<void> {
    try {
      this.notifications = await this.request<Notification[]>('/api/notifications?limit=100', 'GET')
      this.bump()
    } catch {
      /* notifications are best-effort; ignore fetch errors */
    }
  }

  /** A new notification arrived over the WebSocket — prepend it live. */
  private onNotify(n: Notification): void {
    if (this.notifications.some((x) => x.id === n.id)) return
    this.notifications = [n, ...this.notifications]
    this.bump()
  }

  /** Force a fresh state reference so subscribers re-read client getters. */
  private bump(): void {
    this.setState({ ...this.state, now: Date.now() })
  }

  /* ---------- Chat optimistic overlay engine ---------- */

  private threadEndpoint(threadId: ThreadId): string {
    return `/api/messages/${encodeURIComponent(threadId)}`
  }

  private replyRoleFor(threadId: ThreadId): ChatRole {
    return isExec(deptIdFromThread(threadId)) ? 'executive' : 'agent'
  }

  private pendingBubble(threadId: ThreadId, id: string, ts: number, targetDepartmentId?: string | null): ChatMessage {
    const departmentId = targetDepartmentId && targetDepartmentId !== 'exec' ? targetDepartmentId : null
    return {
      id,
      threadId,
      role: departmentId ? 'agent' : this.replyRoleFor(threadId),
      authorName: '…',
      text: '',
      ts,
      pending: true,
      departmentId,
    }
  }

  /** Show a pending reply bubble while an async chat request runs, then refresh. */
  private async withPending<T>(threadId: ThreadId, run: () => Promise<T>): Promise<T> {
    const placeholderId = uid('pending')
    this.optimistic.push(this.pendingBubble(threadId, placeholderId, Date.now() + 1))
    this.inflight.add(placeholderId)
    this.repaintThreads()
    try {
      const value = await run()
      this.inflight.delete(placeholderId)
      this.optimistic = this.optimistic.filter((m) => m.id !== placeholderId)
      await this.refresh()
      return value
    } catch (err) {
      this.inflight.delete(placeholderId)
      this.optimistic = this.optimistic.filter((m) => m.id !== placeholderId)
      this.repaintThreads()
      throw err
    }
  }

  /** Merge server-truth threads with any optimistic overlays (sending/pending/failed). */
  private mergedThreads(): Record<string, ChatMessage[]> {
    const threads: Record<string, ChatMessage[]> = {}
    for (const [tid, msgs] of Object.entries(this.serverThreads)) threads[tid] = [...msgs]
    // live streaming overlays (token-by-token reply text / thinking / tool runs)
    for (const s of this.streaming.values()) {
      const arr = threads[s.threadId] ?? (threads[s.threadId] = [])
      const idx = arr.findIndex((x) => x.id === s.msgId)
      const overlay = this.streamOverlayMessage(s, idx >= 0 ? arr[idx] : undefined)
      if (idx >= 0) arr[idx] = overlay
      else arr.push(overlay)
    }
    for (const m of this.optimistic) {
      const arr = threads[m.threadId] ? [...threads[m.threadId]] : []
      const dup =
        m.status !== 'failed' &&
        arr.some((x) => x.id === m.id || (!!m.clientMessageId && x.clientMessageId === m.clientMessageId))
      if (!dup) arr.push(m)
      threads[m.threadId] = arr
    }
    for (const tid of Object.keys(threads)) {
      threads[tid] = [...threads[tid]]
        .map((m) => {
          // re-attach the live-built timeline to a completed reply once the
          // server snapshot (which has no segments) has replaced the overlay.
          if (m.segments && m.segments.length) return m
          const seg = this.streamSegments.get(m.id)
          return seg && seg.length ? { ...m, segments: seg } : m
        })
        .sort((a, b) => a.ts - b.ts)
    }
    return threads
  }

  private repaintThreads(now = Date.now()): void {
    this.setState({ ...this.state, now, threads: this.mergedThreads() })
  }

  /** Adopt a fresh server snapshot, then re-apply still-relevant optimistic overlays. */
  private applyServerState(server: CompanyState): void {
    if (this.devHold) return // a local demo seed owns the store (DEV only)
    this.serverThreads = server.threads ?? {}
    // drop finished streaming overlays once the snapshot carries the final
    // (non-pending) message — prevents flicker / double render.
    for (const [msgId, s] of this.streaming) {
      const serverMsg = (this.serverThreads[s.threadId] ?? []).find((x) => x.id === msgId)
      if (s.done && serverMsg && !serverMsg.pending) this.streaming.delete(msgId)
    }
    this.optimistic = this.optimistic.filter((m) => {
      if (m.status === 'failed') return true
      if (m.pending) return this.inflight.has(m.id)
      const arr = this.serverThreads[m.threadId] ?? []
      return !arr.some((x) => !!m.clientMessageId && x.clientMessageId === m.clientMessageId)
    })
    this.setState({ ...server, threads: this.mergedThreads() })
  }

  private patchDeptLocal(departmentId: string, patch: EditDepartmentInput): void {
    const dept = this.state.departments.find((d) => d.id === departmentId)
    if (!dept) return
    const providerId = patch.providerId ?? dept.providerId
    let model = patch.model ?? dept.model
    if (!isModelAvailableForProvider(model, providerId)) {
      model = defaultModelForProvider(providerId)
    }
    const modelChanged = model !== dept.model || providerId !== dept.providerId
    const thinkingEffort = coerceThinkingEffort(
      model,
      patch.thinkingEffort ?? (modelChanged ? defaultThinkingEffortForModel(model) : dept.thinkingEffort),
    )
    const speed = coerceModelSpeed(model, patch.speed ?? dept.speed ?? 'standard')
    this.setState({
      ...this.state,
      now: Date.now(),
      departments: this.state.departments.map((d) =>
        d.id === departmentId ? { ...d, ...patch, providerId, model, thinkingEffort, speed } : d,
      ),
    })
  }

  private updateMemoryStats(departmentId: string, memory: DepartmentMemory): void {
    this.setState({
      ...this.state,
      now: Date.now(),
      departments: this.state.departments.map((d) =>
        d.id === departmentId
          ? {
              ...d,
              memory: {
                ...d.memory,
                archiveChunks: memory.archive.length,
                ragEntries: memory.knowledge.length,
                graphNodes: memory.graph.nodes.length,
                graphEdges: memory.graph.edges.length,
              },
            }
          : d,
      ),
    })
  }

  private connect(): void {
    if (this.ws || typeof WebSocket === 'undefined') return
    const ws = new WebSocket(wsUrlFor(this.baseUrl))
    this.ws = ws
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'state') this.applyServerState(msg.state)
      else if (msg.type === 'pulse') this.routePulse(msg.event)
      else if (msg.type === 'notify') this.onNotify(msg.notification as Notification)
    }
    ws.onclose = () => {
      this.ws = null
      if (this.reconnectTimer == null) {
        this.reconnectTimer = window.setTimeout(() => {
          this.reconnectTimer = null
          this.connect()
        }, 1200)
      }
    }
    ws.onerror = () => ws.close()
  }

  private async refresh(): Promise<void> {
    const state = await this.request<CompanyState>('/api/state', 'GET')
    this.applyServerState(state)
    // mutations (new dept, closed task, …) can mint decisions — keep the log fresh
    if (this.decisionsLoaded) void this.refreshDecisions()
  }

  private command<T>(run: () => Promise<T>, then?: (value: T) => void): void {
    void run()
      .then((value) => {
        if (then) then(value)
        else void this.refresh()
      })
      .catch((err) => {
        console.error('[ATRIUM API]', err)
        void this.refresh().catch(() => undefined)
      })
  }

  private async request<T = unknown>(path: string, method: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {
      'X-ATRIUM-Actor': 'owner-ui',
      'X-ATRIUM-Source': 'ui',
    }
    if (body) headers['Content-Type'] = 'application/json'
    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) throw await this.responseError(res, method, path)
    return (await res.json()) as T
  }

  private async requestForm<T = unknown>(path: string, method: string, body: FormData): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: {
        'X-ATRIUM-Actor': 'owner-ui',
        'X-ATRIUM-Source': 'ui',
      },
      body,
    })
    if (!res.ok) throw await this.responseError(res, method, path)
    return (await res.json()) as T
  }

  private async responseError(res: Response, method: string, path: string): Promise<Error> {
    const detail = await this.readErrorDetail(res)
    return new Error(`${method} ${path} failed: ${res.status}${detail ? `: ${detail}` : ''}`)
  }

  private async readErrorDetail(res: Response): Promise<string> {
    const contentType = res.headers.get('content-type') ?? ''
    try {
      if (contentType.includes('json')) {
        const body = await res.clone().json()
        return this.clipErrorDetail(this.redactErrorDetail(this.errorDetailFromJson(body)))
      }
      const text = (await res.clone().text()).trim()
      return this.clipErrorDetail(this.redactErrorDetail(text))
    } catch {
      return ''
    }
  }

  private errorDetailFromJson(value: unknown): string {
    if (typeof value === 'string') return value
    if (Array.isArray(value)) {
      return value
        .map((item) => this.errorDetailFromJson(item))
        .filter(Boolean)
        .join('; ')
    }
    if (value && typeof value === 'object') {
      const record = value as Record<string, unknown>
      for (const key of ['detail', 'message', 'error', 'reason']) {
        const nested = this.errorDetailFromJson(record[key])
        if (nested) return nested
      }
      return JSON.stringify(record)
    }
    return ''
  }

  private redactErrorDetail(value: string): string {
    return value
      .replace(/((?:api[_-]?key|token|authorization|password|secret)=)[^&\s]+/gi, '$1[redacted]')
      .replace(/((?:api[_-]?key|token|authorization|password|secret)["'\s:=]+)([^"',\s}{]+)/gi, '$1[redacted]')
  }

  private clipErrorDetail(value: string): string {
    const trimmed = value.trim()
    return trimmed.length > 1200 ? `${trimmed.slice(0, 1200)}...` : trimmed
  }

  private setState(state: CompanyState): void {
    this.state = state
    this.listeners.forEach((listener) => listener(this.state))
  }

  /* ---------- Live chat streaming overlay ---------- */

  /** Route a WS pulse: stream events drive the live chat overlay; everything
   *  else (canvas effects) flows on to pulse listeners unchanged. */
  private routePulse(event: unknown): void {
    const ev = event as { kind?: string }
    if (ev && typeof ev.kind === 'string' && this.handleStreamPulse(ev)) return
    this.emitPulse(event as PulseEvent)
  }

  private handleStreamPulse(ev: Record<string, unknown>): boolean {
    switch (ev.kind) {
      case 'msg_start': this.onStreamStart(ev); return true
      case 'msg_delta': this.onStreamDelta(ev); return true
      case 'thinking_delta': this.onStreamThinking(ev); return true
      case 'tool_call':
      case 'tool_start':
      case 'tool_activity': this.onStreamTool(ev, false); return true
      case 'tool_result':
      case 'tool_done': this.onStreamTool(ev, true); return true
      case 'msg_done': this.onStreamDone(ev); return true
      default: return false
    }
  }

  private ensureStream(threadId: ThreadId, msgId: string, base?: ChatMessage): StreamState {
    let s = this.streaming.get(msgId)
    if (!s) {
      const segments: TurnSegment[] = []
      s = { threadId, msgId, text: '', thinking: '', toolRuns: new Map(), segments, base, done: false }
      this.streaming.set(msgId, s)
      // Keep the same array reference around so the completed message still has
      // its timeline after the live overlay is pruned. Cap the cache.
      this.streamSegments.set(msgId, segments)
      if (this.streamSegments.size > 80) {
        const oldest = this.streamSegments.keys().next().value
        if (oldest !== undefined) this.streamSegments.delete(oldest)
      }
    } else if (base && !s.base) {
      s.base = base
    }
    return s
  }

  /** The incremental piece of this delta event (the sink sends both an explicit
   *  `chunk` and the full accumulated `text`; prefer the chunk, else diff). */
  private deltaOf(ev: Record<string, unknown>, prevFull: string): string {
    if (typeof ev.chunk === 'string') return ev.chunk
    if (typeof ev.text === 'string') return ev.text.length >= prevFull.length ? ev.text.slice(prevFull.length) : ev.text
    return ''
  }

  /** Append a thinking/text delta to the open same-kind segment, or start a new
   *  one — so a fresh thinking block after a tool call reads as a new step. */
  private appendSegmentText(s: StreamState, kind: 'thinking' | 'text', delta: string): void {
    if (!delta) return
    const last = s.segments[s.segments.length - 1]
    if (last && last.kind === kind) last.text += delta
    else s.segments.push({ kind, text: delta })
  }

  private upsertSegmentTool(s: StreamState, run: ChatToolRun, key: string): void {
    const last = s.segments[s.segments.length - 1]
    if (last && last.kind === 'tools') {
      const i = last.runs.findIndex((r) => String(r.toolUseId ?? r.id) === key)
      if (i >= 0) last.runs[i] = { ...last.runs[i], ...run }
      else last.runs.push(run)
    } else {
      s.segments.push({ kind: 'tools', runs: [run] })
    }
  }

  private onStreamStart(ev: Record<string, unknown>): void {
    const threadId = ev.threadId as ThreadId
    const s = this.ensureStream(threadId, String(ev.msgId), ev.message as ChatMessage | undefined)
    s.done = false
    // the real reply bubble now exists — drop the optimistic pending placeholder
    this.optimistic = this.optimistic.filter((m) => {
      if (m.threadId !== threadId) return true
      if (m.pending && this.inflight.has(m.id)) {
        this.inflight.delete(m.id)
        return false
      }
      return true
    })
    this.scheduleStreamRepaint()
  }

  private onStreamDelta(ev: Record<string, unknown>): void {
    const s = this.ensureStream(ev.threadId as ThreadId, String(ev.msgId))
    this.appendSegmentText(s, 'text', this.deltaOf(ev, s.text))
    if (typeof ev.text === 'string') s.text = ev.text
    else if (typeof ev.chunk === 'string') s.text += ev.chunk
    s.done = false
    this.scheduleStreamRepaint()
  }

  private onStreamThinking(ev: Record<string, unknown>): void {
    const s = this.ensureStream(ev.threadId as ThreadId, String(ev.msgId))
    this.appendSegmentText(s, 'thinking', this.deltaOf(ev, s.thinking))
    if (typeof ev.text === 'string') s.thinking = ev.text
    else if (typeof ev.chunk === 'string') s.thinking += ev.chunk
    this.scheduleStreamRepaint()
  }

  private onStreamTool(ev: Record<string, unknown>, done: boolean): void {
    const run = (ev.run ?? ev.toolRun ?? ev.tool) as ChatToolRun | undefined
    if (!run || !(run.id || run.toolUseId)) return
    let msgId = ev.msgId ? String(ev.msgId) : undefined
    if (!msgId) {
      for (const [id, st] of this.streaming) {
        if (st.threadId === ev.threadId && !st.done) msgId = id
      }
    }
    if (!msgId) return
    const s = this.ensureStream(ev.threadId as ThreadId, msgId)
    const key = String(run.toolUseId ?? run.id)
    const prev = s.toolRuns.get(key)
    const merged = { ...prev, ...run, status: run.status ?? (done ? 'succeeded' : 'running') }
    s.toolRuns.set(key, merged)
    this.upsertSegmentTool(s, merged, key)
    this.scheduleStreamRepaint()
  }

  private onStreamDone(ev: Record<string, unknown>): void {
    const msgId = String(ev.msgId)
    const s = this.streaming.get(msgId)
    if (!s) return
    if (typeof ev.text === 'string' && ev.text) s.text = ev.text
    s.done = true
    s.stopped = !!ev.stopped
    s.error = (ev.error as string | undefined) ?? null
    this.scheduleStreamRepaint()
    // safety net: drop the overlay if no server snapshot reconciles it shortly
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        const cur = this.streaming.get(msgId)
        if (cur && cur.done) {
          this.streaming.delete(msgId)
          this.repaintThreads()
        }
      }, 6000)
    }
  }

  private scheduleStreamRepaint(): void {
    if (this.streamRepaintQueued) return
    this.streamRepaintQueued = true
    const flush = () => {
      this.streamRepaintQueued = false
      this.repaintThreads()
    }
    if (typeof requestAnimationFrame !== 'undefined') requestAnimationFrame(flush)
    else setTimeout(flush, 16)
  }

  /** Build the live overlay message for a streaming reply. */
  private streamOverlayMessage(s: StreamState, existing?: ChatMessage): ChatMessage {
    const base: ChatMessage = existing ?? s.base ?? this.pendingBubble(s.threadId, s.msgId, Date.now())
    const toolRuns = s.toolRuns.size ? Array.from(s.toolRuns.values()) : base.toolRuns ?? null
    const errorDetail = typeof s.error === 'string' ? s.error.trim() : ''
    const failed = Boolean(errorDetail)
    const hasContent = !!s.text || !!s.thinking || (toolRuns?.length ?? 0) > 0
    return {
      ...base,
      id: s.msgId,
      threadId: s.threadId,
      text: s.text || (failed ? errorDetail : s.done ? base.text : ''),
      reasoning: s.thinking || base.reasoning,
      reasoningStatus: s.thinking ? 'available' : base.reasoningStatus,
      toolRuns,
      segments: s.segments.length ? s.segments : base.segments,
      pending: failed ? false : !s.done && !hasContent,
      streaming: failed ? false : !s.done,
      status: failed ? 'failed' : s.done ? base.status : 'sending',
      error: failed ? { code: 'stream_error', detail: errorDetail, retryable: true } : base.error,
    }
  }

  private emitPulse(event: PulseEvent): void {
    this.pulseListeners.forEach((listener) => listener(event))
  }
}
