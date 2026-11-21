import { useState } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { SourceDetailDrawer } from '../components/common/SourceDetailDrawer'
import { Header } from '../components/layout/Header'
import { Sidebar } from '../components/layout/Sidebar'
import { AssistantPage } from '../pages/AssistantPage'
import { AuditPage } from '../pages/AuditPage'
import { BriefsPage } from '../pages/BriefsPage'
import { MemoryPage } from '../pages/MemoryPage'
import { OverviewPage } from '../pages/OverviewPage'
import { UsagePage } from '../pages/UsagePage'

const ROUTE_META: Record<string, { title: string; subtitle: string }> = {
  '/': {
    title: 'Overview Dashboard',
    subtitle: 'Authorized tasks, pending commitments, scheduled meetings, and live updates',
  },
  '/briefs': {
    title: 'Meeting Briefs',
    subtitle: 'Delta-watermarked client meeting summaries grounded in cited records',
  },
  '/assistant': {
    title: 'Ask Assistant',
    subtitle: 'Temporal & ACL-enforced Q&A with entity resolution, clarification, and refusal',
  },
  '/memory': {
    title: 'Memory Management',
    subtitle: 'Inspect persistent claims, submit corrections, and exercise Right to Be Forgotten',
  },
  '/audit': {
    title: 'Activity & Audit',
    subtitle: 'Authorized record ingestion timeline, defense filters, and state traceability',
  },
  '/usage': {
    title: 'System Usage',
    subtitle: '3-tier LLM chain status, hybrid vector store metrics, and token/cost accounting',
  },
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const meta = ROUTE_META[location.pathname] || ROUTE_META['/']

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <Sidebar mobileOpen={mobileOpen} onCloseMobile={() => setMobileOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          title={meta.title}
          subtitle={meta.subtitle}
          onOpenMobileMenu={() => setMobileOpen(true)}
        />
        <main className="flex-1 pb-10">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/briefs" element={<BriefsPage />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/usage" element={<UsagePage />} />
          </Routes>
        </main>
      </div>

      <SourceDetailDrawer />
    </div>
  )
}
