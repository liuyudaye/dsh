import { useMemo, useState } from 'react'
import {
  Background, BackgroundVariant, Handle, MarkerType, MiniMap, Position, ReactFlow, useReactFlow, useStore,
  type Connection, type Edge, type Node, type NodeChange, type NodeProps, type XYPosition,
} from '@xyflow/react'
import { AlignLeft, CircleDot, Diamond, Focus, Layers3, Minus, Plus, StickyNote, Workflow } from 'lucide-react'
import { kindLabels, type BoardEdge, type BoardNode, type Graph, type NodeKind } from './model'

type NodeData = { item: BoardNode }
export type FlowNode = Node<NodeData, 'card'>
type FlowEdge = Edge
export type Selection = { type: 'node' | 'edge'; id: string }

const nodeIcons = { overview: Workflow, module: Layers3, step: AlignLeft, decision: Diamond, note: StickyNote }
const nodeColors: Record<NodeKind, string> = {
  overview: '#e1ae70',
  module: '#e3af76',
  step: '#87aaca',
  decision: '#d5a9dc',
  note: '#97b99a',
}

function BoardCard({ data: { item }, selected, isConnectable }: NodeProps<FlowNode>) {
  const Icon = nodeIcons[item.kind]

  return (
    <div className="pc-node" data-kind={item.kind} data-selected={selected}>
      <Handle id="flow-in" position={Position.Left} type="target" isConnectable={isConnectable} className="pc-port" />
      <Handle id="flow-out" position={Position.Right} type="source" isConnectable={isConnectable} className="pc-port" />
      <Handle id="detail-in" position={Position.Top} type="target" isConnectable={isConnectable} className="pc-port pc-detail-port" />
      <Handle id="detail-out" position={Position.Bottom} type="source" isConnectable={isConnectable} className="pc-port pc-detail-port" />
      <div className="pc-node-type"><Icon size={14} />{kindLabels[item.kind]}</div>
      <div className="pc-node-title">{item.title}</div>
      {item.body && <p className="pc-node-body">{item.body}</p>}
      {item.source_line !== undefined && <span className="pc-node-source">L{item.source_line}</span>}
    </div>
  )
}

const nodeTypes = { card: BoardCard }

interface CanvasProps {
  graph: Graph
  selection?: Selection
  positions: Record<string, XYPosition>
  initialFocus?: string[]
  editable: boolean
  select: (selection?: Selection) => void
  onNodesChange: (changes: NodeChange<FlowNode>[]) => void
  onMoveNodes: (nodes: FlowNode[]) => void
  connect: (connection: Connection) => void
  reconnect: (edge: FlowEdge, connection: Connection) => void
  boardRef: React.RefObject<HTMLElement>
  children?: React.ReactNode
}

function flowEdge(edge: BoardEdge, selected: boolean): FlowEdge {
  const detail = edge.kind === 'detail'
  const color = selected ? '#ebbb82' : detail ? '#779180' : '#8299b3'
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: detail ? 'detail-out' : 'flow-out',
    targetHandle: detail ? 'detail-in' : 'flow-in',
    selected,
    label: edge.label,
    labelStyle: { fill: selected ? '#ffe1b8' : '#d0dcec', fontSize: 13 },
    labelBgStyle: { fill: '#19212c' },
    labelBgPadding: [7, 4],
    labelBgBorderRadius: 4,
    markerEnd: detail ? undefined : { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
    type: 'smoothstep',
    style: { stroke: color, strokeWidth: selected ? 2.5 : 1.7, strokeDasharray: detail ? '6 5' : undefined },
    interactionWidth: 22,
    ariaLabel: edge.label ? `连线，${edge.label}` : '连线',
  }
}

