import {
  ArrowDownToLine, ArrowRight, ChevronRight, ExternalLink, FileText, GitCompareArrows,
  History, Layers3, PencilLine, RotateCcw, Trash2, Workflow, X,
} from 'lucide-react'
import {
  type BoardEdge, type BoardNode, type CanvasConfig, type Graph, type VersionEntry,
  kindLabels, sourceLink,
} from './model'

interface OutlineProps {
  config: CanvasConfig
  graph: Graph
  activeModule?: string
  close: () => void
  focusAll: () => void
  focusModule: (node: BoardNode) => void
}

export function Outline({ config, graph, activeModule, close, focusAll, focusModule }: OutlineProps) {
  const modules = graph.nodes.filter(({ kind }) => kind === 'module')
  const totals = new Map<string, number>()
  for (const node of graph.nodes) {
    if (node.kind !== 'module' && node.module_id) {
      totals.set(node.module_id, (totals.get(node.module_id) ?? 0) + 1)
    }
  }

  return (
    <aside className="pc-outline" aria-label="报告目录" id="pc-outline">
      <div className="pc-panel-head">
        <span><Layers3 size={16} /> 模块目录</span>
        <button className="pc-icon-button pc-mobile-close" type="button" onClick={close} aria-label="关闭目录" title="关闭目录">
          <X size={17} />
        </button>
      </div>
      <div className="pc-outline-scroll">
        <button className="pc-module-link" type="button" aria-current={!activeModule} onClick={focusAll}>
          <Workflow size={16} /><span>整张画布</span><ChevronRight className="pc-module-arrow" size={14} />
        </button>
        {modules.map(node => (
          <button key={node.id} className="pc-module-link" type="button"
            aria-current={activeModule === node.id} onClick={() => focusModule(node)} title={node.title}>
            <span className="pc-module-symbol" aria-hidden="true" />
            <span>{node.title}</span><small>{totals.get(node.id) ?? 0}</small>
          </button>
        ))}
        {modules.length === 0 && <p className="pc-muted pc-outline-empty">画布还没有业务模块</p>}
      </div>
      <div className="pc-report-links">
        {config.reportUrl && <a href={config.reportUrl}><FileText size={15} />阅读原报告<ChevronRight size={14} /></a>}
        {config.compareUrl && <a href={config.compareUrl}><GitCompareArrows size={15} />对比报告版本<ChevronRight size={14} /></a>}
        {config.markdownUrl && <a href={config.markdownUrl}><ArrowDownToLine size={15} />下载 Markdown<ChevronRight size={14} /></a>}
        {config.xmindUrl && <a href={config.xmindUrl}><ArrowDownToLine size={15} />下载 XMind 用例<ChevronRight size={14} /></a>}
      </div>
    </aside>
  )
}

interface InspectorProps {
  config: CanvasConfig
  graph: Graph
  node?: BoardNode
  edge?: BoardEdge
  panel: 'edit' | 'history'
  selectPanel: (panel: 'edit' | 'history') => void
  close: () => void
  readonly: boolean
  pending: boolean
  history: VersionEntry[]
  revision: number
  previewId?: number
  onPreview: (revision: number) => void
  onRestore: (revision: number) => void
  onReturn: () => void
  onEditNode: (patch: Partial<Pick<BoardNode, 'title' | 'body'>>, field: string) => void
  onEditEdge: (patch: Partial<Pick<BoardEdge, 'label' | 'source' | 'target'>>, field?: string) => void
  onConnectNode: (target: string) => void
  onDelete: () => void
  endField: () => void
}

