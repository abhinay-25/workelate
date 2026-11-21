import {
  Activity,
  CheckCircle2,
  Cpu,
  Database,
  DollarSign,
  Gauge,
  RefreshCw,
  Shield,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ErrorState } from '../components/common/ErrorState'
import { Loading } from '../components/common/Loading'
import { PageContainer } from '../components/layout/PageContainer'
import { usageService } from '../services/auditService'
import { UsageResponse } from '../types/api'

export function UsagePage() {
  const [data, setData] = useState<UsageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadUsage = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await usageService.getUsage()
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load telemetry.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadUsage()
  }, [loadUsage])

  if (loading) {
    return (
      <PageContainer>
        <Loading label="Loading LLM provider status and cost telemetry..." />
      </PageContainer>
    )
  }

  if (error || !data) {
    return (
      <PageContainer>
        <ErrorState message={error || 'Unable to load usage data.'} onRetry={() => void loadUsage()} />
      </PageContainer>
    )
  }

  const { totals, embedding, guardrails } = data
  const detPct =
    totals.total_requests > 0
      ? Math.round((totals.deterministic_requests / totals.total_requests) * 100)
      : 100

  return (
    <PageContainer>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold text-slate-900 dark:text-white">
            Model Routing, Vector Index &amp; Cost Telemetry
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Deterministic-first execution with strict token budgets and 3-tier LLM failover (no API
            keys are ever exposed).
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadUsage()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh Telemetry
        </button>
      </div>

      {/* KPI Row */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Tracked Operations</span>
            <Activity className="h-4 w-4 text-indigo-600" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">
            {totals.total_requests}
          </p>
          <p className="mt-1 text-xs text-emerald-600 dark:text-emerald-400">
            {detPct}% resolved at $0.00 (deterministic)
          </p>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Total LLM Tokens</span>
            <Cpu className="h-4 w-4 text-sky-600" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">
            {(totals.total_input_tokens + totals.total_output_tokens).toLocaleString()}
          </p>
          <p className="mt-1 font-mono text-xs text-slate-500">
            {totals.total_input_tokens} in / {totals.total_output_tokens} out
          </p>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Cumulative API Cost</span>
            <DollarSign className="h-4 w-4 text-emerald-600" />
          </div>
          <p className="mt-2 font-mono text-2xl font-bold text-slate-900 dark:text-white">
            ${totals.total_cost_usd.toFixed(6)}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Budgets: {guardrails.brief_token_limit} brief / {guardrails.ask_token_limit} ask
          </p>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Vector Index &amp; Avg Latency</span>
            <Gauge className="h-4 w-4 text-amber-600" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">
            {totals.avg_latency_ms} ms
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {embedding.indexed_records} records ({embedding.indexed_chunks} vectors,{' '}
            {embedding.vector_dim}d)
          </p>
        </div>
      </section>

      {/* 3-Tier LLM Chain + Embedding Engine */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        {data.providers.map((p) => (
          <div
            key={p.provider}
            className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex items-center justify-between">
              <span className="rounded bg-indigo-50 px-2 py-0.5 text-[10px] font-bold uppercase text-indigo-700 dark:bg-indigo-950/70 dark:text-indigo-300">
                {p.tier} &bull; {p.provider}
              </span>
              {p.configured ? (
                <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-600">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Configured
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400">
                  <XCircle className="h-3.5 w-3.5" />
                  Standby
                </span>
              )}
            </div>
            <p className="mt-2.5 font-mono text-xs font-bold text-slate-900 dark:text-slate-100">
              {p.model}
            </p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{p.role}</p>
          </div>
        ))}

        <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <span className="rounded bg-sky-50 px-2 py-0.5 text-[10px] font-bold uppercase text-sky-700 dark:bg-sky-950/70 dark:text-sky-300">
              Hybrid Retrieval
            </span>
            <Database className="h-4 w-4 text-sky-600" />
          </div>
          <p className="mt-2.5 font-mono text-xs font-bold text-slate-900 dark:text-slate-100">
            {embedding.model}
          </p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Backend: <code className="font-mono">{embedding.backend}</code> + FTS5 + XML Sandboxing (
            <Shield className="inline h-3 w-3" /> {String(guardrails.xml_prompt_sandboxing)})
          </p>
        </div>
      </section>

      {/* Chart + Operation Breakdown */}
      {data.by_operation.length > 0 && (
        <section className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
            Operations by Execution Path
          </h3>
          <div className="mt-4 h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.by_operation}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="operation" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="deterministic_count" name="Deterministic ($0)" fill="#10b981" radius={[4, 4, 0, 0]} />
                <Bar dataKey="llm_count" name="LLM Assisted" fill="#4f46e5" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {/* Recent Telemetry Calls Table */}
      <section className="overflow-hidden rounded-xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="border-b border-slate-200/80 px-5 py-3.5 dark:border-slate-800">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
            Recent API Execution Telemetry
          </h3>
        </div>
        {data.recent_calls.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">
            No API calls logged yet. Run a Brief, Ask query, or Memory operation to inspect live
            latency and token accounting.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-200 bg-slate-50/80 text-slate-500 dark:border-slate-800 dark:bg-slate-950/60">
                <tr>
                  <th className="px-4 py-3 font-semibold">Timestamp (UTC)</th>
                  <th className="px-4 py-3 font-semibold">Operation</th>
                  <th className="px-4 py-3 font-semibold">Persona</th>
                  <th className="px-4 py-3 font-semibold">Mode / Model</th>
                  <th className="px-4 py-3 font-semibold">Tokens</th>
                  <th className="px-4 py-3 font-semibold">Cost (USD)</th>
                  <th className="px-4 py-3 font-semibold">Latency</th>
                  <th className="px-4 py-3 font-semibold">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                {data.recent_calls.map((c) => (
                  <tr key={c.id} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                    <td className="px-4 py-2.5 whitespace-nowrap font-mono text-[11px] text-slate-500">
                      {c.created_at_utc}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap font-semibold text-slate-900 dark:text-slate-100">
                      {c.operation}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap font-mono text-slate-600 dark:text-slate-300">
                      {c.person_id || 'system'}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <span
                        className={`rounded px-2 py-0.5 text-[10px] font-semibold ${
                          c.mode === 'deterministic'
                            ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
                            : 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300'
                        }`}
                      >
                        {c.model || c.mode}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap font-mono text-slate-600 dark:text-slate-300">
                      {c.input_tokens} / {c.output_tokens}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap font-mono text-slate-600 dark:text-slate-300">
                      ${c.cost_usd.toFixed(6)}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap font-mono text-slate-600 dark:text-slate-300">
                      {c.latency_ms} ms
                    </td>
                    <td className="max-w-xs truncate px-4 py-2.5 text-slate-500" title={c.detail || ''}>
                      {c.detail || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </PageContainer>
  )
}
