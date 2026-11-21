import { HealthResponse, OverviewResponse, PeopleResponse, RecordDetailResponse } from '../types/api'

const RAW_BASE = import.meta.env.VITE_API_BASE_URL as string | undefined
export const API_BASE = (RAW_BASE && RAW_BASE.trim() ? RAW_BASE.trim() : '/api').replace(/\/+$/, '')

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    ...(init?.headers as Record<string, string> | undefined),
  }

  let res: Response
  try {
    res = await fetch(url, { ...init, headers })
  } catch {
    throw new ApiError(0, 'Unable to reach backend API server. Verify Uvicorn is running on port 8000.')
  }

  let payload: unknown = null
  const contentType = res.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    try {
      payload = await res.json()
    } catch {
      payload = null
    }
  } else {
    const txt = await res.text()
    payload = txt ? { detail: txt } : null
  }

  if (!res.ok) {
    const detail =
      (payload && typeof payload === 'object' && 'detail' in payload && typeof (payload as { detail: unknown }).detail === 'string'
        ? (payload as { detail: string }).detail
        : `Request failed with HTTP ${res.status}`)
    throw new ApiError(res.status, detail)
  }

  return payload as T
}

export const coreApi = {
  getHealth: () => requestJson<HealthResponse>('/health'),
  getPeople: () => requestJson<PeopleResponse>('/people'),
  getOverview: (personId: string, date?: string) => {
    const qs = new URLSearchParams({ person_id: personId })
    if (date) qs.set('date', date)
    return requestJson<OverviewResponse>(`/overview?${qs.toString()}`)
  },
  getRecordDetail: (recordId: string, personId: string) => {
    const qs = new URLSearchParams({ person_id: personId })
    return requestJson<RecordDetailResponse>(`/records/${encodeURIComponent(recordId)}?${qs.toString()}`)
  },
}
