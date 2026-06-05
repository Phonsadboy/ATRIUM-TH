import type {
  CompanyState,
  Department,
  Task,
  ThreadId,
  DepartmentMemory,
  AiProviderId,
  ModelId,
  ModelSpeed,
  ThinkingEffort,
  Priority,
  AccentName,
  ApprovalStatus,
  CostReport,
  CostReportScope,
  Notification,
  PolicyMode,
  Decision,
  OfficeLayout,
  SendMessageOptions,
  SendMessageResponse,
  ControlTaskInput,
  TaskControlResponse,
  UpdateOfficeLayoutInput,
} from './types'

export type Unsubscribe = () => void

/** Transient "something happened here" signal for canvas effects. */
export interface PulseEvent {
  kind: 'handoff' | 'compaction' | 'spend' | 'state' | 'autonomous' | 'done'
  departmentId: ID
  toDepartmentId?: ID
}

export interface CreateDepartmentInput {
  id?: ID
  name: string
  role: string
  charter?: string
  providerId: AiProviderId
  model: ModelId
  thinkingEffort?: ThinkingEffort
  speed?: ModelSpeed
  agentName: string
  accent?: AccentName
  emoji?: string
  autonomy?: boolean
  skills?: string[]
  tools?: string[]
}

/** Editable fields of an existing department (everything the user can change). */
export type EditDepartmentInput = Partial<
  Pick<
    Department,
    | 'name'
    | 'role'
    | 'charter'
    | 'emoji'
    | 'accent'
    | 'providerId'
    | 'model'
    | 'thinkingEffort'
    | 'speed'
    | 'agentName'
    | 'autonomy'
    | 'skills'
    | 'tools'
  >
>

export interface AssignTaskInput {
  id?: ID
  title: string
  detail?: string
  departmentId: ID
  priority?: Priority
  /** who is assigning — defaults to the user */
  byExecutive?: boolean
  projectId?: ID
  watchers?: ID[]
  parentTaskId?: ID | null
  deadlineAt?: number | null
  reviewIntervalMs?: number | null
}

export interface ReassignTaskInput {
  departmentId: ID
  requestedBy?: string
  reason?: string | null
}

type ID = string

/**
 * The single surface the UI talks to, implemented by `ApiClient` against the
 * FastAPI backend over REST + WebSocket.
 */
export interface CompanyClient {
  getState(): CompanyState
  subscribe(listener: (state: CompanyState) => void): Unsubscribe
  /** subscribe to transient effect signals (handoffs, compaction, …) */
  onPulse(listener: (event: PulseEvent) => void): Unsubscribe

  sendMessage(
    threadId: ThreadId,
    text: string,
    opts?: SendMessageOptions,
  ): Promise<SendMessageResponse | null>
  createDepartment(input: CreateDepartmentInput): Department
  assignTask(input: AssignTaskInput): Promise<Task>
  reassignTask(taskId: ID, input: ReassignTaskInput): Promise<Task>
  updateTaskReviewSchedule(taskId: ID, reviewIntervalMs: number | null): Promise<Task>
  controlTask(taskId: ID, input: ControlTaskInput): Promise<TaskControlResponse>
  updateOfficeLayout(input: UpdateOfficeLayoutInput): Promise<OfficeLayout>

  setRunning(running: boolean): void
  setDepartmentProvider(departmentId: ID, providerId: AiProviderId): void
  setDepartmentModel(departmentId: ID, model: ModelId): void
  setDepartmentThinkingEffort(departmentId: ID, effort: ThinkingEffort): void
  /** Toggle Claude Fast Mode for a department. The backend decides whether the
   *  model can run fast and the UI syncs to the returned (coerced) value. */
  setDepartmentSpeed(departmentId: ID, speed: ModelSpeed): void
  setDepartmentAutonomy(departmentId: ID, autonomy: boolean): void
  /** set the company-wide daily spend cap (USD) */
  setCompanyBudget(dailyCapUsd: number): void
  editDepartment(departmentId: ID, patch: EditDepartmentInput): void
  closeDepartment(departmentId: ID): void

  resolveApproval(approvalId: ID, decision: ApprovalStatus): void
  /** set the global Owner Mode guardrail (how much asks for approval) */
  setPermissionPolicy(mode: PolicyMode): void
  toggleObjective(objectiveId: ID, enabled: boolean): void

  getCostReport(scope: CostReportScope, deptId?: ID): CostReport

  getMemory(departmentId: ID): DepartmentMemory
  deleteKnowledge(departmentId: ID, id: ID): void
  editKnowledge(
    departmentId: ID,
    id: ID,
    patch: { title?: string; text?: string; tags?: string[] },
  ): void

  /** Notification inbox — autonomous-work alerts (task done, budget, blocked, …). */
  getNotifications(): Notification[]
  markNotificationRead(id: ID): void
  markAllNotificationsRead(): void

  /** Durable decision log (audit trail of org/work decisions). */
  getDecisions(): Decision[]
}
