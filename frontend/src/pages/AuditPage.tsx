import { Search, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { EmptyState } from '../components/common/EmptyState'
import { ErrorState } from '../components/common/ErrorState'
import { Loading } from '../components/common/Loading'
import { SourceCitation } from '../components/common/SourceCitation'
import { PageContainer } from '../components/layout/PageContainer'
import { useAppContext } from '../hooks/useAppContext'
import { auditService } from '../services/auditService'
import { AuditResponse } from '../types/api'

const CATEGORY_OPTIONS = [
  { value: 'all', label: 'All Events' },
  { value: 'correction', label: 'Corrections' },
  { value: 'preference', label: 'Preferences' },
  { value: 'forget', label: 'Forgotten Topics' },
  { value: 'security_filter', label: 'Security Filters' },
  { value: 'claim_update', label: 'Claim Updates' },
  { value: 'record', label: 'Work Records' },
]

export function AuditPage() {
  const { personId, currentPerson, memoryVersion } = useAppContext()
  const [dayFilter, setDayFilter] = useState<number | null>(null)
  const [category, setCategory] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [data, setData] = useState<AuditResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadAudit = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await auditService.getAudit(personId, {
        day: dayFilter,
        category,
        search,
      })
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit trail.')
    } finally {
      setLoading(false)
    }
  }, [personId, dayFilter, category, search])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadAudit()
    }, 150)
    return () => window.clearTimeout(timer)
  }, [loadAudit, memoryVersion])

  return (
    <PageContainer>
      {/* Top Filters & Day Coverage */}
      <section className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Activity, Security &amp; Audit Traceability
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Strictly authorized record, memory, and defense audit log for{' '}
              <span className="font-semibold text-slate-700 dark:text-slate-200">
                {currentPerson?.name || personId}
              </span>
              . Zero private or cross-organization records are ever exposed.
            </p>
          </div>

          {data && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {Object.entries(data.visibility_counts).map(([vis, cnt]) => (
                <span
                  key={vis}
                  className="inline-flex items-center gap-1 rounded-lg bg-slate-100 px-2.5 py-1 font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
                  {vis}: <strong>{cnt}</strong>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Ingested Day Selector Strip */}
        {data && (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3.5 dark:border-slate-800">
            <button
              type="button"
              onClick={() => setDayFilter(null)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium ${
                dayFilter === null
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'
              }`}
            >
              All Days ({data.current_day})
            </button>
            {data.ingested_days.map((d) => (
              <button
                key={d.day}
                type="button"
                onClick={() => setDayFilter(d.day)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium ${
                  dayFilter === d.day
                    ? 'bg-indigo-600 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'
                }`}
              >
                Day {d.day} ({d.visible_records} visible)
              </button>
            ))}
          </div>
        )}

        {/* Search + Category Filter */}
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap gap-1.5">
            {CATEGORY_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setCategory(opt.value)}
                className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                  category === opt.value
                    ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute top-2.5 left-3 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter records, people, IDs..."
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pr-3 pl-8 text-xs text-slate-800 outline-none focus:border-indigo-500 focus:bg-white dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            />
          </div>
        </div>
      </section>

      {loading && <Loading label="Filtering authorized audit log..." />}

      {!loading && error && <ErrorState message={error} onRetry={() => void loadAudit()} />}

      {!loading && !error && data && data.events.length === 0 && (
        <EmptyState
          title="No matching audit events"
          description="Try clearing the search box or switching to 'All Days' / 'All Events'."
        />
      )}

      {!loading && !error && data && data.events.length > 0 && (
        <section className="overflow-hidden rounded-xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-200 bg-slate-50/80 text-slate-500 dark:border-slate-800 dark:bg-slate-950/60">
                <tr>
                  <th className="px-4 py-3 font-semibold">Category</th>
                  <th className="px-4 py-3 font-semibold">Day / UTC</th>
                  <th className="px-4 py-3 font-semibold">Event &amp; Summary</th>
                  <th className="px-4 py-3 font-semibold">Actor</th>
                  <th className="px-4 py-3 font-semibold">ACL</th>
                  <th className="px-4 py-3 font-semibold">Sources</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                {data.events.map((ev) => (
                  <tr
                    key={ev.event_uid}
                    className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30"
                  >
                    <td className="px-4 py-3 whitespace-nowrap">
                      <span
                        className={`rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase ${
                          ev.category === 'security_filter' || ev.category === 'forget'
                            ? 'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300'
                            : ev.category === 'correction' || ev.category === 'preference'
                              ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300'
                              : ev.category === 'claim_update'
                                ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
                                : 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300'
                        }`}
                      >
                        {ev.category.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap font-mono text-[11px] text-slate-500">
                      D{ev.day}
                      {ev.at_utc ? <div className="text-[10px]">{ev.at_utc}</div> : null}
                    </td>
                    <td className="max-w-md px-4 py-3">
                      <p className="font-semibold text-slate-900 dark:text-slate-100">{ev.title}</p>
                      <p className="mt-0.5 text-slate-600 dark:text-slate-400">{ev.summary}</p>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-slate-700 dark:text-slate-300">
                      {ev.actor_name || ev.actor_id || '—'}
                      {ev.client_name ? (
                        <div className="text-[10px] text-slate-400">{ev.client_name}</div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        {ev.visibility}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {ev.sources.length > 0 ? (
                          ev.sources.map((s) => <SourceCitation key={s} recordId={s} />)
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </PageContainer>
  )
}