export function BoardCanvas(props: CanvasProps) {
  const [measurements, setMeasurements] = useState<Record<string, FlowNode['measured']>>({})
  const nodes = useMemo(() => props.graph.nodes.map((item): FlowNode => ({
    id: item.id,
    type: 'card',
    data: { item },
    position: props.positions[item.id] ?? { x: item.x, y: item.y },
    measured: measurements[item.id],
    selected: props.selection?.type === 'node' && props.selection.id === item.id,
    ariaLabel: `${kindLabels[item.kind]}，${item.title}`,
  })), [props.graph.nodes, props.positions, props.selection, measurements])
  const edges = useMemo(() => props.graph.edges.map(item =>
    flowEdge(item, props.selection?.type === 'edge' && props.selection.id === item.id)),
  [props.graph.edges, props.selection])

  return (
    <section className="pc-stage" ref={props.boardRef} aria-label="编辑流程画布">
      <ReactFlow<FlowNode, FlowEdge>
        className="pc-flow"
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode="dark"
        fitView
        fitViewOptions={{ nodes: props.initialFocus?.map(id => ({ id })), padding: 0.27, maxZoom: 0.94 }}
        minZoom={0.12}
        maxZoom={2.5}
        nodeExtent={[[-1_000_000, -1_000_000], [1_000_000, 1_000_000]]}
        nodesDraggable={props.editable}
        nodesConnectable={props.editable}
        edgesReconnectable={props.editable}
        deleteKeyCode={null}
        multiSelectionKeyCode={null}
        onNodesChange={(changes) => {
          const resized: typeof measurements = {}
          for (const change of changes) {
            if (change.type === 'dimensions' && change.dimensions) resized[change.id] = change.dimensions
          }
          if (Object.keys(resized).length) setMeasurements(current => ({ ...current, ...resized }))
          props.onNodesChange(changes)
        }}
        onNodeDragStop={(_event, node, dragged) => props.onMoveNodes(dragged.length ? dragged : [node])}
        onNodeClick={(_event, node) => props.select({ type: 'node', id: node.id })}
        onEdgeClick={(_event, edge) => props.select({ type: 'edge', id: edge.id })}
        onPaneClick={() => props.select(undefined)}
        onConnect={props.connect}
        onReconnect={props.reconnect}
        isValidConnection={connection => connection.source !== connection.target}
        connectionLineStyle={{ stroke: '#e3b684', strokeWidth: 2 }}
        ariaLabelConfig={{
          'node.a11yDescription.default': '按 Enter 选中节点，使用方向键移动，按 Escape 取消选择。',
          'edge.a11yDescription.default': '按 Enter 选中连线，按 Escape 取消选择。',
          'handle.ariaLabel': '节点连接端口',
        }}
      >
        <Background variant={BackgroundVariant.Dots} gap={23} size={1.2} color="#465262" />
        <MiniMap<FlowNode>
          className="pc-minimap"
          ariaLabel="画布缩略图"
          pannable
          zoomable
          nodeColor={node => nodeColors[node.data.item.kind]}
          maskColor="#0d131db0"
        />
        <ZoomControls />
      </ReactFlow>
      {props.graph.nodes.length === 0 && <div className="pc-stage-empty"><CircleDot size={23} />添加一个节点开始编辑</div>}
      {props.children}
    </section>
  )
}

function ZoomControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow()
  const zoom = useStore(state => state.transform[2])
  const duration = 220

  return (
    <div className="pc-zoom" role="group" aria-label="画布缩放">
      <button type="button" className="pc-icon-button" title="缩小" aria-label="缩小" onClick={() => void zoomOut({ duration })}><Minus size={17} /></button>
      <span>{Math.round(zoom * 100)}%</span>
      <button type="button" className="pc-icon-button" title="放大" aria-label="放大" onClick={() => void zoomIn({ duration })}><Plus size={17} /></button>
      <button type="button" className="pc-icon-button pc-zoom-fit" title="适合画布" aria-label="适合画布"
        onClick={() => void fitView({ duration, padding: 0.22 })}><Focus size={17} /></button>
    </div>
  )
}
