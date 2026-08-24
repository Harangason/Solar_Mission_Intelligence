import { useMemo, useRef, useState, useEffect } from 'react'

import { rankRouteCandidates } from '../routeCandidateSelection'

export type RouteCalculationCandidateStatus =
  | 'running'
  | 'geometry-valid'
  | 'performance-valid'
  | 'success'
  | 'rejected'
  | 'error'

export interface RouteCalculationCandidateTrace {
  id: string
  iteration: number
  date: string
  stage: string
  fullCorridorCheck: boolean
  status: RouteCalculationCandidateStatus
  message?: string
  geometricScore: number
  quality?: number
  geometryValid?: boolean
  endpointsReached?: boolean
  maximumEndpointResidualKm?: number
  performanceEvaluated?: boolean
  hypotheticalInterstellarAsymptote?: boolean
  feasible?: boolean
  corridorSatisfied?: boolean
  collisionFree?: boolean
  requiredInjectionDeltaVKmS?: number
  availableInjectionDeltaVKmS?: number
  targetCorrectionDeltaVKmS?: number
  corridorInsertionDeficitKmS?: number
  targetAlignmentDeg?: number
  totalFlightDays?: number
  routePoints?: Array<[number, number, number]>
}

export interface RouteCalculationGeometryPoint {
  date: string
  score: number
  shortlisted: boolean
}

export interface RouteCalculationTrace {
  runId: string
  routeLabel: string
  running: boolean
  baseDate: string
  searchStartDate: string
  searchEndDate: string
  broadStepDays: number
  graphNodes: number
  graphEdges: number
  geometricShortlist: number
  geometryPoints?: RouteCalculationGeometryPoint[]
  preflightBudget: number
  fullValidationBudget: number
  candidates: RouteCalculationCandidateTrace[]
  resultCount: number
  flightReadyCount: number
  goodResultCount?: number
  targetGoodResults?: number
  adaptiveRound?: number
  progressPercent?: number
  progressMessage?: string
  stopReason?: string
  bestDate?: string
  error?: string
}

export interface RouteCalculationRunSummary {
  runId: string
  routeLabel: string
  status: string
  startedAtUtc: string
}

interface RouteCalculationDialogProps {
  trace: RouteCalculationTrace
  availableRuns: RouteCalculationRunSummary[]
  historyLoading: boolean
  onRunSelect: (runId: string) => void
  onClose: () => void
  selectionMode?: boolean
  selectableCandidateIds?: string[]
  onCandidateApply?: (candidateId: string) => void
}

const STAGE_NAMES: Record<string, string> = {
  'basin-preflight': 'Geometrische Vorprüfung',
  'graph-refinement-level-1': 'Nachsuche E1',
  'graph-refinement-level-2': 'Nachsuche E2',
  'graph-refinement-level-3': 'Nachsuche E3',
  'graph-refinement-level-4': 'Nachsuche E4',
  'corridor-full-validation': 'Geometrische Wegpunktprüfung',
  'adaptive-passage-round-1': 'Adaptive Passage R1',
  'adaptive-passage-round-2': 'Adaptive Passage R2',
  'adaptive-passage-round-3': 'Adaptive Passage R3',
}

const STATUS_NAMES: Record<RouteCalculationCandidateStatus, string> = {
  running: 'läuft',
  'geometry-valid': 'geometrisch gültig',
  'performance-valid': 'flugfähig',
  success: 'erfolgreich',
  rejected: 'verworfen',
  error: 'Fehler',
}

function finite(value: number | undefined): value is number {
  return value !== undefined && Number.isFinite(value)
}

function metric(value: number | undefined, digits = 1) {
  return finite(value) ? value.toFixed(digits) : '–'
}

function stageName(stage: string) {
  return STAGE_NAMES[stage] ?? stage
}

function friendlyCalculationError(message: string | undefined) {
  if (!message) return ''
  if (/failed to fetch/i.test(message)) {
    return 'Backend nicht erreichbar oder waehrend der Berechnung neu gestartet. Der Lauf wurde unterbrochen; bitte Serverstatus pruefen und den Solverlauf erneut starten.'
  }
  return message
}

function candidateDeficit(candidate: RouteCalculationCandidateTrace) {
  if (!candidate.performanceEvaluated) return undefined
  if (
    !finite(candidate.requiredInjectionDeltaVKmS)
    || !finite(candidate.availableInjectionDeltaVKmS)
  ) return undefined
  return Math.max(
    0,
    candidate.requiredInjectionDeltaVKmS
      + (candidate.targetCorrectionDeltaVKmS ?? 0)
      - candidate.availableInjectionDeltaVKmS,
  ) + (candidate.corridorInsertionDeficitKmS ?? 0)
}

function routePath(points: Array<[number, number, number]> | undefined) {
  if (!points || points.length < 2) return ''
  const projected = points
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
    .map(([x, y]) => ({ x, y: -y }))
  if (projected.length < 2) return ''
  let minX = projected[0].x
  let maxX = projected[0].x
  let minY = projected[0].y
  let maxY = projected[0].y
  for (const point of projected) {
    minX = Math.min(minX, point.x)
    maxX = Math.max(maxX, point.x)
    minY = Math.min(minY, point.y)
    maxY = Math.max(maxY, point.y)
  }
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  const scale = Math.min(620 / width, 260 / height)
  const offsetX = 340 - (minX + maxX) * scale / 2
  const offsetY = 150 - (minY + maxY) * scale / 2
  return projected.map((point, index) => (
    `${index === 0 ? 'M' : 'L'} ${(point.x * scale + offsetX).toFixed(2)} ${(point.y * scale + offsetY).toFixed(2)}`
  )).join(' ')
}

