import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { coreApi } from '../services/api'
import { AvailableDate, HealthResponse, Person } from '../types/api'

interface AppContextValue {
  people: Person[]
  personId: string
  setPersonId: (id: string) => void
  currentPerson: Person | null
  selectedDate: string
  setSelectedDate: (date: string) => void
  availableDates: AvailableDate[]
  health: HealthResponse | null
  healthError: string | null
  refreshHealth: () => Promise<void>
  activeRecordId: string | null
  openRecordDrawer: (recordId: string) => void
  closeRecordDrawer: () => void
  darkMode: boolean
  toggleDarkMode: () => void
  memoryVersion: number
  bumpMemoryVersion: () => void
}

const AppContext = createContext<AppContextValue | undefined>(undefined)

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [people, setPeople] = useState<Person[]>([])
  const [personId, setPersonIdState] = useState<string>(() => {
    return localStorage.getItem('workmate_person_id') || 'p02'
  })
  const [selectedDate, setSelectedDateState] = useState<string>(() => {
    return localStorage.getItem('workmate_selected_date') || '2026-11-06'
  })
  const [availableDates, setAvailableDates] = useState<AvailableDate[]>([
    { day: 1, date: '2026-11-02' },
    { day: 2, date: '2026-11-03' },
    { day: 3, date: '2026-11-04' },
    { day: 4, date: '2026-11-05' },
    { day: 5, date: '2026-11-06' },
  ])
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [activeRecordId, setActiveRecordId] = useState<string | null>(null)
  const [memoryVersion, setMemoryVersion] = useState<number>(0)
  const [darkMode, setDarkMode] = useState<boolean>(() => {
    return localStorage.getItem('workmate_theme') === 'dark'
  })

  useEffect(() => {
    const root = document.documentElement
    if (darkMode) {
      root.classList.add('dark')
      localStorage.setItem('workmate_theme', 'dark')
    } else {
      root.classList.remove('dark')
      localStorage.setItem('workmate_theme', 'light')
    }
  }, [darkMode])

  const setPersonId = useCallback((id: string) => {
    setPersonIdState(id)
    localStorage.setItem('workmate_person_id', id)
  }, [])

  const setSelectedDate = useCallback((d: string) => {
    setSelectedDateState(d)
    localStorage.setItem('workmate_selected_date', d)
  }, [])

  const refreshHealth = useCallback(async () => {
    try {
      const [h, p] = await Promise.all([coreApi.getHealth(), coreApi.getPeople()])
      setHealth(h)
      setHealthError(null)
      setPeople(p.people)
      if (h.available_dates && h.available_dates.length > 0) {
        setAvailableDates(h.available_dates)
      }
      if (p.people.length > 0 && !p.people.some((item) => item.person_id === personId)) {
        const def = p.people.find((item) => item.person_id === 'p02') || p.people[0]
        setPersonId(def.person_id)
      }
    } catch (err) {
      setHealthError(err instanceof Error ? err.message : 'Backend connection failed.')
    }
  }, [personId, setPersonId])

  useEffect(() => {
    void refreshHealth()
  }, [refreshHealth])

  const currentPerson = people.find((p) => p.person_id === personId) || null

  return (
    <AppContext.Provider
      value={{
        people,
        personId,
        setPersonId,
        currentPerson,
        selectedDate,
        setSelectedDate,
        availableDates,
        health,
        healthError,
        refreshHealth,
        activeRecordId,
        openRecordDrawer: (rid) => setActiveRecordId(rid),
        closeRecordDrawer: () => setActiveRecordId(null),
        darkMode,
        toggleDarkMode: () => setDarkMode((prev) => !prev),
        memoryVersion,
        bumpMemoryVersion: () => setMemoryVersion((v) => v + 1),
      }}
    >
      {children}
    </AppContext.Provider>
  )
}

export function useAppContext(): AppContextValue {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useAppContext must be used inside AppProvider')
  return ctx
}
