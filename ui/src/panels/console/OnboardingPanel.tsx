import { useState } from 'react'
import { client } from '../../state/useCompany'
import { Field, Pill, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import type { OrgPlan, OrgPlanDepartment, OrgPlanStatus, ProviderEnvField, ProviderEnvUpdate } from '../../contract/types'
import { PROVIDER_LIST, defaultModelForProvider, providerRouteLabel } from '../../contract/models'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync, useNow } from './shared'

const STATUS: Record<OrgPlanStatus, { label: string; hex: string }> = {
  proposed: { label: 'รออนุมัติ', hex: ACCENT_HEX.amber },
  approved: { label: 'อนุมัติแล้ว', hex: ACCENT_HEX.teal },
  rejected: { label: 'ตีกลับ', hex: ACCENT_HEX.coral },
  applied: { label: 'สร้างแล้ว', hex: ACCENT_HEX.sky },
}

export function OnboardingPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listOrgPlans({ limit: 100 }), [])
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const plans = data ?? []

  return (
    <Section
      title="ตั้งบริษัท"
      hint="ผังองค์กรที่ผู้บริหารเสนอ ก่อนอนุมัติสร้างแผนกจริง"
      actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ เสนอผังองค์กร'}</PrimaryBtn>}
    >
      <ProviderEnvSettings />
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : plans.length === 0 && !error ? (
        <Empty text="ยังไม่มีผังองค์กรที่เสนอ" />
      ) : (
        <div className="space-y-2.5">
          {plans.map((plan) => (
            <PlanRow
              key={plan.id}
              plan={plan}
              open={openId === plan.id}
              onToggle={() => setOpenId(openId === plan.id ? null : plan.id)}
              onChanged={reload}
            />
          ))}
        </div>
      )}
    </Section>
  )
}

function sourceLabel(source: string): string {
  if (source === 'dotenv') return '.env'
  if (source === 'process') return 'process env'
  if (source === 'default') return 'default'
  return 'empty'
}

function providerGroupColor(id: string): string {
  if (id === 'openai' || id === 'audio_transcription') return ACCENT_HEX.sky
  if (id === 'chatgpt_account') return ACCENT_HEX.teal
  if (id === 'claude_code') return ACCENT_HEX.lavender
  if (id === 'image_generation') return ACCENT_HEX.honey
  return ACCENT_HEX.amber
}

function fieldInputType(field: ProviderEnvField): string {
  if (field.kind === 'number') return 'number'
  return 'text'
}

function confirmProviderEnvImpact(fields: ProviderEnvField[], action: string): boolean {
  const impacts = fields
    .map((field) => {
      const processNote = field.source === 'process'
        ? ' process env ปัจจุบันจะถูก overlay ให้เห็นผลทันที แต่ถ้า launch config เดิมยังอยู่ ค่านี้อาจกลับมาหลัง restart'
        : ''
      return field.impact ? `${field.key}: ${field.impact}${processNote}` : processNote ? `${field.key}:${processNote}` : ''
    })
    .filter(Boolean)
  if (!impacts.length) return true
  return window.confirm(`${action}\n\n${impacts.join('\n')}`)
}

