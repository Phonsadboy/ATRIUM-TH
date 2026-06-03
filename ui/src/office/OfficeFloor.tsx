/* ============================================================
   ATRIUM — office floor (Canvas 2D, no game engine)

   One continuous, scrollable building. Departments live in rooms of
   at most ten; you drag to pan, wheel to zoom, click a desk to chat,
   and (in arrange mode) drag a desk to a new spot or another room.
   The renderer is a plain <canvas> driven by requestAnimationFrame —
   the only thing reused from the previous build is the pixel art.
   ============================================================ */

import { useEffect, useMemo, useRef } from 'react'
import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import type { OfficeLayoutPlan } from '../state/ui'
import {
  OFFICE_BACKGROUNDS,
  type OfficeBackgroundId,
} from './backgrounds'
import { ImageStore, allOfficeAssets } from './assets'
import { ACCENT_HEX } from '../lib/visuals'
import { buildWorld, roomCenter, type WorldModel } from './world'
import { drawWorld, drawFx, timeTint, type Camera, type FrameState, type Fx, type ViewRect } from './render'
import { Actors } from './actors'
import { installDevOffice } from './devOffice'
import { deptIdFromThread } from '../lib/threads'
import type { ThreadId } from '../contract/types'

const MIN_ZOOM = 0.18
const MAX_ZOOM = 2.4

declare global {
  interface Window {
    __atriumOfficeLayout?: {
      backgrounds: typeof OFFICE_BACKGROUNDS
      applyPlan: (plan: OfficeLayoutPlan) => void
      moveDepartmentToRoom: (deptId: string, roomIndex: number) => void
      setDepartmentPosition: (deptId: string, pos: { x: number; y: number }) => void
      setRoomBackground: (roomIndex: number, backgroundId: OfficeBackgroundId) => void
    }
  }
}