function CandidateRoutePlot({ path }: { path: string }) {
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 })
  const dragRef = useRef<{
    pointerId: number
    startX: number
    startY: number
    viewX: number
    viewY: number
  } | null>(null)

  const zoomAt = (factor: number, anchorX = 340, anchorY = 150) => {
    setView((current) => {
      const scale = Math.min(32, Math.max(1, current.scale * factor))
      const ratio = scale / current.scale
      return {
        scale,
        x: anchorX - (anchorX - current.x) * ratio,
        y: anchorY - (anchorY - current.y) * ratio,
      }
    })
  }
  const resetView = () => setView({ scale: 1, x: 0, y: 0 })

  return (
    <div className="calculation-route-chart">
      <div className="calculation-chart-controls" aria-label="Bahnansicht steuern">
        <span aria-live="polite">Zoom {view.scale.toLocaleString('de-DE', { maximumFractionDigits: 1 })}×</span>
        <button type="button" aria-label="Bahnansicht verkleinern" onClick={() => zoomAt(1 / 1.5)}>−</button>
        <button type="button" aria-label="Bahnansicht vergrößern" onClick={() => zoomAt(1.5)}>+</button>
        <button type="button" disabled={view.scale <= 1.001} onClick={resetView}>Zurücksetzen</button>
      </div>
      <svg
        viewBox="0 0 680 300"
        className="calculation-route-plot is-interactive"
        role="img"
        aria-label="Projizierter Verlauf der ausgewählten Route. Mit dem Mausrad zoomen, durch Ziehen verschieben und per Doppelklick zurücksetzen."
        onDoubleClick={resetView}
        onWheel={(event) => {
          event.preventDefault()
          const bounds = event.currentTarget.getBoundingClientRect()
          const anchorX = (event.clientX - bounds.left) / bounds.width * 680
          const anchorY = (event.clientY - bounds.top) / bounds.height * 300
          zoomAt(Math.exp(-event.deltaY * 0.0015), anchorX, anchorY)
        }}
        onPointerDown={(event) => {
          dragRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            viewX: view.x,
            viewY: view.y,
          }
          event.currentTarget.classList.add('is-panning')
          event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current
          if (!drag || drag.pointerId !== event.pointerId) return
          const bounds = event.currentTarget.getBoundingClientRect()
          setView((current) => ({
            ...current,
            x: drag.viewX + (event.clientX - drag.startX) / bounds.width * 680,
            y: drag.viewY + (event.clientY - drag.startY) / bounds.height * 300,
          }))
        }}
        onPointerUp={(event) => {
          if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null
          event.currentTarget.classList.remove('is-panning')
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
        }}
        onPointerCancel={(event) => {
          dragRef.current = null
          event.currentTarget.classList.remove('is-panning')
        }}
      >
        <defs>
          <clipPath id="calculation-route-clip"><rect x="0" y="0" width="680" height="300" /></clipPath>
        </defs>
        <line x1="20" y1="150" x2="660" y2="150" className="calculation-axis" />
        <line x1="340" y1="12" x2="340" y2="288" className="calculation-axis" />
        <g clipPath="url(#calculation-route-clip)">
          <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
            <path d={path} className="calculation-route-path" />
          </g>
        </g>
      </svg>
      <p className="calculation-chart-hint">Mausrad: zoomen · Ziehen: verschieben · Doppelklick: zurücksetzen</p>
    </div>
  )
}

