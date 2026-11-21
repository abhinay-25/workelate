import { requestJson } from './api'
import { AskRequest, AskResponse } from '../types/api'

export const assistantService = {
  ask: (payload: AskRequest) =>
    requestJson<AskResponse>('/ask', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
