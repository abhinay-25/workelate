import { requestJson } from './api'
import { AuditResponse, UsageResponse } from '../types/api'

export const auditService = {
  getAudit: (personId: string, opts?: { day?: number | null; category?: string; search?: string }) => {
    const qs = new URLSearchParams({ person_id: personId })
    if (opts?.day) qs.set('day', String(opts.day))
    if (opts?.category && opts.category !== 'all') qs.set('category', opts.category)
    if (opts?.search && opts.search.trim()) qs.set('search', opts.search.trim())
    return requestJson<AuditResponse>(`/audit?${qs.toString()}`)
  },
}

export const usageService = {
  getUsage: () => requestJson<UsageResponse>('/usage'),
}
