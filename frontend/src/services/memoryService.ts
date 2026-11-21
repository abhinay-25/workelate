import { requestJson } from './api'
import { MemoryCorrectResponse, MemoryForgetResponse, MemoryResponse } from '../types/api'

export const memoryService = {
  getMemory: (personId: string, date?: string) => {
    const qs = new URLSearchParams({ person_id: personId })
    if (date) qs.set('date', date)
    return requestJson<MemoryResponse>(`/memory?${qs.toString()}`)
  },
  submitCorrection: (personId: string, words: string, meetingId?: string | null) =>
    requestJson<MemoryCorrectResponse>('/memory/correct', {
      method: 'POST',
      body: JSON.stringify({
        person_id: personId,
        words,
        meeting_id: meetingId || null,
      }),
    }),
  forget: (personId: string, what: string) =>
    requestJson<MemoryForgetResponse>('/memory/forget', {
      method: 'POST',
      body: JSON.stringify({ person_id: personId, what }),
    }),
}
