import { UserCheck } from 'lucide-react'
import { useAppContext } from '../../hooks/useAppContext'
import { Person } from '../../types/api'

export function PersonSelector() {
  const { people, personId, setPersonId } = useAppContext()

  const grouped = people.reduce<Record<string, Person[]>>((acc, p) => {
    const key = `${p.org_name} (${p.org_id})`
    if (!acc[key]) acc[key] = []
    acc[key].push(p)
    return acc
  }, {})

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <UserCheck className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
        <label htmlFor="persona-select" className="sr-only">
          Active Persona
        </label>
        <select
          id="persona-select"
          value={personId}
          onChange={(e) => setPersonId(e.target.value)}
          className="bg-transparent pr-2 text-xs font-semibold text-slate-800 outline-none dark:text-slate-100"
        >
          {Object.entries(grouped).map(([orgLabel, members]) => (
            <optgroup key={orgLabel} label={orgLabel} className="bg-white text-slate-900 dark:bg-slate-900 dark:text-slate-100">
              {members.map((m) => (
                <option key={m.person_id} value={m.person_id}>
                  {m.name} ({m.person_id}) — {m.role}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
    </div>
  )
}
