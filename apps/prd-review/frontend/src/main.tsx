import { createRoot } from 'react-dom/client'
import '@xyflow/react/dist/style.css'
import { CanvasApp } from './CanvasApp'
import './canvas.css'

const root = document.getElementById('prd-canvas-root')
if (root) {
  const { reportId, csrf, reportUrl, compareUrl, sourceCompareUrl, markdownUrl, xmindUrl } = root.dataset
  createRoot(root).render(<CanvasApp config={{
    reportId: reportId ?? '',
    csrf: csrf ?? '',
    reportUrl: reportUrl ?? '/',
    compareUrl: compareUrl ?? '',
    sourceCompareUrl: sourceCompareUrl ?? '',
    markdownUrl,
    xmindUrl,
  }} />)
}
