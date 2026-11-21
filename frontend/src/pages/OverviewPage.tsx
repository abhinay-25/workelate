import {
  AlertCircle,
  ArrowRight,
  Brain,
  CalendarClock,
  CheckCircle2,
  Clock,
  Layers,
  MessageSquareText,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { EmptyState } from '../components/common/EmptyState'
import { ErrorState } from '../components/common/ErrorState'
import { Loading } from '../components/common/Loading'
import { SourceCitation } from '../components/common/SourceCitation'
import { PageContainer } from '../components/layout/PageContainer'
import { useAppContext } from '../hooks/useAppContext'
import { coreApi } from '../services/api'
import { OverviewResponse } from '../types/api'

export function OverviewPage() {
  const { personId, selectedDate, memoryVersion } = useAppContext()
  const navigate = useNavigate()
  const [data, setData] = useState<OverviewResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadOverview = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await coreApi.getOverview(personId, selectedDate)
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard overview.')
    } finally {
      setLoading(false)
    }
  }, [personId, selectedDate])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview, memoryVersion])

  if (loading) {
    return (
      <PageContainer>
        <Loading label="Loading personalized workspace overview..." />
      </PageContainer>
    )
  }

  if (error || !data) {
    return (
      <PageContainer>
        <ErrorState message={error || 'Unable to load overview.'} onRetry={() => void loadOverview()} />
      </PageContainer>
    )
  }

  const { person, metrics } = data

  return (
    <PageContainer>
      {/* Welcome Banner */}
      <section className="rounded-2xl border border-indigo-200/70 bg-gradient-to-br from-indigo-600 via-indigo-700 to-slate-900 p-6 text-white shadow-md">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div>
            <div className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1 text-[11px] font-medium backdrop-blur-sm">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />
              {person.org_name} ({person.org_id}) &bull; Day {data.current_day} Active
            </div>
            <h2 className="mt-3 text-2xl font-bold tracking-tight">
              Welcome back, {person.name}
            </h2>
            <p className="mt-1 text-sm text-indigo-100">
              {person.role}
              {person.team ? ` • ${person.team} team` : ''} &bull; Timezone: {person.zone} &bull; Viewing{' '}
              <span className="font-mono font-semibold text-white">{data.selected_date}</span>
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => navigate('/briefs')}
              className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-xs font-semibold text-indigo-700 shadow-sm transition hover:bg-indigo-50"
            >
              <CalendarClock className="h-4 w-4" />
              Generate Brief
            </button>
            <button
              type="button"
              onClick={() => navigate('/assistant')}
              className="inline-flex items-center gap-2 rounded-xl border border-white/25 bg-white/10 px-4 py-2.5 text-xs font-semibold text-white backdrop-blur-sm transition hover:bg-white/20"
            >
              <MessageSquareText className="h-4 w-4" />
              Ask Assistant
            </button>
          </div>
        </div>
      </section>

      {/* Summary Metric Cards */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Active Tasks</span>
            <CheckCircle2 className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">{metrics.active_tasks}</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {metrics.blocked_tasks > 0 ? (
              <span className="font-medium text-rose-600 dark:text-rose-400">
                {metrics.blocked_tasks} currently blocked
              </span>
            ) : (
              'All visible tasks unblocked'
            )}
          </p>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
              Pending Commitments
            </span>
            <Clock className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">
            {metrics.pending_commitments}
          </p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Open promises visible to {person.first_name}
          </p>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
              Tracked Claims &amp; Facts
            </span>
            <Brain className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">{metrics.tracked_claims}</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Authoritative winning memory state
          </p>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
              Meetings on {data.selected_date}
            </span>
            <Layers className="h-4 w-4 text-sky-600 dark:text-sky-400" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">
            {metrics.meetings_on_date}
          </p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {metrics.visible_records} authorized workspace records
          </p>
        </div>
      </section>

      {/* Meetings on Selected Date */}
      <section className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
              Scheduled Meetings on {data.selected_date}
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Meetings attended by {person.name} eligible for pre-meeting briefing
            </p>
          </div>
          <button
            type="button"
            onClick={() => navigate('/briefs')}
            className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
          >
            Open Briefs
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </div>

        {data.meetings_on_date.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="No meetings scheduled on this date"
              description="Try switching the selected date in the header (e.g., 2026-11-05 or 2026-11-06) or switch persona to view their calendar."
            />
          </div>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
            {data.meetings_on_date.map((m) => (
              <div
                key={m.meeting_id}
                className="flex items-center justify-between rounded-xl border border-slate-200/80 bg-slate-50/70 p-4 dark:border-slate-800 dark:bg-slate-950/60"
              >
                <div className="min-w-0 pr-3">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                      {m.title}
                    </span>
                    {m.has_cached_brief && (
                      <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                        Cached
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {m.client_name || 'Internal'} &bull;{' '}
                    <span className="font-mono">{m.start_utc}</span> &bull; {m.attendees.length}{' '}
                    attendees
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => navigate('/briefs')}
                  className="shrink-0 rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 dark:border-indigo-800 dark:bg-slate-900 dark:text-indigo-300 dark:hover:bg-indigo-950"
                >
                  View Brief
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Main Grid: Recent Activity + Upcoming Deadlines */}
      <section className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Recent Authorized Activity */}
        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm lg:col-span-7 dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                Recent Authorized Updates
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Click any citation badge to inspect the sanitized source record
              </p>
            </div>
            <button
              type="button"
              onClick={() => navigate('/audit')}
              className="text-xs font-semibold text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
            >
              Full Audit &rarr;
            </button>
          </div>

          <div className="mt-4 space-y-3">
            {data.recent_activity.length === 0 ? (
              <EmptyState
                title="No recent non-boilerplate updates"
                description="All noise and unauthorized records are filtered automatically."
              />
            ) : (
              data.recent_activity.map((item) => (
                <div
                  key={item.record_id}
                  className="rounded-xl border border-slate-200/70 bg-slate-50/50 p-3.5 transition hover:border-slate-300 dark:border-slate-800 dark:bg-slate-950/40 dark:hover:border-slate-700"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <SourceCitation recordId={item.record_id} />
                      <span className="rounded bg-slate-200/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        {item.type}
                      </span>
                      {item.client_name && (
                        <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 dark:bg-sky-950/60 dark:text-sky-300">
                          {item.client_name}
                        </span>
                      )}
                      {item.has_injection_span && (
                        <span className="inline-flex items-center gap-1 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700 dark:bg-rose-950/60 dark:text-rose-300">
                          <ShieldAlert className="h-3 w-3" />
                          Sanitized
                        </span>
                      )}
                    </div>
                    <span className="font-mono text-[11px] text-slate-400">{item.at_utc}</span>
                  </div>
                  {item.title_or_subject && (
                    <p className="mt-1.5 text-xs font-semibold text-slate-800 dark:text-slate-200">
                      {item.title_or_subject}
                    </p>
                  )}
                  <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300">
                    {item.preview}
                  </p>
                  <p className="mt-1.5 text-[11px] text-slate-400">
                    By {item.author_name} ({item.visibility})
                    {item.channel ? ` • ${item.channel}` : ''}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Upcoming Deadlines & Commitments */}
        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm lg:col-span-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                Upcoming Deadlines &amp; Promises
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Tracked tasks and open peer commitments
              </p>
            </div>
            <button
              type="button"
              onClick={() => navigate('/memory')}
              className="text-xs font-semibold text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
            >
              Memory &rarr;
            </button>
          </div>

          <div className="mt-4 space-y-3">
            {data.upcoming_deadlines.length === 0 ? (
              <EmptyState
                title="No pending deadlines"
                description="All visible tasks and commitments are closed."
              />
            ) : (
              data.upcoming_deadlines.map((d) => (
                <div
                  key={`${d.kind}-${d.item_id}`}
                  className="rounded-xl border border-slate-200/70 bg-slate-50/50 p-3.5 dark:border-slate-800 dark:bg-slate-950/40"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                            d.kind === 'promise'
                              ? 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300'
                              : d.status === 'blocked'
                                ? 'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300'
                                : 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300'
                          }`}
                        >
                          {d.kind === 'promise' ? 'Promise' : d.status.replace('_', ' ')}
                        </span>
                        {d.client_name && (
                          <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
                            {d.client_name}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs font-semibold text-slate-900 dark:text-slate-100">
                        {d.title}
                      </p>
                      <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                        Owner: {d.owner_name} &bull; Due:{' '}
                        <span className="font-mono font-medium text-slate-700 dark:text-slate-300">
                          {d.due_date}
                        </span>
                      </p>
                      {d.blocker && (
                        <p className="mt-1 flex items-center gap-1 text-[11px] text-rose-600 dark:text-rose-400">
                          <AlertCircle className="h-3 w-3 shrink-0" />
                          {d.blocker}
                        </p>
                      )}
                    </div>
                  </div>
                  {d.sources.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {d.sources.map((s) => (
                        <SourceCitation key={s} recordId={s} />
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </PageContainer>
  )
}
