import { useState } from 'react'
import { client, useSelector, shallowArrayEqual } from '../state/useCompany'
import { useSpeedCatalog } from '../state/useCatalog'
import { useUI } from '../state/ui'
import { Field, Modal, Toggle, inputClass, withAlpha } from '../components/primitives'
import { ACCENT_HEX, ACCENT_LIST } from '../lib/visuals'
import {
  AI_PROVIDERS,
  MODELS,
  THINKING_EFFORTS,
  coerceThinkingEffort,
  defaultThinkingEffortForModel,
  defaultModelForProvider,
  isModelAvailableForProvider,
  modelEffortLabel,
  modelRouteLabel,
  modelsForProvider,
  providerRouteLabel,
  thinkingEffortsForModel,
  PROVIDER_LIST,
} from '../contract/models'
import type {
  AccentName,
  AiProviderId,
  Department,
  ModelId,
  ModelSpeed,
  ThinkingEffort,
} from '../contract/types'

const EMOJI_PICKS = ['🟣', '📈', '🤝', '🧪', '🚀', '🛠️', '📣', '📊', '🧠', '💡', '🗂️', '🎯']

export function EditDeptModal() {
  const editDeptId = useUI((s) => s.editDeptId)
  const close = useUI((s) => s.closeEditDept)
  // resolve the dept in render (not inside the selector) so the modal opens
  // immediately when editDeptId changes, independent of company-state ticks
  const departments = useSelector((s) => s.departments, shallowArrayEqual)
  const dept = editDeptId ? departments.find((d) => d.id === editDeptId) ?? null : null
  const open = editDeptId !== null && dept !== null

  return (
    <Modal open={open} onClose={close} width={620} title="แก้ไขแผนก">
      {/* key={dept.id} remounts the form so its fields re-seed cleanly per department */}
      {dept && <EditDeptForm key={dept.id} dept={dept} onClose={close} />}
    </Modal>
  )
}

