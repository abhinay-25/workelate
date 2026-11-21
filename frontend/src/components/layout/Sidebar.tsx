import {
  Activity,
  Brain,
  CalendarClock,
  Cpu,
  LayoutDashboard,
  MessageSquareText,
  Moon,
  ShieldCheck,
  Sun,
  X,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useAppContext } from '../../hooks/useAppContext'

const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: LayoutDashboard },
  { to: '/briefs', label: 'Meeting Briefs', icon: CalendarClock },
  { to: '/assistant', label: 'Ask Assistant', icon: MessageSquareText },
  { to: '/memory', label: 'Memory', icon: Brain },
  { to: '/audit', label: 'Activity & Audit', icon: Activity },
  { to: '/usage', label: 'System Usage', icon: Cpu },
]

interface SidebarProps {
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

export function Sidebar({ mobileOpen, onCloseMobile }: SidebarProps) {
  const { currentPerson, health, darkMode, toggleDarkMode } = useAppContext()

  const content = (
    <div className="flex h-full w-64 flex-col border-r border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between border-b border-slate-200/80 px-5 py-4 dark:border-slate-800">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-sm">
            <Brain className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-bold tracking-tight text-slate-900 dark:text-white">
                WorkMate
              </span>
              <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
                R3
              </span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">Assistant That Remembers</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onCloseMobile}
          className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 lg:hidden dark:hover:bg-slate-800"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <nav className="flex-1 space-y-1 p-3">
        <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Workspace
        </p>
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              onClick={onCloseMobile}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
                  isActive
                    ? 'bg-indigo-50 text-indigo-700 shadow-sm dark:bg-indigo-950/70 dark:text-indigo-300'
                    : 'text-slate-600 hover:bg-slate-100/80 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800/70 dark:hover:text-slate-100'
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {item.label}
            </NavLink>
          )
        })}
      </nav>

      <div className="space-y-3 border-t border-slate-200/80 p-4 dark:border-slate-800">
        {currentPerson && (
          <div className="rounded-xl border border-slate-200/80 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-950/60">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Active Context
              </span>
              <span className="inline-flex items-center gap-1 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950/70 dark:text-emerald-300">
                <ShieldCheck className="h-3 w-3" />
                ACL Active
              </span>
            </div>
            <p className="mt-1.5 truncate text-xs font-semibold text-slate-900 dark:text-slate-100">
              {currentPerson.name}
            </p>
            <p className="truncate text-[11px] text-slate-500 dark:text-slate-400">
              {currentPerson.org_name} &bull; {currentPerson.role}
            </p>
          </div>
        )}

        <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
          <span>{health ? `Day ${health.current_day} Synced` : 'Checking DB...'}</span>
          <button
            type="button"
            onClick={toggleDarkMode}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            {darkMode ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            {darkMode ? 'Light' : 'Dark'}
          </button>
        </div>
      </div>
    </div>
  )

  return (
    <>
      <aside className="hidden lg:block">{content}</aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="fixed inset-0 bg-slate-950/50" onClick={onCloseMobile} />
          <div className="relative z-10 h-full">{content}</div>
        </div>
      )}
    </>
  )
}
