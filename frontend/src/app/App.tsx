import { BrowserRouter } from 'react-router-dom'
import { AppProvider } from '../hooks/useAppContext'
import { AppShell } from './router'

export function App() {
  return (
    <BrowserRouter>
      <AppProvider>
        <AppShell />
      </AppProvider>
    </BrowserRouter>
  )
}

export default App