function EditDeptForm({ dept, onClose }: { dept: Department; onClose: () => void }) {
  const [name, setName] = useState(dept.name)
  const [agentName, setAgentName] = useState(dept.agentName)
  const [role, setRole] = useState(dept.role)
  const [charter, setCharter] = useState(dept.charter)
  const [providerId, setProviderId] = useState<AiProviderId>(dept.providerId)
  const [model, setModel] = useState<ModelId>(dept.model)
  const [thinkingEffort, setThinkingEffort] = useState<ThinkingEffort>(dept.thinkingEffort)
  const [speed, setSpeed] = useState<ModelSpeed>(dept.speed ?? 'standard')
  const [accent, setAccent] = useState<AccentName>(dept.accent)
  const [emoji, setEmoji] = useState(dept.emoji)
  const [autonomy, setAutonomy] = useState(dept.autonomy)
  const [skills, setSkills] = useState(dept.skills.join(', '))
  const [tools, setTools] = useState(dept.tools.join(', '))

  const speedCatalog = useSpeedCatalog()
  const canSubmit = name.trim() !== '' && agentName.trim() !== '' && role.trim() !== ''
  const provider = AI_PROVIDERS[providerId]
  const modelInfo = MODELS[model]
  const availableModels = modelsForProvider(providerId)
  const availableThinkingEfforts = thinkingEffortsForModel(model)
  const fastSupported = speedCatalog.supportsFast(model)
  const fastMode = speedCatalog.speedModes.find((m) => m.id === 'fast')

  const submit = () => {
    if (!canSubmit) return
    client.editDepartment(dept.id, {
      name: name.trim(),
      agentName: agentName.trim(),
      role: role.trim(),
      charter: charter.trim(),
      providerId,
      model,
      thinkingEffort,
      speed: speedCatalog.coerceSpeed(model, speed),
      accent,
      emoji: emoji.trim() || dept.emoji,
      autonomy,
      skills: skills
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      tools: tools
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean),
    })
    onClose()
  }

  const remove = () => {
    if (!confirm(`ปิดแผนก${dept.name} ถาวร? งานที่ค้างจะถูกย้ายกลับไปรอคิว`)) return
    client.closeDepartment(dept.id)
    onClose()
  }

  const selectProvider = (nextProviderId: AiProviderId) => {
    const keepModel = isModelAvailableForProvider(model, nextProviderId)
    const nextModel = keepModel ? model : defaultModelForProvider(nextProviderId)
    setProviderId(nextProviderId)
    setModel(nextModel)
    setThinkingEffort((currentEffort) =>
      keepModel ? coerceThinkingEffort(nextModel, currentEffort) : defaultThinkingEffortForModel(nextModel),
    )
    setSpeed((currentSpeed) => speedCatalog.coerceSpeed(nextModel, currentSpeed))
  }

  const selectModel = (nextModel: ModelId) => {
    setModel(nextModel)
    setThinkingEffort((currentEffort) => coerceThinkingEffort(nextModel, currentEffort))
    setSpeed((currentSpeed) => speedCatalog.coerceSpeed(nextModel, currentSpeed))
  }

  return (
    <>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="ชื่อแผนก">
            <input
              className={inputClass}
              style={{ borderColor: 'var(--color-line-soft)' }}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="เช่น การเงิน"
            />
          </Field>
          <Field label="ชื่อเอเจนต์">
            <input
              className={inputClass}
              style={{ borderColor: 'var(--color-line-soft)' }}
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              placeholder="เช่น เฟิน"
            />
          </Field>
        </div>

        <Field label="บทบาทย่อ">
          <input
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder="เช่น ดูแลงบ · ออกใบเสนอราคา · รายงานการเงิน"
          />
        </Field>

        <Field label="ภารกิจ (ไม่บังคับ)">
          <textarea
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)', minHeight: 60, resize: 'none' }}
            value={charter}
            onChange={(e) => setCharter(e.target.value)}
            placeholder="อธิบายขอบเขตงานและเป้าหมายของแผนกนี้"
          />
        </Field>

        {/* provider */}
        <Field label="AI Provider" hint={`${providerRouteLabel(provider.id)} · ${provider.authTokenEnv} · ${provider.baseUrlEnv}=${provider.baseUrl}`}>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
            {PROVIDER_LIST.map((p) => {
              const on = providerId === p.id
              const route = providerRouteLabel(p.id)
              const accent = p.id === 'openai' || p.id === 'chatgpt_account' ? ACCENT_HEX.sky : p.id === 'claude_code' ? ACCENT_HEX.lavender : ACCENT_HEX.teal
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => selectProvider(p.id)}
                  className="min-h-[74px] rounded-xl border px-3 py-2 text-left transition-colors"
                  style={{
                    borderColor: on ? withAlpha(accent, 0.55) : 'var(--color-line-soft)',
                    background: on ? withAlpha(accent, 0.11) : 'transparent',
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[12px] font-medium text-[var(--color-cream)]">{p.label}</span>
                    <span
                      className="shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-medium"
                      style={{ borderColor: withAlpha(accent, 0.35), color: accent }}
                    >
                      {route}
                    </span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-[10px] leading-snug text-[var(--color-cream-faint)]">{p.purpose}</div>
                </button>
              )
            })}
          </div>
        </Field>

        {/* model */}
        <Field label="โมเดล" hint={`${modelInfo.blurb} · ${modelRouteLabel(model)} · Think ${modelEffortLabel(model)}`}>
          <div className="grid grid-cols-2 gap-2">
            {availableModels.map((m) => {
              const on = model === m.id
              const accent = m.tier === 'gpt' ? ACCENT_HEX.sky : ACCENT_HEX.amber
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => selectModel(m.id)}
                  className="rounded-xl border px-2 py-2 text-center transition-colors"
                  style={{
                    borderColor: on ? withAlpha(accent, 0.5) : 'var(--color-line-soft)',
                    background: on ? withAlpha(accent, 0.1) : 'transparent',
                  }}
                >
                  <div className="text-[12px] font-medium text-[var(--color-cream)]">{m.label}</div>
                  <div className="truncate text-[9px] text-[var(--color-cream-faint)]">{modelRouteLabel(m.id)}</div>
                  <div
                    className="text-[10px] tabular-nums text-[var(--color-cream-faint)]"
                    style={{ fontFamily: 'var(--font-mono)' }}
                  >
                    ${m.inputPerMTok}/${m.outputPerMTok}
                  </div>
                </button>
              )
            })}
          </div>
        </Field>

        {/* fast mode — Opus-only; availability comes from the live catalog and
            the backend re-validates on save */}
        <div
          className="flex items-center justify-between rounded-xl border px-3 py-2.5"
          style={{
            borderColor:
              fastSupported && speed === 'fast'
                ? withAlpha(ACCENT_HEX.honey, 0.45)
                : 'var(--color-line-soft)',
            opacity: fastSupported ? 1 : 0.55,
          }}
        >
          <div className="min-w-0 pr-2">
            <div className="text-[12px] font-medium text-[var(--color-cream)]">⚡ Fast Mode</div>
            <div className="text-[10px] text-[var(--color-cream-faint)]">
              {fastSupported
                ? fastMode?.blurb ?? 'ตอบเร็วขึ้นด้วย Claude Fast Mode'
                : modelInfo.tier === 'gpt'
                  ? 'GPT route ใช้ Standard speed เสมอ'
                  : 'รุ่นนี้ไม่รองรับ Fast Mode — เลือก Opus เพื่อเปิด'}
            </div>
          </div>
          <Toggle
            on={fastSupported && speed === 'fast'}
            onChange={(on) => {
              if (fastSupported) setSpeed(on ? 'fast' : 'standard')
            }}
            accent={ACCENT_HEX.honey}
          />
        </div>

        <Field label="ระดับ Think" hint={`${THINKING_EFFORTS[thinkingEffort].blurb} · ${THINKING_EFFORTS[thinkingEffort].apiShape}`}>
          <div className="grid grid-cols-3 gap-2">
            {availableThinkingEfforts.map((effort) => {
              const on = thinkingEffort === effort.id
              return (
                <button
                  key={effort.id}
                  type="button"
                  onClick={() => setThinkingEffort(effort.id)}
                  className="rounded-xl border px-2 py-2 text-center text-[11px] font-medium transition-colors"
                  style={{
                    borderColor: on ? withAlpha(ACCENT_HEX.sky, 0.55) : 'var(--color-line-soft)',
                    background: on ? withAlpha(ACCENT_HEX.sky, 0.12) : 'transparent',
                    color: on ? ACCENT_HEX.sky : 'var(--color-cream-dim)',
                  }}
                >
                  {effort.label}
                </button>
              )
            })}
          </div>
        </Field>

        {/* accent + emoji */}
        <div className="grid grid-cols-2 gap-3">
          <Field label="สีประจำแผนก">
            <div className="flex items-center gap-2 pt-1">
              {ACCENT_LIST.map((a) => (
                <button
                  key={a}
                  type="button"
                  onClick={() => setAccent(a)}
                  className="h-7 w-7 rounded-full transition-transform"
                  style={{
                    background: ACCENT_HEX[a],
                    outline: accent === a ? `2px solid ${ACCENT_HEX[a]}` : 'none',
                    outlineOffset: 2,
                    transform: accent === a ? 'scale(1.1)' : 'scale(1)',
                  }}
                  title={a}
                />
              ))}
            </div>
          </Field>
          <Field label="ไอคอน">
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {EMOJI_PICKS.map((e) => (
                <button
                  key={e}
                  type="button"
                  onClick={() => setEmoji(e)}
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-sm transition-colors"
                  style={{
                    background: emoji === e ? withAlpha(ACCENT_HEX[accent], 0.18) : 'var(--color-surface-3)',
                    border: emoji === e ? `1px solid ${withAlpha(ACCENT_HEX[accent], 0.5)}` : '1px solid transparent',
                  }}
                >
                  {e}
                </button>
              ))}
            </div>
          </Field>
        </div>

        <Field label="ทักษะ (คั่นด้วยจุลภาค, ไม่บังคับ)">
          <input
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            value={skills}
            onChange={(e) => setSkills(e.target.value)}
            placeholder="เช่น บัญชี, งบประมาณ, พยากรณ์"
          />
        </Field>

        <Field label="เครื่องมือ (คั่นด้วยจุลภาค, ไม่บังคับ)">
          <input
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            value={tools}
            onChange={(e) => setTools(e.target.value)}
            placeholder="เช่น ค้นเว็บ, สเปรดชีต, ปฏิทิน"
          />
        </Field>

        {/* autonomy */}
        <div
          className="flex items-center justify-between rounded-xl border px-3 py-2.5"
          style={{ borderColor: 'var(--color-line-soft)' }}
        >
          <div>
            <div className="text-[12px] font-medium text-[var(--color-cream)]">ทำงานเองได้</div>
            <div className="text-[10px] text-[var(--color-cream-faint)]">ริเริ่มงานตามวัตถุประสงค์</div>
          </div>
          <Toggle on={autonomy} onChange={setAutonomy} />
        </div>
      </div>

      <div className="mt-5 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={remove}
          className="rounded-xl border px-4 py-2 text-sm font-medium transition-colors"
          style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.45), color: ACCENT_HEX.coral }}
        >
          ปิดแผนก
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border px-4 py-2 text-sm text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
            style={{ borderColor: 'var(--color-line-soft)' }}
          >
            ยกเลิก
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-xl px-4 py-2 text-sm font-semibold transition-opacity disabled:opacity-40"
            style={{ background: ACCENT_HEX.amber, color: '#1a1610' }}
          >
            บันทึก
          </button>
        </div>
      </div>
    </>
  )
}
