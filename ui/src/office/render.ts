/* ============================================================
   ATRIUM — office canvas renderer (pure drawing)

   Stateless draw routines: given a 2D context, the world model, the
   camera, the image cache, the actor layer and a clock, paint one
   frame. All geometry is world-space CSS px; the caller installs the
   camera transform (dpr · zoom) before calling drawWorld.

   Two layers:
     • stations — rooms, decor and desks, drawn where the world says.
       These never move (an empty chair stays when its owner walks off).
     • characters — drawn at live ACTOR positions, y-sorted so a walker
       correctly overlaps the desks it passes. Nameplates, parcels and
       speech bubbles float above the moving body.
   ============================================================ */

import type { AgentState } from '../contract/types'
import { ACCENT_HEX, STATE_HEX } from '../lib/visuals'
import type { ImageStore } from './assets'
import { charPath, COFFEE_MUG_PATH, DECOR, FLOOR_TILE_PATH, PARCEL_PATH, walkFrame } from './assets'
import { HEADER_H, type DeptSprite, type RoomModel, type WorldModel } from './world'
import { actorPose, STEP_MS, type Actor, type Actors, type Bubble } from './actors'

const FONT = '"Sarabun", "Noto Sans Thai", system-ui, sans-serif'
const INK = '#13100b'
const CREAM = '#efe7d7'

/* ---------- time-of-day wash ---------- */

let TINT_HOUR_OVERRIDE: number | null = null

/** DEV: force the time-of-day for the tint (0–23), or null to follow the clock. */
export function setTintHour(hour: number | null): void {
  TINT_HOUR_OVERRIDE = hour
}

/** A subtle full-screen color wash by time of day (null = neutral daylight). */
export function timeTint(): string | null {
  const h = TINT_HOUR_OVERRIDE ?? new Date().getHours() + new Date().getMinutes() / 60
  if (h >= 20 || h < 6) return 'rgba(18,26,68,0.22)' // night — cool blue
  if (h >= 18) return 'rgba(116,58,38,0.13)' // dusk — warm
  if (h < 8) return 'rgba(120,82,52,0.11)' // dawn — warm
  return null // daylight
}

export interface Camera {
  x: number
  y: number
  zoom: number
}

export interface FrameState {
  selectedId: string | null
  hoverId: string | null
  arrange: boolean
  dropRoomIndex: number | null
  /** live drag offset applied to one department before it is committed */
  drag: { id: string; dx: number; dy: number } | null
  /** live drag offset applied to a whole room before it is committed */
  roomDrag: { index: number; dx: number; dy: number } | null
  now: number
}

export interface Fx {
  kind: 'ripple' | 'burst' | 'float' | 'travel'
  x: number
  y: number
  x2?: number
  y2?: number
  color: string
  text?: string
  charSrc?: string
  start: number
  dur: number
}

/* ---------- color helpers ---------- */

