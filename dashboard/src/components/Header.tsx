import { useState, useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useUIStore } from '../store/uiStore'
import { useRunStore } from '../store/runStore'
import { fetchHealth } from '../api/health'

export function Header() {
  const [searchParams] = useSearchParams()
  const { currentRun } = useRunStore()
  const [runInput, setRunInput] = useState(searchParams.get('run') ?? currentRun?.run_id ?? '')
  const [healthOk, setHealthOk] = useState<boolean | null>(null)
  const navigate = useNavigate()
  const { theme, toggleTheme, activeView, toggleSidebar } = useUIStore()
  const inputRef = useRef<HTMLInputElement>(null)

  // Sync input when URL run param changes
  useEffect(() => {
    const urlRun = searchParams.get('run')
    if (urlRun && urlRun !== runInput) {
      setRunInput(urlRun)
    }
  }, [searchParams])

  useEffect(() => {
    fetchHealth().then(() => setHealthOk(true)).catch(() => setHealthOk(false))
    const id = setInterval(() => {
      fetchHealth().then(() => setHealthOk(true)).catch(() => setHealthOk(false))
    }, 15000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT') {
        e.preventDefault()
        inputRef.current?.focus()
      }
      if (e.key === '[' && document.activeElement?.tagName !== 'INPUT') {
        toggleSidebar()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [toggleSidebar])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!runInput.trim()) return
    if (activeView === 'compare') {
      navigate(`/compare?left=${runInput}`)
    } else {
      navigate(`/${activeView}?run=${runInput}`)
    }
  }

  return (
    <header className="h-14 border-b border-ctp-surface0 bg-ctp-mantle flex items-center justify-between px-6 shrink-0">
      <div className="flex items-center gap-4">
        <button onClick={toggleSidebar} className="text-ctp-subtext0 hover:text-white transition-colors">
          <span className="material-symbols-outlined">menu</span>
        </button>
        <div className="flex items-center gap-2">
          <span className="text-ctp-subtext0 text-sm font-mono tracking-tight">run_</span>
          <form onSubmit={handleSubmit} className="max-w-[400px] w-full relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 material-symbols-outlined text-ctp-subtext0 text-lg">search</span>
            <input
              ref={inputRef}
              type="text"
              value={runInput}
              onChange={(e) => setRunInput(e.target.value)}
              placeholder="Search Run ID..."
              className="w-full bg-ctp-surface0 border-none rounded-lg pl-10 pr-4 py-1.5 text-sm font-mono text-ctp-blue focus:ring-1 focus:ring-ctp-blue/50 focus:outline-none placeholder:text-ctp-surface2"
            />
          </form>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {/* Health indicator */}
        <div className="flex items-center gap-2 px-3 py-1 bg-ctp-surface0 rounded-full">
          <div className={`size-2 rounded-full ${
            healthOk === true ? 'bg-ctp-green animate-pulse' : healthOk === false ? 'bg-ctp-red' : 'bg-ctp-overlay0'
          }`} />
          <span className="text-[10px] font-bold text-ctp-subtext0 uppercase tracking-widest">
            {healthOk === true ? 'Healthy' : healthOk === false ? 'Offline' : 'Checking'}
          </span>
        </div>

        <div className="h-6 w-px bg-ctp-surface0" />

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          className="text-ctp-subtext0 hover:text-white transition-colors"
          title="Toggle theme"
        >
          <span className="material-symbols-outlined">
            {theme === 'dark' ? 'dark_mode' : 'light_mode'}
          </span>
        </button>
      </div>
    </header>
  )
}
