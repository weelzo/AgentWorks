import { apiFetch } from './client'
import type { RunData, RunListItem } from './types'

export async function fetchRun(runId: string): Promise<RunData> {
  return apiFetch<RunData>(`/runs/${runId}`)
}

export async function listRuns(params?: {
  agent_id?: string
  team_id?: string
  limit?: number
  offset?: number
}): Promise<RunListItem[]> {
  const searchParams = new URLSearchParams()
  if (params?.agent_id) searchParams.set('agent_id', params.agent_id)
  if (params?.team_id) searchParams.set('team_id', params.team_id)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  if (params?.offset) searchParams.set('offset', String(params.offset))
  const qs = searchParams.toString()
  return apiFetch<RunListItem[]>(`/runs${qs ? `?${qs}` : ''}`)
}

export async function deleteRun(runId: string): Promise<void> {
  await fetch(`/api/v1/runs/${runId}`, { method: 'DELETE' })
}
