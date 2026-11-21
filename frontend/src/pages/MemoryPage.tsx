import {
  AlertCircle,
  Brain,
  CheckCircle2,
  Edit3,
  Eraser,
  Lock,
  Settings2,
  Trash2,
} from 'lucide-react'
import React, { useCallback, useEffect, useState } from 'react'
import { ConfirmDialog } from '../components/common/ConfirmDialog'
import { EmptyState } from '../components/common/EmptyState'
import { ErrorState } from '../components/common/ErrorState'
import { Loading } from '../components/common/Loading'
import { SourceCitation } from '../components/common/SourceCitation'
import { PageContainer } from '../components/layout/PageContainer'
import { useAppContext } from '../hooks/useAppContext'
import { memoryService } from '../services/memoryService'
import { MemoryResponse } from '../types/api'

type MemoryTab = 'claims' | 'tasks_promises' | 'preferences_aliases' | 'notes_forgotten'

export function MemoryPage() {
  const { personId, selectedDate, memoryVersion, bumpMemoryVersion } = useAppContext()
  const [data, setData] = useState<MemoryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<MemoryTab>('claims')

  // Correction state
  const [correctionText, setCorrectionText] = useState('')
  const [correctLoading, setCorrectLoading] = useState(false)
  const [correctFeedback, setCorrectFeedback] = useState<{
    applied: boolean
    message: string
  } | null>(null)

  // Forget state
  const [forgetWhat, setForgetWhat] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [forgetLoading, setForgetLoading] = useState(false)
  const [forgetResult, setForgetResult] = useState<string | null>(null)

  const loadMemory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await memoryService.getMemory(personId, selectedDate)
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load persistent memory.')
    } finally {
      setLoading(false)
    }
  }, [personId, selectedDate])

  useEffect(() => {
    void loadMemory()
  }, [loadMemory, memoryVersion])

  const handleSubmitCorrection = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!correctionText.trim() || correctLoading) return
    setCorrectLoading(true)
    setCorrectFeedback(null)
    try {
      const res = await memoryService.submitCorrection(personId, correctionText.trim())
      setCorrectFeedback({ applied: res.applied, message: res.message })
      if (res.applied) {
        setCorrectionText('')
        bumpMemoryVersion()
        await loadMemory()
      }
    } catch (err) {
      setCorrectFeedback({
        applied: false,
        message: err instanceof Error ? err.message : 'Failed to apply correction.',
      })
    } finally {
      setCorrectLoading(false)
    }
  }

  const handleConfirmForget = async () => {
    if (!forgetWhat.trim()) return
    setForgetLoading(true)
    setForgetResult(null)
    try {
      const res = await memoryService.forget(personId, forgetWhat.trim())
      setConfirmOpen(false)
      setForgetWhat('')
      setForgetResult(
        `Forgotten "${res.what}": retracted ${res.forgotten_records} authored record(s) and suppressed keywords [${res.keywords.join(', ')}].`
      )
      bumpMemoryVersion()
      await loadMemory()
    } catch (err) {
      setConfirmOpen(false)
      setForgetResult(err instanceof Error ? err.message : 'Failed to execute forget request.')
    } finally {
      setForgetLoading(false)
    }
  }

  if (loading) {
    return (
      <PageContainer>
        <Loading label="Loading authorized persistent memory..." />
      </PageContainer>
    )
  }

  if (error || !data) {
    return (
      <PageContainer>
        <ErrorState message={error || 'Unable to load memory.'} onRetry={() => void loadMemory()} />
      </PageContainer>
    )
  }

  return (
    <PageContainer>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
        {/* Left 8 Columns: Authorized Memory Inspector */}
        <section className="space-y-4 xl:col-span-8">
          <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-bold text-slate-900 dark:text-white">
                  Persistent Memory State &mdash; {data.person.name}
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Facts, corrections, promises, learned aliases, preferences, and retracted topics
                  visible up to Day {data.current_day}.
                </p>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-3.5 dark:border-slate-800">
              {(
                [
                  ['claims', `Facts & Claims (${data.claims.length})`],
                  [
                    'tasks_promises',
                    `Tasks (${data.tasks.length}) & Promises (${data.promises.length})`,
                  ],
                  [
                    'preferences_aliases',
                    `Preferences & Aliases (${data.entity_aliases.length})`,
                  ],
                  [
                    'notes_forgotten',
                    `Private Notes (${data.personal_notes.length}) & Forgotten (${data.forgotten_topics.length})`,
                  ],
                ] as [MemoryTab, string][]
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setTab(key)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                    tab === key
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {tab === 'claims' && (
            <div className="space-y-3">
              {data.claims.length === 0 ? (
                <EmptyState title="No active claims" description="No tracked structured claims visible." />
              ) : (
                data.claims.map((c) => (
                  <div
                    key={c.subject_key}
                    className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-bold text-slate-900 dark:text-slate-100">
                          {c.subject_key}
                        </span>
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {c.predicate}
                        </span>
                        {c.source_kind === 'feedback_correction' && (
                          <span className="rounded bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold text-indigo-700 dark:bg-indigo-950/70 dark:text-indigo-300">
                            Feedback Correction Override
                          </span>
                        )}
                        {c.is_expired && (
                          <span className="rounded bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-950/70 dark:text-amber-300">
                            Expired Window
                          </span>
                        )}
                      </div>
                      {c.client_name && (
                        <span className="text-xs font-medium text-slate-500">{c.client_name}</span>
                      )}
                    </div>
                    <p className="mt-2 text-sm font-semibold text-slate-900 dark:text-white">
                      {c.value}
                    </p>
                    {c.old_value && (
                      <p className="mt-0.5 text-xs text-slate-500">
                        Previously: <span className="line-through">{c.old_value}</span>
                      </p>
                    )}
                    <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
                      <div className="flex flex-wrap gap-1.5">
                        {c.sources.map((s) => (
                          <SourceCitation key={s} recordId={s} />
                        ))}
                      </div>
                      <span className="font-mono">
                        Effective: {c.effective_at_utc}
                        {c.valid_until ? ` (until ${c.valid_until})` : ''}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {tab === 'tasks_promises' && (
            <div className="space-y-5">
              <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                  Tracked Tasks ({data.tasks.length})
                </h3>
                <div className="mt-3 space-y-2.5">
                  {data.tasks.map((t) => (
                    <div
                      key={t.task_id}
                      className="rounded-xl border border-slate-200/70 bg-slate-50/50 p-3 dark:border-slate-800 dark:bg-slate-950/40"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-slate-900 dark:text-slate-100">
                          {t.title}
                        </span>
                        <span className="rounded bg-slate-200/70 px-2 py-0.5 text-[10px] font-semibold uppercase text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                          {t.status}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        Owner: {t.owner_name} &bull; Due: <span className="font-mono">{t.due}</span>
                        {t.client_name ? ` • ${t.client_name}` : ''}
                      </p>
                      {t.blocked_reason && (
                        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                          Blocked: {t.blocked_reason}
                        </p>
                      )}
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {t.sources.map((s) => (
                          <SourceCitation key={s} recordId={s} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                  Commitments &amp; Promises ({data.promises.length})
                </h3>
                <div className="mt-3 space-y-2.5">
                  {data.promises.map((p, idx) => (
                    <div
                      key={idx}
                      className="rounded-xl border border-slate-200/70 bg-slate-50/50 p-3 dark:border-slate-800 dark:bg-slate-950/40"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-slate-900 dark:text-slate-100">
                          {p.owner_name}: {p.what}
                        </span>
                        <span
                          className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
                            p.status === 'fulfilled'
                              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
                              : 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300'
                          }`}
                        >
                          {p.status}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        Due: <span className="font-mono">{p.due}</span>
                        {p.client_name ? ` • ${p.client_name}` : ''}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {p.sources.map((s) => (
                          <SourceCitation key={s} recordId={s} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === 'preferences_aliases' && (
            <div className="space-y-5">
              <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <div className="flex items-center gap-2">
                  <Settings2 className="h-4 w-4 text-indigo-600" />
                  <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                    Learned Brief Preferences (Per-User Isolated)
                  </h3>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <span className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-medium dark:bg-slate-800">
                    skip_newsletters:{' '}
                    <strong>{String(!!data.active_preferences_summary.skip_newsletters)}</strong>
                  </span>
                  <span className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-medium dark:bg-slate-800">
                    max_changed:{' '}
                    <strong>{data.active_preferences_summary.max_changed ?? 'unlimited'}</strong>
                  </span>
                  <span className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-medium dark:bg-slate-800">
                    blockers_first:{' '}
                    <strong>{String(!!data.active_preferences_summary.blockers_first)}</strong>
                  </span>
                  <span className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-medium dark:bg-slate-800">
                    most_important_first:{' '}
                    <strong>{String(!!data.active_preferences_summary.most_important_first)}</strong>
                  </span>
                </div>
                {(data.active_preferences_summary.ignore_topics || []).length > 0 && (
                  <p className="mt-3 text-xs text-slate-600 dark:text-slate-300">
                    Ignored topics:{' '}
                    <span className="font-mono">
                      {(data.active_preferences_summary.ignore_topics || []).join(', ')}
                    </span>
                  </p>
                )}

                {data.preference_history.length > 0 && (
                  <div className="mt-4 space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
                    {data.preference_history.map((pr, idx) => (
                      <div key={idx} className="text-xs text-slate-600 dark:text-slate-300">
                        <span className="font-mono text-indigo-600 dark:text-indigo-400">
                          Day {pr.day}
                        </span>{' '}
                        &bull; <strong>{pr.pref_key}</strong> = {pr.pref_val} &mdash; &ldquo;
                        {pr.source_words}&rdquo;
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                  Learned Entity Aliases ({data.entity_aliases.length})
                </h3>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800">
                        <th className="py-2 pr-3 font-medium">Alias / Nickname</th>
                        <th className="py-2 pr-3 font-medium">Resolves To</th>
                        <th className="py-2 pr-3 font-medium">Evidence</th>
                        <th className="py-2 font-medium">Source</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                      {data.entity_aliases.map((al, i) => (
                        <tr key={i}>
                          <td className="py-2 pr-3 font-mono font-semibold text-slate-800 dark:text-slate-200">
                            {al.alias}
                          </td>
                          <td className="py-2 pr-3 text-slate-700 dark:text-slate-300">
                            {al.entity_label} ({al.entity_id})
                          </td>
                          <td className="py-2 pr-3 text-slate-500">{al.evidence_kind}</td>
                          <td className="py-2">
                            {al.source_record_id ? <SourceCitation recordId={al.source_record_id} /> : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {tab === 'notes_forgotten' && (
            <div className="space-y-5">
              <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <div className="flex items-center gap-2">
                  <Lock className="h-4 w-4 text-amber-600" />
                  <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                    Private &amp; Restricted Personal Notes ({data.personal_notes.length})
                  </h3>
                </div>
                {data.personal_notes.length === 0 ? (
                  <p className="mt-3 text-xs text-slate-500">
                    No active personal notes visible for {data.person.name}.
                  </p>
                ) : (
                  <div className="mt-3 space-y-2.5">
                    {data.personal_notes.map((pn, i) => (
                      <div
                        key={i}
                        className="rounded-xl border border-amber-200/70 bg-amber-50/40 p-3 dark:border-amber-900/50 dark:bg-amber-950/20"
                      >
                        <p className="text-sm text-slate-900 dark:text-slate-100">{pn.text}</p>
                        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
                          <div className="flex gap-1.5">
                            {pn.sources.map((s) => (
                              <SourceCitation key={s} recordId={s} />
                            ))}
                          </div>
                          <span>
                            {pn.visibility} &bull; {pn.at_utc}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <div className="flex items-center gap-2">
                  <Eraser className="h-4 w-4 text-rose-600" />
                  <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                    Forgotten Topics History ({data.forgotten_topics.length})
                  </h3>
                </div>
                {data.forgotten_topics.length === 0 ? (
                  <p className="mt-3 text-xs text-slate-500">
                    No topics have been forgotten by {data.person.name} yet.
                  </p>
                ) : (
                  <div className="mt-3 space-y-2.5">
                    {data.forgotten_topics.map((ft) => (
                      <div
                        key={ft.forget_id}
                        className="rounded-xl border border-rose-200/70 bg-rose-50/40 p-3 text-xs dark:border-rose-900/50 dark:bg-rose-950/20"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-rose-900 dark:text-rose-200">
                            &ldquo;{ft.raw_words}&rdquo;
                          </span>
                          <span className="font-mono text-rose-600 dark:text-rose-400">
                            Day {ft.day}
                          </span>
                        </div>
                        <p className="mt-1 text-slate-600 dark:text-slate-300">
                          Suppressed keywords: <code className="font-mono">{ft.keywords.join(', ')}</code> &bull;
                          Retracted record count: {ft.affected_record_ids.length}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>

        {/* Right 4 Columns: Interactive Actions (Corrections + Forget) */}
        <aside className="space-y-6 xl:col-span-4">
          {/* Submit Correction */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center gap-2">
              <Edit3 className="h-4 w-4 text-indigo-600" />
              <h3 className="text-sm font-bold text-slate-900 dark:text-white">
                Submit Factual Correction
              </h3>
            </div>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Override stale docs deterministically. Hedged phrasing (e.g., &ldquo;I think maybe...&rdquo;)
              is automatically rejected by the anti-hallucination guard.
            </p>

            <form onSubmit={handleSubmitCorrection} className="mt-4 space-y-3">
              <textarea
                rows={3}
                value={correctionText}
                onChange={(e) => setCorrectionText(e.target.value)}
                placeholder="e.g. No, Rohan Kapoor handles Alderpoint billing now, not Kavya Nair."
                className="w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-900 outline-none focus:border-indigo-500 focus:bg-white dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
              <div className="flex flex-wrap gap-1.5">
                {[
                  'No, Rohan Kapoor handles Alderpoint billing now, not Kavya Nair.',
                  'The launch date for Northbeam is 18 Nov.',
                ].map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => setCorrectionText(ex)}
                    className="rounded-md bg-slate-100 px-2 py-1 text-left text-[10px] font-medium text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
                  >
                    Example: {ex.slice(0, 34)}...
                  </button>
                ))}
              </div>
              <button
                type="submit"
                disabled={correctLoading || !correctionText.trim()}
                className="w-full rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
              >
                {correctLoading ? 'Applying Correction...' : 'Apply Correction'}
              </button>
            </form>

            {correctFeedback && (
              <div
                className={`mt-3 rounded-xl border p-3 text-xs ${
                  correctFeedback.applied
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-200'
                    : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200'
                }`}
              >
                <div className="flex items-start gap-2">
                  {correctFeedback.applied ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                  ) : (
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  )}
                  <span>{correctFeedback.message}</span>
                </div>
              </div>
            )}
          </div>

          {/* Right to be Forgotten */}
          <div className="rounded-2xl border border-rose-200/80 bg-white p-5 shadow-sm dark:border-rose-900/50 dark:bg-slate-900">
            <div className="flex items-center gap-2 text-rose-600 dark:text-rose-400">
              <Trash2 className="h-4 w-4" />
              <h3 className="text-sm font-bold text-slate-900 dark:text-white">
                Forget Information
              </h3>
            </div>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Retract records authored by {data.person.first_name}, purge FTS5 &amp; dense vectors,
              recompute derived claims/promises, and wipe <code className="font-mono">brief_cache</code>.
            </p>

            <div className="mt-4 space-y-3">
              <input
                type="text"
                value={forgetWhat}
                onChange={(e) => setForgetWhat(e.target.value)}
                placeholder="e.g. my Dubai trip"
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs text-slate-900 outline-none focus:border-rose-500 focus:bg-white dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
              <button
                type="button"
                onClick={() => setForgetWhat('my Dubai trip')}
                className="rounded-md bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
              >
                Example: &ldquo;my Dubai trip&rdquo;
              </button>
              <button
                type="button"
                disabled={!forgetWhat.trim()}
                onClick={() => setConfirmOpen(true)}
                className="w-full rounded-xl bg-rose-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-rose-700 disabled:opacity-50"
              >
                Forget Topic Permanently...
              </button>
            </div>

            {forgetResult && (
              <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                <div className="flex items-start gap-2">
                  <Brain className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />
                  <span>{forgetResult}</span>
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Confirm Cascading Memory Retraction"
        confirmLabel="Yes, Forget Permanently"
        loading={forgetLoading}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void handleConfirmForget()}
        description={
          <div className="space-y-2">
            <p>
              You are about to forget{' '}
              <strong className="text-slate-900 dark:text-white">&ldquo;{forgetWhat}&rdquo;</strong>{' '}
              for <strong>{data.person.name}</strong>.
            </p>
            <ul className="list-disc space-y-1 pl-4 text-xs text-slate-500 dark:text-slate-400">
              <li>Marks matching authored records as forgotten and clears their FTS5 text.</li>
              <li>Deletes matching dense vectors from SQLite / Qdrant storage.</li>
              <li>Recomputes derived tasks, promises, doc versions, and claims.</li>
              <li>Clears cached meeting briefs and blocks residual mentions for {data.person.first_name}.</li>
            </ul>
          </div>
        }
      />
    </PageContainer>
  )
}
