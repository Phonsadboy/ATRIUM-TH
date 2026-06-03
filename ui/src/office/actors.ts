/* ============================================================
   ATRIUM — office actors (the "game" layer)

   The world model (world.ts) says where each department's DESK is.
   This module gives every department a moving body — an "actor" — that
   normally sits at its desk but can stand up and walk: to hand a task
   to another desk, to gather for a meeting, or to grab coffee.

   It is deliberately decoupled from the world model: `sync()` reads the
   latest desk anchors, every other command takes plain world-space
   points. The renderer reads actor positions to draw the character on
   top of (and walking between) the static desks.

   Pure simulation: no canvas, no React. OfficeFloor drives it by calling
   sync() + update() each frame and issuing commands from pulses/threads.
   ============================================================ */

import type { Facing } from './assets'
import type { WorldModel } from './world'

const WALK_SPEED = 92 // world px per second
const ARRIVE_EPS = 2.5 // px — close enough to count as "there"
export const STEP_MS = 165 // ms per walk frame (also the fake step-bob period)
const MEETING_RADIUS = 50 // px — ring the participants stand on

export type ActorPose = 'sit' | 'walk' | 'stand'
export type BubbleKind = 'speech' | 'emote'

export interface Bubble {
  text: string
  kind: BubbleKind
  until: number
}

interface Leg {
  x: number
  y: number
  /** ms to wait on arrival (0 = pass straight through) */
  pause: number
  /** facing while paused (otherwise derived from the walk direction) */
  face?: Facing
  /** carry a prop in hand on the way to this leg */
  carry?: 'parcel' | 'mug'
  /** show this bubble on arrival */
  bubble?: { text: string; kind: BubbleKind; dur: number }
}

interface Errand {
  kind: 'handoff' | 'coffee'
  legs: Leg[]
  i: number
  /** 0 = not waiting; else the timestamp the wait ends */
  waitUntil: number
}

export interface Actor {
  id: string
  x: number
  y: number
  facing: Facing
  moving: boolean
  /** ms accumulated while walking → drives the walk frame + step-bob */
  walkPhase: number
  /** prop drawn in hand right now (parcel for handoffs, mug after coffee) */
  carry: 'parcel' | 'mug' | null
  /** desk anchor (feet), refreshed from the world each sync */
  home: { x: number; y: number }
  /** sustained target that overrides home (a meeting spot); null = go home */
  station: { x: number; y: number; face: Facing } | null
  /** transient scripted trip; takes priority over station/home */
  errand: Errand | null
  bubble: Bubble | null
}

function faceFromVec(dx: number, dy: number): Facing {
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'right' : 'left'
  return dy >= 0 ? 'down' : 'up'
}

/** Visible pose for the renderer to pick the right sprite. */
export function actorPose(a: Actor): ActorPose {
  if (a.moving) return 'walk'
  if (a.station) return 'stand'
  return 'sit'
}

export class Actors {
  private map = new Map<string, Actor>()
  private lastNow = 0
  private arrange = false

  get(id: string): Actor | undefined {
    return this.map.get(id)
  }

  list(): Actor[] {
    return [...this.map.values()]
  }

  setArrange(on: boolean): void {
    this.arrange = on
  }

  /** Reconcile actors against the current desks: spawn newcomers seated at
   *  their desk, refresh home anchors, drop departed departments. */
  sync(world: WorldModel): void {
    const seen = new Set<string>()
    for (const room of world.rooms) {
      for (const d of room.depts) {
        seen.add(d.id)
        const home = { x: d.anchor.x, y: d.anchor.y }
        const a = this.map.get(d.id)
        if (!a) {
          this.map.set(d.id, {
            id: d.id,
            x: home.x,
            y: home.y,
            facing: 'down',
            moving: false,
            walkPhase: 0,
            carry: null,
            home,
            station: null,
            errand: null,
            bubble: null,
          })
        } else {
          a.home = home
        }
      }
    }
    for (const id of [...this.map.keys()]) if (!seen.has(id)) this.map.delete(id)
  }

  /** Advance every actor toward its target. `now` is the rAF timestamp (ms). */
  update(now: number): void {
    const dt = this.lastNow ? Math.min(0.05, Math.max(0, (now - this.lastNow) / 1000)) : 0
    this.lastNow = now
    for (const a of this.map.values()) {
      if (a.bubble && now >= a.bubble.until) a.bubble = null

      // arrange mode: freeze behaviour, just settle everyone at their desk
      if (this.arrange) {
        this.moveToward(a, a.home.x, a.home.y, dt)
        continue
      }

      if (a.errand) {
        this.stepErrand(a, dt, now)
        continue
      }

      const target = a.station ?? a.home
      const arrived = this.moveToward(a, target.x, target.y, dt)
      if (arrived && a.station) a.facing = a.station.face
    }
  }

