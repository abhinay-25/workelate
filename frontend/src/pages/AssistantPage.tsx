import {
  Bot,
  Clock,
  HelpCircle,
  Send,
  ShieldAlert,
  Sparkles,
  User,
} from 'lucide-react'
import React, { useEffect, useState } from 'react'
import { SourceCitation } from '../components/common/SourceCitation'
import { PageContainer } from '../components/layout/PageContainer'
import { useAppContext } from '../hooks/useAppContext'
import { assistantService } from '../services/assistantService'
import { AskResponse } from '../types/api'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  atUtc?: string
  response?: AskResponse
  error?: string
}

const SUGGESTED_QUESTIONS = [
  'Who owns billing for the Mumbai client?',
  'Who owns the Northbeam revised sow?',
  'Is the Friday thing done?',
  'What did Meera say about me?',
  'Write an executive memo summarizing current project risks and recommendations.',
  'What are the key strategic risks across our accounts and what should leadership do next?',
  'Based on available information, what should we prioritize this week?',
]

export function AssistantPage() {
  const { personId, currentPerson, selectedDate, availableDates } = useAppContext()
  const [question, setQuestion] = useState('')
  const [atCutoff, setAtCutoff] = useState(`${selectedDate}T18:00:00Z`)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setAtCutoff(`${selectedDate}T18:00:00Z`)
  }, [selectedDate])

  useEffect(() => {
    setMessages([])
  }, [personId])

  const sendQuestion = async (rawQ: string) => {
    const trimmed = rawQ.trim()
    if (!trimmed || submitting) return

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      text: trimmed,
      atUtc: atCutoff,
    }
    setMessages((prev) => [...prev, userMsg])
    setQuestion('')
    setSubmitting(true)

    try {
      const res = await assistantService.ask({
        person_id: personId,
        question: trimmed,
        at: atCutoff.trim() || null,
      })
      const assistantText =
        res.status === 'refused'
          ? 'I cannot share that information because it involves private or restricted records you are not authorized to view.'
          : res.status === 'clarify'
            ? res.clarify?.question || 'Could you clarify which item you mean?'
            : res.answer?.text || 'I have nothing on that.'

      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: 'assistant',
          text: assistantText,
          atUtc: res.at_utc,
          response: res,
        },
      ])
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}`,
          role: 'assistant',
          text: err instanceof Error ? err.message : 'Request failed.',
          error: err instanceof Error ? err.message : 'Request failed.',
        },
      ])
    } finally {
      setSubmitting(false)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    void sendQuestion(question)
  }

  return (
    <PageContainer>
      <section className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Ask WorkMate Assistant
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Grounded hybrid search + deterministic facts with strict temporal cutoffs and privacy
              refusals for{' '}
              <span className="font-semibold text-slate-700 dark:text-slate-200">
                {currentPerson?.name || personId} ({personId})
              </span>
              .
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Clock className="h-4 w-4 text-slate-400" />
            <label htmlFor="ask-cutoff" className="text-xs font-medium text-slate-600 dark:text-slate-300">
              As-of UTC:
            </label>
            <input
              id="ask-cutoff"
              type="text"
              value={atCutoff}
              onChange={(e) => setAtCutoff(e.target.value)}
              className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 font-mono text-xs text-slate-800 outline-none focus:border-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            />
            <div className="flex flex-wrap gap-1">
              {availableDates.map((d) => (
                <button
                  key={d.day}
                  type="button"
                  onClick={() => setAtCutoff(`${d.date}T18:00:00Z`)}
                  className="rounded-md bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                >
                  D{d.day}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Suggested Queries */}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3.5 dark:border-slate-800">
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            <Sparkles className="h-3 w-3 text-indigo-500" />
            Try:
          </span>
          {SUGGESTED_QUESTIONS.map((sq) => (
            <button
              key={sq}
              type="button"
              onClick={() => void sendQuestion(sq)}
              disabled={submitting}
              className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-700 transition hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-300 dark:hover:border-indigo-700 dark:hover:bg-indigo-950/60"
            >
              {sq}
            </button>
          ))}
        </div>
      </section>

      {/* Conversation Thread */}
      <section className="flex min-h-[420px] flex-col justify-between rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {messages.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-center">
              <div className="rounded-2xl bg-indigo-50 p-4 text-indigo-600 dark:bg-indigo-950/60 dark:text-indigo-400">
                <Bot className="h-7 w-7" />
              </div>
              <h3 className="mt-3 text-sm font-semibold text-slate-900 dark:text-white">
                Ask anything about tasks, handovers, clients, or promises
              </h3>
              <p className="mt-1 max-w-md text-xs text-slate-500 dark:text-slate-400">
                WorkMate resolves aliases (like <code className="font-mono">NB</code> or{' '}
                <code className="font-mono">Mumbai client</code>), enforces org &amp; private ACLs,
                and cites exact source records. Switch to Rohan Kapoor (<code className="font-mono">p03</code>)
                and ask &ldquo;What did Meera say about me?&rdquo; to see privacy enforcement live.
              </p>
            </div>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={`flex gap-3 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                {m.role === 'assistant' && (
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white">
                    <Bot className="h-4 w-4" />
                  </div>
                )}

                <div
                  className={`max-w-3xl rounded-2xl p-4 ${
                    m.role === 'user'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : m.response?.status === 'refused'
                        ? 'border border-rose-200 bg-rose-50/80 text-slate-900 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-slate-100'
                        : m.response?.status === 'clarify'
                          ? 'border border-amber-200 bg-amber-50/70 text-slate-900 dark:border-amber-900/60 dark:bg-amber-950/25 dark:text-slate-100'
                          : 'border border-slate-200/80 bg-slate-50/70 text-slate-900 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-100'
                  }`}
                >
                  {m.error && (
                    <p className="text-sm font-medium text-rose-600 dark:text-rose-400">{m.error}</p>
                  )}

                  {m.role === 'user' && <p className="text-sm leading-relaxed">{m.text}</p>}

                  {m.role === 'assistant' && m.response && (
                    <div className="space-y-3">
                      {m.response.status === 'refused' && (
                        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-rose-700 dark:text-rose-300">
                          <ShieldAlert className="h-4 w-4" />
                          Access Refused (Reason: {m.response.refused?.reason || 'private'})
                        </div>
                      )}

                      {m.response.status === 'clarify' && (
                        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-300">
                          <HelpCircle className="h-4 w-4" />
                          Clarification Required
                        </div>
                      )}

                      <p className="text-sm leading-relaxed">{m.text}</p>

                      {m.response.status === 'clarify' && m.response.clarify && (
                        <div className="flex flex-wrap gap-2 pt-1">
                          {m.response.clarify.options.map((opt) => (
                            <button
                              key={opt}
                              type="button"
                              disabled={submitting}
                              onClick={() => void sendQuestion(opt)}
                              className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-left text-xs font-medium text-slate-800 shadow-sm transition hover:border-amber-500 hover:bg-amber-50 dark:border-amber-800 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
                            >
                              {opt}
                            </button>
                          ))}
                        </div>
                      )}

                      {m.response.matched.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5 pt-1">
                          <span className="text-[11px] font-medium text-slate-500">Resolved:</span>
                          {m.response.matched.map((mat, i) => (
                            <span
                              key={i}
                              className="rounded-md bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-slate-200 dark:bg-slate-900 dark:text-slate-300 dark:ring-slate-700"
                            >
                              {mat.phrase} &rarr; {mat.label || mat.id}
                            </span>
                          ))}
                        </div>
                      )}

                      {m.response.answer && m.response.answer.sources.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-200/70 pt-2.5 dark:border-slate-800">
                          <span className="text-[11px] font-semibold text-slate-500">Sources:</span>
                          {m.response.answer.sources.map((sid) => (
                            <SourceCitation key={sid} recordId={sid} />
                          ))}
                        </div>
                      )}

                      <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] font-mono text-slate-400">
                        <div className="flex items-center gap-2">
                          <span
                            className={`rounded px-1.5 py-0.5 font-sans text-[10px] font-semibold ${
                              m.response.usage.input_tokens > 0 || m.response.usage.mode === 'llm_assisted'
                                ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950/70 dark:text-indigo-300'
                                : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/70 dark:text-emerald-300'
                            }`}
                          >
                            {m.response.usage.input_tokens > 0 || m.response.usage.mode === 'llm_assisted'
                              ? `LLM: ${m.response.usage.provider || 'groq'} / ${m.response.usage.model || 'active'}`
                              : 'Deterministic ($0)'}
                          </span>
                          <span>as of {m.response.at_utc}</span>
                        </div>
                        <span>
                          {m.response.usage.ms} ms &bull; {m.response.usage.input_tokens} in /{' '}
                          {m.response.usage.output_tokens} out &bull; $
                          {m.response.usage.cost_usd.toFixed(6)}
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                {m.role === 'user' && (
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                    <User className="h-4 w-4" />
                  </div>
                )}
              </div>
            ))
          )}

          {submitting && (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
              Evaluating authorized memory and hybrid index...
            </div>
          )}
        </div>

        <form
          onSubmit={handleSubmit}
          className="flex items-center gap-2.5 border-t border-slate-200/80 p-4 dark:border-slate-800"
        >
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={`Ask as ${currentPerson?.name || personId}...`}
            disabled={submitting}
            className="flex-1 rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:bg-white dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:border-indigo-500"
          />
          <button
            type="submit"
            disabled={submitting || !question.trim()}
            className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
            Ask
          </button>
        </form>
      </section>
    </PageContainer>
  )
}
