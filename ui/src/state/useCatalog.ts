import { useSyncExternalStore } from 'react'
import { client } from './useCompany'
import { SPEED_MODE_LIST, speedsForModel as staticSpeedsForModel } from '../contract/models'
import type { SpeedModeInfo } from '../contract/models'
import type { CatalogResponse, ModelId, ModelSpeed } from '../contract/types'

/**
 * Live model/provider catalog from `GET /api/catalog`, fetched once and cached
 * at module scope. The backend is the source of truth for which models can run
 * Fast Mode (`models[].supportedSpeeds`) and which speed modes exist
 * (`speedModes`); the UI reads those to enable/disable the Fast toggle per
 * model. Until the fetch resolves, or if it fails, we fall back to the static
 * mirror in `contract/models.ts`.
 */
export interface SpeedCatalog {
  /** speed modes (Standard, Fast, …) in display order */
  speedModes: SpeedModeInfo[]
  /** speeds the given model supports, per the live catalog (or static fallback) */
  supportedSpeeds(modelId: ModelId): ModelSpeed[]
  /** does this model offer Fast Mode? */
  supportsFast(modelId: ModelId): boolean
  /** clamp a requested speed to one the model actually supports */
  coerceSpeed(modelId: ModelId, speed: ModelSpeed): ModelSpeed
}

function asSpeedArray(value: unknown): ModelSpeed[] | null {
  if (!Array.isArray(value)) return null
  const out = value.filter((s): s is ModelSpeed => s === 'standard' || s === 'fast')
  return out.length ? out : null
}

function parseSpeedModes(value: Record<string, unknown>[] | undefined): SpeedModeInfo[] | null {
  if (!Array.isArray(value) || value.length === 0) return null
  const modes: SpeedModeInfo[] = []
  for (const m of value) {
    const id = m.id
    if (id !== 'standard' && id !== 'fast') continue
    modes.push({
      id,
      label: typeof m.label === 'string' ? m.label : id,
      apiShape: typeof m.apiShape === 'string' ? m.apiShape : '',
      blurb: typeof m.blurb === 'string' ? m.blurb : '',
    })
  }
  return modes.length ? modes : null
}

function buildCatalog(raw: CatalogResponse | null): SpeedCatalog {
  const liveSpeeds = new Map<string, ModelSpeed[]>()
  for (const m of raw?.models ?? []) {
    const id = typeof m.id === 'string' ? m.id : null
    const speeds = asSpeedArray(m.supportedSpeeds)
    if (id && speeds) liveSpeeds.set(id, speeds)
  }
  const speedModes = parseSpeedModes(raw?.speedModes) ?? SPEED_MODE_LIST

  const supportedSpeeds = (modelId: ModelId): ModelSpeed[] =>
    liveSpeeds.get(modelId) ?? staticSpeedsForModel(modelId)

  return {
    speedModes,
    supportedSpeeds,
    supportsFast: (modelId) => supportedSpeeds(modelId).includes('fast'),
    coerceSpeed: (modelId, speed) => (supportedSpeeds(modelId).includes(speed) ? speed : 'standard'),
  }
}

// Module-scoped cache + tiny external store. `derived` is a stable reference
// between loads, which is exactly what useSyncExternalStore needs.
let derived: SpeedCatalog = buildCatalog(null)
let started = false
const listeners = new Set<() => void>()

function load(): void {
  if (started) return
  started = true
  client
    .getCatalog()
    .then((raw) => {
      derived = buildCatalog(raw)
      listeners.forEach((l) => l())
    })
    .catch(() => {
      /* keep the static fallback — the backend re-validates speed anyway */
    })
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  load()
  return () => {
    listeners.delete(listener)
  }
}

export function useSpeedCatalog(): SpeedCatalog {
  return useSyncExternalStore(subscribe, () => derived)
}