  private stepErrand(a: Actor, dt: number, now: number): void {
    const errand = a.errand!
    const leg = errand.legs[errand.i]
    if (!leg) {
      a.errand = null
      a.carry = null
      return
    }
    if (errand.waitUntil > 0) {
      a.moving = false
      a.walkPhase = 0
      if (leg.face) a.facing = leg.face
      if (now >= errand.waitUntil) {
        errand.waitUntil = 0
        errand.i += 1
        if (errand.i >= errand.legs.length) {
          a.errand = null
          a.carry = null
        }
      }
      return
    }
    a.carry = leg.carry ?? null
    const arrived = this.moveToward(a, leg.x, leg.y, dt)
    if (arrived) {
      if (leg.bubble) a.bubble = { text: leg.bubble.text, kind: leg.bubble.kind, until: now + leg.bubble.dur }
      if (leg.pause > 0) {
        errand.waitUntil = now + leg.pause
      } else {
        errand.i += 1
        if (errand.i >= errand.legs.length) {
          a.errand = null
          a.carry = null
        }
      }
    }
  }

  /** Step toward (tx,ty). Returns true once arrived (and snaps + stops). */
  private moveToward(a: Actor, tx: number, ty: number, dt: number): boolean {
    const dx = tx - a.x
    const dy = ty - a.y
    const dist = Math.hypot(dx, dy)
    const step = WALK_SPEED * dt
    if (dist <= Math.max(step, ARRIVE_EPS)) {
      a.x = tx
      a.y = ty
      if (a.moving) a.walkPhase = 0
      a.moving = false
      return true
    }
    a.x += (dx / dist) * step
    a.y += (dy / dist) * step
    a.facing = faceFromVec(dx, dy)
    a.moving = true
    a.walkPhase += dt * 1000
    return false
  }

  /* ---------- commands ---------- */

  /** Walk to just in front of a target desk, drop a parcel, walk home. */
  handoff(fromId: string, target: { x: number; y: number }): void {
    const a = this.map.get(fromId)
    if (!a) return
    const deliver = { x: target.x, y: target.y + 26 }
    a.errand = {
      kind: 'handoff',
      i: 0,
      waitUntil: 0,
      legs: [
        {
          x: deliver.x,
          y: deliver.y,
          pause: 750,
          carry: 'parcel',
          face: faceFromVec(target.x - deliver.x, target.y - deliver.y),
          bubble: { text: '📦', kind: 'emote', dur: 1500 },
        },
        { x: a.home.x, y: a.home.y, pause: 0 },
      ],
    }
  }

  /** Walk to a spot (e.g. the watercooler), pause, walk home. */
  coffee(id: string, spot: { x: number; y: number }): void {
    const a = this.map.get(id)
    if (!a || a.errand || a.station) return
    a.errand = {
      kind: 'coffee',
      i: 0,
      waitUntil: 0,
      legs: [
        { x: spot.x, y: spot.y - 6, pause: 1900, face: 'up', bubble: { text: '☕', kind: 'emote', dur: 2200 } },
        { x: a.home.x, y: a.home.y, pause: 0, carry: 'mug' },
      ],
    }
  }

  /** Gather `ids` in a ring around `spot`; send everyone else home. */
  meeting(ids: string[], spot: { x: number; y: number }): void {
    const want = new Set(ids)
    for (const [id, a] of this.map) if (!want.has(id) && a.station) a.station = null
    const n = Math.max(1, ids.length)
    ids.forEach((id, k) => {
      const a = this.map.get(id)
      if (!a) return
      const ang = -Math.PI / 2 + (k / n) * Math.PI * 2
      const px = spot.x + Math.cos(ang) * MEETING_RADIUS
      const py = spot.y + Math.sin(ang) * MEETING_RADIUS * 0.66 // squash for the ¾ view
      a.station = { x: px, y: py, face: faceFromVec(spot.x - px, spot.y - py) }
    })
  }

  /** Everyone returns to their desk. */
  endMeeting(): void {
    for (const a of this.map.values()) a.station = null
  }

  /** Show a speech/emote bubble above an actor for `dur` ms. */
  say(id: string, text: string, kind: BubbleKind, dur: number, now: number): void {
    const a = this.map.get(id)
    if (a) a.bubble = { text, kind, until: now + dur }
  }
}
