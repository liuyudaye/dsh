import { useEffect, useMemo, useRef, useState } from 'react'
import { ReactFlowProvider, useReactFlow, type Connection, type Edge, type NodeChange, type XYPosition } from '@xyflow/react'
import {
  AlertCircle, ArrowLeft, Check, ChevronDown, CircleDot, Diamond, History, Layers3, LoaderCircle, PanelLeft,
  PanelRight, Plus, Redo2, Save, StickyNote, Undo2, Workflow, X,
} from 'lucide-react'
import { BoardCanvas, type FlowNode, type Selection } from './BoardCanvas'
import { Changes, Inspector, Outline } from './BoardPanels'
import {
  CanvasRequestError, canvasRequest, kindLabels, noteChanges, useEditor,
  type BoardEdge, type BoardNode, type CanvasConfig, type CanvasDocument, type Graph, type NodeKind,
} from './model'

type NewKind = Exclude<NodeKind, 'overview'>
const newKinds = [
  ['step', Workflow], ['decision', Diamond], ['note', StickyNote], ['module', Layers3],
] as const

function firstModule(graph: Graph) {
  return graph.nodes.find(item => item.kind === 'module') ?? graph.nodes[0]
}

function moduleFor(node?: BoardNode) {
  return node?.kind === 'module' ? node.id : node?.module_id
}

function nearbyNodes(graph: Graph, anchor: BoardNode, width: number) {
  const columns = width < 480 ? 0 : width < 760 ? 400 : 780
  return graph.nodes.filter(node => node.id === anchor.id || (
    node.module_id === anchor.id && Math.abs(node.x - anchor.x) <= columns && Math.abs(node.y - anchor.y) <= 192
  )).map(({ id }) => ({ id }))
}

function newId(prefix: string) {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return `${prefix}-${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}`
}

function changePositions(graph: Graph, positions: Record<string, XYPosition>): Graph {
  let changed = false
  const nodes = graph.nodes.map((node) => {
    const position = positions[node.id]
    if (!position || (node.x === position.x && node.y === position.y)) return node
    changed = true
    return { ...node, ...position }
  })
  return changed ? { ...graph, nodes } : graph
}

export function CanvasApp({ config }: { config: CanvasConfig }) {
  const [document, setDocument] = useState<CanvasDocument>()
  const [failure, setFailure] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    canvasRequest(config, 'load', { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setDocument(result) })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFailure(error instanceof Error ? error.message : '画布暂时无法读取。')
      })
    return () => controller.abort()
  }, [config, attempt])

  if (document) {
    return <ReactFlowProvider><CanvasEditor config={config} initial={document} /></ReactFlowProvider>
  }

  return (
    <main className="pc-load">
      <div className="pc-load-panel">
        {failure
          ? <>
            <AlertCircle size={25} />
            <p role="alert">{failure}</p>
            <div className="pc-load-actions">
              <a className="pc-button pc-quiet" href={config.reportUrl}><ArrowLeft size={15} />返回报告</a>
              <button className="pc-button pc-primary" type="button"
                onClick={() => { setFailure(''); setAttempt(count => count + 1) }}>重试</button>
            </div>
          </>
          : <LoaderCircle className="pc-spinner" size={28} role="status" aria-label="正在打开画布" />}
      </div>
    </main>
  )
}

