import { lazy, Suspense, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { TopBar } from './panels/TopBar'
import { LeftRail } from './panels/LeftRail'
import { OfficeFloor } from './office/OfficeFloor'
import { ActivityTicker } from './panels/ActivityTicker'
import { RightDock } from './panels/RightDock'
import { ApprovalsDrawer } from './panels/ApprovalsDrawer'
import { NotificationsDrawer } from './panels/NotificationsDrawer'
import { DecisionsDrawer } from './panels/DecisionsDrawer'
import { TaskBoard } from './panels/TaskBoard'
import { CreateDeptModal } from './panels/CreateDeptModal'
import { AssignTaskModal } from './panels/AssignTaskModal'
import { FinancePanel } from './panels/FinancePanel'
import { EditDeptModal } from './panels/EditDeptModal'
import { useUI } from './state/ui'
import type { MobileSection } from './state/ui'

const Console = lazy(() => import('./panels/console/Console').then((module) => ({ default: module.Console })))

const MOBILE_TABS: { id: MobileSection; label: string }[] = [
  { id: 'depts', label: 'แผนก' },
  { id: 'office', label: 'ออฟฟิศ' },
  { id: 'detail', label: 'แชท/งาน' },
]

export default function App() {
  // which single section shows on narrow screens; ignored on wide layouts
  const mobileSection = useUI((s) => s.mobileSection)
  const setMobileSection = useUI((s) => s.setMobileSection)
  const leftCollapsed = useUI((s) => s.leftCollapsed)
  const dockCollapsed = useUI((s) => s.dockCollapsed)
  const dockWidth = useUI((s) => s.dockWidth)
  const setDockWidth = useUI((s) => s.setDockWidth)

  // Desktop column widths come from these CSS vars; the mobile media query in
  // index.css sets its own single-column grid and ignores them.
  const mainStyle = {
    '--left-col': leftCollapsed ? '48px' : '296px',
    '--dock-col': dockCollapsed ? '48px' : `${dockWidth}px`,
  } as CSSProperties

  // Drag the divider on the dock's left edge to resize the chat column.
  const onResizeStart = (e: ReactPointerEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = useUI.getState().dockWidth
    const leftW = useUI.getState().leftCollapsed ? 48 : 296
    const maxW = Math.max(360, window.innerWidth - leftW - 360)
    const onMove = (ev: PointerEvent) => {
      const next = startW + (startX - ev.clientX) // drag left → wider dock
      setDockWidth(Math.min(maxW, Math.max(340, next)))
    }
    const onUp = () => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      useUI.getState().commitDockWidth()
    }
    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar />

      <main className="app-main grid min-h-0 flex-1" data-section={mobileSection} style={mainStyle}>
        <LeftRail />

        {/* office floor */}
        <section className="app-office relative min-h-0 overflow-hidden">
          <OfficeFloor />
          <ActivityTicker />
        </section>

        {/* context dock */}
        <aside
          className="app-dock relative flex min-h-0 flex-col"
          style={{ borderLeft: '1px solid var(--color-line-soft)' }}
        >
          {!dockCollapsed && (
            <div
              className="dock-resize"
              onPointerDown={onResizeStart}
              role="separator"
              aria-orientation="vertical"
              aria-label="ปรับความกว้างแผงแชท"
            />
          )}
          <RightDock />
        </aside>
      </main>

      {/* mobile section switcher — only visible under the responsive breakpoint */}
      <nav className="app-tabbar" aria-label="สลับมุมมอง">
        {MOBILE_TABS.map((tab) => {
          const active = mobileSection === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setMobileSection(tab.id)}
              aria-pressed={active}
              className="app-tabbar-btn"
              data-active={active}
            >
              {tab.label}
            </button>
          )
        })}
      </nav>

      {/* overlays */}
      <ApprovalsDrawer />
      <NotificationsDrawer />
      <DecisionsDrawer />
      <TaskBoard />
      <CreateDeptModal />
      <AssignTaskModal />
      <FinancePanel />
      <EditDeptModal />
      <Suspense fallback={null}>
        <Console />
      </Suspense>
    </div>
  )
}