function CandidateQualityPlot({
  candidates,
  selectedId,
  onSelect,
  searchStartDate,
  searchEndDate,
}: {
  candidates: RouteCalculationCandidateTrace[]
  selectedId: string
  onSelect: (id: string) => void
  searchStartDate: string
  searchEndDate: string
}) {
  const [viewport, setViewport] = useState({ start: 0, end: 1 })
  const dragRef = useRef<{
    pointerId: number
    startX: number
    viewportStart: number
    viewportEnd: number
  } | null>(null)
  const plotted = candidates.filter((candidate) => finite(candidate.quality))
  if (plotted.length === 0) {
    return <p className="calculation-empty">Noch keine bewerteten Solvervarianten.</p>
  }
  const timestamps = plotted.map((candidate) => new Date(`${candidate.date}T00:00:00Z`).getTime())
  const qualities = plotted.map((candidate) => candidate.quality as number)
  const requestedMinTime = new Date(`${searchStartDate}T00:00:00Z`).getTime()
  const requestedMaxTime = new Date(`${searchEndDate}T00:00:00Z`).getTime()
  const candidateMinTime = Math.min(...timestamps)
  const candidateMaxTime = Math.max(...timestamps)
  const minTime = Number.isFinite(requestedMinTime)
    ? Math.min(requestedMinTime, candidateMinTime)
    : candidateMinTime
  const maxTime = Number.isFinite(requestedMaxTime)
    ? Math.max(requestedMaxTime, candidateMaxTime)
    : candidateMaxTime
  const minQuality = Math.min(...qualities)
  const maxQuality = Math.max(...qualities)
  const timeSpan = Math.max(1, maxTime - minTime)
  const qualitySpan = Math.max(1, maxQuality - minQuality)
  const viewportSpan = viewport.end - viewport.start
  const visibleMinTime = minTime + viewport.start * timeSpan
  const visibleMaxTime = minTime + viewport.end * timeSpan
  const visibleTimeSpan = Math.max(1, visibleMaxTime - visibleMinTime)
  const zoomLevel = 1 / viewportSpan
  const fullHorizonYears = timeSpan / (365.25 * 86_400_000)
  const visibleHorizonYears = visibleTimeSpan / (365.25 * 86_400_000)

  const formatDate = (timestamp: number) => new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(timestamp)

  const zoomAt = (factor: number, anchor = 0.5) => {
    setViewport((current) => {
      const currentSpan = current.end - current.start
      const nextSpan = Math.min(1, Math.max(1 / 32, currentSpan * factor))
      const domainAnchor = current.start + currentSpan * anchor
      let start = domainAnchor - nextSpan * anchor
      start = Math.min(1 - nextSpan, Math.max(0, start))
      return { start, end: start + nextSpan }
    })
  }

  const resetViewport = () => setViewport({ start: 0, end: 1 })
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => ({
    x: 62 + fraction * 610,
    label: formatDate(visibleMinTime + visibleTimeSpan * fraction),
  }))

  return (
    <div className="calculation-quality-chart">
      <div className="calculation-chart-controls" aria-label="Diagrammansicht steuern">
        <span aria-live="polite">
          {zoomLevel.toLocaleString('de-DE', { maximumFractionDigits: 1 })}× · {' '}
          {viewportSpan >= 0.999
            ? `Suchhorizont ${fullHorizonYears.toLocaleString('de-DE', { maximumFractionDigits: 1 })} Jahre`
            : `Ausschnitt ${visibleHorizonYears.toLocaleString('de-DE', { maximumFractionDigits: 1 })} von ${fullHorizonYears.toLocaleString('de-DE', { maximumFractionDigits: 1 })} Jahren`}
          {' · '}{formatDate(visibleMinTime)} – {formatDate(visibleMaxTime)}
        </span>
        <button type="button" aria-label="Diagramm verkleinern" onClick={() => zoomAt(1.5)}>−</button>
        <button type="button" aria-label="Diagramm vergrößern" onClick={() => zoomAt(2 / 3)}>+</button>
        <button type="button" disabled={viewportSpan >= 0.999} onClick={resetViewport}>Zurücksetzen</button>
      </div>
      <svg
        viewBox="0 0 700 250"
        className="calculation-quality-plot"
        role="img"
        aria-label="Qualität der Solvervarianten über dem Startdatum. Mit dem Mausrad zoomen, durch Ziehen verschieben und per Doppelklick zurücksetzen."
        onDoubleClick={resetViewport}
        onWheel={(event) => {
          event.preventDefault()
          const bounds = event.currentTarget.getBoundingClientRect()
          const plotLeft = bounds.left + bounds.width * 54 / 700
          const plotWidth = bounds.width * 628 / 700
          const anchor = Math.min(1, Math.max(0, (event.clientX - plotLeft) / plotWidth))
          zoomAt(Math.exp(event.deltaY * 0.0015), anchor)
        }}
        onPointerDown={(event) => {
          if ((event.target as Element).closest('.calculation-point')) return
          dragRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            viewportStart: viewport.start,
            viewportEnd: viewport.end,
          }
          event.currentTarget.classList.add('is-panning')
          event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current
          if (!drag || drag.pointerId !== event.pointerId) return
          const bounds = event.currentTarget.getBoundingClientRect()
          const plotWidth = bounds.width * 628 / 700
          const span = drag.viewportEnd - drag.viewportStart
          const shift = -(event.clientX - drag.startX) / plotWidth * span
          const start = Math.min(1 - span, Math.max(0, drag.viewportStart + shift))
          setViewport({ start, end: start + span })
        }}
        onPointerUp={(event) => {
          if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null
          event.currentTarget.classList.remove('is-panning')
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
        }}
        onPointerCancel={(event) => {
          dragRef.current = null
          event.currentTarget.classList.remove('is-panning')
        }}
      >
        <line x1="54" y1="18" x2="54" y2="216" className="calculation-axis" />
        <line x1="54" y1="216" x2="682" y2="216" className="calculation-axis" />
        <text x="12" y="28" className="calculation-axis-label">Qualität</text>
        <text x="48" y="31" textAnchor="end" className="calculation-tick">{maxQuality.toFixed(0)}</text>
        <text x="48" y="214" textAnchor="end" className="calculation-tick">{minQuality.toFixed(0)}</text>
        {xTicks.map((tick, index) => (
          <g key={`${tick.label}-${index}`}>
            <line x1={tick.x} y1="216" x2={tick.x} y2="221" className="calculation-axis" />
            <text
              x={tick.x}
              y="239"
              textAnchor={index === 0 ? 'start' : index === xTicks.length - 1 ? 'end' : 'middle'}
              className="calculation-tick"
            >
              {tick.label}
            </text>
          </g>
        ))}
        {plotted.map((candidate, index) => {
          if (timestamps[index] < visibleMinTime || timestamps[index] > visibleMaxTime) return null
          const x = 62 + (timestamps[index] - visibleMinTime) / visibleTimeSpan * 610
          const y = 206 - (qualities[index] - minQuality) / qualitySpan * 178
          return (
            <g key={candidate.id}>
              <circle
                cx={x}
                cy={y}
                r={candidate.id === selectedId ? 8 : 5}
                className={`calculation-point is-${candidate.status}${candidate.id === selectedId ? ' is-selected' : ''}`}
                tabIndex={0}
                role="button"
                aria-label={`Variante ${candidate.iteration}, ${candidate.date}, Qualität ${candidate.quality?.toFixed(1)}`}
                onClick={() => onSelect(candidate.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') onSelect(candidate.id)
                }}
              />
            </g>
          )
        })}
      </svg>
      <p className="calculation-chart-hint">Mausrad: zoomen · Ziehen: verschieben · Doppelklick: zurücksetzen</p>
    </div>
  )
}