function ProviderEnvSettings() {
  const { data, loading, error, reload } = useAsync(() => client.getProviderEnvSettings(), [])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const setDraft = (key: string, value: string) => {
    setDrafts((items) => ({ ...items, [key]: value }))
    setMessage(null)
  }

  const save = async () => {
    if (!data || busy) return
    const updates: ProviderEnvUpdate[] = []
    const changedFields: ProviderEnvField[] = []
    for (const group of data.groups) {
      for (const field of group.fields) {
        if (!Object.prototype.hasOwnProperty.call(drafts, field.key)) continue
        const value = drafts[field.key] ?? ''
        const current = field.value ?? ''
        if (value === current) continue
        updates.push({ key: field.key, value })
        changedFields.push(field)
      }
    }
    if (updates.length === 0) {
      setMessage('ไม่มีค่าที่เปลี่ยน')
      return
    }
    if (!confirmProviderEnvImpact(changedFields, 'ยืนยันบันทึก provider .env และ apply กับ runtime ตอนนี้?')) return
    setBusy(true)
    try {
      await client.updateProviderEnvSettings(updates)
      setDrafts({})
      setMessage('บันทึกแล้ว')
      reload()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const clearField = async (field: ProviderEnvField) => {
    if (busy) return
    if (!confirmProviderEnvImpact([field], `ยืนยันล้างค่า ${field.key} จาก .env และ runtime ตอนนี้?`)) return
    setBusy(true)
    setMessage(null)
    try {
      await client.updateProviderEnvSettings([{ key: field.key, unset: true }])
      setDrafts((items) => {
        const next = { ...items }
        delete next[field.key]
        return next
      })
      setMessage('ล้างค่าแล้ว')
      reload()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-4 rounded-xl border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-[var(--color-cream)]">Provider .env</div>
          <div className="mt-0.5 truncate text-[10px] text-[var(--color-cream-faint)]" style={{ fontFamily: 'var(--font-mono)' }}>
            {data?.envPath ?? 'system/.env'}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data?.processOverrides.length ? <Pill color={ACCENT_HEX.coral}>{data.processOverrides.length} process override</Pill> : null}
          <GhostBtn onClick={reload} tint={ACCENT_HEX.sky}>รีเฟรช</GhostBtn>
          <PrimaryBtn onClick={save} disabled={busy || !Object.keys(drafts).length}>{busy ? 'กำลังบันทึก' : 'บันทึก .env'}</PrimaryBtn>
        </div>
      </div>

      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : data ? (
        <div className="grid gap-2 lg:grid-cols-2">
          {data.groups.map((group) => {
            const color = providerGroupColor(group.id)
            return (
              <div
                key={group.id}
                className="rounded-lg border p-2.5"
                style={{ borderColor: withAlpha(color, 0.28), background: withAlpha(color, 0.045) }}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[12px] font-semibold text-[var(--color-cream)]">{group.label}</div>
                    {group.credentialId && (
                      <div className="truncate text-[10px] text-[var(--color-cream-faint)]" style={{ fontFamily: 'var(--font-mono)' }}>
                        {group.credentialId}
                      </div>
                    )}
                  </div>
                  <Pill color={color}>{group.subsystemId ? 'subsystem' : 'provider'}</Pill>
                </div>
                <div className="space-y-2">
                  {group.fields.map((field) => {
                    const value = Object.prototype.hasOwnProperty.call(drafts, field.key)
                      ? drafts[field.key]
                      : field.value ?? ''
                    const placeholder = field.kind === 'secret' && field.configured
                      ? field.maskedValue || 'configured'
                      : field.placeholder
                    return (
                      <div key={field.key} className="grid gap-1">
                        <div className="flex items-center justify-between gap-2">
                          <div className="min-w-0 text-[10px] text-[var(--color-cream-faint)]" style={{ fontFamily: 'var(--font-mono)' }}>
                            <span className="truncate">{field.key}</span>
                            {field.sourceKey && field.sourceKey !== field.key ? <span className="ml-1">via {field.sourceKey}</span> : null}
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            <Pill color={field.configured ? ACCENT_HEX.teal : ACCENT_HEX.coral}>{field.configured ? 'set' : 'empty'}</Pill>
                            <span className="text-[10px] text-[var(--color-cream-faint)]">{sourceLabel(field.source)}</span>
                          </div>
                        </div>
                        <Field
                          label={field.label}
                          hint={
                            field.impact || (field.restartRecommended ? 'restart recommended' : undefined)
                          }
                        >
                          <div className="flex gap-2">
                            {field.kind === 'boolean' ? (
                              <select
                                className={inputClass}
                                style={{ borderColor: 'var(--color-line-soft)' }}
                                value={value}
                                onChange={(e) => setDraft(field.key, e.target.value)}
                              >
                                <option value="">default</option>
                                <option value="true">true</option>
                                <option value="false">false</option>
                                <option value="1">1</option>
                                <option value="0">0</option>
                              </select>
                            ) : (
                              <input
                                className={inputClass}
                                style={{ borderColor: 'var(--color-line-soft)' }}
                                type={fieldInputType(field)}
                                value={value}
                                onChange={(e) => setDraft(field.key, e.target.value)}
                                placeholder={placeholder}
                              />
                            )}
                            <button
                              type="button"
                              onClick={() => clearField(field)}
                              disabled={busy || !field.configured}
                              className="shrink-0 rounded-xl border px-2.5 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-45"
                              style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.32), color: ACCENT_HEX.coral }}
                            >
                              ล้าง
                            </button>
                          </div>
                        </Field>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      ) : null}
      {message && <div className="mt-2 text-[11px] text-[var(--color-cream-faint)]">{message}</div>}
    </div>
  )
}

function PlanRow({
  plan,
  open,
  onToggle,
  onChanged,
}: {
  plan: OrgPlan
  open: boolean
  onToggle: () => void
  onChanged: () => void
}) {
  const now = useNow()
  const [editing, setEditing] = useState(false)
  const st = STATUS[plan.status]
  const resolve = (decision: 'approved' | 'rejected') => {
    void client.resolveOrgPlan(plan.id, { decision, resolvedBy: 'user' }).then(onChanged).catch(() => undefined)
  }

  return (
    <Row accent={st.hex}>
      <button type="button" onClick={onToggle} className="block w-full text-left">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{plan.objective}</div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="text-[10px] tabular-nums text-[var(--color-cream-faint)]">{plan.departments.length} ฝ่าย</span>
            <Pill color={st.hex}>{st.label}</Pill>
          </div>
        </div>
        {plan.interviewSummary && (
          <div className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
            {plan.interviewSummary}
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[var(--color-cream-faint)]">
          <span>{plan.departments.map((d) => d.name).join(' · ')}</span>
          <span className="ml-auto">{relTime(plan.updatedAt, now)}ที่แล้ว</span>
        </div>
      </button>

      {open && (
        <div className="mt-3 border-t pt-3" style={{ borderColor: 'var(--color-line-soft)' }}>
          {editing ? (
            <EditForm plan={plan} onCancel={() => setEditing(false)} onDone={() => { setEditing(false); onChanged() }} />
          ) : (
            <>
              <div className="space-y-2">
                {plan.departments.map((dept) => (
                  <div
                    key={dept.id ?? dept.name}
                    className="rounded-xl border px-3 py-2"
                    style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0 text-[12px] font-semibold text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{dept.name}</div>
                      {dept.id && <span className="shrink-0 text-[10px] text-[var(--color-cream-faint)]">{dept.id}</span>}
                    </div>
                    <div className="mt-1 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{dept.role}</div>
                    {dept.tools && dept.tools.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {dept.tools.slice(0, 6).map((tool) => <Pill key={tool} color={ACCENT_HEX.sky}>{tool}</Pill>)}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {plan.appliedDepartmentIds.length > 0 && (
                <div className="mt-2 text-[11px] text-[var(--color-cream-faint)]">
                  สร้างแล้ว: <span className="text-[var(--color-cream-dim)]">{plan.appliedDepartmentIds.join(' · ')}</span>
                </div>
              )}

              {plan.status === 'proposed' && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <GhostBtn onClick={() => setEditing(true)} tint={ACCENT_HEX.sky}>ปรับผังองค์กร</GhostBtn>
                  <button
                    type="button"
                    onClick={() => resolve('approved')}
                    className="min-w-[180px] flex-1 rounded-xl py-2 text-xs font-semibold"
                    style={{ background: withAlpha(ACCENT_HEX.teal, 0.18), color: ACCENT_HEX.teal, border: `1px solid ${withAlpha(ACCENT_HEX.teal, 0.4)}` }}
                  >
                    อนุมัติและสร้างแผนก
                  </button>
                  <button
                    type="button"
                    onClick={() => resolve('rejected')}
                    className="min-w-[120px] flex-1 rounded-xl py-2 text-xs font-semibold"
                    style={{ background: withAlpha(ACCENT_HEX.coral, 0.14), color: ACCENT_HEX.coral, border: `1px solid ${withAlpha(ACCENT_HEX.coral, 0.35)}` }}
                  >
                    ตีกลับ
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </Row>
  )
}

const EMPTY_DEPT: OrgPlanDepartment = {
  id: '',
  name: '',
  role: '',
  charter: '',
  agentName: '',
  providerId: 'claude_code',
  tools: [],
  skills: [],
}

function cleanDepartments(departments: OrgPlanDepartment[]): OrgPlanDepartment[] {
  return departments
    .map((dept) => ({
      ...dept,
      id: dept.id?.trim() || null,
      name: dept.name.trim(),
      role: dept.role.trim(),
      charter: dept.charter?.trim() || null,
      agentName: dept.agentName?.trim() || null,
      providerId: dept.providerId,
      tools: (dept.tools ?? []).filter(Boolean),
      skills: (dept.skills ?? []).filter(Boolean),
    }))
    .filter((dept) => dept.name && dept.role)
}

function DepartmentEditor({
  dept,
  idx,
  onPatch,
  onRemove,
  showId = false,
}: {
  dept: OrgPlanDepartment
  idx: number
  onPatch: (idx: number, patch: Partial<OrgPlanDepartment>) => void
  onRemove: (idx: number) => void
  showId?: boolean
}) {
  return (
    <div className="rounded-xl border p-2.5" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}>
      <div className="grid gap-2 md:grid-cols-2">
        <Field label="ชื่อฝ่าย">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={dept.name} onChange={(e) => onPatch(idx, { name: e.target.value })} />
        </Field>
        <Field label="Agent">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={dept.agentName ?? ''} onChange={(e) => onPatch(idx, { agentName: e.target.value })} />
        </Field>
      </div>
      <div className="mt-2">
        <Field label="Provider">
          <select
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            value={dept.providerId}
            onChange={(e) => {
              const providerId = e.target.value as OrgPlanDepartment['providerId']
              onPatch(idx, { providerId, model: defaultModelForProvider(providerId) })
            }}
          >
            {PROVIDER_LIST.map((provider) => (
              <option key={provider.id} value={provider.id}>
                {provider.label} · {providerRouteLabel(provider.id)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {showId && (
        <div className="mt-2">
          <Field label="รหัสฝ่าย">
            <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={dept.id ?? ''} onChange={(e) => onPatch(idx, { id: e.target.value })} placeholder="เช่น dept_strategy" />
          </Field>
        </div>
      )}
      <div className="mt-2">
        <Field label="บทบาท">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 48, resize: 'none' }} value={dept.role} onChange={(e) => onPatch(idx, { role: e.target.value })} />
        </Field>
      </div>
      <div className="mt-2 flex justify-between">
        <button type="button" onClick={() => onRemove(idx)} className="text-[11px]" style={{ color: ACCENT_HEX.coral }}>ลบฝ่ายนี้</button>
      </div>
    </div>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const [objective, setObjective] = useState('')
  const [summary, setSummary] = useState('')
  const [departments, setDepartments] = useState<OrgPlanDepartment[]>([
    { ...EMPTY_DEPT, name: 'Strategy', role: 'วางแผน เป้าหมาย และลำดับความสำคัญ', agentName: 'Strategy Lead' },
    { ...EMPTY_DEPT, name: 'Operations', role: 'แตกงาน ประสานงาน และติดตาม execution', agentName: 'Ops Lead' },
  ])
  const [busy, setBusy] = useState(false)

  const updateDept = (idx: number, patch: Partial<OrgPlanDepartment>) => {
    setDepartments((items) => items.map((item, i) => (i === idx ? { ...item, ...patch } : item)))
  }
  const removeDept = (idx: number) => setDepartments((items) => items.filter((_, i) => i !== idx))
  const addDept = () => setDepartments((items) => [...items, { ...EMPTY_DEPT }])

  const submit = () => {
    const clean = cleanDepartments(departments)
    if (!objective.trim() || clean.length === 0 || busy) return
    setBusy(true)
    void client
      .createOrgPlan({
        objective: objective.trim(),
        interviewSummary: summary.trim(),
        departments: clean,
        createdBy: 'executive',
        requireApproval: true,
      })
      .then(onDone)
      .catch(() => setBusy(false))
  }

  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="โจทย์บริษัท">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="เช่น สร้างบริษัท AI ดูแลโปรเจกต์เปิดตัวสินค้า" />
        </Field>
        <Field label="สรุปสัมภาษณ์">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 64, resize: 'none' }} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="สิ่งที่ผู้บริหารได้จากการถามโจทย์" />
        </Field>

        <div className="space-y-2">
          {departments.map((dept, idx) => (
            <DepartmentEditor key={idx} dept={dept} idx={idx} onPatch={updateDept} onRemove={removeDept} />
          ))}
        </div>
      </div>
      <div className="mt-3 flex justify-between gap-2">
        <GhostBtn onClick={addDept} tint={ACCENT_HEX.sky}>+ เพิ่มฝ่าย</GhostBtn>
        <div className="flex gap-2">
          <GhostBtn onClick={onDone}>ยกเลิก</GhostBtn>
          <PrimaryBtn onClick={submit} disabled={!objective.trim() || busy}>เสนอผังองค์กร</PrimaryBtn>
        </div>
      </div>
    </div>
  )
}

function EditForm({ plan, onCancel, onDone }: { plan: OrgPlan; onCancel: () => void; onDone: () => void }) {
  const [objective, setObjective] = useState(plan.objective)
  const [summary, setSummary] = useState(plan.interviewSummary)
  const [departments, setDepartments] = useState<OrgPlanDepartment[]>(plan.departments.map((dept) => ({ ...dept })))
  const [busy, setBusy] = useState(false)

  const updateDept = (idx: number, patch: Partial<OrgPlanDepartment>) => {
    setDepartments((items) => items.map((item, i) => (i === idx ? { ...item, ...patch } : item)))
  }
  const removeDept = (idx: number) => setDepartments((items) => items.filter((_, i) => i !== idx))
  const addDept = () => setDepartments((items) => [...items, { ...EMPTY_DEPT }])

  const submit = () => {
    const clean = cleanDepartments(departments)
    if (!objective.trim() || clean.length === 0 || busy) return
    setBusy(true)
    void client
      .updateOrgPlan(plan.id, {
        objective: objective.trim(),
        interviewSummary: summary.trim(),
        departments: clean,
        updatedBy: 'user',
      })
      .then(onDone)
      .catch(() => setBusy(false))
  }

  return (
    <div className="rounded-xl border p-3" style={{ borderColor: withAlpha(ACCENT_HEX.sky, 0.35), background: withAlpha(ACCENT_HEX.sky, 0.06) }}>
      <div className="space-y-3">
        <Field label="โจทย์บริษัท">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={objective} onChange={(e) => setObjective(e.target.value)} />
        </Field>
        <Field label="สรุปสัมภาษณ์ / เหตุผลที่ปรับ">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 64, resize: 'none' }} value={summary} onChange={(e) => setSummary(e.target.value)} />
        </Field>
        <div className="space-y-2">
          {departments.map((dept, idx) => (
            <DepartmentEditor key={idx} dept={dept} idx={idx} onPatch={updateDept} onRemove={removeDept} showId />
          ))}
        </div>
      </div>
      <div className="mt-3 flex justify-between gap-2">
        <GhostBtn onClick={addDept} tint={ACCENT_HEX.sky}>+ เพิ่มฝ่าย</GhostBtn>
        <div className="flex gap-2">
          <GhostBtn onClick={onCancel}>ยกเลิก</GhostBtn>
          <PrimaryBtn onClick={submit} disabled={!objective.trim() || busy}>บันทึกผังองค์กร</PrimaryBtn>
        </div>
      </div>
    </div>
  )
}