function rgba(hex: string, a: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${a})`
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + rr, y)
  ctx.arcTo(x + w, y, x + w, y + h, rr)
  ctx.arcTo(x + w, y + h, x, y + h, rr)
  ctx.arcTo(x, y + h, x, y, rr)
  ctx.arcTo(x, y, x + w, y, rr)
  ctx.closePath()
}

/** cover-fit an image into a destination rect, clipped to it. */
function drawCover(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  dx: number,
  dy: number,
  dw: number,
  dh: number,
) {
  const ir = img.naturalWidth / img.naturalHeight
  const dr = dw / dh
  let sw = img.naturalWidth
  let sh = img.naturalHeight
  if (ir > dr) sw = img.naturalHeight * dr
  else sh = img.naturalWidth / dr
  const sx = (img.naturalWidth - sw) / 2
  const sy = (img.naturalHeight - sh) / 2
  ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh)
}

/** Draw a pixel sprite anchored at its feet (cx, feetY), optionally mirrored. */
function drawSprite(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  cx: number,
  feetY: number,
  w: number,
  h: number,
  flip: boolean,
  bobY: number,
  alpha: number,
) {
  const y = feetY - h + bobY
  ctx.imageSmoothingEnabled = true
  ctx.globalAlpha = alpha
  if (flip) {
    ctx.save()
    ctx.translate(cx, 0)
    ctx.scale(-1, 1)
    ctx.drawImage(img, -w / 2, y, w, h)
    ctx.restore()
  } else {
    ctx.drawImage(img, cx - w / 2, y, w, h)
  }
  ctx.globalAlpha = 1
  ctx.imageSmoothingEnabled = true
}

/* ---------- world floor ---------- */

interface ViewRect {
  x: number
  y: number
  w: number
  h: number
}

function drawFloor(ctx: CanvasRenderingContext2D, images: ImageStore, view: ViewRect) {
  // base carpet
  ctx.fillStyle = '#1a160f'
  ctx.fillRect(view.x, view.y, view.w, view.h)
  const tile = images.get(FLOOR_TILE_PATH)
  if (tile) {
    const T = 64
    const x0 = Math.floor(view.x / T) * T
    const y0 = Math.floor(view.y / T) * T
    ctx.globalAlpha = 0.5
    for (let x = x0; x < view.x + view.w; x += T) {
      for (let y = y0; y < view.y + view.h; y += T) ctx.drawImage(tile, x, y, T, T)
    }
    ctx.globalAlpha = 1
  }
  // vignette toward the building edges
  ctx.fillStyle = 'rgba(10,8,5,0.35)'
  ctx.fillRect(view.x, view.y, view.w, view.h)
}

/* ---------- room ---------- */

function drawRoom(
  ctx: CanvasRenderingContext2D,
  room: RoomModel,
  images: ImageStore,
  state: FrameState,
) {
  const { x, y, w, h } = room.rect
  const accent = ACCENT_HEX[room.accent]

  // drop shadow under the room slab
  ctx.fillStyle = 'rgba(0,0,0,0.4)'
  roundRect(ctx, x + 6, y + 10, w, h, 18)
  ctx.fill()

  // floor / background image filling the interior (below the header band)
  ctx.save()
  roundRect(ctx, x, y, w, h, 16)
  ctx.clip()
  const bg = images.get(`/art/backgrounds/${room.backgroundId}.png`)
  ctx.fillStyle = '#0f0c08'
  ctx.fillRect(x, y, w, h)
  if (bg) {
    ctx.imageSmoothingEnabled = true
    drawCover(ctx, bg, x, y + HEADER_H, w, h - HEADER_H)
  }
  // readability wash over the artwork
  ctx.fillStyle = 'rgba(12,10,6,0.30)'
  ctx.fillRect(x, y + HEADER_H, w, h - HEADER_H)
  ctx.restore()

  // header band
  ctx.save()
  roundRect(ctx, x, y, w, HEADER_H, 16)
  ctx.clip()
  const grad = ctx.createLinearGradient(x, y, x, y + HEADER_H)
  grad.addColorStop(0, rgba(INK, 0.96))
  grad.addColorStop(1, rgba(INK, 0.82))
  ctx.fillStyle = grad
  ctx.fillRect(x, y, w, HEADER_H)
  ctx.fillStyle = rgba(accent, 0.9)
  ctx.fillRect(x, y + HEADER_H - 3, w, 3)
  ctx.restore()

  // title + capacity chip
  ctx.fillStyle = room.isExec ? accent : CREAM
  ctx.font = `600 22px ${FONT}`
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillText(room.title, x + 22, y + HEADER_H / 2 + 1)

  const chip = `${room.count}/10`
  ctx.font = `600 14px ${FONT}`
  const cw = ctx.measureText(chip).width + 22
  roundRect(ctx, x + w - cw - 16, y + HEADER_H / 2 - 12, cw, 24, 12)
  ctx.fillStyle = rgba(accent, 0.18)
  ctx.fill()
  ctx.strokeStyle = rgba(accent, 0.5)
  ctx.lineWidth = 1
  ctx.stroke()
  ctx.fillStyle = accent
  ctx.textAlign = 'center'
  ctx.fillText(chip, x + w - cw / 2 - 16, y + HEADER_H / 2 + 1)

  // drop-target highlight (arrange mode)
  if (state.arrange && state.dropRoomIndex === room.index) {
    roundRect(ctx, x + 4, y + 4, w - 8, h - 8, 14)
    ctx.strokeStyle = rgba(ACCENT_HEX.teal, 0.9)
    ctx.lineWidth = 3
    ctx.setLineDash([10, 7])
    ctx.stroke()
    ctx.setLineDash([])
  } else {
    // resting wall outline
    roundRect(ctx, x, y, w, h, 16)
    ctx.strokeStyle = rgba(accent, 0.35)
    ctx.lineWidth = 2
    ctx.stroke()
  }
}

/** Background props (plants, watercooler, board). Drawn before the desks so a
 *  packed room simply hides them behind the furniture instead of clashing. */
function drawDecor(ctx: CanvasRenderingContext2D, room: RoomModel, images: ImageStore) {
  ctx.imageSmoothingEnabled = true
  for (const item of room.decor) {
    let img = images.get(item.src)
    if (!img && item.src === DECOR.meetingTable) img = images.get(DECOR.whiteboard) // optional art → fallback
    if (!img) continue
    // soft contact shadow so props sit on the floor
    ctx.imageSmoothingEnabled = true
    ctx.fillStyle = 'rgba(0,0,0,0.22)'
    ctx.beginPath()
    ctx.ellipse(item.x, item.y - 2, item.w * 0.36, item.w * 0.12, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.imageSmoothingEnabled = true
    ctx.globalAlpha = 0.96
    ctx.drawImage(img, item.x - item.w / 2, item.y - item.h, item.w, item.h)
    ctx.globalAlpha = 1
  }
  ctx.imageSmoothingEnabled = true
}

/* ---------- character bob by state (seated) ---------- */

function motion(state: AgentState, phase: number, now: number): { dx: number; dy: number } {
  const t = now / 1000 + phase * 6.283
  switch (state) {
    case 'working':
    case 'review':
      return { dx: 0, dy: -Math.abs(Math.sin(t * 1.7)) * 3 }
    case 'thinking':
      return { dx: Math.sin(t * 1.1) * 1.5, dy: 0 }
    case 'handoff':
      return { dx: 0, dy: -Math.abs(Math.sin(t * 5)) * 3 }
    case 'blocked':
      return { dx: Math.sin(t * 14) * 1.4, dy: 0 }
    case 'idle':
      return { dx: 0, dy: Math.sin(t * 0.9) * 1.2 }
    default:
      return { dx: 0, dy: Math.sin(t * 0.5) * 0.6 }
  }
}

/* ---------- station (chair/base + foreground desk) ---------- */

function drawStationBase(ctx: CanvasRenderingContext2D, d: DeptSprite, images: ImageStore, state: FrameState) {
  const accent = ACCENT_HEX[d.accent]
  const sel = state.selectedId === d.id
  const hov = state.hoverId === d.id
  const drag = state.drag && state.drag.id === d.id ? state.drag : null
  const odx = drag ? drag.dx : 0
  const ody = drag ? drag.dy : 0
  const cell = { x: d.cell.x + odx, y: d.cell.y + ody, w: d.cell.w, h: d.cell.h }
  const ax = d.anchor.x + odx
  const ay = d.anchor.y + ody
  const s = d.scale

  // selection / hover cell ring
  if (sel || hov || (state.arrange && !d.isExec)) {
    roundRect(ctx, cell.x + 4, cell.y + 2, cell.w - 8, cell.h - 4, 12)
    if (sel) {
      ctx.fillStyle = rgba(accent, 0.12)
      ctx.fill()
      ctx.strokeStyle = rgba(accent, 0.95)
      ctx.lineWidth = 2.5
      ctx.setLineDash(state.arrange ? [9, 6] : [])
      ctx.stroke()
      ctx.setLineDash([])
    } else if (state.arrange && !d.isExec) {
      ctx.strokeStyle = rgba(accent, 0.55)
      ctx.lineWidth = 1.5
      ctx.setLineDash([8, 6])
      ctx.stroke()
      ctx.setLineDash([])
    } else {
      ctx.strokeStyle = rgba(accent, 0.4)
      ctx.lineWidth = 1.5
      ctx.stroke()
    }
  }

  ctx.imageSmoothingEnabled = true
  // chair (the seat stays put even when the worker walks off)
  const chair = images.get(d.chairSrc)
  if (chair) {
    const cw = 52 * s
    ctx.drawImage(chair, ax - cw / 2, ay - cw * 0.86, cw, cw)
  }
  ctx.imageSmoothingEnabled = true
}

function drawStationForeground(
  ctx: CanvasRenderingContext2D,
  d: DeptSprite,
  images: ImageStore,
  ox: number,
  oy: number,
) {
  const accent = ACCENT_HEX[d.accent]
  const cell = { x: d.cell.x + ox, y: d.cell.y + oy, w: d.cell.w, h: d.cell.h }
  const ax = d.anchor.x + ox
  const ay = d.anchor.y + oy
  const s = d.scale

  ctx.imageSmoothingEnabled = true
  // Draw the desk after the seated character so the lower body tucks behind
  // the front edge instead of appearing on top of the tabletop.
  const desk = images.get(d.deskSrc)
  if (desk) {
    const dw = 82 * s
    ctx.drawImage(desk, ax - dw / 2, ay - dw * 0.24, dw, dw)
  }
  ctx.imageSmoothingEnabled = true

  // task chip at the bottom of the cell
  if (d.taskCount > 0 && !d.isExec) {
    const label = d.taskCount > 1 ? `${d.taskCount} งาน` : fit(ctx, d.taskTitle || 'งาน', 96)
    ctx.font = `600 13px ${FONT}`
    const tw = ctx.measureText(label).width + 30
    const tx = cell.x + cell.w / 2 - tw / 2
    const ty = cell.y + cell.h - 26
    roundRect(ctx, tx, ty, tw, 22, 7)
    ctx.fillStyle = rgba(INK, 0.6)
    ctx.fill()
    ctx.strokeStyle = rgba(accent, 0.5)
    ctx.lineWidth = 1
    ctx.stroke()
    const parcel = images.get(PARCEL_PATH)
    if (parcel) {
      ctx.imageSmoothingEnabled = true
      ctx.drawImage(parcel, tx + 7, ty + 4, 14, 14)
      ctx.imageSmoothingEnabled = true
    }
    ctx.fillStyle = CREAM
    ctx.fillText(label, tx + 26, ty + 12)
  }
}

/* ---------- character (drawn at the live actor position) ---------- */

function drawCharacter(
  ctx: CanvasRenderingContext2D,
  d: DeptSprite,
  a: Actor,
  images: ImageStore,
  state: FrameState,
  ox: number,
  oy: number,
) {
  const accent = ACCENT_HEX[d.accent]
  const s = d.scale
  const chw = 34 * s
  const ax = a.x + ox
  const ay = a.y + oy
  const offline = d.state === 'offline'
  const pose = actorPose(a)

  // contact shadow follows the body
  ctx.fillStyle = 'rgba(0,0,0,0.32)'
  ctx.beginPath()
  ctx.ellipse(ax, ay + 2, 20 * s, 6.5 * s, 0, 0, Math.PI * 2)
  ctx.fill()

  // sprite: walking → directional walk cycle; standing (meeting) → a standing
  // pose, switched to mouth-moving talk frames while speaking; seated → the
  // desk pose with its breathing bob.
  if (pose === 'walk') {
    const step = Math.floor(a.walkPhase / STEP_MS)
    const wf = walkFrame(d.charId, a.facing, step)
    const img = images.get(wf.src) ?? images.get(d.charSrc)
    if (img) drawSprite(ctx, img, ax, ay, chw, chw, wf.flip, step % 2 === 0 ? 0 : -3, offline ? 0.7 : 1)
  } else if (pose === 'stand') {
    const talking = a.bubble?.kind === 'speech'
    const poseName = talking ? (Math.floor(state.now / 240) % 2 === 0 ? 'talk-1' : 'talk-2') : 'stand-down'
    const img = images.get(charPath(d.charId, poseName)) ?? images.get(charPath(d.charId, 'walk-down'))
    if (img) drawSprite(ctx, img, ax, ay, chw, chw, a.facing === 'left', 0, offline ? 0.7 : 1)
  } else {
    const char = images.get(d.charSrc)
    if (char) {
      const { dx, dy } = motion(d.state, d.phase, state.now)
      drawSprite(ctx, char, ax + dx, ay + dy, chw, chw, false, 0, offline ? 0.7 : 1)
    }
  }

  // prop in hand (parcel during a handoff, mug on the way back from coffee)
  if (a.carry) {
    const prop = images.get(a.carry === 'mug' ? COFFEE_MUG_PATH : PARCEL_PATH)
    if (prop) {
      ctx.imageSmoothingEnabled = true
      ctx.drawImage(prop, ax + 9 * s, ay - chw * 0.5, 15, 15)
      ctx.imageSmoothingEnabled = true
    }
  }

  // floating nameplate above the head
  const headY = ay - chw - 4
  const plateH = 22
  ctx.font = `600 13px ${FONT}`
  const name = fit(ctx, d.name, 150)
  const plateW = Math.min(180, ctx.measureText(name).width + 30)
  const px = ax - plateW / 2
  const py = headY - plateH
  roundRect(ctx, px, py, plateW, plateH, 7)
  ctx.fillStyle = rgba(INK, 0.74)
  ctx.fill()
  ctx.strokeStyle = rgba(accent, 0.55)
  ctx.lineWidth = 1
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(px + 12, py + plateH / 2, 3.5, 0, Math.PI * 2)
  ctx.fillStyle = STATE_HEX[d.state]
  ctx.fill()
  ctx.fillStyle = d.isExec ? accent : CREAM
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillText(name, px + 22, py + plateH / 2 + 0.5)

  // speech bubble (priority) or a passive state emote, above the nameplate
  if (a.bubble) {
    drawBubble(ctx, ax, py - 5, a.bubble)
  } else {
    const e = emoteFor(d.state)
    if (e) drawEmote(ctx, ax, py - 5, e.text, e.color, state.now, d.phase)
  }
}

function emoteFor(state: AgentState): { text: string; color: string } | null {
  switch (state) {
    case 'blocked':
      return { text: '!', color: STATE_HEX.blocked }
    case 'thinking':
      return { text: '?', color: STATE_HEX.thinking }
    case 'offline':
      return { text: 'z', color: STATE_HEX.offline }
    default:
      return null
  }
}

/** A small pulsing glyph in a colored chip (state cue, not a message). */
function drawEmote(
  ctx: CanvasRenderingContext2D,
  cx: number,
  baseY: number,
  text: string,
  color: string,
  now: number,
  phase: number,
) {
  const pulse = 0.7 + 0.3 * Math.sin(now / 260 + phase * 6.283)
  const r = 11
  const cy = baseY - r
  ctx.beginPath()
  ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.fillStyle = rgba(INK, 0.8)
  ctx.fill()
  ctx.strokeStyle = rgba(color, pulse)
  ctx.lineWidth = 2
  ctx.stroke()
  ctx.fillStyle = color
  ctx.font = `800 15px ${FONT}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.globalAlpha = pulse
  ctx.fillText(text, cx, cy + 1)
  ctx.globalAlpha = 1
}

