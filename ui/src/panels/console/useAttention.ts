// "Needs your attention" aggregator. Pulls the handful of things that actually
// require an owner decision (approvals, project sign-offs, live war rooms, tool
// approvals, proposed org plans) plus a knowledge-health warning, from the live
// snapshot + a few console endpoints. Every fetch is best-effort: one failing
// source never blanks the others. Drives both the nav badges and the Overview.
import { useCallback, useEffect, useState } from 'react'
import { client, useSelector } from '../../state/useCompany'
import { isHumanApproval } from '../../lib/approvals'
import type {
  Approval,
  KnowledgeDebtDepartment,
  OrgPlan,
  Project,
  WarRoom,
} from '../../contract/types'

export type BadgeKey = 'projectReviews' | 'warRooms' | 'toolApprovals' | 'orgPlans' | 'debt'

export interface Attention {
  /** owner-facing approvals still pending (from the live snapshot) */
  approvals: Approval[]
  /** projects waiting for your final sign-off */
  projectReviews: Project[]
  /** war rooms currently open */
  warRooms: WarRoom[]
  /** guarded tool runs awaiting approval */
  toolApprovals: Approval[]
  /** executive org-structure plans proposed for you */
  orgPlans: OrgPlan[]
  /** departments whose knowledge health is slipping */
  debt: KnowledgeDebtDepartment[]
  /** per-section nav badge counts */
  badges: Record<BadgeKey, number>
  /** total decisions waiting (excludes the soft debt warning) */
  decisions: number
  loading: boolean
  reload: () => void
}

const DEBT_THRESHOLD = 0.6

export function useAttention(enabled: boolean): Attention {
  // approvals ride the live snapshot, so they stay current without polling
  const approvals = useSelector(
    (s) => s.approvals.filter((a) => a.status === 'pending' && isHumanApproval(a)),
    (a, b) => a.length === b.length && a.every((x, i) => x.id === b[i]?.id),
  )

  const [fetched, setFetched] = useState<{
    projectReviews: Project[]
    warRooms: WarRoom[]
    toolApprovals: Approval[]
    orgPlans: OrgPlan[]
    debt: KnowledgeDebtDepartment[]
  }>({ projectReviews: [], warRooms: [], toolApprovals: [], orgPlans: [], debt: [] })
  const [loading, setLoading] = useState(false)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!enabled) return
    let alive = true
    void Promise.resolve()
      .then(() => {
        if (!alive) return null
        setLoading(true)
        return Promise.allSettled([
          client.listProjects({ limit: 200 }),
          client.listWarRooms({ status: 'active', limit: 100 }),
          client.listToolApprovals({ status: 'pending', limit: 100 }),
          client.listOrgPlans({ status: 'proposed', limit: 50 }),
          client.getKnowledgeDebt(),
        ])
      })
      .then((results) => {
        if (!alive || !results) return
        const [projects, wars, tools, plans, debt] = results
        setFetched({
          projectReviews:
            projects.status === 'fulfilled'
              ? projects.value.filter((p) => p.reviewStatus === 'pending_user')
              : [],
          warRooms: wars.status === 'fulfilled' ? wars.value : [],
          toolApprovals: tools.status === 'fulfilled' ? tools.value.filter(isHumanApproval) : [],
          orgPlans: plans.status === 'fulfilled' ? plans.value : [],
          debt:
            debt.status === 'fulfilled'
              ? debt.value.departments.filter((d) => d.health < DEBT_THRESHOLD)
              : [],
        })
        setLoading(false)
      })
      .catch(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [enabled, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  const badges: Record<BadgeKey, number> = {
    projectReviews: fetched.projectReviews.length,
    warRooms: fetched.warRooms.length,
    toolApprovals: fetched.toolApprovals.length,
    orgPlans: fetched.orgPlans.length,
    debt: fetched.debt.length,
  }
  const decisions =
    approvals.length +
    badges.projectReviews +
    badges.toolApprovals +
    badges.orgPlans

  return { approvals, ...fetched, badges, decisions, loading, reload }
}
