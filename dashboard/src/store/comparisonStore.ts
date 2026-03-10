import { create } from 'zustand'
import type { RunData } from '../api/types'
import { fetchRun } from '../api/runs'

interface ComparisonStore {
  leftRun: RunData | null
  rightRun: RunData | null
  loading: boolean
  error: string | null

  loadComparison: (leftId: string, rightId: string) => Promise<void>
  clearComparison: () => void
}

export const useComparisonStore = create<ComparisonStore>((set) => ({
  leftRun: null,
  rightRun: null,
  loading: false,
  error: null,

  loadComparison: async (leftId: string, rightId: string) => {
    set({ loading: true, error: null })
    try {
      const [left, right] = await Promise.all([fetchRun(leftId), fetchRun(rightId)])
      set({ leftRun: left, rightRun: right, loading: false })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to load runs', loading: false })
    }
  },

  clearComparison: () => {
    set({ leftRun: null, rightRun: null, error: null })
  },
}))
