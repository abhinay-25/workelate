import { AlertTriangle, RotateCcw } from 'lucide-react'

interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
}

export function ErrorState({ title = 'Unable to load section', message, onRetry }: ErrorStateProps) {
  return (
    <div className="rounded-xl border border-rose-200 bg-rose-50/70 p-6 dark:border-rose-900/50 dark:bg-rose-950/30">
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-rose-100 p-2 text-rose-600 dark:bg-rose-900/50 dark:text-rose-400">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-rose-900 dark:text-rose-200">{title}</h3>
          <p className="mt-1 text-sm text-rose-700 dark:text-rose-300">{message}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-700 shadow-sm hover:bg-rose-50 dark:border-rose-800 dark:bg-slate-900 dark:text-rose-300 dark:hover:bg-slate-800"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
