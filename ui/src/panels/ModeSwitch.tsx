import { useSelector } from '../state/useCompany'
import { Icon } from '../components/Icon'
import { withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'

export function ModeSwitch() {
  const mode = useSelector((s) => s.permissionPolicy?.mode ?? 'full_auto')
  const fullAuto = mode === 'full_auto'
  const color = fullAuto ? ACCENT_HEX.coral : ACCENT_HEX.amber

  return (
    <div
      title="สิทธิ์กลางของ AI ทำงานแบบ Full Auto เสมอ"
      className="flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium"
      style={{
        borderColor: withAlpha(color, 0.4),
        color,
        background: withAlpha(color, 0.1),
      }}
    >
      <Icon name="autonomy" size={14} />
      <span className="hidden text-[var(--color-cream-faint)] lg:inline">สิทธิ์กลาง:</span>
      {fullAuto ? 'Full Auto' : 'กำลังปรับเป็น Full Auto'}
    </div>
  )
}
