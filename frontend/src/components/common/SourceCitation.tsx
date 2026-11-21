import { FileText } from 'lucide-react'
import { useAppContext } from '../../hooks/useAppContext'

interface SourceCitationProps {
  recordId: string
  className?: string
}

export function SourceCitation({ recordId, className = '' }: SourceCitationProps) {
  const { openRecordDrawer } = useAppContext()

  return (
    <button
      type="button"
      onClick={() => openRecordDrawer(recordId)}
      title={`Inspect authorized source record ${recordId}`}
      className={`inline-flex items-center gap-1 rounded-md border border-indigo-200/80 bg-indigo-50/70 px-1.5 py-0.5 font-mono text-[11px] font-medium text-indigo-700 transition hover:border-indigo-400 hover:bg-indigo-100 dark:border-indigo-800/70 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:border-indigo-600 dark:hover:bg-indigo-900/60 ${className}`}
    >
      <FileText className="h-3 w-3" />
      {recordId}
    </button>
  )
}