/** Speech bubble (chat) or emote bubble (errand cue) above the head. */
function drawBubble(ctx: CanvasRenderingContext2D, cx: number, baseY: number, b: Bubble) {
  if (b.kind === 'emote') {
    const r = 15
    const cy = baseY - r
    roundRect(ctx, cx - r, cy - r, r * 2, r * 2, 9)
    ctx.fillStyle = rgba(CREAM, 0.96)
    ctx.fill()
    ctx.strokeStyle = rgba(INK, 0.5)
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.fillStyle = INK
    ctx.font = `600 16px ${FONT}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(b.text, cx, cy + 1)
    return
  }
  ctx.font = `600 13px ${FONT}`
  const text = fit(ctx, b.text, 160)
  const w = ctx.measureText(text).width + 20
  const h = 24
  const x = cx - w / 2
  const y = baseY - h
  roundRect(ctx, x, y, w, h, 9)
  ctx.fillStyle = rgba(CREAM, 0.97)
  ctx.fill()
  ctx.strokeStyle = rgba(INK, 0.45)
  ctx.lineWidth = 1
  ctx.stroke()
  // tail
  ctx.beginPath()
  ctx.moveTo(cx - 5, y + h - 0.5)
  ctx.lineTo(cx + 5, y + h - 0.5)
  ctx.lineTo(cx, y + h + 6)
  ctx.closePath()
  ctx.fillStyle = rgba(CREAM, 0.97)
  ctx.fill()
  ctx.fillStyle = INK
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, cx, y + h / 2)
}

/** truncate text to a pixel width using the current ctx font. */
function fit(ctx: CanvasRenderingContext2D, text: string, maxW: number): string {
  const t = text.trim()
  if (ctx.measureText(t).width <= maxW) return t
  let lo = 0
  let hi = t.length
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (ctx.measureText(t.slice(0, mid) + '…').width <= maxW) lo = mid
    else hi = mid - 1
  }
  return t.slice(0, lo) + '…'
}

/* ---------- effects ---------- */

export function drawFx(ctx: CanvasRenderingContext2D, fx: Fx, images: ImageStore, now: number) {
  const t = (now - fx.start) / fx.dur
  if (t >= 1) return
  if (fx.kind === 'ripple') {
    const r = 8 + t * 44
    ctx.strokeStyle = rgba(fx.color, (1 - t) * 0.8)
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.arc(fx.x, fx.y, r, 0, Math.PI * 2)
    ctx.stroke()
  } else if (fx.kind === 'burst') {
    for (let i = 0; i < 7; i++) {
      const a = (i / 7) * Math.PI * 2
      const r = t * 34
      ctx.fillStyle = rgba(fx.color, 1 - t)
      ctx.beginPath()
      ctx.arc(fx.x + Math.cos(a) * r, fx.y + Math.sin(a) * r, 3, 0, Math.PI * 2)
      ctx.fill()
    }
  } else if (fx.kind === 'float') {
    ctx.fillStyle = rgba(fx.color, 1 - t)
    ctx.font = `700 22px ${FONT}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(fx.text || '', fx.x, fx.y - t * 40)
  } else if (fx.kind === 'travel' && fx.x2 != null && fx.y2 != null) {
    const cx = (fx.x + fx.x2) / 2
    const cy = (fx.y + fx.y2) / 2 - 70
    const it = 1 - t
    const x = it * it * fx.x + 2 * it * t * cx + t * t * fx.x2
    const y = it * it * fx.y + 2 * it * t * cy + t * t * fx.y2
    const parcel = images.get(PARCEL_PATH)
    if (fx.charSrc) {
      const walker = images.get(fx.charSrc)
      if (walker) {
        ctx.imageSmoothingEnabled = true
        ctx.drawImage(walker, x - 17, y - 34, 34, 34)
        ctx.imageSmoothingEnabled = true
      }
    }
    if (parcel) {
      ctx.imageSmoothingEnabled = true
      ctx.drawImage(parcel, x - 8, y - 50, 16, 16)
      ctx.imageSmoothingEnabled = true
    }
  }
}