function CanvasEditor({ config, initial }: { config: CanvasConfig; initial: CanvasDocument }) {
  const [saved, setSaved] = useState(initial)
  const [edit, dispatch] = useEditor(initial.graph)
  const [selection, setSelection] = useState<Selection | undefined>(() => {
    const node = firstModule(initial.graph)
    return node && { type: 'node', id: node.id }
  })
  const [positions, setPositions] = useState<Record<string, XYPosition>>({})
  const [preview, setPreview] = useState<CanvasDocument>()
  const [panel, setPanel] = useState<'edit' | 'history'>('edit')
  const [mobilePanel, setMobilePanel] = useState<'outline' | 'inspect'>()
  const [showAdd, setShowAdd] = useState(false)
  const [job, setJob] = useState<'save' | 'restore' | 'preview' | 'reload'>()
  const [notice, setNotice] = useState<{ message: string; conflict?: boolean }>()
  const [confirm, setConfirm] = useState<{ operation: 'restore' | 'reload'; revision?: number }>()
  const running = useRef(false)
  const boardRef = useRef<HTMLElement>(null)
  const addRef = useRef<HTMLDivElement>(null)
  const { fitView, screenToFlowPosition } = useReactFlow<FlowNode>()
  const dirty = edit.graph !== edit.saved
  const canEdit = !preview && job === undefined
  const currentGraph = preview?.graph ?? edit.graph
  const selectedNode = selection?.type === 'node' ? currentGraph.nodes.find(({ id }) => id === selection.id) : undefined
  const selectedEdge = selection?.type === 'edge' ? currentGraph.edges.find(({ id }) => id === selection.id) : undefined
  const activeModule = moduleFor(selectedNode)
  const initialFocus = useMemo(() => {
    const item = firstModule(initial.graph)
    const canvasWidth = window.innerWidth > 875 ? window.innerWidth - 460 : window.innerWidth
    return item ? nearbyNodes(initial.graph, item, canvasWidth).map(({ id }) => id) : undefined
  }, [initial.graph])
  const comparison = useMemo(() => preview ? noteChanges(preview.graph, saved.graph) : undefined, [saved.graph, preview])

  useEffect(() => {
    if (!dirty) return
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault() }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [dirty])

  useEffect(() => {
    if (!showAdd) return
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !addRef.current?.contains(event.target)) setShowAdd(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [showAdd])

  function focusModule(node: BoardNode) {
    setSelection({ type: 'node', id: node.id })
    setMobilePanel(undefined)
    const width = boardRef.current?.clientWidth ?? window.innerWidth
    void fitView({ nodes: nearbyNodes(currentGraph, node, width), padding: 0.25, maxZoom: 1.04, duration: 280 })
  }

  function focusAll() {
    setSelection(undefined)
    setMobilePanel(undefined)
    void fitView({ padding: 0.22, duration: 280 })
  }

  function select(item?: Selection) {
    setSelection(item)
    if (item) {
      setPanel('edit')
      setMobilePanel('inspect')
    }
  }

  function editGraph(change: (graph: Graph) => Graph, field?: string) {
    if (canEdit && !running.current) dispatch({ type: 'edit', edit: change, field })
  }

  function onNodesChange(changes: NodeChange<FlowNode>[]) {
    const movement: Record<string, XYPosition> = {}
    const keyMovement: Record<string, XYPosition> = {}
    for (const change of changes) {
      if (change.type === 'select' && change.selected) setSelection({ type: 'node', id: change.id })
      if (!canEdit || change.type !== 'position' || !change.position) continue
      if (change.dragging) movement[change.id] = change.position
      else if (change.dragging === undefined) keyMovement[change.id] = change.position
    }
    if (Object.keys(movement).length) setPositions(previous => ({ ...previous, ...movement }))
    if (Object.keys(keyMovement).length) editGraph(graph => changePositions(graph, keyMovement))
  }

  function onMoveNodes(nodes: FlowNode[]) {
    const moved = Object.fromEntries(nodes.map(({ id, position }) => [id, position]))
    editGraph(graph => changePositions(graph, moved))
    setPositions({})
  }

  function addNode(kind: NewKind) {
    if (!canEdit || !boardRef.current) return
    const board = boardRef.current.getBoundingClientRect()
    const position = screenToFlowPosition({ x: board.left + board.width * 0.48, y: board.top + board.height * 0.42 })
    const id = newId('node')
    const newNode: BoardNode = {
      id, kind, title: kindLabels[kind], body: '',
      x: Math.max(-1_000_000, Math.min(1_000_000, Math.round(position.x / 20) * 20)),
      y: Math.max(-1_000_000, Math.min(1_000_000, Math.round(position.y / 20) * 20)),
      ...(kind !== 'module' && activeModule ? { module_id: activeModule } : {}),
    }
    editGraph(graph => ({ ...graph, nodes: [...graph.nodes, newNode] }))
    select({ type: 'node', id })
    setShowAdd(false)
  }

  function editNode(patch: Partial<Pick<BoardNode, 'title' | 'body'>>, field: string) {
    if (!selectedNode) return
    editGraph(graph => ({
      ...graph, nodes: graph.nodes.map(node => node.id === selectedNode.id ? { ...node, ...patch } : node),
    }), `${selectedNode.id}:${field}`)
  }

  function editEdge(patch: Partial<Pick<BoardEdge, 'label' | 'source' | 'target'>>, field?: string) {
    if (!selectedEdge) return
    editGraph(graph => ({
      ...graph, edges: graph.edges.map(edge => edge.id === selectedEdge.id ? { ...edge, ...patch } : edge),
    }), field ? `${selectedEdge.id}:${field}` : undefined)
  }

  function connectNodes(source: string, target: string, detail = false) {
    if (!canEdit || source === target) return
    const kind = detail || currentGraph.nodes.find(({ id }) => id === target)?.kind === 'note' ? 'detail' : 'flow'
    const edge: BoardEdge = { id: newId('edge'), source, target, label: '', kind }
    editGraph(graph => ({ ...graph, edges: [...graph.edges, edge] }))
    setSelection({ type: 'edge', id: edge.id })
  }

  function connect(connection: Connection) {
    connectNodes(connection.source, connection.target, connection.sourceHandle === 'detail-out' || connection.targetHandle === 'detail-in')
  }

  function reconnect(old: Edge, connection: Connection) {
    if (connection.source === connection.target) return
    editGraph(graph => ({
      ...graph,
      edges: graph.edges.map(edge => edge.id !== old.id || (edge.source === connection.source && edge.target === connection.target)
        ? edge : { ...edge, source: connection.source, target: connection.target }),
    }))
  }

  function deleteSelected() {
    if (!selection) return
    editGraph(graph => selection.type === 'edge'
      ? { ...graph, edges: graph.edges.filter(({ id }) => id !== selection.id) }
      : {
        nodes: graph.nodes.filter(({ id }) => id !== selection.id)
          .map(node => node.module_id === selection.id ? { ...node, module_id: undefined } : node),
        edges: graph.edges.filter(({ source, target }) => source !== selection.id && target !== selection.id),
      })
    setSelection(undefined)
  }

  async function run(operation: 'save' | 'restore' | 'preview' | 'reload', revision?: number) {
    if (running.current) return
    if (operation === 'save' && edit.graph.nodes.some(({ title }) => !title.trim())) {
      setNotice({ message: '请给画布上未命名的节点填写标题。' })
      return
    }
    running.current = true
    setNotice(undefined)
    setConfirm(undefined)
    setJob(operation)
    try {
      const received = await canvasRequest(config, operation === 'preview' || operation === 'reload' ? 'load' : operation, {
        baseRevision: saved.revision, graph: edit.graph, revision,
      })
      if (operation === 'preview') {
        setPreview(received)
        setPanel('history')
      } else {
        setSaved(received)
        setPreview(undefined)
        dispatch({
          type: operation === 'save' ? 'acknowledge' : operation === 'restore' ? 'restore' : 'reset',
          graph: received.graph,
        })
        if (operation !== 'save') setSelection(undefined)
      }
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : '操作没有完成，请重试。',
        conflict: error instanceof CanvasRequestError && error.status === 409,
      })
    } finally {
      setJob(undefined)
      running.current = false
    }
  }

  function ask(operation: 'restore' | 'reload', revision?: number) {
    if (dirty) setConfirm({ operation, revision })
    else void run(operation, revision)
  }

  function onShortcuts(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      setShowAdd(false)
      setMobilePanel(undefined)
      setConfirm(undefined)
      return
    }
    if (!canEdit || (event.target instanceof HTMLElement && event.target.closest('input, textarea, select, [contenteditable]'))) return
    if (event.metaKey || event.ctrlKey) {
      const key = event.key.toLowerCase()
      if (key === 'z' || key === 'y') {
        event.preventDefault()
        dispatch({ type: key === 'y' || event.shiftKey ? 'redo' : 'undo' })
      } else if (key === 's') {
        event.preventDefault()
        if (dirty) void run('save')
      }
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      if (selection && !(event.target instanceof HTMLElement && event.target.closest('button, a'))) {
        event.preventDefault()
        deleteSelected()
      }
    }
  }

  return (
    <div className="pc-app" onKeyDown={onShortcuts}>
      <header className="pc-header">
        <div className="pc-title-area">
          <a className="pc-back pc-icon-button" href={config.reportUrl} aria-label="返回报告" title="返回报告"><ArrowLeft size={18} /></a>
          <div className="pc-title">
            <h1>{saved.title}</h1>
            <div className="pc-document-meta">报告第 {saved.report_revision} 版 <span>·</span> 画布 v{saved.revision}</div>
          </div>
        </div>
        <div className="pc-toolbar" role="group" aria-label="画布操作">
          <button type="button" className="pc-icon-button pc-panel-toggle" aria-label="切换目录" title="目录"
            aria-controls="pc-outline" aria-expanded={mobilePanel === 'outline'}
            onClick={() => setMobilePanel(mobilePanel === 'outline' ? undefined : 'outline')}><PanelLeft size={18} /></button>
          <div className="pc-add" ref={addRef}>
            <button type="button" className="pc-button" aria-expanded={showAdd} disabled={!canEdit} onClick={() => setShowAdd(!showAdd)}>
              <Plus size={16} />添加<ChevronDown size={13} />
            </button>
            {showAdd && <div className="pc-add-menu">
              {newKinds.map(([kind, Icon]) => (
                <button type="button" key={kind} onClick={() => addNode(kind)}><Icon size={16} />{kindLabels[kind]}</button>
              ))}
            </div>}
          </div>
          <div className="pc-undo-actions">
            <button type="button" className="pc-icon-button" title="撤销" aria-label="撤销"
              disabled={!canEdit || !edit.past.length} onClick={() => dispatch({ type: 'undo' })}><Undo2 size={18} /></button>
            <button type="button" className="pc-icon-button" title="重做" aria-label="重做"
              disabled={!canEdit || !edit.future.length} onClick={() => dispatch({ type: 'redo' })}><Redo2 size={18} /></button>
          </div>
          <button type="button" className="pc-icon-button pc-panel-toggle" aria-label="切换检查器" title="检查器"
            aria-controls="pc-inspector" aria-expanded={mobilePanel === 'inspect'}
            onClick={() => { setPanel('edit'); setMobilePanel(mobilePanel === 'inspect' ? undefined : 'inspect') }}><PanelRight size={18} /></button>
          <button type="button" className="pc-button pc-history-toggle" onClick={() => { setPanel('history'); setMobilePanel('inspect') }}>
            <History size={16} /><span>版本记录</span>
          </button>
          <button type="button" className="pc-button pc-primary" disabled={!canEdit || !dirty} onClick={() => void run('save')}>
            {job === 'save' ? <LoaderCircle size={16} className="pc-spinner" /> : <Save size={16} />}保存版本
          </button>
        </div>
      </header>
      <div className="pc-layout" data-mobile-panel={mobilePanel ?? ''}>
        <Outline
          config={config} graph={currentGraph} activeModule={activeModule}
          close={() => setMobilePanel(undefined)} focusAll={focusAll} focusModule={focusModule}
        />
        <BoardCanvas
          graph={currentGraph} positions={positions} selection={selection} initialFocus={initialFocus} editable={canEdit}
          select={select} onNodesChange={onNodesChange} onMoveNodes={onMoveNodes} connect={connect} reconnect={reconnect}
          boardRef={boardRef}
        >
          <div className="pc-stage-meta">
            <span><Workflow size={15} />流程画布</span>
            <span className="pc-stage-status" data-dirty={dirty}>
              {dirty ? <CircleDot size={12} /> : <Check size={13} />}{dirty ? '有未保存的改动' : '已同步'}
            </span>
          </div>
          {(preview || notice || confirm) && (
            <div className="pc-callout-stack">
              {preview && <div className="pc-callout pc-preview-notice">
                <span><History size={16} />正在查看 v{preview.revision}</span>
                {comparison && <Changes counts={comparison} />}
                <button className="pc-button pc-quiet" type="button" disabled={job !== undefined}
                  onClick={() => setPreview(undefined)}>返回当前</button>
              </div>}
              {notice && <div className="pc-callout pc-error" role="alert">
                <span><AlertCircle size={17} />{notice.message}</span>
                {notice.conflict && <button className="pc-button pc-quiet" type="button" onClick={() => ask('reload')}>重新加载</button>}
                <button className="pc-icon-button" type="button" onClick={() => setNotice(undefined)} aria-label="关闭提示" title="关闭提示"><X size={15} /></button>
              </div>}
              {confirm && <div className="pc-callout pc-confirm">
                <span>存在未保存的改动。{confirm.operation === 'restore' ? '回退' : '重新加载'}后会离开当前草稿。</span>
                <button className="pc-button pc-quiet" type="button" onClick={() => setConfirm(undefined)}>取消</button>
                <button className="pc-button" type="button" onClick={() => void run(confirm.operation, confirm.revision)}>
                  确认{confirm.operation === 'restore' ? '回退' : '加载'}
                </button>
              </div>}
            </div>
          )}
        </BoardCanvas>
        <Inspector
          config={config} graph={currentGraph} node={selectedNode} edge={selectedEdge} panel={panel} selectPanel={setPanel}
          close={() => setMobilePanel(undefined)} readonly={!canEdit} pending={job !== undefined}
          history={saved.history} revision={saved.revision} previewId={preview?.revision}
          onPreview={revision => void run('preview', revision)} onRestore={revision => ask('restore', revision)}
          onReturn={() => setPreview(undefined)} onEditNode={editNode} onEditEdge={editEdge}
          onConnectNode={(target) => { if (selectedNode) connectNodes(selectedNode.id, target) }}
          onDelete={deleteSelected} endField={() => dispatch({ type: 'end-field' })}
        />
      </div>
    </div>
  )
}
