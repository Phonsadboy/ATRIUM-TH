import { useSelector } from '../state/useCompany'
import { Icon } from '../components/Icon'
import { withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'

export function ModeSwitch() {
  const mode = useSelector((s) => s.permissionPolicy?.mode ?? 'full_auto')
  const fullAuto = mode === 'full_auto'
  const color = fullAuto ? ACCENT_HEX.coral : ACCENT_HEX.amber

  return (
    <span
      title={fullAuto ? 'สิทธิ์กลางของ AI ทำงานแบบ Full Auto เสมอ' : 'กำลังปรับสิทธิ์กลางเป็น Full Auto'}
      className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-medium"
      style={{
        borderColor: withAlpha(color, 0.4),
        color,
        background: withAlpha(color, 0.1),
      }}
    >
      <Icon name="autonomy" size={13} />
      {fullAuto ? 'Auto' : 'ปรับ Auto'}
    </span>
  )
}
