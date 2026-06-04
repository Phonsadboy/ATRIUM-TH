import type { AiProviderId, ModelId, ModelSpeed, ThinkingEffort } from './types'

export interface AiProviderInfo {
  id: AiProviderId
  label: string
  shortLabel: string
  purpose: string
  baseUrl: string
  baseUrlEnv: string
  authTokenEnv: string
  trafficEnv?: string
  wireApi?: string
  blurb: string
}

export interface ModelInfo {
  id: ModelId
  label: string
  tier: 'opus' | 'sonnet' | 'gpt'
  providerIds: AiProviderId[]
  /** USD per million tokens. Real provider billing can differ; used for local cost accounting. */
  inputPerMTok: number
  outputPerMTok: number
  /** Response speeds this model offers. Fast Mode is Opus-only; the backend
   *  catalog (`models[].supportedSpeeds`) is authoritative and overlays this. */
  supportedSpeeds: ModelSpeed[]
  supportedEfforts?: ThinkingEffort[]
  defaultThinkingEffort?: ThinkingEffort
  contextWindowTokens: number
  blurb: string
}

export interface ThinkingEffortInfo {
  id: ThinkingEffort
  label: string
  apiShape: string
  blurb: string
}

export const AI_PROVIDERS: Record<AiProviderId, AiProviderInfo> = {
  anthropic: {
    id: 'anthropic',
    label: 'Claude AI',
    shortLabel: 'Claude',
    purpose: 'ผู้ให้บริการตรงจาก Anthropic',
    baseUrl: 'https://api.anthropic.com',
    baseUrlEnv: 'ATRIUM_ANTHROPIC_BASE_URL',
    authTokenEnv: 'ATRIUM_ANTHROPIC_AUTH_TOKEN',
    blurb: 'ใช้สำหรับเทียบพฤติกรรมกับ Claude API ตรง',
  },
  openai: {
    id: 'openai',
    label: 'OpenAI Platform',
    shortLabel: 'OpenAI',
    purpose: 'ใช้ OpenAI API key สำหรับแชทผ่าน Responses API และ subsystem ของ ATRIUM',
    baseUrl: 'https://api.openai.com/v1',
    baseUrlEnv: 'ATRIUM_OPENAI_BASE_URL',
    authTokenEnv: 'ATRIUM_OPENAI_API_KEY',
    wireApi: 'responses',
    blurb: 'Platform API key แยกจาก ChatGPT OAuth; ใช้ได้ทั้ง provider route และ subsystem เช่น audio/transcription',
  },
  chatgpt_account: {
    id: 'chatgpt_account',
    label: 'ChatGPT Account',
    shortLabel: 'ChatGPT',
    purpose: 'ใช้บัญชี ChatGPT ผ่าน OAuth แบบ Codex',
    baseUrl: 'https://chatgpt.com/backend-api/codex',
    baseUrlEnv: 'ATRIUM_CHATGPT_ACCOUNT_BASE_URL',
    authTokenEnv: 'data/auth/chatgpt-account.json',
    wireApi: 'chatgpt-codex-responses',
    blurb: 'ช่องทาง account/subscription แยกจาก OpenAI API key',
  },
  claude_code: {
    id: 'claude_code',
    label: 'Claude Code Account',
    shortLabel: 'Claude Code',
    purpose: 'ใช้บัญชี Claude ผ่าน Claude Code CLI',
    baseUrl: 'local claude CLI',
    baseUrlEnv: 'ATRIUM_CLAUDE_CODE_COMMAND',
    authTokenEnv: 'Claude Code OAuth / setup-token',
    wireApi: 'claude-code-cli',
    blurb: 'ช่องทาง subscription account; ไม่ใช่ Anthropic Messages API token',
  },
}

export const PROVIDER_LIST: AiProviderInfo[] = [
  AI_PROVIDERS.claude_code,
  AI_PROVIDERS.openai,
  AI_PROVIDERS.chatgpt_account,
  AI_PROVIDERS.anthropic,
]

