import {
  CalendarClock,
  Check,
  Copy,
  HelpCircle,
  RefreshCw,
  Sparkles,
  Users,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { EmptyState } from '../components/common/EmptyState'
import { ErrorState } from '../components/common/ErrorState'
import { Loading } from '../components/common/Loading'
import { SourceCitation } from '../components/common/SourceCitation'
import { PageContainer } from '../components/layout/PageContainer'
import { useAppContext } from '../hooks/useAppContext'
import { coreApi } from '../services/api'
import { briefService } from '../services/briefService'
import { BriefResponse, ClientInfo, EnrichedBriefItem } from '../types/api'

function formatBriefAsMarkdown(b: EnrichedBriefItem): string {
  const lines: string[] = [
    `# Meeting Brief: ${b.meeting_title || b.meeting_id}`,
    `Client: ${b.client_name || 'Internal'} | Start: ${b.start_utc || 'N/A'} | Status: ${b.status}`,
    '',
  ]
  if (b.changed.length > 0) {
    lines.push('## What Changed')
    for (const c of b.changed) {
      lines.push(`- ${c.text} [sources: ${c.sources.join(', ')}]`)
    }
    lines.push('')
  }
  if (b.promises.length > 0) {
    lines.push('## Open Commitments')
    for (const p of b.promises) {
      lines.push(`- ${p.owner_name || p.owner}: ${p.what} (due ${p.due}) [${p.sources.join(', ')}]`)
    }
    lines.push('')
  }
  if (b.question_to_ask) {
    lines.push(`## Suggested Question to Ask`)
    lines.push(`${b.question_to_ask.text} [${b.question_to_ask.sources.join(', ')}]`)
  }
  return lines.join('\n')
}

export function BriefsPage() {
  const { personId, selectedDate, setSelectedDate, availableDates, currentPerson, memoryVersion } =
    useAppContext()
  const [clientId, setClientId] = useState<string>('')
  const [clients, setClients] = useState<ClientInfo[]>([])
  const [result, setResult] = useState<BriefResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [copiedMid, setCopiedMid] = useState<string | null>(null)

  useEffect(() => {
    coreApi
      .getOverview(personId, selectedDate)
      .then((ov) => setClients(ov.clients))
      .catch(() => setClients([]))
  }, [personId, selectedDate])

  const fetchBriefs = useCallback(
    async (regenerate = false) => {
      setLoading(true)
      setError(null)
      try {
        const res = await briefService.generate({
          person_id: personId,
          date: selectedDate,
          client_id: clientId || null,
          regenerate,
        })
        setResult(res)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to generate meeting brief.')
      } finally {
        setLoading(false)
      }
    },
    [personId, selectedDate, clientId]
  )

  useEffect(() => {
    void fetchBriefs(false)
  }, [fetchBriefs, memoryVersion])

  const handleCopy = (b: EnrichedBriefItem) => {
    void navigator.clipboard.writeText(formatBriefAsMarkdown(b))
    setCopiedMid(b.meeting_id)
    window.setTimeout(() => setCopiedMid(null), 1800)
  }

  return (
    <PageContainer>
      {/* Controls Bar */}
      <section className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Pre-Meeting Intelligence Briefs
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Per-attendee watermarked briefs honoring learned preferences, corrections, and org ACLs
              for <span className="font-semibold text-slate-700 dark:text-slate-200">{currentPerson?.name || personId}</span>.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <select
              aria-label="Filter by client"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              <option value="">All Clients</option>
              {clients.map((c) => (
                <option key={c.client_id} value={c.client_id}>
                  {c.name}
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={() => void fetchBriefs(false)}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-60"
            >
              <Sparkles className="h-3.5 w-3.5" />
              Generate Brief
            </button>

            <button
              type="button"
              onClick={() => void fetchBriefs(true)}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              Force Regenerate
            </button>
          </div>
        </div>

        {/* Quick Date Pills */}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3.5 dark:border-slate-800">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Select Date:
          </span>
          {availableDates.map((d) => {
            const active = selectedDate === d.date
            return (
              <button
                key={d.day}
                type="button"
                onClick={() => setSelectedDate(d.date)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                  active
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                }`}
              >
                Day {d.day} ({d.date})
              </button>
            )
          })}
        </div>
      </section>

      {loading && <Loading label="Synthesizing grounded pre-meeting briefs..." />}

      {!loading && error && (
        <ErrorState message={error} onRetry={() => void fetchBriefs(false)} />
      )}

      {!loading && !error && result && result.briefs.length === 0 && (
        <EmptyState
          icon={<CalendarClock className="h-5 w-5" />}
          title={`No meetings found for ${currentPerson?.name || personId} on ${selectedDate}`}
          description="Try selecting another date pill above (e.g., Day 4 or Day 5) or clearing the client filter. For example, Rohan Kapoor (p03) or Neha Joshi (p12) have client meetings on Day 5 (2026-11-06)."
        />
      )}

      {!loading && !error && result && result.briefs.length > 0 && (
        <div className="space-y-5">
          {result.briefs.map((b) => (
            <article
              key={b.meeting_id}
              className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
            >
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-4 dark:border-slate-800">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase ${
                        b.status === 'brief'
                          ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/70 dark:text-indigo-300'
                          : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
                      }`}
                    >
                      {b.status === 'brief' ? 'Brief Ready' : 'Nothing New'}
                    </span>
                    {b.client_name && (
                      <span className="rounded-md bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700 dark:bg-sky-950/60 dark:text-sky-300">
                        {b.client_name}
                      </span>
                    )}
                    <span className="font-mono text-xs text-slate-400">{b.meeting_id}</span>
                  </div>
                  <h3 className="mt-2 text-lg font-bold text-slate-900 dark:text-white">
                    {b.meeting_title || b.meeting_id}
                  </h3>
                  <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
                    {b.start_utc && <span className="font-mono">Start: {b.start_utc}</span>}
                    {b.attendees.length > 0 && (
                      <span className="inline-flex items-center gap-1">
                        <Users className="h-3.5 w-3.5" />
                        Attendees: {b.attendees.join(', ')}
                      </span>
                    )}
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => handleCopy(b)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  {copiedMid === b.meeting_id ? (
                    <>
                      <Check className="h-3.5 w-3.5 text-emerald-600" />
                      Copied
                    </>
                  ) : (
                    <>
                      <Copy className="h-3.5 w-3.5" />
                      Copy Markdown
                    </>
                  )}
                </button>
              </div>

              {b.status === 'nothing_new' ? (
                <div className="mt-4 rounded-xl bg-slate-50 p-4 text-sm text-slate-600 dark:bg-slate-950/50 dark:text-slate-300">
                  No new substantive client updates, blockers, ownership handovers, or open items
                  since {currentPerson?.first_name || personId}&apos;s previous meeting watermark.
                </div>
              ) : (
                <div className="mt-5 space-y-5">
                  {b.question_to_ask && (
                    <div className="rounded-xl border border-indigo-200/80 bg-indigo-50/70 p-4 dark:border-indigo-900/60 dark:bg-indigo-950/40">
                      <div className="flex items-start gap-2.5">
                        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600 dark:text-indigo-400" />
                        <div className="flex-1">
                          <p className="text-[11px] font-semibold uppercase tracking-wider text-indigo-700 dark:text-indigo-300">
                            Suggested Question To Ask
                          </p>
                          <p className="mt-1 text-sm font-medium text-slate-900 dark:text-slate-100">
                            {b.question_to_ask.text}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {b.question_to_ask.sources.map((s) => (
                              <SourceCitation key={s} recordId={s} />
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                    {/* Changed Items */}
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                        What Changed ({b.changed.length})
                      </h4>
                      <ul className="mt-2.5 space-y-2.5">
                        {b.changed.map((c, idx) => (
                          <li
                            key={idx}
                            className="rounded-xl border border-slate-200/80 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-950/50"
                          >
                            <p className="text-sm text-slate-800 dark:text-slate-200">{c.text}</p>
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {c.sources.map((s) => (
                                <SourceCitation key={s} recordId={s} />
                              ))}
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>

                    {/* Promises + Open These */}
                    <div className="space-y-5">
                      <div>
                        <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                          Open Commitments ({b.promises.length})
                        </h4>
                        {b.promises.length === 0 ? (
                          <p className="mt-2 text-xs text-slate-500">No pending promises for this client.</p>
                        ) : (
                          <ul className="mt-2.5 space-y-2.5">
                            {b.promises.map((p, idx) => (
                              <li
                                key={idx}
                                className="rounded-xl border border-amber-200/70 bg-amber-50/50 p-3 dark:border-amber-900/50 dark:bg-amber-950/20"
                              >
                                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                  {p.owner_name || p.owner}: {p.what}
                                </p>
                                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                                  Due: <span className="font-mono font-medium">{p.due}</span>
                                </p>
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                  {p.sources.map((s) => (
                                    <SourceCitation key={s} recordId={s} />
                                  ))}
                                </div>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>

                      {b.open_these.length > 0 && (
                        <div>
                          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                            Records to Open
                          </h4>
                          <ul className="mt-2.5 space-y-2">
                            {b.open_these.map((o, idx) => (
                              <li
                                key={idx}
                                className="flex items-start gap-2 rounded-xl border border-slate-200/80 bg-white p-2.5 text-xs dark:border-slate-800 dark:bg-slate-950/50"
                              >
                                <SourceCitation recordId={o.record} className="shrink-0" />
                                <span className="text-slate-600 dark:text-slate-300">{o.why}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              <footer className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3 text-[11px] text-slate-400 dark:border-slate-800">
                <span>
                  Mode:{' '}
                  {b.usage.input_tokens > 0 || b.usage.output_tokens > 0
                    ? 'Hybrid (Deterministic + LLM Fallback)'
                    : 'Pure Deterministic ($0.00)'}
                </span>
                <span className="font-mono">
                  {b.usage.ms} ms &bull; {b.usage.input_tokens} in / {b.usage.output_tokens} out &bull; $
                  {b.usage.cost_usd.toFixed(6)}
                </span>
              </footer>
            </article>
          ))}
        </div>
      )}
    </PageContainer>
  )
}
