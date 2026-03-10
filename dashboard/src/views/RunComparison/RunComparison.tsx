import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import { useComparisonStore } from '../../store/comparisonStore'
import { RunSelector } from './RunSelector'
import { StatsDiff } from './StatsDiff'
import { TimelineDiff } from './TimelineDiff'

export function RunComparison() {
  const [searchParams] = useSearchParams()
  const leftId = searchParams.get('left') ?? ''
  const rightId = searchParams.get('right') ?? ''
  const { leftRun, rightRun, loading, error, loadComparison } = useComparisonStore()

  useEffect(() => {
    if (leftId && rightId) {
      loadComparison(leftId, rightId)
    }
  }, [leftId, rightId, loadComparison])

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {/* Title */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-ctp-text">Run Comparison</h2>
          <p className="text-ctp-subtext0 text-sm">Side-by-side analysis of two execution traces</p>
        </div>
      </div>

      <RunSelector leftId={leftId} rightId={rightId} leftRun={leftRun} rightRun={rightRun} />

      {loading && (
        <div className="space-y-6">
          <div className="h-48 bg-ctp-surface0 rounded-xl animate-pulse" />
          <div className="grid grid-cols-2 gap-8">
            <div className="h-64 bg-ctp-surface0 rounded-xl animate-pulse" />
            <div className="h-64 bg-ctp-surface0 rounded-xl animate-pulse" />
          </div>
        </div>
      )}

      {error && <div className="text-ctp-red text-sm">{error}</div>}

      {leftRun && rightRun && (
        <>
          <StatsDiff left={leftRun} right={rightRun} />
          <TimelineDiff left={leftRun} right={rightRun} />
        </>
      )}

      {!leftId && !rightId && !loading && (
        <div className="flex flex-col items-center justify-center py-16 text-ctp-subtext0 gap-3">
          <span className="material-symbols-outlined text-6xl text-ctp-surface1">compare_arrows</span>
          <p className="text-sm">Enter two run IDs above to compare execution traces.</p>
        </div>
      )}
    </div>
  )
}