export const MODELS: Record<ModelId, ModelInfo> = {
  'claude-sonnet-4-6': {
    id: 'claude-sonnet-4-6',
    label: 'Sonnet 4.6',
    tier: 'sonnet',
    providerIds: ['anthropic', 'claude_code'],
    inputPerMTok: 3,
    outputPerMTok: 15,
    supportedSpeeds: ['standard'],
    contextWindowTokens: 1_000_000,
    blurb: 'เร็ว ฉลาด สมดุล เหมาะกับงานทั่วไปและ agent loop',
  },
  'claude-opus-4-7': {
    id: 'claude-opus-4-7',
    label: 'Opus 4.7',
    tier: 'opus',
    providerIds: ['claude_code'],
    inputPerMTok: 15,
    outputPerMTok: 75,
    supportedSpeeds: ['standard', 'fast'],
    contextWindowTokens: 1_000_000,
    blurb: 'รุ่น Opus สำหรับงาน reasoning ที่ต้องใช้ xhigh',
  },
  'claude-opus-4-6': {
    id: 'claude-opus-4-6',
    label: 'Opus 4.6',
    tier: 'opus',
    providerIds: ['claude_code'],
    inputPerMTok: 15,
    outputPerMTok: 75,
    supportedSpeeds: ['standard', 'fast'],
    contextWindowTokens: 1_000_000,
    blurb: 'รุ่น Opus legacy ที่รองรับ Claude Fast Mode',
  },
  'claude-opus-4-8': {
    id: 'claude-opus-4-8',
    label: 'Opus 4.8',
    tier: 'opus',
    providerIds: ['anthropic', 'claude_code'],
    inputPerMTok: 15,
    outputPerMTok: 75,
    supportedSpeeds: ['standard', 'fast'],
    contextWindowTokens: 1_000_000,
    blurb: 'รุ่นหนักสุดสำหรับ reasoning และงาน agentic coding',
  },
  'gpt-5.5': {
    id: 'gpt-5.5',
    label: 'GPT-5.5',
    tier: 'gpt',
    providerIds: ['openai', 'chatgpt_account'],
    inputPerMTok: 15,
    outputPerMTok: 75,
    supportedSpeeds: ['standard'],
    supportedEfforts: ['low', 'medium', 'high', 'xhigh'],
    defaultThinkingEffort: 'medium',
    contextWindowTokens: 1_000_000,
    blurb: 'GPT route หลักผ่าน Responses API สำหรับงาน reasoning หนัก',
  },
  'gpt-5.4-mini': {
    id: 'gpt-5.4-mini',
    label: 'GPT-5.4 Mini',
    tier: 'gpt',
    providerIds: ['openai', 'chatgpt_account'],
    inputPerMTok: 1,
    outputPerMTok: 5,
    supportedSpeeds: ['standard'],
    supportedEfforts: ['low', 'medium', 'high', 'xhigh'],
    defaultThinkingEffort: 'medium',
    contextWindowTokens: 1_000_000,
    blurb: 'รุ่น GPT เบา เร็วกว่า เหมาะกับงานทั่วไปและงาน UI agent',
  },
  'gpt-5.3-codex': {
    id: 'gpt-5.3-codex',
    label: 'GPT-5.3 Codex',
    tier: 'gpt',
    providerIds: ['openai', 'chatgpt_account'],
    inputPerMTok: 5,
    outputPerMTok: 25,
    supportedSpeeds: ['standard'],
    supportedEfforts: ['low', 'medium', 'high', 'xhigh'],
    defaultThinkingEffort: 'medium',
    contextWindowTokens: 1_000_000,
    blurb: 'รุ่น GPT/Codex สำหรับงาน coding ผ่าน Responses API',
  },
}

export const MODEL_LIST: ModelInfo[] = [
  MODELS['claude-sonnet-4-6'],
  MODELS['claude-opus-4-6'],
  MODELS['claude-opus-4-7'],
  MODELS['claude-opus-4-8'],
  MODELS['gpt-5.5'],
  MODELS['gpt-5.4-mini'],
  MODELS['gpt-5.3-codex'],
]

export const DEFAULT_MODEL: ModelId = 'claude-sonnet-4-6'

export const THINKING_EFFORTS: Record<ThinkingEffort, ThinkingEffortInfo> = {
  off: {
    id: 'off',
    label: 'Off',
    apiShape: 'omit thinking หรือ {type:"disabled"}',
    blurb: 'ไม่เปิด extended/adaptive thinking เพื่อลด latency',
  },
  low: {
    id: 'low',
    label: 'Low',
    apiShape: 'thinking:{type:"adaptive"} + effort:"low"',
    blurb: 'เน้นเร็วและประหยัด เหมาะกับงานสั้นหรือซ้ำเยอะ',
  },
  medium: {
    id: 'medium',
    label: 'Medium',
    apiShape: 'thinking:{type:"adaptive"} + effort:"medium"',
    blurb: 'สมดุลระหว่างคุณภาพกับต้นทุน',
  },
  high: {
    id: 'high',
    label: 'High',
    apiShape: 'thinking:{type:"adaptive"} + effort:"high"',
    blurb: 'ค่า default ของ Claude สำหรับงาน reasoning ที่จริงจัง',
  },
  xhigh: {
    id: 'xhigh',
    label: 'XHigh',
    apiShape: 'thinking:{type:"adaptive"} + effort:"xhigh"',
    blurb: 'คิดลึกเป็นพิเศษ ใช้กับ Opus 4.7/4.8',
  },
  max: {
    id: 'max',
    label: 'Max',
    apiShape: 'thinking:{type:"adaptive"} + effort:"max"',
    blurb: 'ความสามารถสูงสุด ใช้เมื่อคุ้มกับต้นทุนและ latency',
  },
}

