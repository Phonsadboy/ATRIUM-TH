import { useSelector, client, shallowArrayEqual } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Drawer, Pill, withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'
import { money, relTime } from '../lib/format'
import { Icon, type IconName } from '../components/Icon'
import type { Approval, ApprovalKind } from '../contract/types'
import { isExecutiveReviewApproval, isHumanApproval } from '../lib/approvals'

const KIND_LABEL: Record<ApprovalKind, string> = {
  external_action: 'การกระทำภายนอก',
  publish: 'เผยแพร่สู่สาธารณะ',
  destructive_action: 'การลบ/ย้อนกลับไม่ได้',
  task_close: 'ขอปิดงาน',
}

const KIND_ICON: Record<ApprovalKind, IconName> = {
  external_action: 'send',
  publish: 'approve',
  destructive_action: 'alert',
  task_close: 'approve',
}

export function ApprovalsDrawer() {
  const open = useUI((s) => s.approvalsOpen)
  const toggle = useUI((s) => s.toggleApprovals)
  const approvals = useSelector((s) => s.approvals, shallowArrayEqual)
  const now = useSelector((s) => s.now)
  const departments = useSelector(
    (s) => s.departments.map((d) => ({ id: d.id, emoji: d.emoji, name: d.name })),
    (a, b) => a.length === b.length && a.every((x, i) => x.id === b[i].id),
  )

  const executivePending = approvals.filter((a) => a.status === 'pending' && isExecutiveReviewApproval(a))
  const pending = approvals.filter((a) => a.status === 'pending' && isHumanApproval(a))
  const resolved = approvals.filter((a) => a.status !== 'pending' && isHumanApproval(a))
  const deptName = (id: string | null) => {
    const d = id ? departments.find((x) => x.id === id) : undefined
    return d ? `${d.emoji} ฝ่าย${d.name}` : 'บริษัท'
  }

  return (
    <Drawer
      open={open}
      onClose={() => toggle(false)}
      title={
        <span className="flex items-center gap-2">
          อนุมัติ
          {pending.length > 0 && (
            <span
              className="inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-bold text-black"
              style={{ background: ACCENT_HEX.coral }}
            >
              {pending.length}
            </span>
          )}
        </span>
      }
    >
      {/* Owner Mode policy is fixed at Full Auto; the drawer only shows pending gates that still exist for audit/recovery. */}
      <div
        className="mb-3 rounded-2xl border p-2.5"
        style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.28), background: withAlpha(ACCENT_HEX.coral, 0.08) }}
      >
        <div className="mb-1.5 text-[10px] font-semibold tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">
          Owner Mode
        </div>
        <div className="flex items-center gap-2">
          <Pill color={ACCENT_HEX.coral}>Full Auto เสมอ</Pill>
          <span className="text-[10px] leading-relaxed text-[var(--color-cream-faint)]">
            เครื่องมือทำงานทันทีและเก็บ audit/checkpoint แทนการสลับโหมดอนุมัติ
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-x-hidden overflow-y-auto pr-1">
        {pending.length === 0 && resolved.length === 0 && (
          <div className="mt-12 text-center text-sm text-[var(--color-cream-faint)]">
            {executivePending.length > 0
              ? `ผู้บริหาร AI กำลังตรวจงานปิด ${executivePending.length} รายการในคิว`
              : 'ยังไม่มีคำขออนุมัติที่ต้องให้คุณตัดสิน'}
          </div>
        )}

        {pending.length > 0 && (
          <div className="space-y-2.5">
            {pending.map((a) => (
              <Card key={a.id} a={a} now={now} deptName={deptName(a.departmentId)} pending />
            ))}
          </div>
        )}

        {resolved.length > 0 && (
          <div>
            <div className="mb-2 text-[10px] font-semibold tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">
              ตัดสินใจแล้ว
            </div>
            <div className="space-y-2.5">
              {resolved.map((a) => (
                <Card key={a.id} a={a} now={now} deptName={deptName(a.departmentId)} />
              ))}
            </div>
          </div>
        )}
      </div>

      {pending.length > 0 && (
        <div className="mt-3 text-center text-[11px] text-[var(--color-cream-faint)]">
          แสดงเฉพาะ human gate จริง งานปิดของแผนกให้ผู้บริหาร AI ตรวจในคิว
        </div>
      )}
    </Drawer>
  )
}

function Card({
  a,
  now,
  deptName,
  pending,
}: {
  a: Approval
  now: number
  deptName: string
  pending?: boolean
}) {
  const approved = a.status === 'approved'
  const destructive = a.kind === 'destructive_action'
  const tint = destructive ? ACCENT_HEX.coral : ACCENT_HEX.amber
  return (
    <div
      className="rounded-2xl border p-3"
      style={{
        borderColor: pending ? withAlpha(ACCENT_HEX.coral, 0.3) : 'var(--color-line-soft)',
        background: 'var(--color-surface-2)',
        opacity: pending ? 1 : 0.7,
      }}
    >
      <div className="flex items-start gap-2.5">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl"
          style={{ background: withAlpha(tint, 0.14) }}
        >
          <Icon name={KIND_ICON[a.kind] ?? 'alert'} size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{a.title}</div>
          <div className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
            {a.detail}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <Pill color={destructive ? ACCENT_HEX.coral : undefined}>{KIND_LABEL[a.kind] ?? a.kind}</Pill>
            <span className="text-[11px] text-[var(--color-cream-faint)]">{deptName}</span>
            {a.costUsd != null && a.costUsd > 0 && (
              <span
                className="inline-flex items-center gap-1 text-[11px] tabular-nums"
                style={{ fontFamily: 'var(--font-mono)', color: ACCENT_HEX.amber }}
              >
                <Icon name="budget" size={11} />
                {money(a.costUsd)}
              </span>
            )}
            <span className="ml-auto text-[10px] text-[var(--color-cream-faint)]">
              {relTime(a.ts, now)}ที่แล้ว
            </span>
          </div>
        </div>
      </div>

      {pending ? (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => client.resolveApproval(a.id, 'approved')}
            className="flex-1 rounded-xl py-2 text-xs font-semibold transition-opacity hover:opacity-90"
            style={{ background: withAlpha(ACCENT_HEX.teal, 0.18), color: ACCENT_HEX.teal, border: `1px solid ${withAlpha(ACCENT_HEX.teal, 0.4)}` }}
          >
            อนุมัติ
          </button>
          <button
            type="button"
            onClick={() => client.resolveApproval(a.id, 'rejected')}
            className="flex-1 rounded-xl py-2 text-xs font-semibold transition-opacity hover:opacity-90"
            style={{ background: withAlpha(ACCENT_HEX.coral, 0.14), color: ACCENT_HEX.coral, border: `1px solid ${withAlpha(ACCENT_HEX.coral, 0.35)}` }}
          >
            ปฏิเสธ
          </button>
        </div>
      ) : (
        <div className="mt-2.5">
          <Pill color={approved ? ACCENT_HEX.teal : ACCENT_HEX.coral}>
            <Icon name={approved ? 'approve' : 'reject'} size={12} />
            {approved ? 'อนุมัติแล้ว' : 'ปฏิเสธแล้ว'}
          </Pill>
        </div>
      )}
    </div>
  )
}
