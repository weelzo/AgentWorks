import { useEffect } from 'react'
import { NavLink, useSearchParams } from 'react-router'
import { useUIStore } from '../store/uiStore'
import { useRunStore } from '../store/runStore'
import { Badge } from './Badge'
import { formatCost } from '../utils/format'
import type { AgentState } from '../api/types'

const NAV_ITEMS = [
  { key: 'runs', label: 'Runs', path: '/runs', shortcut: '1', icon: 'list_alt', needsRun: false },
  { key: 'timeline', label: 'Timeline', path: '/timeline', shortcut: '2', icon: 'timeline', needsRun: true },
  { key: 'states', label: 'States', path: '/states', shortcut: '3', icon: 'hub', needsRun: true },
  { key: 'cost', label: 'Cost', path: '/cost', shortcut: '4', icon: 'payments', needsRun: true },
  { key: 'conversation', label: 'Chat', path: '/conversation', shortcut: '5', icon: 'chat_bubble', needsRun: true },
  { key: 'compare', label: 'Compare', path: '/compare', shortcut: '6', icon: 'compare_arrows', needsRun: false },
] as const

export function Sidebar() {
  const { sidebarOpen, setView } = useUIStore()
  const { currentRun } = useRunStore()
  const [searchParams] = useSearchParams()
  const runParam = searchParams.get('run') ?? currentRun?.run_id ?? null

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (document.activeElement?.tagName === 'INPUT') return
      const idx = parseInt(e.key) - 1
      if (idx >= 0 && idx < NAV_ITEMS.length) {
        setView(NAV_ITEMS[idx].key)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setView])

  return (
    <aside className={`${sidebarOpen ? 'w-56' : 'w-16'} border-r border-ctp-surface0 bg-ctp-mantle flex flex-col justify-between shrink-0 transition-all duration-200`}>
      <div className="flex flex-col gap-1 p-3">
        {/* Logo */}
        <div className="flex items-center gap-3 px-3 py-4 mb-4">
          <div className="size-8 bg-ctp-blue rounded-lg flex items-center justify-center text-ctp-crust shrink-0">
            <span className="material-symbols-outlined font-bold text-lg">bolt</span>
          </div>
          {sidebarOpen && (
            <div>
              <h1 className="text-white text-lg font-bold leading-none">AgentWorks</h1>
              <p className="text-ctp-subtext0 text-xs mt-1">v1.0.0</p>
            </div>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex flex-col gap-1">
          {NAV_ITEMS.map((item) => {
            const to = runParam && item.needsRun
              ? `${item.path}?run=${runParam}`
              : item.path
            return (
              <NavLink
                key={item.key}
                to={to}
                onClick={() => setView(item.key)}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                    isActive
                      ? 'bg-ctp-blue/10 text-ctp-blue'
                      : 'text-ctp-subtext0 hover:bg-ctp-surface0 hover:text-white'
                  }`
                }
              >
                <span className="material-symbols-outlined">{item.icon}</span>
                {sidebarOpen && (
                  <span className="text-sm font-medium">{item.label}</span>
                )}
              </NavLink>
            )
          })}
        </nav>
      </div>

      {/* Bottom mini-stats */}
      <div className="p-3 border-t border-ctp-surface0">
        {currentRun && (
          <div className="flex flex-col gap-3 py-2">
            <div className="flex items-center gap-3 px-3">
              <Badge state={currentRun.state as AgentState} />
            </div>
            <div className="flex items-center gap-3 px-3 text-ctp-subtext0">
              <span className="material-symbols-outlined text-sm">sync</span>
              {sidebarOpen && <span className="text-xs font-medium">Iter: {currentRun.iteration_count}</span>}
            </div>
            <div className="flex items-center gap-3 px-3 text-ctp-subtext0">
              <span className="material-symbols-outlined text-sm">monetization_on</span>
              {sidebarOpen && <span className="text-xs font-medium">Cost: {formatCost(currentRun.total_cost_usd)}</span>}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}
