import { requestJson } from './api'
import { BriefRequest, BriefResponse } from '../types/api'

export const briefService = {
  generate: (payload: BriefRequest) =>
    requestJson<BriefResponse>('/brief', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
