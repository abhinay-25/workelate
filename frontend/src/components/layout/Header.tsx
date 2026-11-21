import { Calendar, Menu, Shield } from 'lucide-react'
import { useAppContext } from '../../hooks/useAppContext'
import { PersonSelector } from '../common/PersonSelector'

interface HeaderProps {
  title: string
  subtitle?: string
  onOpenMobileMenu: () => void
}

export function Header({ title, subtitle, onOpenMobileMenu }: HeaderProps) {
  const { currentPerson, selectedDate, setSelectedDate, availableDates, health, healthError } =
    useAppContext()

  return (
    <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/90 px-4 py-3 backdrop-blur-md sm:px-6 dark:border-slate-800 dark:bg-slate-900/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onOpenMobileMenu}
            className="rounded-lg border border-slate-200 p-1.5 text-slate-600 hover:bg-slate-100 lg:hidden dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div>
            <h1 className="text-base font-bold tracking-tight text-slate-900 sm:text-lg dark:text-white">
              {title}
            </h1>
            {subtitle && (
              <p className="text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
            <Calendar className="h-3.5 w-3.5 text-slate-500" />
            <select
              aria-label="Selected Workspace Date"
              value={selectedDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="bg-transparent text-xs font-medium text-slate-800 outline-none dark:text-slate-100"
            >
              {availableDates.map((d) => (
                <option key={d.day} value={d.date} className="bg-white dark:bg-slate-900">
                  Day {d.day} &bull; {d.date}
                </option>
              ))}
            </select>
          </div>

          <PersonSelector />

          {currentPerson && (
            <span
              title="Switching personas evaluates all data strictly against backend org & visibility ACL rules."
              className="hidden items-center gap-1 rounded-lg border border-indigo-200/80 bg-indigo-50/70 px-2.5 py-1.5 text-[11px] font-medium text-indigo-700 xl:inline-flex dark:border-indigo-900 dark:bg-indigo-950/60 dark:text-indigo-300"
            >
              <Shield className="h-3.5 w-3.5" />
              Demo Persona &bull; Backend ACL Enforced
            </span>
          )}

          <div
            title={healthError || 'Backend SQLite & Vector Engine Connected'}
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ${
              health && !healthError
                ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300'
                : 'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300'
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                health && !healthError ? 'bg-emerald-500' : 'bg-rose-500'
              }`}
            />
            {health && !healthError ? `${health.total_records} records` : 'API Offline'}
          </div>
        </div>
      </div>
    </header>
  )
}
