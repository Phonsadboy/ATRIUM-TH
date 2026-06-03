/* ============================================================
   ATRIUM — office sprite catalogue + lazy image cache

   A tiny dependency-free image loader. `get()` returns a decoded
   <img> once it is ready (else null), kicking off the load on first
   request. The render loop runs every frame, so sprites simply pop
   in as they finish decoding — no preload barrier, no callbacks.
   ============================================================ */

import { OFFICE_BACKGROUNDS } from './backgrounds'

export type CharPose =
  | 'sit-idle'
  | 'sit-work'
  | 'sit-sleep'
  | 'walk-down'
  | 'stand-down'
  | 'talk-1'
  | 'talk-2'

/** char01..char10 plus the executive. */
export const CHAR_IDS = ['exec', ...Array.from({ length: 10 }, (_, i) => `char${String(i + 1).padStart(2, '0')}`)]

export function charPath(charId: string, pose: CharPose): string {
  return `/art/characters/${charId}/${pose}.png`
}

export function deskPath(n: number): string {
  return `/art/furniture/desk-${String(((n % 10) + 10) % 10 + 1).padStart(2, '0')}.png`
}

export function chairPath(n: number): string {
  return `/art/furniture/chair-${String(((n % 4) + 4) % 4 + 1).padStart(2, '0')}.png`
}

export const PARCEL_PATH = '/art/furniture/parcel.png'
export const PLANT_PATH = '/art/furniture/plant-01.png'
export const FLOOR_TILE_PATH = '/art/floor/floor-tile.png'

/* ---------- walk cycle (game mode) ---------- */

export type Facing = 'down' | 'up' | 'left' | 'right'

/** How many real walk frames ship per direction.
 *
 *  0 → no dedicated art yet: the engine falls back to the front-facing
 *      `walk-down` sprite (mirrored for `left`) plus a procedural step-bob.
 *  1 → a single `walk-<dir>.png`.
 *  N → numbered frames `walk-<dir>-1.png` … `walk-<dir>-N.png`.
 *
 *  Bump these as art lands (see ART_ASSETS.md §10). Right-facing movement
 *  mirrors the left-facing frames so we do not need duplicate art. */
export const WALK_FRAMES: Record<Facing, number> = {
  down: 2,
  up: 2,
  left: 2,
  right: 0,
}

/** Resolve the sprite for one walk step, honouring {@link WALK_FRAMES}.
 *  `flip` means draw it horizontally mirrored (used to make `right` from
 *  `left` art, and to vary the front-facing fallback). `frame` is a 0-based
 *  step index that the caller advances over time. */
export function walkFrame(charId: string, facing: Facing, frame: number): { src: string; flip: boolean } {
  const count = WALK_FRAMES[facing]
  if (count >= 1) {
    const suffix = count === 1 ? '' : `-${(((frame % count) + count) % count) + 1}`
    return { src: `/art/characters/${charId}/walk-${facing}${suffix}.png`, flip: false }
  }
  // `right` mirrors `left` art when that exists
  if (facing === 'right' && WALK_FRAMES.left >= 1) {
    const c = WALK_FRAMES.left
    const suffix = c === 1 ? '' : `-${(((frame % c) + c) % c) + 1}`
    return { src: `/art/characters/${charId}/walk-left${suffix}.png`, flip: true }
  }
  // universal fallback: the one sprite that always ships, mirrored for `left`
  return { src: charPath(charId, 'walk-down'), flip: facing === 'left' }
}

/* ---------- decor props (already in the art pack, now placed in rooms) ---------- */

export const DECOR = {
  plantSmall: '/art/furniture/plant-01.png',
  plantTall: '/art/furniture/plant-02.png',
  lamp: '/art/furniture/lamp.png',
  bookshelf: '/art/furniture/bookshelf.png',
  cabinet: '/art/furniture/cabinet.png',
  serverRack: '/art/furniture/server-rack.png',
  watercooler: '/art/furniture/watercooler.png',
  whiteboard: '/art/furniture/whiteboard.png',
  meetingTable: '/art/furniture/meeting-table.png',
  door: '/art/furniture/door.png',
  rug: '/art/floor/rug.png',
} as const

/** Carried in hand on the walk back from the watercooler. */
export const COFFEE_MUG_PATH = '/art/furniture/coffee-mug.png'

/** Lazy, frame-driven image cache. */
export class ImageStore {
  private cache = new Map<string, HTMLImageElement>()

  /** Loaded image, or null while it decodes (load kicks off on first call). */
  get(src: string): HTMLImageElement | null {
    let img = this.cache.get(src)
    if (!img) {
      img = new Image()
      img.decoding = 'async'
      img.src = src
      this.cache.set(src, img)
    }
    return img.complete && img.naturalWidth > 0 ? img : null
  }

  /** Warm a batch of sources so the first frames already have art. */
  preload(srcs: string[]) {
    for (const s of srcs) this.get(s)
  }
}

/** Everything the office can draw — queued once on boot. */
export function allOfficeAssets(): string[] {
  const out: string[] = [FLOOR_TILE_PATH, PARCEL_PATH, PLANT_PATH, COFFEE_MUG_PATH, ...Object.values(DECOR)]
  const poses: CharPose[] = ['sit-idle', 'sit-work', 'sit-sleep', 'walk-down', 'stand-down', 'talk-1', 'talk-2']
  const dirs: Facing[] = ['down', 'up', 'left', 'right']
  for (const c of CHAR_IDS) {
    for (const p of poses) out.push(charPath(c, p))
    // queue whatever extra walk frames are configured to exist
    for (const dir of dirs) {
      for (let f = 0; f < WALK_FRAMES[dir]; f++) out.push(walkFrame(c, dir, f).src)
    }
  }
  for (let i = 0; i < 10; i++) out.push(deskPath(i))
  for (let i = 0; i < 4; i++) out.push(chairPath(i))
  for (const b of OFFICE_BACKGROUNDS) out.push(b.path)
  return out
}
