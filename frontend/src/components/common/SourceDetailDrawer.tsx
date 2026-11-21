import { ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useAppContext } from '../../hooks/useAppContext'
import { ApiError, coreApi } from '../../services/api'
import { RecordDetailResponse } from '../../types/api'

export function SourceDetailDrawer() {
  const { activeRecordId, closeRecordDrawer, personId } = useAppContext()
  const [record, setRecord] = useState<RecordDetailResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ status: number; message: string } | null>(null)

  useEffect(() => {
    if (!activeRecordId) {
      setRecord(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    setRecord(null)
    coreApi
      .getRecordDetail(activeRecordId, personId)
      .then((data) => {
        if (!cancelled) setRecord(data)
      })
      .catch((err) => {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setError({ status: err.status, message: err.detail })
          } else {
            setError({ status: 500, message: 'Failed to load record details.' })
          }
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [activeRecordId, personId])

  if (!activeRecordId) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/50 backdrop-blur-sm">
      <div className="flex h-full w-full max-w-lg flex-col border-l border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wider text-indigo-600 dark:text-indigo-400">
              Authorized Source Inspector
            </p>
            <h3 className="font-mono text-base font-bold text-slate-900 dark:text-slate-100">
              {activeRecordId}
            </h3>
          </div>
          <button
            type="button"
            onClick={closeRecordDrawer}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
              Verifying backend ACL &amp; loading record...
            </div>
          )}

          {error && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 dark:border-rose-900/60 dark:bg-rose-950/40">
              <div className="flex items-center gap-2 text-rose-700 dark:text-rose-300">
                <ShieldAlert className="h-5 w-5" />
                <span className="text-sm font-semibold">
                  {error.status === 403 ? 'Backend Access Denied (HTTP 403)' : `Unavailable (HTTP ${error.status})`}
                </span>
              </div>
              <p className="mt-2 text-sm text-rose-700 dark:text-rose-200">{error.message}</p>
            </div>
          )}

          {record && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium uppercase text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  {record.type}
                </span>
                <span
                  className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium ${
                    record.visibility === 'team'
                      ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
                      : 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300'
                  }`}
                >
                  <ShieldCheck className="h-3.5 w-3.5" />
                  visibility: {record.visibility}
                </span>
                <span className="rounded-md bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300">
                  Day {record.day}
                </span>
                {record.has_injection_span && (
                  <span className="rounded-md bg-rose-100 px-2.5 py-1 text-xs font-medium text-rose-700 dark:bg-rose-950/70 dark:text-rose-300">
                    Prompt-Injection Sanitized
                  </span>
                )}
              </div>

              <dl className="grid grid-cols-2 gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-3.5 text-xs dark:border-slate-800 dark:bg-slate-950/50">
                <div>
                  <dt className="text-slate-500">Author</dt>
                  <dd className="mt-0.5 font-medium text-slate-900 dark:text-slate-100">
                    {record.author_name} ({record.author_id})
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Timestamp (UTC)</dt>
                  <dd className="mt-0.5 font-mono text-slate-900 dark:text-slate-100">{record.at_utc}</dd>
                </div>
                {record.client_name && (
                  <div>
                    <dt className="text-slate-500">Client</dt>
                    <dd className="mt-0.5 font-medium text-slate-900 dark:text-slate-100">
                      {record.client_name}
                    </dd>
                  </div>
                )}
                {record.channel && (
                  <div>
                    <dt className="text-slate-500">Channel</dt>
                    <dd className="mt-0.5 font-mono text-slate-900 dark:text-slate-100">{record.channel}</dd>
                  </div>
                )}
              </dl>

              {(record.title || record.subject) && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Subject / Title</p>
                  <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-slate-100">
                    {record.title || record.subject}
                  </p>
                </div>
              )}

              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Sanitized Content</p>
                <div className="mt-1.5 whitespace-pre-wrap rounded-xl border border-slate-200 bg-white p-4 text-sm leading-relaxed text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200">
                  {record.sanitized_body}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