/* ---------- frame ---------- */

export function drawWorld(
  ctx: CanvasRenderingContext2D,
  world: WorldModel,
  images: ImageStore,
  state: FrameState,
  view: ViewRect,
  actors: Actors,
) {
  drawFloor(ctx, images, view)

  // pass 1 — rooms, decor and chair/base hit areas (static)
  const visible: RoomModel[] = []
  for (const room of world.rooms) {
    const rd = state.roomDrag && state.roomDrag.index === room.index ? state.roomDrag : null
    if (!rd && !overlaps(room.rect, view)) continue
    visible.push(room)
    ctx.save()
    if (rd) ctx.translate(rd.dx, rd.dy)
    drawRoom(ctx, room, images, state)
    drawDecor(ctx, room, images)
    for (const d of room.depts) drawStationBase(ctx, d, images, state)
    ctx.restore()
  }

  // pass 2 — characters and foreground desks, y-sorted for correct overlap
  type Entry =
    | { kind: 'character'; d: DeptSprite; a: Actor; ox: number; oy: number; y: number }
    | { kind: 'desk'; d: DeptSprite; ox: number; oy: number; y: number }
  const entries: Entry[] = []
  const drawn = new Set<string>()
  for (const room of visible) {
    const rd = state.roomDrag && state.roomDrag.index === room.index ? state.roomDrag : null
    for (const d of room.depts) {
      const a = actors.get(d.id)
      if (!a) continue
      let ox = rd ? rd.dx : 0
      let oy = rd ? rd.dy : 0
      if (state.drag && state.drag.id === d.id) {
        ox += state.drag.dx
        oy += state.drag.dy
      }
      entries.push({ kind: 'character', d, a, ox, oy, y: a.y + oy })
      entries.push({ kind: 'desk', d, ox, oy, y: d.anchor.y + oy + 18 * d.scale })
      drawn.add(d.id)
    }
  }
  // include actors that wandered out of their (possibly culled) home room
  for (const a of actors.list()) {
    if (drawn.has(a.id) || (!a.errand && !a.station)) continue
    if (a.x < view.x - 60 || a.x > view.x + view.w + 60 || a.y < view.y - 90 || a.y > view.y + view.h + 60) continue
    const d = world.byId.get(a.id)
    if (d) entries.push({ kind: 'character', d, a, ox: 0, oy: 0, y: a.y })
  }
  entries.sort((p, q) => p.y - q.y)
  for (const e of entries) {
    if (e.kind === 'character') drawCharacter(ctx, e.d, e.a, images, state, e.ox, e.oy)
    else drawStationForeground(ctx, e.d, images, e.ox, e.oy)
  }
}

function overlaps(a: ViewRect, b: ViewRect): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
}

export type { ViewRect }