function GeometryGraphPopup({
  trace,
  onClose,
}: {
  trace: RouteCalculationTrace
  onClose: () => void
}) {
  const storedPoints = (trace.geometryPoints ?? []).filter((point) => (
    finite(point.score) && Number.isFinite(new Date(`${point.date}T00:00:00Z`).getTime())
  ))
  const fallbackByDate = new Map<string, RouteCalculationGeometryPoint>()
  if (storedPoints.length === 0) {
    for (const candidate of trace.candidates) {
      if (!finite(candidate.geometricScore)) continue
      const current = fallbackByDate.get(candidate.date)
      fallbackByDate.set(candidate.date, {
        date: candidate.date,
        score: Math.max(current?.score ?? Number.NEGATIVE_INFINITY, candidate.geometricScore),
        shortlisted: (current?.shortlisted ?? false) || candidate.fullCorridorCheck,
      })
    }
  }
  const points = (storedPoints.length > 0 ? storedPoints : [...fallbackByDate.values()])
    .sort((left, right) => left.date.localeCompare(right.date))
  const timestamps = points.map((point) => new Date(`${point.date}T00:00:00Z`).getTime())
  const scores = points.map((point) => point.score)
  const minTime = timestamps.length > 0 ? Math.min(...timestamps) : 0
  const maxTime = timestamps.length > 0 ? Math.max(...timestamps) : 1
  const minScore = scores.length > 0 ? Math.min(...scores) : 0
  const maxScore = scores.length > 0 ? Math.max(...scores) : 1
  const timeSpan = Math.max(1, maxTime - minTime)
  const scoreSpan = Math.max(1, maxScore - minScore)
  const projected = points.map((point, index) => ({
    ...point,
    x: 72 + (timestamps[index] - minTime) / timeSpan * 784,
    y: 326 - (point.score - minScore) / scoreSpan * 264,
  }))
  const graphPath = projected.map((point, index) => (
    `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
  )).join(' ')
  const dateFormatter = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: 'UTC',
  })
  const dateTicks = [0, 0.5, 1].map((fraction) => ({
    x: 72 + fraction * 784,
    label: dateFormatter.format(minTime + fraction * timeSpan),
  }))

  return (
    <div
      className="calculation-geometry-backdrop"
      role="presentation"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        className="calculation-geometry-popup"
        role="dialog"
        aria-modal="true"
        aria-labelledby="geometry-popup-title"
      >
        <header>
          <div>
            <small>Geometrischer Suchraum</small>
            <h3 id="geometry-popup-title">{trace.graphNodes.toLocaleString('de-DE')} Geometriepunkte</h3>
            <p>{trace.routeLabel}</p>
          </div>
          <button type="button" aria-label="Geometrieansicht schließen" onClick={onClose}>×</button>
        </header>

        <div className="calculation-geometry-stats">
          <article><span>Knoten</span><strong>{trace.graphNodes.toLocaleString('de-DE')}</strong></article>
          <article><span>Kanten</span><strong>{trace.graphEdges.toLocaleString('de-DE')}</strong></article>
          <article><span>Shortlist</span><strong>{trace.geometricShortlist.toLocaleString('de-DE')}</strong></article>
          <article><span>Ansicht</span><strong>{points.length.toLocaleString('de-DE')} Stützpunkte</strong></article>
        </div>

        {points.length > 1
          ? (
            <svg
              viewBox="0 0 900 380"
              className="calculation-geometry-view"
              role="img"
              aria-label="Geometrischer Score des zeitlichen Konstellationsgraphen über dem Startdatum"
            >
              {[62, 194, 326].map((y) => (
                <line key={y} x1="72" y1={y} x2="856" y2={y} className="calculation-geometry-grid" />
              ))}
              <line x1="72" y1="48" x2="72" y2="326" className="calculation-axis" />
              <line x1="72" y1="326" x2="856" y2="326" className="calculation-axis" />
              <text x="16" y="34" className="calculation-axis-label">Geometrischer Score</text>
              <text x="64" y="66" textAnchor="end" className="calculation-tick">{maxScore.toFixed(0)}</text>
              <text x="64" y="326" textAnchor="end" className="calculation-tick">{minScore.toFixed(0)}</text>
              <path d={graphPath} className="calculation-geometry-path" />
              {projected.filter((point) => point.shortlisted).map((point) => (
                <circle key={point.date} cx={point.x} cy={point.y} r="4" className="calculation-geometry-shortlist-point">
                  <title>{point.date} · Score {point.score.toFixed(1)} · Shortlist</title>
                </circle>
              ))}
              {dateTicks.map((tick, index) => (
                <g key={`${tick.label}-${index}`}>
                  <line x1={tick.x} y1="326" x2={tick.x} y2="332" className="calculation-axis" />
                  <text
                    x={tick.x}
                    y="354"
                    textAnchor={index === 0 ? 'start' : index === dateTicks.length - 1 ? 'end' : 'middle'}
                    className="calculation-tick"
                  >
                    {tick.label}
                  </text>
                </g>
              ))}
            </svg>
          )
          : <p className="calculation-geometry-empty">Für diesen Lauf sind noch keine darstellbaren Geometriepunkte vorhanden.</p>}

        <footer>
          <div className="calculation-geometry-legend">
            <span><i className="graph" /> Zeitlicher Graph / Nachbarschaftskanten</span>
            <span><i className="shortlist" /> Für die Solver-Shortlist gewählt</span>
          </div>
          <p>
            {storedPoints.length > 0
              ? `Kompakte, persistierte Ansicht von ${trace.graphNodes.toLocaleString('de-DE')} bewerteten Startzeit-Konstellationen.`
              : 'Historischer Lauf: Ansicht aus den persistierten Solvervarianten rekonstruiert.'}
          </p>
        </footer>
      </section>
    </div>
  )
}

function ShortlistPopup({
  trace,
  onSelect,
  onClose,
}: {
  trace: RouteCalculationTrace
  onSelect: (candidateId: string) => void
  onClose: () => void
}) {
  const storedShortlist = (trace.geometryPoints ?? [])
    .filter((point) => point.shortlisted && finite(point.score))
  const fallbackByDate = new Map<string, RouteCalculationGeometryPoint>()
  if (storedShortlist.length === 0) {
    for (const candidate of trace.candidates) {
      if (!finite(candidate.geometricScore)) continue
      const current = fallbackByDate.get(candidate.date)
      fallbackByDate.set(candidate.date, {
        date: candidate.date,
        score: Math.max(current?.score ?? Number.NEGATIVE_INFINITY, candidate.geometricScore),
        shortlisted: true,
      })
    }
  }
  const fallbackShortlist = [...fallbackByDate.values()]
    .sort((left, right) => right.score - left.score)
    .slice(0, trace.geometricShortlist)
  const points = (storedShortlist.length > 0 ? storedShortlist : fallbackShortlist)
    .sort((left, right) => left.date.localeCompare(right.date))
  const candidatesByDate = new Map<string, RouteCalculationCandidateTrace[]>()
  for (const candidate of trace.candidates) {
    const current = candidatesByDate.get(candidate.date) ?? []
    current.push(candidate)
    candidatesByDate.set(candidate.date, current)
  }
  const rows = points.map((point) => {
    const candidates = candidatesByDate.get(point.date) ?? []
    const preflight = candidates.find((candidate) => !candidate.fullCorridorCheck) ?? candidates[0]
    const fullValidation = [...candidates].reverse().find((candidate) => candidate.fullCorridorCheck)
    return { point, preflight, fullValidation }
  })
  const evaluated = rows.filter((row) => row.preflight).length
  const fullyValidated = rows.filter((row) => row.fullValidation).length

  const stateLabel = (candidate: RouteCalculationCandidateTrace | undefined) => {
    if (!candidate) return 'wartet'
    return STATUS_NAMES[candidate.status] ?? candidate.status
  }

  return (
    <div
      className="calculation-geometry-backdrop"
      role="presentation"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        className="calculation-geometry-popup calculation-shortlist-popup"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortlist-popup-title"
      >
        <header>
          <div>
            <small>Auswahl vor dem Vollsolver</small>
            <h3 id="shortlist-popup-title">{trace.geometricShortlist.toLocaleString('de-DE')} geometrische Shortlist-Kandidaten</h3>
            <p>{trace.routeLabel}</p>
          </div>
          <button type="button" aria-label="Shortlist schließen" onClick={onClose}>×</button>
        </header>

        <div className="calculation-geometry-stats">
          <article><span>Ausgewählt</span><strong>{points.length.toLocaleString('de-DE')}</strong></article>
          <article><span>Vorprüfung</span><strong>{evaluated.toLocaleString('de-DE')}</strong></article>
          <article><span>Vollprüfung</span><strong>{fullyValidated.toLocaleString('de-DE')}</strong></article>
          <article><span>Wartend</span><strong>{Math.max(0, points.length - evaluated).toLocaleString('de-DE')}</strong></article>
        </div>

        <div className="calculation-shortlist-table-wrap">
          <table className="calculation-table calculation-shortlist-table">
            <thead>
              <tr>
                <th>Datum</th>
                <th>Geo-Score</th>
                <th>Vorprüfung</th>
                <th>Qualität</th>
                <th>Endpunktrest</th>
                <th>Korridor</th>
                <th>Kollision</th>
                <th>Vollprüfung</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ point, preflight, fullValidation }) => (
                <tr key={point.date}>
                  <td>
                    {preflight
                      ? (
                        <button
                          type="button"
                          className="calculation-shortlist-select"
                          onClick={() => {
                            onSelect((fullValidation ?? preflight).id)
                            onClose()
                          }}
                        >
                          {point.date}
                        </button>
                      )
                      : point.date}
                  </td>
                  <td>{metric(point.score)}</td>
                  <td><span className={`calculation-status is-${preflight?.status ?? 'waiting'}`}>{stateLabel(preflight)}</span></td>
                  <td>{metric(preflight?.quality)}</td>
                  <td>{finite(preflight?.maximumEndpointResidualKm) ? `${metric(preflight?.maximumEndpointResidualKm, 2)} km` : '–'}</td>
                  <td>{preflight?.corridorSatisfied === undefined ? '–' : preflight.corridorSatisfied ? 'erfüllt' : 'verfehlt'}</td>
                  <td>{preflight?.collisionFree === undefined ? '–' : preflight.collisionFree ? 'frei' : 'Treffer'}</td>
                  <td><span className={`calculation-status is-${fullValidation?.status ?? 'waiting'}`}>{stateLabel(fullValidation)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <footer>
          <p>
            Die Shortlist enthält zeitlich getrennte lokale Maxima des geometrischen Suchgraphen.
            Adaptive Passage- und Nachbarschaftsvarianten werden anschließend unter „Solverläufe · adaptiv“ ergänzt.
          </p>
        </footer>
      </section>
    </div>
  )
}

export function RouteCalculationDialog({
  trace,
  availableRuns,
  historyLoading,
  onRunSelect,
  onClose,
  selectionMode = false,
  selectableCandidateIds = [],
  onCandidateApply,
}: RouteCalculationDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [stageFilter, setStageFilter] = useState('all')
  const [tableFilters, setTableFilters] = useState({
    query: '',
    date: '',
    stage: 'all',
    status: 'all',
    qualityMin: '',
    qualityMax: '',
  })
  const [selectedId, setSelectedId] = useState('')
  const [geometryPopupOpen, setGeometryPopupOpen] = useState(false)
  const [shortlistPopupOpen, setShortlistPopupOpen] = useState(false)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return undefined
    dialog.showModal()
    return () => {
      if (dialog.open) dialog.close()
    }
  }, [])

  const stages = useMemo(
    () => [...new Set(trace.candidates.map((candidate) => candidate.stage))],
    [trace.candidates],
  )
  const filteredCandidates = useMemo(
    () => rankRouteCandidates(
      trace.candidates.filter((candidate) => (
        (!selectionMode || candidate.fullCorridorCheck)
        && (stageFilter === 'all' || candidate.stage === stageFilter)
      )),
    ),
    [selectionMode, stageFilter, trace.candidates],
  )
  const tableCandidates = useMemo(() => {
    const query = tableFilters.query.trim().toLowerCase()
    const qualityMin = tableFilters.qualityMin === '' ? undefined : Number(tableFilters.qualityMin)
    const qualityMax = tableFilters.qualityMax === '' ? undefined : Number(tableFilters.qualityMax)
    return filteredCandidates.filter((candidate) => {
      const statusLabel = STATUS_NAMES[candidate.status] ?? candidate.status
      const searchable = [
        String(candidate.iteration),
        candidate.date,
        stageName(candidate.stage),
        statusLabel,
        candidate.message ?? '',
      ].join(' ').toLowerCase()
      return (
        (!query || searchable.includes(query))
        && (!tableFilters.date || candidate.date.includes(tableFilters.date))
        && (tableFilters.stage === 'all' || candidate.stage === tableFilters.stage)
        && (tableFilters.status === 'all' || candidate.status === tableFilters.status)
        && (!Number.isFinite(qualityMin) || (finite(candidate.quality) && candidate.quality >= qualityMin!))
        && (!Number.isFinite(qualityMax) || (finite(candidate.quality) && candidate.quality <= qualityMax!))
      )
    })
  }, [filteredCandidates, tableFilters])
  const resetTableFilters = () => setTableFilters({
    query: '',
    date: '',
    stage: 'all',
    status: 'all',
    qualityMin: '',
    qualityMax: '',
  })
  const selectedCandidate = (
    filteredCandidates.find((candidate) => candidate.id === selectedId)
    ?? filteredCandidates[0]
  )
  const selectableIds = useMemo(() => new Set(selectableCandidateIds), [selectableCandidateIds])
  const selectedCandidateCanApply = Boolean(
    selectedCandidate && selectableIds.has(selectedCandidate.id),
  )
  const selectedRoutePath = routePath(selectedCandidate?.routePoints)
  const solvedCount = trace.candidates.filter((candidate) => candidate.status !== 'running').length
  const fullValidationCount = trace.candidates.filter((candidate) => candidate.fullCorridorCheck).length
  const targetGoodResults = trace.targetGoodResults ?? 10
  const goodResultCount = trace.goodResultCount ?? trace.flightReadyCount
  const deficit = selectedCandidate ? candidateDeficit(selectedCandidate) : undefined
  const comparisonCandidate = selectedCandidate
    ? [...trace.candidates].reverse().find((candidate) => (
        candidate.date === selectedCandidate.date
        && candidate.fullCorridorCheck !== selectedCandidate.fullCorridorCheck
      ))
    : undefined
  const requiredDelta = (
    selectedCandidate
    && comparisonCandidate
    && selectedCandidate.performanceEvaluated
    && comparisonCandidate.performanceEvaluated
    && finite(selectedCandidate.requiredInjectionDeltaVKmS)
    && finite(comparisonCandidate.requiredInjectionDeltaVKmS)
  )
    ? selectedCandidate.requiredInjectionDeltaVKmS - comparisonCandidate.requiredInjectionDeltaVKmS
    : undefined
  const fallbackProgress = Math.min(99, Math.round(
    solvedCount / Math.max(1, trace.preflightBudget + trace.fullValidationBudget) * 100,
  ))
  const progressPercent = trace.running
    ? Math.max(0, Math.min(99, Math.round(trace.progressPercent ?? fallbackProgress)))
    : 100
  const progressState = trace.running
    ? 'running'
    : trace.error
      ? 'error'
      : 'complete'
  const progressMessage = trace.running
    ? trace.progressMessage ?? 'Solverlauf wird vorbereitet …'
    : trace.error
      ? 'Berechnung mit Fehler beendet.'
      : trace.stopReason ?? `Berechnung abgeschlossen · ${trace.resultCount} Ergebnisse.`

  return (
    <dialog
      ref={dialogRef}
      className="route-calculation-dialog"
      aria-labelledby="route-calculation-title"
      onCancel={(event) => {
        event.preventDefault()
        if (shortlistPopupOpen) setShortlistPopupOpen(false)
        else if (geometryPopupOpen) setGeometryPopupOpen(false)
        else onClose()
      }}
    >
      <header>
        <div>
          <small>{trace.running ? 'Live · Solver läuft' : 'Abgeschlossener Solverlauf'}</small>
          <h2 id="route-calculation-title">{selectionMode ? 'Beste Solver-Route auswählen' : 'Routenberechnungen analysieren'}</h2>
          <p>{trace.routeLabel}</p>
        </div>
        <div className="calculation-dialog-actions">
          <label>
            <span>Gespeicherter Lauf</span>
            <select
              value={trace.runId}
              disabled={historyLoading}
              onChange={(event) => onRunSelect(event.target.value)}
            >
              {availableRuns.map((run) => (
                <option key={run.runId} value={run.runId}>
                  {new Date(run.startedAtUtc).toLocaleString('de-DE')} · {run.status} · {run.routeLabel}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="wizard-close" aria-label="Analyse schließen" onClick={onClose}>×</button>
        </div>
      </header>

      <div className="route-calculation-content">
        <section
          className={`calculation-progress is-${progressState}`}
          aria-label="Berechnungsfortschritt"
          aria-live="polite"
        >
          <div>
            <strong>{progressPercent}%</strong>
            <span>{progressMessage}</span>
          </div>
          <div
            className="calculation-progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progressPercent}
            aria-valuetext={`${progressPercent} Prozent · ${progressMessage}`}
          >
            <i style={{ width: `${progressPercent}%` }} />
          </div>
        </section>

        <section className="calculation-funnel" aria-label="Suchtrichter">
          <article className="is-interactive">
            <button type="button" aria-label={`${trace.graphNodes.toLocaleString('de-DE')} Geometriepunkte als Ansicht öffnen`} onClick={() => setGeometryPopupOpen(true)}>
              <strong>{trace.graphNodes.toLocaleString('de-DE')}</strong>
              <span>Geometriepunkte</span>
            </button>
          </article>
          <span aria-hidden="true">→</span>
          <article className="is-interactive">
            <button type="button" aria-label={`${trace.geometricShortlist.toLocaleString('de-DE')} Shortlist-Kandidaten öffnen`} onClick={() => setShortlistPopupOpen(true)}>
              <strong>{trace.geometricShortlist}</strong>
              <span>Geometrische Shortlist</span>
            </button>
          </article>
          <span aria-hidden="true">→</span>
          <article><strong>{solvedCount}</strong><span>Solverläufe · adaptiv</span></article>
          <span aria-hidden="true">→</span>
          <article><strong>{fullValidationCount}</strong><span>Vollprüfungen</span></article>
          <span aria-hidden="true">→</span>
          <article><strong>{goodResultCount}/{targetGoodResults}</strong><span>gute Resultate · Ziel</span></article>
        </section>

        <div className="calculation-meta">
          <span>Fenster {trace.searchStartDate} – {trace.searchEndDate}</span>
          <span>Raster {trace.broadStepDays} Tage</span>
          <span>{trace.graphEdges.toLocaleString('de-DE')} Graphkanten</span>
          <span>Run {trace.runId.slice(0, 8)}</span>
          <span>Adaptive Runde {trace.adaptiveRound ?? 0}</span>
          <span>{trace.flightReadyCount} strikt flugfähig</span>
        </div>

        {trace.error ? <p className="calculation-error">{friendlyCalculationError(trace.error)}</p> : null}
        {trace.stopReason
          ? <p className={goodResultCount >= targetGoodResults ? 'calculation-empty' : 'calculation-error'}>{trace.stopReason}</p>
          : null}

        <section className="calculation-grid">
          <article className="calculation-panel">
            <header>
              <div>
                <small>Variantenvergleich</small>
                <h3>Qualität nach Startdatum</h3>
              </div>
              <label>
                <span>Stufe</span>
                <select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
                  <option value="all">Alle Stufen</option>
                  {stages.map((stage) => <option key={stage} value={stage}>{stageName(stage)}</option>)}
                </select>
              </label>
            </header>
            <CandidateQualityPlot
              key={`${trace.runId}-${stageFilter}`}
              candidates={filteredCandidates}
              selectedId={selectedCandidate?.id ?? ''}
              onSelect={setSelectedId}
              searchStartDate={trace.searchStartDate}
              searchEndDate={trace.searchEndDate}
            />
          </article>

          <article className="calculation-panel">
            <header>
              <div>
                <small>Ausgewählte Variante</small>
                <h3>{selectedCandidate ? `#${selectedCandidate.iteration} · ${selectedCandidate.date}` : 'Noch keine Variante'}</h3>
              </div>
              {selectedCandidate ? <span className={`calculation-status is-${selectedCandidate.status}`}>{STATUS_NAMES[selectedCandidate.status] ?? selectedCandidate.status}</span> : null}
            </header>
            {selectedRoutePath
              ? (
                <CandidateRoutePlot key={selectedCandidate?.id} path={selectedRoutePath} />
              )
              : <p className="calculation-empty">Für diese Variante ist noch kein Routenverlauf vorhanden.</p>}
            {selectedCandidate?.hypotheticalInterstellarAsymptote
              ? <p className="calculation-route-hypothetical">Hypothetische Zielrichtung – gerader Katalogstrahl über 50 AE, ohne lokale Ephemeride oder physikalische Sternankunft.</p>
              : null}
            {selectedCandidate?.status === 'rejected' && !selectedCandidate.hypotheticalInterstellarAsymptote
              ? <p className="calculation-route-warning">Diagnosebahn – diese Variante ist kein gültiges Ergebnis. {selectedCandidate.message ?? ''}</p>
              : null}
          </article>
        </section>

        {selectedCandidate
          ? (
            <section className="calculation-metrics" aria-label="Kennzahlen der ausgewählten Variante">
              <article><span>Stufe</span><strong>{stageName(selectedCandidate.stage)}</strong></article>
              <article><span>Qualität</span><strong>{metric(selectedCandidate.quality)}</strong></article>
              <article><span>Geometrie</span><strong>{selectedCandidate.geometryValid === undefined ? 'historischer Lauf' : selectedCandidate.geometryValid ? 'gültig' : 'ungültig'}</strong></article>
              <article><span>Endpunktrest</span><strong>{metric(selectedCandidate.maximumEndpointResidualKm, 2)} km</strong></article>
              <article><span>Δv erforderlich</span><strong>{selectedCandidate.performanceEvaluated ? `${metric(selectedCandidate.requiredInjectionDeltaVKmS, 2)} km/s` : 'noch nicht bewertet'}</strong></article>
              <article><span>Δv verfügbar</span><strong>{selectedCandidate.performanceEvaluated ? `${metric(selectedCandidate.availableInjectionDeltaVKmS, 2)} km/s` : 'noch nicht bewertet'}</strong></article>
              <article><span>Δv-Defizit</span><strong>{selectedCandidate.performanceEvaluated ? `${metric(deficit, 2)} km/s` : 'noch nicht bewertet'}</strong></article>
              <article><span>Zielrest</span><strong>{metric(selectedCandidate.targetAlignmentDeg)}°</strong></article>
              <article><span>Korridor</span><strong>{selectedCandidate.corridorSatisfied === undefined ? '–' : selectedCandidate.corridorSatisfied ? 'erfüllt' : 'verfehlt'}</strong></article>
              <article><span>Kollision</span><strong>{selectedCandidate.collisionFree === undefined ? '–' : selectedCandidate.collisionFree ? 'frei' : 'Treffer'}</strong></article>
            </section>
          )
          : null}

        {selectedCandidate && comparisonCandidate
          ? (
            <section className={`calculation-comparison${finite(requiredDelta) && Math.abs(requiredDelta) > 5 ? ' has-large-delta' : ''}`}>
              <div>
                <small>Stufenvergleich für {selectedCandidate.date}</small>
                <strong>{stageName(comparisonCandidate.stage)} → {stageName(selectedCandidate.stage)}</strong>
              </div>
              <span>Δv Soll {metric(comparisonCandidate.requiredInjectionDeltaVKmS, 2)} → {metric(selectedCandidate.requiredInjectionDeltaVKmS, 2)} km/s</span>
              <span>Zielrest {metric(comparisonCandidate.targetAlignmentDeg)}° → {metric(selectedCandidate.targetAlignmentDeg)}°</span>
              <span>Sprung {finite(requiredDelta) ? `${requiredDelta >= 0 ? '+' : ''}${requiredDelta.toFixed(2)} km/s` : '–'}</span>
            </section>
          )
          : null}

        <section className="calculation-table-wrap">
          <table className="calculation-table">
            <thead>
              <tr>
                <th>{selectionMode ? 'Rang' : '#'}</th><th>Datum</th><th>Stufe</th><th>Status</th><th>Qualität</th><th>Δv Soll</th><th>Δv Defizit</th><th>Zielrest</th>
              </tr>
              <tr className="calculation-table-filters">
                <th>
                  <button type="button" onClick={resetTableFilters}>Reset</button>
                </th>
                <th>
                  <input
                    type="search"
                    placeholder="Datum"
                    value={tableFilters.date}
                    onChange={(event) => setTableFilters((current) => ({ ...current, date: event.target.value }))}
                  />
                </th>
                <th>
                  <select
                    value={tableFilters.stage}
                    onChange={(event) => setTableFilters((current) => ({ ...current, stage: event.target.value }))}
                  >
                    <option value="all">Alle</option>
                    {stages.map((stage) => <option key={stage} value={stage}>{stageName(stage)}</option>)}
                  </select>
                </th>
                <th>
                  <select
                    value={tableFilters.status}
                    onChange={(event) => setTableFilters((current) => ({ ...current, status: event.target.value }))}
                  >
                    <option value="all">Alle</option>
                    {(Object.keys(STATUS_NAMES) as RouteCalculationCandidateStatus[]).map((status) => (
                      <option key={status} value={status}>{STATUS_NAMES[status]}</option>
                    ))}
                  </select>
                </th>
                <th>
                  <div className="calculation-range-filter">
                    <input
                      type="number"
                      placeholder="min"
                      value={tableFilters.qualityMin}
                      onChange={(event) => setTableFilters((current) => ({ ...current, qualityMin: event.target.value }))}
                    />
                    <input
                      type="number"
                      placeholder="max"
                      value={tableFilters.qualityMax}
                      onChange={(event) => setTableFilters((current) => ({ ...current, qualityMax: event.target.value }))}
                    />
                  </div>
                </th>
                <th colSpan={3}>
                  <input
                    type="search"
                    placeholder="Suche in Nr., Datum, Stufe, Status"
                    value={tableFilters.query}
                    onChange={(event) => setTableFilters((current) => ({ ...current, query: event.target.value }))}
                  />
                </th>
              </tr>
            </thead>
            <tbody>
              {tableCandidates.map((candidate, index) => (
                <tr
                  key={candidate.id}
                  className={candidate.id === selectedCandidate?.id ? 'is-selected' : ''}
                >
                  <td>
                    <button
                      type="button"
                      className="calculation-row-select"
                      aria-label={`Variante ${candidate.iteration} auswählen`}
                      onClick={() => setSelectedId(candidate.id)}
                    >
                      {selectionMode ? index + 1 : candidate.iteration}
                    </button>
                  </td>
                  <td>{candidate.date}</td>
                  <td>{stageName(candidate.stage)}</td>
                  <td><span className={`calculation-status is-${candidate.status}`}>{STATUS_NAMES[candidate.status] ?? candidate.status}</span></td>
                  <td>{metric(candidate.quality)}</td>
                  <td>{metric(candidate.requiredInjectionDeltaVKmS, 2)}</td>
                  <td>{metric(candidateDeficit(candidate), 2)}</td>
                  <td>{metric(candidate.targetAlignmentDeg)}°</td>
                </tr>
              ))}
              {tableCandidates.length === 0 ? (
                <tr>
                  <td colSpan={8} className="calculation-table-empty">Keine Varianten passen zu den aktiven Filtern.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </section>
      </div>

      <footer>
        <p>{selectionMode && !trace.running && !selectedCandidateCanApply
          ? 'Nur vollständig geprüfte, flugfähige Kandidaten aus diesem aktuellen Solverlauf können übernommen werden.'
          : trace.running
            ? `Suche läuft bis mindestens ${targetGoodResults} gute Resultate gefunden oder alle Passagevarianten ausgeschöpft sind.`
            : `${tableCandidates.length}/${trace.candidates.length} Solvervarianten sichtbar. ${trace.stopReason ?? ''}`}</p>
        <div className="calculation-footer-actions">
          {selectionMode
            ? (
              <button
                type="button"
                disabled={trace.running || !selectedCandidateCanApply}
                onClick={() => selectedCandidate && onCandidateApply?.(selectedCandidate.id)}
              >
                Ausgewählte Route übernehmen
              </button>
            )
            : null}
          <button type="button" className={selectionMode ? 'secondary' : undefined} onClick={onClose}>Schließen</button>
        </div>
      </footer>
      {geometryPopupOpen ? <GeometryGraphPopup trace={trace} onClose={() => setGeometryPopupOpen(false)} /> : null}
      {shortlistPopupOpen
        ? (
          <ShortlistPopup
            trace={trace}
            onSelect={setSelectedId}
            onClose={() => setShortlistPopupOpen(false)}
          />
        )
        : null}
    </dialog>
  )
}