export function OfficeFloor() {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const departments = useSelector((s) => s.departments)
  const tasks = useSelector((s) => s.tasks)
  const selectedDeptId = useUI((s) => s.selectedDeptId)
  const select = useUI((s) => s.select)
  const setRightTab = useUI((s) => s.setRightTab)
  const arrangeMode = useUI((s) => s.arrangeMode)
  const toggleArrange = useUI((s) => s.toggleArrange)
  const officeRoomIndex = useUI((s) => s.officeRoomIndex)
  const setOfficeRoom = useUI((s) => s.setOfficeRoom)
  const departmentRooms = useUI((s) => s.departmentRooms)
  const moveDepartmentToOfficeRoom = useUI((s) => s.moveDepartmentToOfficeRoom)
  const roomOverrides = useUI((s) => s.roomOverrides)
  const setRoomOverride = useUI((s) => s.setRoomOverride)
  const roomPositions = useUI((s) => s.roomPositions)
  const setRoomPosition = useUI((s) => s.setRoomPosition)
  const officeRoomCount = useUI((s) => s.officeRoomCount)
  const addOfficeRoom = useUI((s) => s.addOfficeRoom)
  const officeRoomBackgrounds = useUI((s) => s.officeRoomBackgrounds)
  const setOfficeRoomBackground = useUI((s) => s.setOfficeRoomBackground)
  const applyOfficePlan = useUI((s) => s.applyOfficePlan)

  const world = useMemo<WorldModel>(
    () =>
      buildWorld(departments, tasks, {
        departmentRooms,
        roomOverrides,
        roomBackgrounds: officeRoomBackgrounds,
        roomPositions,
        minRooms: officeRoomCount,
      }),
    [departments, tasks, departmentRooms, roomOverrides, officeRoomBackgrounds, roomPositions, officeRoomCount],
  )

  // ---- mutable render state (kept out of React so the rAF loop never restarts) ----
  const worldRef = useRef(world)
  const imagesRef = useRef(new ImageStore())
  const camRef = useRef<Camera>({ x: 0, y: 0, zoom: 0.6 })
  const camTargetRef = useRef<Camera | null>(null)
  const userAdjustedRef = useRef(false)
  const fxRef = useRef<Fx[]>([])
  const actorsRef = useRef(new Actors())
  const frameRef = useRef<FrameState>({
    selectedId: null,
    hoverId: null,
    arrange: false,
    dropRoomIndex: null,
    drag: null,
    roomDrag: null,
    now: 0,
  })
  const dragRef = useRef<
    | { mode: 'pan'; px: number; py: number; camX: number; camY: number }
    | { mode: 'dept'; id: string; startX: number; startY: number; baseDx: number; baseDy: number; moved: boolean }
    | { mode: 'room'; index: number; startX: number; startY: number; baseX: number; baseY: number; moved: boolean }
    | null
  >(null)
  // mirror the latest React state into the refs the rAF loop reads
  useEffect(() => {
    worldRef.current = world
    frameRef.current.selectedId = selectedDeptId
    frameRef.current.arrange = arrangeMode
    actorsRef.current.sync(world)
    actorsRef.current.setArrange(arrangeMode)
  }, [world, selectedDeptId, arrangeMode])

  /* ---------- camera helpers ---------- */

  const clampCamera = () => {
    const cam = camRef.current
    const w = worldRef.current
    const c = canvasRef.current
    if (!c) return
    const vw = c.clientWidth / cam.zoom
    const vh = c.clientHeight / cam.zoom
    const M = 140 // how far past the edge you may scroll
    const fit = (worldLen: number, viewLen: number, lo: number) => {
      if (viewLen >= worldLen) return lo + (worldLen - viewLen) / 2 // center when world is smaller
      return null
    }
    const cx = fit(w.width, vw, 0)
    const cy = fit(w.height, vh, 0)
    cam.x = cx != null ? cx : Math.min(Math.max(cam.x, -M), w.width - vw + M)
    cam.y = cy != null ? cy : Math.min(Math.max(cam.y, -M), w.height - vh + M)
  }

  const fitWorld = () => {
    const cam = camRef.current
    const c = canvasRef.current
    const w = worldRef.current
    if (!c || w.width < 1) return
    const zoom = Math.min(c.clientWidth / w.width, c.clientHeight / w.height) * 0.94
    cam.zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom))
    cam.x = w.width / 2 - c.clientWidth / cam.zoom / 2
    cam.y = w.height / 2 - c.clientHeight / cam.zoom / 2
    camTargetRef.current = null // cancel any pending fly-to
    clampCamera()
  }

  const flyToRoom = (index: number) => {
    const center = roomCenter(worldRef.current, index)
    const c = canvasRef.current
    if (!center || !c) return
    const zoom = Math.min(MAX_ZOOM, Math.max(0.7, camRef.current.zoom < 0.7 ? 0.85 : camRef.current.zoom))
    camTargetRef.current = {
      zoom,
      x: center.x - c.clientWidth / zoom / 2,
      y: center.y - c.clientHeight / zoom / 2,
    }
  }

  const zoomAt = (sx: number, sy: number, next: number) => {
    const cam = camRef.current
    const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next))
    if (z === cam.zoom) return
    const wx = cam.x + sx / cam.zoom
    const wy = cam.y + sy / cam.zoom
    cam.zoom = z
    cam.x = wx - sx / z
    cam.y = wy - sy / z
    camTargetRef.current = null
    userAdjustedRef.current = true
    clampCamera()
  }

  const screenToWorld = (clientX: number, clientY: number) => {
    const c = canvasRef.current
    const cam = camRef.current
    const r = c!.getBoundingClientRect()
    return { x: cam.x + (clientX - r.left) / cam.zoom, y: cam.y + (clientY - r.top) / cam.zoom }
  }

  const deptAt = (wx: number, wy: number) => {
    for (const room of worldRef.current.rooms) {
      for (const d of room.depts) {
        const c = d.cell
        if (wx >= c.x && wx <= c.x + c.w && wy >= c.y && wy <= c.y + c.h) return d
      }
    }
    return null
  }

  const roomAt = (wx: number, wy: number) => {
    for (const room of worldRef.current.rooms) {
      const r = room.rect
      if (wx >= r.x && wx <= r.x + r.w && wy >= r.y && wy <= r.y + r.h) return room
    }
    return null
  }

  /* ---------- boot: canvas + render loop + input ---------- */

  useEffect(() => {
    const host = hostRef.current
    const canvas = canvasRef.current
    if (!host || !canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    imagesRef.current.preload(allOfficeAssets())

    let raf = 0
    let disposed = false
    const dpr = () => Math.min(window.devicePixelRatio || 1, 2)

    const resize = () => {
      const w = Math.max(1, Math.round(host.clientWidth))
      const h = Math.max(1, Math.round(host.clientHeight))
      const ratio = dpr()
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      canvas.width = Math.round(w * ratio)
      canvas.height = Math.round(h * ratio)
      if (!userAdjustedRef.current) fitWorld()
      else clampCamera()
    }
    const ro = new ResizeObserver(resize)
    ro.observe(host)
    resize()
    fitWorld()

    const render = (now: number) => {
      if (disposed) return
      raf = requestAnimationFrame(render)
      const cam = camRef.current
      // ease toward a fly-to target
      const tgt = camTargetRef.current
      if (tgt) {
        cam.x += (tgt.x - cam.x) * 0.18
        cam.y += (tgt.y - cam.y) * 0.18
        cam.zoom += (tgt.zoom - cam.zoom) * 0.18
        if (Math.abs(tgt.x - cam.x) < 0.5 && Math.abs(tgt.y - cam.y) < 0.5 && Math.abs(tgt.zoom - cam.zoom) < 0.002) {
          cam.x = tgt.x
          cam.y = tgt.y
          cam.zoom = tgt.zoom
          camTargetRef.current = null
        }
        clampCamera()
      }

      const ratio = dpr()
      const s = ratio * cam.zoom
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.setTransform(s, 0, 0, s, -cam.x * s, -cam.y * s)

      const view: ViewRect = {
        x: cam.x,
        y: cam.y,
        w: canvas.clientWidth / cam.zoom,
        h: canvas.clientHeight / cam.zoom,
      }
      const frame = frameRef.current
      frame.now = now
      actorsRef.current.update(now)
      drawWorld(ctx, worldRef.current, imagesRef.current, frame, view, actorsRef.current)

      const fx = fxRef.current
      if (fx.length) {
        for (const f of fx) drawFx(ctx, f, imagesRef.current, now)
        fxRef.current = fx.filter((f) => now - f.start < f.dur)
      }

      // time-of-day wash over the whole scene (screen space)
      const tint = timeTint()
      if (tint) {
        ctx.setTransform(1, 0, 0, 1, 0, 0)
        ctx.fillStyle = tint
        ctx.fillRect(0, 0, canvas.width, canvas.height)
      }
    }
    raf = requestAnimationFrame(render)

    /* ----- pointer input ----- */
    const onDown = (e: PointerEvent) => {
      canvas.setPointerCapture(e.pointerId)
      const w = screenToWorld(e.clientX, e.clientY)
      const d = deptAt(w.x, w.y)
      if (frameRef.current.arrange && d && !d.isExec) {
        const ov = useUI.getState().roomOverrides[d.id]
        dragRef.current = {
          mode: 'dept',
          id: d.id,
          startX: w.x,
          startY: w.y,
          baseDx: ov?.x ?? 0,
          baseDy: ov?.y ?? 0,
          moved: false,
        }
        return
      }
      // arrange mode, empty room space → drag the whole room
      if (frameRef.current.arrange && !d) {
        const room = roomAt(w.x, w.y)
        if (room) {
          dragRef.current = {
            mode: 'room',
            index: room.index,
            startX: w.x,
            startY: w.y,
            baseX: room.rect.x,
            baseY: room.rect.y,
            moved: false,
          }
          return
        }
      }
      dragRef.current = { mode: 'pan', px: e.clientX, py: e.clientY, camX: camRef.current.x, camY: camRef.current.y }
    }

    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current
      const w = screenToWorld(e.clientX, e.clientY)
      if (!drag) {
        const d = deptAt(w.x, w.y)
        const id = d?.id ?? null
        if (id !== frameRef.current.hoverId) {
          frameRef.current.hoverId = id
        }
        if (d) canvas.style.cursor = frameRef.current.arrange && !d.isExec ? 'grab' : 'pointer'
        else canvas.style.cursor = frameRef.current.arrange && roomAt(w.x, w.y) ? 'move' : 'grab'
        return
      }
      if (drag.mode === 'room') {
        drag.moved = drag.moved || Math.abs(w.x - drag.startX) > 3 || Math.abs(w.y - drag.startY) > 3
        frameRef.current.roomDrag = { index: drag.index, dx: w.x - drag.startX, dy: w.y - drag.startY }
        canvas.style.cursor = 'move'
        return
      }
      if (drag.mode === 'pan') {
        const cam = camRef.current
        cam.x = drag.camX - (e.clientX - drag.px) / cam.zoom
        cam.y = drag.camY - (e.clientY - drag.py) / cam.zoom
        userAdjustedRef.current = true
        camTargetRef.current = null
        clampCamera()
        canvas.style.cursor = 'grabbing'
      } else {
        const dx = drag.baseDx + (w.x - drag.startX)
        const dy = drag.baseDy + (w.y - drag.startY)
        drag.moved = drag.moved || Math.abs(w.x - drag.startX) > 3 || Math.abs(w.y - drag.startY) > 3
        frameRef.current.drag = { id: drag.id, dx: w.x - drag.startX, dy: w.y - drag.startY }
        const target = roomAt(w.x, w.y)
        frameRef.current.dropRoomIndex =
          target && target.index !== worldRef.current.byId.get(drag.id)?.roomIndex ? target.index : null
        // stash the computed absolute delta for commit
        ;(drag as { _dx?: number; _dy?: number })._dx = dx
        ;(drag as { _dx?: number; _dy?: number })._dy = dy
      }
    }

    const onUp = (e: PointerEvent) => {
      const drag = dragRef.current
      dragRef.current = null
      canvas.style.cursor = 'grab'
      if (!drag) return
      if (drag.mode === 'pan') {
        // a clean tap (no real movement) selects the department under it
        if (Math.abs(e.clientX - drag.px) < 4 && Math.abs(e.clientY - drag.py) < 4) {
          const w = screenToWorld(e.clientX, e.clientY)
          const d = deptAt(w.x, w.y)
          if (d) {
            select(d.id)
            setRightTab('chat')
          }
        }
        return
      }
      if (drag.mode === 'room') {
        const rd = frameRef.current.roomDrag
        frameRef.current.roomDrag = null
        if (rd && drag.moved) {
          setRoomPosition(drag.index, { x: Math.max(0, drag.baseX + rd.dx), y: Math.max(0, drag.baseY + rd.dy) })
        } else {
          setOfficeRoom(drag.index)
        }
        return
      }
      // dept drag end
      frameRef.current.drag = null
      const dropRoom = frameRef.current.dropRoomIndex
      frameRef.current.dropRoomIndex = null
      const stash = drag as { _dx?: number; _dy?: number }
      if (dropRoom != null) {
        moveDepartmentToOfficeRoom(drag.id, dropRoom)
      } else if (drag.moved) {
        setRoomOverride(drag.id, { x: stash._dx ?? drag.baseDx, y: stash._dy ?? drag.baseDy })
      } else {
        select(drag.id)
        setRightTab('chat')
      }
    }

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = canvas.getBoundingClientRect()
      zoomAt(e.clientX - r.left, e.clientY - r.top, camRef.current.zoom * Math.pow(1.0017, -e.deltaY))
    }

    canvas.addEventListener('pointerdown', onDown)
    canvas.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    canvas.addEventListener('wheel', onWheel, { passive: false })

    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      ro.disconnect()
      canvas.removeEventListener('pointerdown', onDown)
      canvas.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      canvas.removeEventListener('wheel', onWheel)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // refit when the building grows/shrinks (unless the user has taken control)
  useEffect(() => {
    worldRef.current = world
    if (!userAdjustedRef.current) fitWorld()
    else clampCamera()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [world.rooms.length, world.width, world.height])

  // fly the camera to the focused room when it changes
  useEffect(() => {
    flyToRoom(officeRoomIndex)
  }, [officeRoomIndex])

  // selecting a department elsewhere brings its room into view
  useEffect(() => {
    if (!selectedDeptId) return
    const idx = world.roomByDept.get(selectedDeptId)
    if (idx === undefined) return
    if (idx !== officeRoomIndex) setOfficeRoom(idx)
    else flyToRoom(idx)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDeptId])

  // transient pulses → canvas effects
  useEffect(() => {
    return client.onPulse((e) => {
      const w = worldRef.current
      const from = w.byId.get(e.departmentId)
      if (!from) return
      const now = performance.now()
      const col = ACCENT_HEX[from.accent]
      const fx = fxRef.current
      if (e.kind === 'handoff' && e.toDepartmentId) {
        const to = w.byId.get(e.toDepartmentId)
        if (to) {
          // the worker physically gets up, walks the parcel over, returns
          actorsRef.current.handoff(from.id, { x: to.anchor.x, y: to.anchor.y })
          fx.push({ kind: 'ripple', x: to.anchor.x, y: to.anchor.y - 16, color: ACCENT_HEX[to.accent], start: now, dur: 700 })
        }
      } else if (e.kind === 'done') {
        fx.push({ kind: 'burst', x: from.anchor.x, y: from.anchor.y - 20, color: '#57d6bf', start: now, dur: 600 })
      } else if (e.kind === 'spend') {
        fx.push({ kind: 'float', x: from.anchor.x, y: from.anchor.y - 30, color: '#f0735f', text: '฿', start: now, dur: 900 })
      } else if (e.kind === 'autonomous') {
        fx.push({ kind: 'burst', x: from.anchor.x, y: from.anchor.y - 20, color: '#b3a4ee', start: now, dur: 600 })
      } else {
        fx.push({ kind: 'ripple', x: from.anchor.x, y: from.anchor.y - 16, color: col, start: now, dur: 700 })
      }
    })
  }, [])

  // chat messages → speech bubbles, and active meetings → characters gathering
  useEffect(() => {
    const MEETING_RECENT_MS = 60_000
    const FRESH_MS = 6_000
    const seen = new Map<string, string>() // threadId → last message id
    let primed = false

    const apply = () => {
      const s = client.getState()
      const w = worldRef.current
      const actors = actorsRef.current
      const now = performance.now()
      const serverNow = s.now || Date.now()

      // speech bubbles from freshly-arrived agent messages
      for (const [threadId, msgs] of Object.entries(s.threads)) {
        if (!msgs || !msgs.length) continue
        const last = msgs[msgs.length - 1]
        if (seen.get(threadId) === last.id) continue
        seen.set(threadId, last.id)
        if (!primed) continue // don't replay the backlog on first load
        if (last.pending || last.streaming || last.role === 'user') continue
        if (serverNow - last.ts > FRESH_MS) continue // only bubble what just happened
        const deptId = last.departmentId ?? deptIdFromThread(threadId as ThreadId)
        if (!deptId || !w.byId.has(deptId)) continue
        const text = (last.text || '').trim()
        if (text) actors.say(deptId, text.slice(0, 80), 'speech', 5500, now)
      }

      // meetings: gather the most-recently-active war room / meeting thread
      let active: { ids: string[]; roomIndex: number } | null = null
      let activeTs = 0
      for (const [threadId, msgs] of Object.entries(s.threads)) {
        if (!(threadId.startsWith('meet:') || threadId.startsWith('war:')) || !msgs?.length) continue
        const recent = msgs.filter((m) => serverNow - m.ts < MEETING_RECENT_MS)
        if (!recent.length) continue
        const ids = [...new Set(recent.map((m) => m.departmentId).filter((x): x is string => !!x && w.byId.has(x)))]
        const ts = recent[recent.length - 1].ts
        if (ids.length && ts > activeTs) {
          activeTs = ts
          active = { ids, roomIndex: w.roomByDept.get(ids[0]) ?? 0 }
        }
      }
      if (active) {
        const spot = w.rooms[active.roomIndex]?.meetingSpot
        if (spot) actors.meeting(active.ids, spot)
      } else {
        actors.endMeeting()
      }
    }

    apply()
    primed = true
    return client.subscribe(apply)
  }, [])

  // DEV: console playground (__atriumDev.seed/handoff/meeting/say/coffee)
  useEffect(() => {
    if (!import.meta.env.DEV) return
    return installDevOffice({ client, actors: actorsRef.current, getWorld: () => worldRef.current })
  }, [])

  // ambient life: now and then an idle worker strolls to the watercooler
  useEffect(() => {
    const id = window.setInterval(() => {
      if (frameRef.current.arrange || Math.random() < 0.55) return
      const actors = actorsRef.current
      const w = worldRef.current
      const idle = client.getState().departments.filter((d) => d.state === 'idle')
      const free = idle.filter((d) => {
        const a = actors.get(d.id)
        return a && !a.errand && !a.station
      })
      if (!free.length) return
      const d = free[Math.floor(Math.random() * free.length)]
      const spot = w.rooms[w.roomByDept.get(d.id) ?? 0]?.coolerSpot
      if (spot) actors.coffee(d.id, spot)
    }, 9000)
    return () => window.clearInterval(id)
  }, [])

  // external-script interop surface (parity with the old build)
  useEffect(() => {
    const api = {
      backgrounds: OFFICE_BACKGROUNDS,
      applyPlan: applyOfficePlan,
      moveDepartmentToRoom: moveDepartmentToOfficeRoom,
      setDepartmentPosition: setRoomOverride,
      setRoomBackground: setOfficeRoomBackground,
    }
    const onPlan = (ev: Event) => {
      const d = (ev as CustomEvent<OfficeLayoutPlan>).detail
      if (d && typeof d === 'object') applyOfficePlan(d)
    }
    const onRoom = (ev: Event) => {
      const d = (ev as CustomEvent<{ deptId?: string; roomIndex?: number }>).detail
      if (d?.deptId && typeof d.roomIndex === 'number') moveDepartmentToOfficeRoom(d.deptId, d.roomIndex)
    }
    const onPos = (ev: Event) => {
      const d = (ev as CustomEvent<{ deptId?: string; position?: { x: number; y: number } }>).detail
      if (d?.deptId && d.position) setRoomOverride(d.deptId, d.position)
    }
    const onBg = (ev: Event) => {
      const d = (ev as CustomEvent<{ roomIndex?: number; backgroundId?: OfficeBackgroundId }>).detail
      if (typeof d?.roomIndex === 'number' && d.backgroundId) setOfficeRoomBackground(d.roomIndex, d.backgroundId)
    }
    window.__atriumOfficeLayout = api
    window.addEventListener('atrium:office-plan', onPlan)
    window.addEventListener('atrium:office-department-room', onRoom)
    window.addEventListener('atrium:office-department-position', onPos)
    window.addEventListener('atrium:office-room-background', onBg)
    document.documentElement.dataset.atriumOfficeLayout = 'ready'
    document.documentElement.dataset.atriumOfficeBackgrounds = String(OFFICE_BACKGROUNDS.length)
    return () => {
      if (window.__atriumOfficeLayout === api) delete window.__atriumOfficeLayout
      window.removeEventListener('atrium:office-plan', onPlan)
      window.removeEventListener('atrium:office-department-room', onRoom)
      window.removeEventListener('atrium:office-department-position', onPos)
      window.removeEventListener('atrium:office-room-background', onBg)
      delete document.documentElement.dataset.atriumOfficeLayout
      delete document.documentElement.dataset.atriumOfficeBackgrounds
    }
  }, [applyOfficePlan, moveDepartmentToOfficeRoom, setOfficeRoomBackground, setRoomOverride])

  const focusedBg = officeRoomBackgrounds[String(officeRoomIndex)] ?? world.rooms[officeRoomIndex]?.backgroundId

  return (
    <>
      <div ref={hostRef} className="absolute inset-0">
        <canvas ref={canvasRef} className="block touch-none select-none" />
      </div>
      <OfficeControls
        rooms={world.rooms.map((r) => ({ index: r.index, count: r.count, isExec: r.isExec }))}
        focusedRoom={officeRoomIndex}
        focusedBg={focusedBg}
        onFocusRoom={setOfficeRoom}
        onAddRoom={() => addOfficeRoom(world.rooms.length)}
        onSetBackground={(bg) => setOfficeRoomBackground(officeRoomIndex, bg)}
        arrange={arrangeMode}
        onToggleArrange={() => toggleArrange()}
        onZoom={(dir) => {
          const c = canvasRef.current
          if (!c) return
          if (dir === 'fit') {
            userAdjustedRef.current = false
            fitWorld()
          } else {
            zoomAt(c.clientWidth / 2, c.clientHeight / 2, camRef.current.zoom * (dir === 'in' ? 1.25 : 0.8))
          }
        }}
      />
    </>
  )
}

/* ---------- overlay controls (DOM, floats over the canvas) ---------- */

const cluster = {
  background: 'rgba(26,22,16,0.82)',
  border: '1px solid var(--color-line-soft)',
  backdropFilter: 'blur(8px)',
} as const

function OfficeControls({
  rooms,
  focusedRoom,
  focusedBg,
  onFocusRoom,
  onAddRoom,
  onSetBackground,
  arrange,
  onToggleArrange,
  onZoom,
}: {
  rooms: { index: number; count: number; isExec: boolean }[]
  focusedRoom: number
  focusedBg: OfficeBackgroundId | undefined
  onFocusRoom: (index: number) => void
  onAddRoom: () => void
  onSetBackground: (bg: OfficeBackgroundId) => void
  arrange: boolean
  onToggleArrange: () => void
  onZoom: (dir: 'in' | 'out' | 'fit') => void
}) {
  const chip =
    'rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition-colors shrink-0'
  const idle = {
    background: 'rgba(19,16,9,0.42)',
    borderColor: 'var(--color-line-soft)',
    color: 'var(--color-cream-dim)',
  }
  return (
    <>
      {/* top-left: room navigator + background */}
      <div className="pointer-events-auto absolute top-3 left-3 z-10 flex max-w-[min(82vw,640px)] flex-col items-start gap-2">
        <div className="flex items-center gap-1.5 rounded-xl px-2 py-1.5" style={cluster}>
          <span className="mr-0.5 text-[10px] tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">ตึก</span>
          <div className="flex max-w-[min(60vw,460px)] items-center gap-1.5 overflow-x-auto">
            {rooms.map((r) => {
              const active = r.index === focusedRoom
              return (
                <button
                  key={r.index}
                  type="button"
                  onClick={() => onFocusRoom(r.index)}
                  className={chip}
                  style={active ? { background: '#f4a945', borderColor: '#f4a945', color: '#13100b' } : idle}
                  title={`${r.isExec ? 'ห้องผู้บริหาร' : `ห้อง ${r.index + 1}`}: ${r.count}/10 แผนก`}
                >
                  {r.isExec ? 'ผู้บริหาร' : `ห้อง ${r.index + 1}`}
                  <span className="ml-1 opacity-70">{r.count}/10</span>
                </button>
              )
            })}
            {!rooms.length && (
              <span className="px-1 text-[11px] text-[var(--color-cream-faint)]">ยังไม่มีแผนก</span>
            )}
          </div>
          <button
            type="button"
            onClick={onAddRoom}
            className={`${chip} flex items-center gap-1`}
            style={{ background: 'rgba(244,169,69,0.16)', borderColor: 'rgba(244,169,69,0.4)', color: '#ffc879' }}
            title="เพิ่มห้องใหม่ (ห้องว่าง — ลากแผนกเข้าไปได้)"
          >
            <span className="text-sm leading-none">＋</span>
            ห้อง
          </button>
        </div>

        <label className="flex items-center gap-2 rounded-xl px-2 py-1.5" style={cluster}>
          <span className="text-[10px] tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">พื้นหลัง</span>
          <select
            value={focusedBg}
            onChange={(e) => onSetBackground(e.target.value as OfficeBackgroundId)}
            className="h-7 rounded-lg border px-2 text-[11px] font-semibold outline-none"
            style={{ background: 'rgba(19,16,9,0.62)', borderColor: 'var(--color-line-soft)', color: 'var(--color-cream)' }}
          >
            {OFFICE_BACKGROUNDS.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>

        <div
          className="pointer-events-none rounded-lg px-2.5 py-1 text-[10px] tracking-[0.14em] text-[var(--color-cream-faint)] uppercase"
          style={{ background: 'rgba(19,16,9,0.5)', backdropFilter: 'blur(4px)' }}
        >
          {arrange
            ? 'ลากโต๊ะเพื่อย้าย · ลากข้ามห้องเพื่อเปลี่ยนห้อง · ลากพื้นห้องเพื่อย้ายทั้งห้อง'
            : 'ลาก/สกรอลล์เพื่อสำรวจ · คลิกแผนกเพื่อคุย'}
        </div>
      </div>

      {/* top-right: arrange toggle + zoom */}
      <div className="pointer-events-auto absolute top-3 right-3 z-10 flex flex-col items-end gap-2">
        <button
          type="button"
          onClick={onToggleArrange}
          title="จัดห้อง — ลากย้ายโต๊ะแผนก (ผู้บริหารตรึงอยู่กับที่)"
          className="flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-xs font-medium transition-colors"
          style={arrange ? { background: '#57d6bf', color: '#13100b', border: '1px solid #57d6bf' } : { ...cluster, color: 'var(--color-cream-dim)' }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20" />
          </svg>
          {arrange ? 'เสร็จ' : 'จัดห้อง'}
        </button>
        <div className="flex flex-col items-center gap-0.5 rounded-xl p-1" style={cluster}>
          <ZoomBtn label="ขยาย" onClick={() => onZoom('in')}>
            <span className="text-lg leading-none">+</span>
          </ZoomBtn>
          <ZoomBtn label="ย่อ" onClick={() => onZoom('out')}>
            <span className="text-lg leading-none">−</span>
          </ZoomBtn>
          <div className="my-0.5 h-px w-5" style={{ background: 'var(--color-line-soft)' }} />
          <ZoomBtn label="เห็นทั้งตึก" onClick={() => onZoom('fit')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4" />
            </svg>
          </ZoomBtn>
        </div>
      </div>
    </>
  )
}

function ZoomBtn({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-cream-dim)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-cream)]"
    >
      {children}
    </button>
  )
}
