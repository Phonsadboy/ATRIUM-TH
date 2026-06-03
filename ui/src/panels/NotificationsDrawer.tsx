import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Drawer, Dot, withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'
import { relTime } from '../lib/format'
import { Icon, type IconName } from '../components/Icon'
import type { NotifType, Severity } from '../contract/types'

const TYPE_LABEL: Record<NotifType, string> = {
  approval: 'รออนุมัติ',
  budget: 'งบประมาณ',
  blocked: 'ติดขัด',
  task_done: 'งานเสร็จ',
  digest: 'สรุป',
  crash: 'ระบบล่ม',
  knowledge_debt: 'หนี้ความรู้',
  security: 'ความปลอดภัย',
}

const TYPE_ICON: Record<NotifType, IconName> = {
  approval: 'approve',
  budget: 'budget',
  blocked: 'pause',
  task_done: 'tasks',
  digest: 'chat',
  crash: 'alert',
  knowledge_debt: 'memory',
  security: 'alert',
}

const SEVERITY_HEX: Record<Severity, string> = {
  info: ACCENT_HEX.sky,
  good: ACCENT_HEX.teal,
  warn: ACCENT_HEX.amber,
  alert: ACCENT_HEX.coral,
}

export function NotificationsDrawer() {
  const open = useUI((s) => s.notificationsOpen)
  const toggle = useUI((s) => s.toggleNotifications)
  // subscribe to state bumps so the list re-reads client.getNotifications()
  const now = useSelector((s) => s.now)
  const items = client.getNotifications()
  const unread = items.filter((n) => !n.read)

  return (
    <Drawer
      open={open}
      onClose={() => toggle(false)}
      title={
        <span className="flex items-center gap-2">
          แจ้งเตือน
          {unread.length > 0 && (
            <span
              className="inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-bold text-black"
              style={{ background: ACCENT_HEX.coral }}
            >
              {unread.length}
            </span>
          )}
        </span>
      }
    >
      {unread.length > 0 && (
        <div className="mb-2 flex justify-end">
          <button
            type="button"
            onClick={() => client.markAllNotificationsRead()}
            className="text-[11px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
          >
            อ่านทั้งหมด
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-2 overflow-x-hidden overflow-y-auto pr-1">
        {items.length === 0 && (
          <div className="mt-12 text-center text-sm text-[var(--color-cream-faint)]">
            ยังไม่มีแจ้งเตือน
          </div>
        )}
        {items.map((n) => {
          const hex = SEVERITY_HEX[n.severity] ?? ACCENT_HEX.sky
          return (
            <button
              key={n.id}
              type="button"
              onClick={() => !n.read && client.markNotificationRead(n.id)}
              className="flex w-full items-start gap-2.5 rounded-2xl border p-3 text-left transition-colors"
              style={{
                borderColor: n.read ? 'var(--color-line-soft)' : withAlpha(hex, 0.3),
                background: n.read ? 'transparent' : withAlpha(hex, 0.06),
                opacity: n.read ? 0.7 : 1,
                cursor: n.read ? 'default' : 'pointer',
              }}
            >
              <span
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl"
                style={{ background: withAlpha(hex, 0.14) }}
              >
                <Icon name={TYPE_ICON[n.type] ?? 'alert'} size={16} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-medium text-[var(--color-cream)]">
                    {n.title}
                  </span>
                  {!n.read && <Dot color={hex} size={7} />}
                </div>
                <div className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
                  {n.body}
                </div>
                <div className="mt-1.5 flex items-center gap-2 text-[10px] text-[var(--color-cream-faint)]">
                  <span style={{ color: withAlpha(hex, 0.9) }}>{TYPE_LABEL[n.type] ?? n.type}</span>
                  <span>·</span>
                  <span>{relTime(n.ts, now)}ที่แล้ว</span>
                </div>
              </div>
            </button>
          )
        })}
      </div>
    </Drawer>
  )
}