function timeLabel(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

export function Inspector(props: InspectorProps) {
  return (
    <aside className="pc-inspector" aria-label="画布检查器" id="pc-inspector">
      <div className="pc-inspector-tabs">
        <button className="pc-tab" type="button" aria-pressed={props.panel === 'edit'} onClick={() => props.selectPanel('edit')}>
          <PencilLine size={15} />编辑
        </button>
        <button className="pc-tab" type="button" aria-pressed={props.panel === 'history'} onClick={() => props.selectPanel('history')}>
          <History size={15} />版本
        </button>
        <button className="pc-icon-button pc-mobile-close" type="button" onClick={props.close} aria-label="关闭检查器" title="关闭检查器">
          <X size={17} />
        </button>
      </div>
      <div className="pc-inspector-scroll">
        {props.panel === 'history'
          ? <VersionHistory {...props} />
          : props.node || props.edge
            ? <EditPanel {...props} />
            : (
              <div className="pc-empty-inspector">
                <PencilLine size={21} />
                <h2>选择要编辑的内容</h2>
                <p>点击画布上的节点或连线，在这里修改信息。</p>
              </div>
            )}
      </div>
    </aside>
  )
}

function VersionHistory({ history, revision, previewId, onPreview, onRestore, onReturn, pending }: InspectorProps) {
  return (
    <div className="pc-history">
      <h2>画布版本</h2>
      <ol>
        {history.map(version => (
          <li key={version.id} className={previewId === version.id ? 'pc-history-preview' : undefined}>
            <div className="pc-history-title">
              <span className="pc-revision-pill">{`v${version.id}`}</span>
              <strong>{version.label}</strong>
              {version.id === revision && <span className="pc-current-version">当前</span>}
            </div>
            {version.created_at && <time dateTime={version.created_at}>{timeLabel(version.created_at)}</time>}
            {version.id !== revision && (
              <div className="pc-history-actions">
                <button className="pc-button pc-quiet" type="button" disabled={pending}
                  onClick={() => previewId === version.id ? onReturn() : onPreview(version.id)}>
                  {previewId === version.id ? '返回当前' : '查看'}
                </button>
                <button className="pc-button pc-quiet" type="button" disabled={pending} onClick={() => onRestore(version.id)}>
                  <RotateCcw size={13} />回退
                </button>
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

function EditPanel({ config, graph, node, edge, readonly, onEditNode, onEditEdge, onConnectNode, onDelete, endField }: InspectorProps) {
  const lineUrl = node?.source_line === undefined ? undefined : sourceLink(config.sourceCompareUrl, node.source_line)

  return (
    <div className="pc-edit">
      <div className="pc-edit-type" data-kind={node?.kind ?? 'edge'}>
        <span className="pc-type-dot" aria-hidden="true" />{node ? kindLabels[node.kind] : '分支连线'}
      </div>
      {node && (
        <>
          <label className="pc-field">
            <span>标题</span>
            <input type="text" maxLength={500} value={node.title} readOnly={readonly}
              onChange={event => onEditNode({ title: event.target.value }, 'title')} onBlur={endField} />
          </label>
          <label className="pc-field">
            <span>说明</span>
            <textarea aria-label="说明" value={node.body} maxLength={40000} readOnly={readonly}
              onChange={event => onEditNode({ body: event.target.value }, 'body')} onBlur={endField} />
          </label>
          {lineUrl && <a href={lineUrl} className="pc-source-link">原文第 {node.source_line} 行 <ExternalLink size={14} /></a>}
          {!readonly && graph.nodes.length > 1 && (
            <label className="pc-field">
              <span>添加连线</span>
              <select aria-label="添加连线" value="" onChange={event => event.target.value && onConnectNode(event.target.value)}>
                <option value="">连接到另一个节点…</option>
                {graph.nodes.filter(({ id }) => id !== node.id).map(({ id, title }) => <option key={id} value={id}>{title}</option>)}
              </select>
            </label>
          )}
        </>
      )}
      {edge && (
        <>
          <label className="pc-field">
            <span>分支条件</span>
            <input type="text" maxLength={500} value={edge.label} readOnly={readonly}
              onChange={event => onEditEdge({ label: event.target.value }, 'label')} onBlur={endField} />
          </label>
          {(['source', 'target'] as const).map(side => (
            <label className="pc-field" key={side}>
              <span>{side === 'source' ? '起点' : '终点'}</span>
              <select aria-label={side === 'source' ? '起点' : '终点'} disabled={readonly} value={edge[side]}
                onChange={event => onEditEdge({ [side]: event.target.value })}>
                {graph.nodes.filter(({ id }) => id !== edge[side === 'source' ? 'target' : 'source'])
                  .map(({ id, title }) => <option key={id} value={id}>{title}</option>)}
              </select>
            </label>
          ))}
        </>
      )}
      {!readonly && (
        <button className="pc-button pc-delete" type="button" onClick={onDelete}>
          <Trash2 size={15} />删除{node ? '节点' : '连线'}
        </button>
      )}
    </div>
  )
}

export function Changes({ counts }: { counts: { added: number; removed: number; changed: number } }) {
  return (
    <span className="pc-changes">
      <ArrowRight size={14} /> 到当前新增 {counts.added} · 删除 {counts.removed} · 修改 {counts.changed}
    </span>
  )
}
