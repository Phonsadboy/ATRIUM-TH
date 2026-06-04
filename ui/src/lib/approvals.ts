import type { Approval } from '../contract/types'

export function isExecutiveReviewApproval(approval: Approval): boolean {
  const action = approval.action as { action?: unknown } | null | undefined
  return approval.kind === 'task_close' || action?.action === 'close_task'
}

export function isHumanApproval(approval: Approval): boolean {
  return !isExecutiveReviewApproval(approval)
}
