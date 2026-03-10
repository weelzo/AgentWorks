import { create } from 'zustand'
import type { RunListItem } from '../api/types'

type View = 'runs' | 'timeline' | 'states' | 'cost' | 'conversation' | 'compare'

interface UIStore {
  activeView: View
  theme: 'dark' | 'light'
  sidebarOpen: boolean
  recentRuns: RunListItem[]

  setView: (view: View) => void
  toggleTheme: () => void
  toggleSidebar: () => void
  setRecentRuns: (runs: RunListItem[]) => void
}

export const useUIStore = create<UIStore>((set, get) => ({
  activeView: 'runs',
  theme: 'dark',
  sidebarOpen: true,
  recentRuns: [],

  setView: (view) => set({ activeView: view }),

  toggleTheme: () => {
    const next = get().theme === 'dark' ? 'light' : 'dark'
    document.documentElement.classList.remove('dark', 'light')
    document.documentElement.classList.add(next)
    set({ theme: next })
  },

  toggleSidebar: () => set({ sidebarOpen: !get().sidebarOpen }),

  setRecentRuns: (runs) => set({ recentRuns: runs }),
}))
