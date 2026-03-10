import type { StateTransition } from '../api/types'

export interface DivergenceResult {
  divergenceIndex: number | null
  leftOnly: StateTransition[]
  rightOnly: StateTransition[]
  common: StateTransition[]
}

/**
 * Walk two state_history arrays in parallel.
 * First index where to_state or trigger differs = divergence point.
 */
export function findDivergence(
  left: StateTransition[],
  right: StateTransition[],
): DivergenceResult {
  const minLen = Math.min(left.length, right.length)
  let divergenceIndex: number | null = null

  for (let i = 0; i < minLen; i++) {
    if (left[i].to !== right[i].to || left[i].trigger !== right[i].trigger) {
      divergenceIndex = i
      break
    }
  }

  // If no mismatch found but lengths differ, divergence is at the shorter array's end
  if (divergenceIndex === null && left.length !== right.length) {
    divergenceIndex = minLen
  }

  const splitAt = divergenceIndex ?? minLen
  return {
    divergenceIndex,
    common: left.slice(0, splitAt),
    leftOnly: left.slice(splitAt),
    rightOnly: right.slice(splitAt),
  }
}
