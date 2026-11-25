import { useReducer } from 'react'

export type NodeKind = 'overview' | 'module' | 'step' | 'decision' | 'note'

export interface BoardNode {
  id: string
  kind: NodeKind
  x: number
  y: number
  title: string
  body: string
  source_line?: number
  module_id?: string
}

export interface BoardEdge {
  id: string
  source: string
  target: string
  label: string
  kind?: 'flow' | 'detail'
}

export interface Graph {
  nodes: BoardNode[]
  edges: BoardEdge[]
}

export interface VersionEntry {
  id: number
  created_at: string | null
  label: string
}

export interface CanvasDocument {
  title: string
  report_revision: number
  revision: number
  graph: Graph
  history: VersionEntry[]
}

export interface CanvasConfig {
  reportId: string
  csrf: string
  reportUrl: string
  compareUrl: string
  sourceCompareUrl: string
  markdownUrl?: string
  xmindUrl?: string
}

export const kindLabels: Record<NodeKind, string> = {
  overview: '报告概览',
  module: '业务模块',
  step: '流程步骤',
  decision: '判断分支',
  note: '产品说明',
}

export class CanvasRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

export async function canvasRequest(
  config: CanvasConfig,
  operation: 'load' | 'save' | 'restore',
  options: { revision?: number; baseRevision?: number; graph?: Graph; signal?: AbortSignal } = {},
): Promise<CanvasDocument> {
  const path = `/api/versions/${encodeURIComponent(config.reportId)}/canvas`
  const query = options.revision === undefined ? '' : `?revision=${options.revision}`
  const url = operation === 'restore' ? `${path}/restore` : `${path}${operation === 'load' ? query : ''}`
  const payload = operation === 'save'
    ? { base_revision: options.baseRevision, graph: options.graph }
    : { base_revision: options.baseRevision, revision: options.revision }
  const response = await fetch(url, {
    method: operation === 'load' ? 'GET' : 'POST',
    headers: operation === 'load'
      ? { Accept: 'application/json' }
      : { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': config.csrf },
    signal: options.signal,
    ...(operation === 'load' ? {} : { body: JSON.stringify(payload) }),
  })
  const result = await response.json().catch(() => null)
  if (!response.ok || !result) {
    const message = typeof result?.message === 'string' ? result.message : '画布暂时无法读取，请稍后重试。'
    throw new CanvasRequestError(message, response.status)
  }
  return result as CanvasDocument
}

type Edit = (graph: Graph) => Graph

interface EditorState {
  graph: Graph
  saved: Graph
  past: Graph[]
  future: Graph[]
  field?: string
}

type Action =
  | { type: 'edit'; edit: Edit; field?: string }
  | { type: 'acknowledge'; graph: Graph }
  | { type: 'reset' | 'restore'; graph: Graph }
  | { type: 'undo' | 'redo' | 'end-field' }

const initialState = (graph: Graph): EditorState => ({ graph, saved: graph, past: [], future: [] })

function editor(state: EditorState, action: Action): EditorState {
  switch (action.type) {
    case 'edit': {
      const graph = action.edit(state.graph)
      if (graph === state.graph) return state
      const merge = action.field !== undefined && action.field === state.field
      return {
        ...state,
        graph,
        past: merge ? state.past : [...state.past.slice(-79), state.graph],
        future: [],
        field: action.field,
      }
    }
    case 'acknowledge':
      return { ...state, graph: action.graph, saved: action.graph, field: undefined }
    case 'reset':
      return initialState(action.graph)
    case 'restore':
      return { ...initialState(action.graph), past: [...state.past.slice(-79), state.graph] }
    case 'undo': {
      const graph = state.past.at(-1)
      if (graph === undefined) return state
      return { ...state, graph, past: state.past.slice(0, -1), future: [...state.future, state.graph], field: undefined }
    }
    case 'redo': {
      const graph = state.future.at(-1)
      if (graph === undefined) return state
      return { ...state, graph, past: [...state.past, state.graph], future: state.future.slice(0, -1), field: undefined }
    }
    case 'end-field':
      return state.field === undefined ? state : { ...state, field: undefined }
  }
}

export function useEditor(initial: Graph) {
  return useReducer(editor, initial, initialState)
}

export function noteChanges(before: Graph, after: Graph) {
  const stats = { added: 0, removed: 0, changed: 0 }
  function count<T extends { id: string }>(oldItems: T[], newItems: T[]) {
    const old = new Map(oldItems.map(item => [item.id, item]))
    for (const item of newItems) {
      const previous = old.get(item.id)
      if (!previous) stats.added++
      else if (JSON.stringify(previous) !== JSON.stringify(item)) stats.changed++
      old.delete(item.id)
    }
    stats.removed += old.size
  }
  count(before.nodes, after.nodes)
  count(before.edges, after.edges)
  return stats
}

export function sourceLink(path: string, line: number) {
  if (!path) return undefined
  const url = new URL(path, window.location.href)
  url.searchParams.set('full', '1')
  url.hash = `line-left-${line}`
  return url.href
}
