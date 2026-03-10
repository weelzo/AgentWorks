import { create } from 'zustand'
import type { RunData } from '../api/types'
import { fetchRun } from '../api/runs'

interface RunStore {
  currentRun: RunData | null
  loading: boolean
  error: string | null
  pollInterval: number | null

  loadRun: (runId: string) => Promise<void>
  startPolling: (runId: string, intervalMs?: number) => void
  stopPolling: () => void
  clearRun: () => void
}

export const useRunStore = create<RunStore>((set, get) => ({
  currentRun: null,
  loading: false,
  error: null,
  pollInterval: null,

  loadRun: async (runId: string) => {
    set({ loading: true, error: null })
    try {
      const run = await fetchRun(runId)
      set({ currentRun: run, loading: false })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to load run', loading: false })
    }
  },

  startPolling: (runId: string, intervalMs = 2000) => {
    get().stopPolling()
    const id = window.setInterval(() => {
      const { currentRun } = get()
      if (currentRun?.state === 'completed' || currentRun?.state === 'failed') {
        get().stopPolling()
        return
      }
      get().loadRun(runId)
    }, intervalMs)
    set({ pollInterval: id })
  },

  stopPolling: () => {
    const { pollInterval } = get()
    if (pollInterval !== null) {
      clearInterval(pollInterval)
      set({ pollInterval: null })
    }
  },

  clearRun: () => {
    get().stopPolling()
    set({ currentRun: null, loading: false, error: null })
  },
}))
