import React from 'react'

export function PageContainer({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 sm:px-6">{children}</div>
}
