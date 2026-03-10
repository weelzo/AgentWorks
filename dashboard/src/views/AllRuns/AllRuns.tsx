import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { deleteRun, listRuns } from '../../api/runs'
import type { AgentState, ErrorSummary, RunListItem } from '../../api/types'
import { Badge } from '../../components/Badge'
import { StatCard } from '../../components/StatCard'
import { formatCost, formatTokens } from '../../utils/format'

function ErrorIcons({ summary }: { summary?: ErrorSummary }) {
  if (!summary || (summary.retryable === 0 && summary.recoverable === 0 && summary.fatal === 0)) {
    return <span className="text-ctp-overlay0 text-xs">—</span>
  }
  return (
    <div className="flex items-center justify-center gap-1.5">
      {summary.fatal > 0 && (
        <span className="inline-flex items-center gap-0.5 text-ctp-red" title={`${summary.fatal} fatal`}>
          <span className="material-symbols-outlined text-sm">dangerous</span>
          <span className="text-[10px] font-bold">{summary.fatal}</span>
        </span>
      )}
      {summary.recoverable > 0 && (
        <span className="inline-flex items-center gap-0.5 text-ctp-yellow" title={`${summary.recoverable} recoverable`}>
          <span className="material-symbols-outlined text-sm">healing</span>
          <span className="text-[10px] font-bold">{summary.recoverable}</span>
        </span>
      )}
      {summary.retryable > 0 && (
        <span className="inline-flex items-center gap-0.5 text-ctp-peach" title={`${summary.retryable} retried`}>
          <span className="material-symbols-outlined text-sm">autorenew</span>
          <span className="text-[10px] font-bold">{summary.retryable}</span>
        </span>
      )}
    </div>
  )
}

const PAGE_SIZE = 25