export const THINKING_EFFORT_LIST: ThinkingEffortInfo[] = [
  THINKING_EFFORTS.off,
  THINKING_EFFORTS.low,
  THINKING_EFFORTS.medium,
  THINKING_EFFORTS.high,
  THINKING_EFFORTS.xhigh,
  THINKING_EFFORTS.max,
]

export interface SpeedModeInfo {
  id: ModelSpeed
  label: string
  apiShape: string
  blurb: string
}

/** Static mirror of the backend's `speedModes`. The live `GET /api/catalog`
 *  overlays this at runtime (see `useSpeedCatalog`); this is the fallback. */
export const SPEED_MODES: Record<ModelSpeed, SpeedModeInfo> = {
  standard: {
    id: 'standard',
    label: 'Standard',
    apiShape: 'omit speed',
    blurb: 'Claude response path ปกติ',
  },
  fast: {
    id: 'fast',
    label: 'Fast',
    apiShape: 'beta fast-mode-2026-02-01 + speed:"fast"',
    blurb: 'Claude Fast Mode — ตอบเร็วขึ้น ใช้ได้เฉพาะ Opus 4.8/4.7 (ค่าใช้จ่ายต่อโทเคนสูงกว่า)',
  },
}

export const SPEED_MODE_LIST: SpeedModeInfo[] = [SPEED_MODES.standard, SPEED_MODES.fast]

export const DEFAULT_SPEED: ModelSpeed = 'standard'

const OPUS_HIGH_EFFORT_MODELS = new Set<ModelId>([
  'claude-opus-4-6',
  'claude-opus-4-7',
  'claude-opus-4-8',
])

export function modelsForProvider(providerId: AiProviderId): ModelInfo[] {
  return MODEL_LIST.filter((model) => model.providerIds.includes(providerId))
}

export function defaultModelForProvider(providerId: AiProviderId): ModelId {
  if (providerId === 'openai' || providerId === 'chatgpt_account') return 'gpt-5.5'
  if (providerId === 'claude_code') return DEFAULT_MODEL
  return 'claude-opus-4-8'
}

export function isModelAvailableForProvider(
  modelId: ModelId,
  providerId: AiProviderId,
): boolean {
  return MODELS[modelId].providerIds.includes(providerId)
}

export function thinkingEffortsForModel(modelId: ModelId): ThinkingEffortInfo[] {
  const explicit = MODELS[modelId].supportedEfforts
  if (explicit?.length) {
    return explicit.map((effort) => THINKING_EFFORTS[effort])
  }
  return THINKING_EFFORT_LIST.filter(
    (effort) => effort.id !== 'xhigh' || OPUS_HIGH_EFFORT_MODELS.has(modelId),
  )
}

export function defaultThinkingEffortForModel(modelId: ModelId): ThinkingEffort {
  return MODELS[modelId].defaultThinkingEffort ?? 'high'
}

export function providerRouteLabel(providerId: AiProviderId): string {
  if (providerId === 'chatgpt_account') return 'ChatGPT OAuth'
  if (providerId === 'claude_code') return 'Claude Code'
  if (providerId === 'openai') return 'GPT API'
  return 'Claude API'
}

export function modelRouteLabel(modelId: ModelId): string {
  return MODELS[modelId].tier === 'gpt' ? 'Responses API' : 'Anthropic-compatible'
}

export function modelEffortLabel(modelId: ModelId): string {
  return thinkingEffortsForModel(modelId)
    .map((effort) => effort.label)
    .join(' / ')
}

export function isThinkingEffortAvailableForModel(
  modelId: ModelId,
  effort: ThinkingEffort,
): boolean {
  return thinkingEffortsForModel(modelId).some((item) => item.id === effort)
}

export function coerceThinkingEffort(
  modelId: ModelId,
  effort: ThinkingEffort,
): ThinkingEffort {
  if (isThinkingEffortAvailableForModel(modelId, effort)) return effort
  const fallback = defaultThinkingEffortForModel(modelId)
  return isThinkingEffortAvailableForModel(modelId, fallback)
    ? fallback
    : thinkingEffortsForModel(modelId)[0]?.id ?? 'high'
}

/** Speeds the (static) catalog says a model offers. `useSpeedCatalog` prefers
 *  the live backend catalog; these functions are the offline fallback. */
export function speedsForModel(modelId: ModelId): ModelSpeed[] {
  return MODELS[modelId]?.supportedSpeeds ?? [DEFAULT_SPEED]
}

export function modelSupportsFast(modelId: ModelId): boolean {
  return speedsForModel(modelId).includes('fast')
}

export function isSpeedAvailableForModel(modelId: ModelId, speed: ModelSpeed): boolean {
  return speedsForModel(modelId).includes(speed)
}

/** Fast Mode is a separate switch; drop back to 'standard' if the model can't
 *  run it. The backend re-coerces, so this only keeps the UI honest. */
export function coerceModelSpeed(modelId: ModelId, speed: ModelSpeed): ModelSpeed {
  return isSpeedAvailableForModel(modelId, speed) ? speed : DEFAULT_SPEED
}