export function AllRuns() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(true)
  const [filterAgent, setFilterAgent] = useState('')
  const [filterTeam, setFilterTeam] = useState('')
  const [filterState, setFilterState] = useState<string>('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async (reset = false) => {
    setLoading(true)
    setError(null)
    const newOffset = reset ? 0 : offset
    try {
      const result = await listRuns({
        limit: PAGE_SIZE,
        offset: newOffset,
        ...(filterAgent && { agent_id: filterAgent }),
        ...(filterTeam && { team_id: filterTeam }),
      })
      setRuns(reset ? result : [...runs, ...result])
      setHasMore(result.length === PAGE_SIZE)
      if (reset) setOffset(PAGE_SIZE)
      else setOffset(newOffset + PAGE_SIZE)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load runs')
    } finally {
      setLoading(false)
    }
  }, [offset, filterAgent, filterTeam, runs])

  useEffect(() => {
    load(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterAgent, filterTeam])

  const filteredRuns = filterState
    ? runs.filter((r) => r.state === filterState)
    : runs

  const totalCost = runs.reduce((sum, r) => sum + r.total_cost_usd, 0)
  const totalTokens = runs.reduce((sum, r) => sum + r.total_tokens, 0)
  const completedCount = runs.filter((r) => r.state === 'completed').length
  const failedCount = runs.filter((r) => r.state === 'failed').length

  // Selection handlers
  const toggleSelect = (runId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(runId)) next.delete(runId)
      else next.add(runId)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selected.size === filteredRuns.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(filteredRuns.map((r) => r.run_id)))
    }
  }

  const clearSelection = () => setSelected(new Set())

  // Actions
  const openRun = (runId: string) => {
    navigate(`/timeline?run=${runId}`)
  }

  const handleCompare = () => {
    const ids = Array.from(selected)
    if (ids.length === 2) {
      navigate(`/compare?left=${ids[0]}&right=${ids[1]}`)
    }
  }

  const handleDelete = async () => {
    if (selected.size === 0) return
    const count = selected.size
    if (!window.confirm(`Delete ${count} run${count > 1 ? 's' : ''}? This cannot be undone.`)) {
      return
    }
    setDeleting(true)
    try {
      await Promise.all(Array.from(selected).map((id) => deleteRun(id)))
      setRuns((prev) => prev.filter((r) => !selected.has(r.run_id)))
      setSelected(new Set())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete runs')
    } finally {
      setDeleting(false)
    }
  }

  const handleViewCost = () => {
    const ids = Array.from(selected)
    if (ids.length === 1) {
      navigate(`/cost?run=${ids[0]}`)
    }
  }

  const allSelected = filteredRuns.length > 0 && selected.size === filteredRuns.length

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold text-white">All Runs</h2>
        <p className="text-ctp-subtext0 text-sm mt-1">
          Browse and filter agent runs across all teams
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Total Runs" value={String(runs.length)} icon="play_circle" />
        <StatCard
          label="Total Cost"
          value={formatCost(totalCost)}
          icon="payments"
        />
        <StatCard
          label="Total Tokens"
          value={formatTokens(totalTokens)}
          icon="token"
        />
        <StatCard
          label="Success Rate"
          value={
            runs.length > 0
              ? `${Math.round((completedCount / (completedCount + failedCount || 1)) * 100)}%`
              : '-'
          }
          sub={`${completedCount} completed, ${failedCount} failed`}
          icon="monitoring"
        />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-ctp-subtext0 text-sm">filter_alt</span>
          <span className="text-xs text-ctp-subtext0 uppercase font-bold tracking-wider">Filters</span>
        </div>
        <input
          type="text"
          placeholder="Agent ID"
          value={filterAgent}
          onChange={(e) => setFilterAgent(e.target.value)}
          className="bg-ctp-surface0 text-white text-sm rounded-lg px-3 py-1.5 border border-ctp-surface1 focus:border-ctp-blue focus:outline-none w-40 placeholder:text-ctp-overlay0"
        />
        <input
          type="text"
          placeholder="Team ID"
          value={filterTeam}
          onChange={(e) => setFilterTeam(e.target.value)}
          className="bg-ctp-surface0 text-white text-sm rounded-lg px-3 py-1.5 border border-ctp-surface1 focus:border-ctp-blue focus:outline-none w-40 placeholder:text-ctp-overlay0"
        />
        <select
          value={filterState}
          onChange={(e) => setFilterState(e.target.value)}
          className="bg-ctp-surface0 text-white text-sm rounded-lg px-3 py-1.5 border border-ctp-surface1 focus:border-ctp-blue focus:outline-none cursor-pointer"
        >
          <option value="">All States</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="suspended">Suspended</option>
          <option value="planning">Planning</option>
          <option value="executing_tool">Executing</option>
          <option value="awaiting_llm">Awaiting LLM</option>
          <option value="reflecting">Reflecting</option>
          <option value="idle">Idle</option>
        </select>
        {(filterAgent || filterTeam || filterState) && (
          <button
            onClick={() => {
              setFilterAgent('')
              setFilterTeam('')
              setFilterState('')
            }}
            className="text-xs text-ctp-red hover:text-ctp-red/80 transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Selection action bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 bg-ctp-blue/10 border border-ctp-blue/30 rounded-xl px-4 py-3">
          <span className="text-sm text-ctp-blue font-bold">
            {selected.size} selected
          </span>
          <div className="h-4 w-px bg-ctp-surface1" />
          <div className="flex items-center gap-2">
            {selected.size === 1 && (
              <>
                <button
                  onClick={() => openRun(Array.from(selected)[0])}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-ctp-blue/20 text-ctp-blue hover:bg-ctp-blue/30 transition-colors"
                >
                  <span className="material-symbols-outlined text-sm">visibility</span>
                  View
                </button>
                <button
                  onClick={handleViewCost}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-ctp-green/20 text-ctp-green hover:bg-ctp-green/30 transition-colors"
                >
                  <span className="material-symbols-outlined text-sm">payments</span>
                  Cost
                </button>
              </>
            )}
            {selected.size === 2 && (
              <button
                onClick={handleCompare}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-ctp-mauve/20 text-ctp-mauve hover:bg-ctp-mauve/30 transition-colors"
              >
                <span className="material-symbols-outlined text-sm">compare_arrows</span>
                Compare
              </button>
            )}
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-ctp-red/20 text-ctp-red hover:bg-ctp-red/30 transition-colors disabled:opacity-50"
            >
              <span className="material-symbols-outlined text-sm">
                {deleting ? 'hourglass_empty' : 'delete'}
              </span>
              {deleting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
          <div className="ml-auto">
            <button
              onClick={clearSelection}
              className="text-xs text-ctp-subtext0 hover:text-white transition-colors"
            >
              Clear selection
            </button>
          </div>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="bg-ctp-red/10 border border-ctp-red/30 text-ctp-red rounded-lg p-4 text-sm">
          <span className="material-symbols-outlined text-sm align-middle mr-2">error</span>
          {error}
        </div>
      )}

      {/* Table */}
      <div className="bg-ctp-mantle border border-ctp-surface0 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ctp-surface0 text-ctp-subtext0">
              <th className="w-10 px-4 py-3">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="rounded border-ctp-surface1 bg-ctp-surface0 text-ctp-blue focus:ring-ctp-blue/50 cursor-pointer"
                />
              </th>
              <th className="text-left px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Run ID</th>
              <th className="text-left px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Agent</th>
              <th className="text-left px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Team</th>
              <th className="text-left px-4 py-3 font-bold text-[10px] uppercase tracking-wider">State</th>
              <th className="text-center px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Errors</th>
              <th className="text-right px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Iterations</th>
              <th className="text-right px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Tokens</th>
              <th className="text-right px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Cost</th>
              <th className="text-left px-4 py-3 font-bold text-[10px] uppercase tracking-wider">Created</th>
            </tr>
          </thead>
          <tbody>
            {filteredRuns.map((run) => {
              const isSelected = selected.has(run.run_id)
              return (
                <tr
                  key={run.run_id}
                  className={`border-b border-ctp-surface0/50 hover:bg-ctp-surface0/40 transition-colors ${
                    isSelected ? 'bg-ctp-blue/5' : ''
                  }`}
                >
                  <td className="w-10 px-4 py-3">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelect(run.run_id)}
                      className="rounded border-ctp-surface1 bg-ctp-surface0 text-ctp-blue focus:ring-ctp-blue/50 cursor-pointer"
                    />
                  </td>
                  <td
                    className="px-4 py-3 cursor-pointer"
                    onClick={() => openRun(run.run_id)}
                  >
                    <span className="text-ctp-blue font-mono text-xs hover:underline">
                      {run.run_id.slice(0, 8)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-white">{run.agent_id}</td>
                  <td className="px-4 py-3 text-ctp-subtext0">{run.team_id}</td>
                  <td className="px-4 py-3">
                    <Badge state={run.state as AgentState} />
                  </td>
                  <td className="px-4 py-3">
                    <ErrorIcons summary={run.error_summary} />
                  </td>
                  <td className="px-4 py-3 text-right text-white tabular-nums">{run.iteration_count}</td>
                  <td className="px-4 py-3 text-right text-ctp-subtext0 tabular-nums">{formatTokens(run.total_tokens)}</td>
                  <td className="px-4 py-3 text-right text-white tabular-nums">{formatCost(run.total_cost_usd)}</td>
                  <td className="px-4 py-3 text-ctp-subtext0 text-xs">
                    {run.created_at
                      ? new Date(run.created_at).toLocaleString('en-US', {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                          hour12: false,
                        })
                      : '-'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {/* Empty state */}
        {!loading && filteredRuns.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-ctp-subtext0">
            <span className="material-symbols-outlined text-4xl mb-3 opacity-30">inbox</span>
            <p className="text-sm">No runs found</p>
            <p className="text-xs mt-1 opacity-60">
              {filterAgent || filterTeam || filterState
                ? 'Try adjusting your filters'
                : 'Start a run via the API to see it here'}
            </p>
          </div>
        )}

        {/* Loading skeleton */}
        {loading && runs.length === 0 && (
          <div className="p-4 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-10 bg-ctp-surface0 rounded animate-pulse" />
            ))}
          </div>
        )}

        {/* Load more */}
        {hasMore && !loading && filteredRuns.length > 0 && (
          <div className="p-4 text-center">
            <button
              onClick={() => load()}
              className="text-sm text-ctp-blue hover:text-ctp-blue/80 transition-colors font-medium"
            >
              Load more runs
            </button>
          </div>
        )}

        {loading && runs.length > 0 && (
          <div className="p-4 text-center">
            <span className="text-xs text-ctp-subtext0">Loading...</span>
          </div>
        )}
      </div>
    </div>
  )
}
