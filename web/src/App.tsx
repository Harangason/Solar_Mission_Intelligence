import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'

import { logActivity, setActivityProjectId } from './activityLog'
import { ActivitySettingsDialog } from './components/ActivitySettingsDialog'
import { ProjectDialog } from './components/ProjectDialog'
import type { EntryCorridorDefinition } from './entryCorridorGeometry'
import {
  createProject,
  deleteProject,
  listProjects,
  loadProject,
  updateProject,
  type ProjectState,
  type ProjectSummary,
} from './projectStore'
import { DEFAULT_ROUTE_SECTION, type RouteSectionDefinition } from './routeSections'
import type { WaypointRouteResult } from './components/PlannedWaypointRoute'
import type { MissionConfig, MissionResult, VisualConfig } from './types'

const TwoDView = lazy(() => import('./components/TwoDView').then(({ TwoDView }) => ({ default: TwoDView })))
const ThreeDView = lazy(() => import('./components/ThreeDView').then(({ ThreeDView }) => ({ default: ThreeDView })))

type ViewMode = 'menu' | 'calculation' | '2d' | '3d'

const WORKSPACES = [
  { id: 'menu', number: '00', label: 'Übersicht', detail: 'Mission starten und fortsetzen' },
  { id: 'calculation', number: '01', label: 'Berechnung', detail: 'Routen, Korridore und Solver' },
  { id: '2d', number: '02', label: '2D-Ansicht', detail: 'Bahnen und Projektionen prüfen' },
  { id: '3d', number: '03', label: '3D-Ansicht', detail: 'Flugbahnen räumlich erkunden' },
] as const

export function App() {
  const [viewMode, setViewMode] = useState<ViewMode>('menu')
  const [routeSections, setRouteSections] = useState<RouteSectionDefinition[]>([])
  const [activeRouteSectionId, setActiveRouteSectionId] = useState('')
  const [plannedMissionDate, setPlannedMissionDate] = useState<string | null>(null)
  const [plannedRoute, setPlannedRoute] = useState<WaypointRouteResult | null>(null)
  const [missionConfig, setMissionConfig] = useState<MissionConfig | null>(null)
  const [visualConfig, setVisualConfig] = useState<VisualConfig | null>(null)
  const [missionResult, setMissionResult] = useState<MissionResult | null>(null)
  const [projectLoadToken, setProjectLoadToken] = useState(0)
  const [projectId, setProjectId] = useState('')
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [projectDialogMode, setProjectDialogMode] = useState<'save-as' | 'open' | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [projectBusy, setProjectBusy] = useState(false)
  const [projectError, setProjectError] = useState<string | null>(null)
  const [projectStatus, setProjectStatus] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [solverRouteDialogOpen, setSolverRouteDialogOpen] = useState(false)
  const [navigationOpen, setNavigationOpen] = useState(false)
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try { return localStorage.getItem('solar-ui-theme') === 'dark' ? 'dark' : 'light' } catch { return 'light' }
  })
  const [savedContent, setSavedContent] = useState<string | null>(null)
  const projectOperation = useRef(false)
  const workspaceRef = useRef<HTMLElement>(null)
  const activeRouteSection = routeSections.find((section) => section.id === activeRouteSectionId) ?? routeSections[0]
  const projectContent = useMemo(() => JSON.stringify({
    routeSections, plannedMissionDate, plannedRoute, missionConfig, visualConfig, missionResult,
  }), [routeSections, plannedMissionDate, plannedRoute, missionConfig, visualConfig, missionResult])
  const hasUnsavedChanges = savedContent === null
    ? Boolean(routeSections.length || plannedRoute || missionConfig || missionResult)
    : projectContent !== savedContent

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('solar-ui-theme', theme) } catch { /* Theme still works without storage. */ }
  }, [theme])

  useEffect(() => {
    if (!hasUnsavedChanges) return
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warnBeforeLeaving)
    return () => window.removeEventListener('beforeunload', warnBeforeLeaving)
  }, [hasUnsavedChanges])

  const navigate = (mode: ViewMode) => {
    setViewMode(mode)
    setNavigationOpen(false)
    workspaceRef.current?.focus()
  }

  useEffect(() => {
    setActivityProjectId(projectId)
  }, [projectId])

  useEffect(() => {
    if (viewMode !== '3d') setSolverRouteDialogOpen(false)
  }, [viewMode])

  useEffect(() => {
    if (!routeSections.some((section) => section.id === activeRouteSectionId)) {
      setActiveRouteSectionId(routeSections[0]?.id ?? '')
    }
  }, [activeRouteSectionId, routeSections])

  useEffect(() => {
    const controlName = (control: Element) => (
      control.getAttribute('aria-label')
      || control.textContent?.replace(/\s+/g, ' ').trim()
      || control.getAttribute('name')
      || control.tagName.toLocaleLowerCase('de-DE')
    ).slice(0, 180)
    const onClick = (event: MouseEvent) => {
      const target = event.target instanceof Element
        ? event.target.closest('button, a[href], [role="button"]')
        : null
      if (!target || !target.closest('.app-shell')) return
      logActivity({
        category: 'ui',
        action: target instanceof HTMLAnchorElement ? 'link-click' : 'button-click',
        projectId,
        details: {
          control: controlName(target),
          view: viewMode,
        },
      })
    }
    const onChange = (event: Event) => {
      const target = event.target
      if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) return
      if (!target.closest('.app-shell')) return
      const isCheckbox = target instanceof HTMLInputElement && target.type === 'checkbox'
      const includeValue = target instanceof HTMLSelectElement
        || (target instanceof HTMLInputElement && ['range', 'number', 'checkbox', 'radio'].includes(target.type))
      logActivity({
        category: 'ui',
        action: 'control-change',
        projectId,
        values: includeValue
          ? { value: isCheckbox ? target.checked : target.value }
          : undefined,
        details: {
          control: controlName(target),
          controlType: target instanceof HTMLSelectElement ? 'select' : target.type,
          view: viewMode,
        },
      })
    }
    const onPointerUp = (event: globalThis.PointerEvent) => {
      const target = event.target
      if (!(target instanceof HTMLCanvasElement) || !target.closest('.app-shell')) return
      logActivity({
        category: 'ui',
        action: 'canvas-interaction',
        projectId,
        values: { x: Math.round(event.clientX), y: Math.round(event.clientY) },
        details: { view: viewMode },
      })
    }
    document.addEventListener('click', onClick)
    document.addEventListener('change', onChange)
    document.addEventListener('pointerup', onPointerUp)
    return () => {
      document.removeEventListener('click', onClick)
      document.removeEventListener('change', onChange)
      document.removeEventListener('pointerup', onPointerUp)
    }
  }, [projectId, viewMode])

  const setEntryCorridor: Dispatch<SetStateAction<EntryCorridorDefinition>> = useCallback((action) => {
    setRouteSections((current) => {
      const effectiveSectionId = current.some((section) => section.id === activeRouteSectionId)
        ? activeRouteSectionId
        : current[0]?.id
      return current.map((section) => {
        if (section.id !== effectiveSectionId) return section
        const corridor = typeof action === 'function' ? action(section.corridor) : action
        return { ...section, corridor }
      })
    })
  }, [activeRouteSectionId])

  const updateRouteSections: Dispatch<SetStateAction<RouteSectionDefinition[]>> = useCallback((action) => {
    setRouteSections(action)
    setPlannedRoute(null)
    setPlannedMissionDate(null)
  }, [])

  const applyPlannedSolution = useCallback((
    date: string,
    sections: RouteSectionDefinition[],
    route: WaypointRouteResult,
  ) => {
    setRouteSections(sections)
    setPlannedMissionDate(date)
    setPlannedRoute(route)
    setSolverRouteDialogOpen(false)
    setViewMode('3d')
  }, [])

  const setWaypointId: Dispatch<SetStateAction<string>> = useCallback((action) => {
    setRouteSections((current) => {
      const effectiveSectionId = current.some((section) => section.id === activeRouteSectionId)
        ? activeRouteSectionId
        : current[0]?.id
      return current.map((section) => {
        if (section.id !== effectiveSectionId) return section
        const targetId = typeof action === 'function' ? action(section.targetId) : action
        return { ...section, targetId }
      })
    })
  }, [activeRouteSectionId])

  const currentProjectState = (): ProjectState => ({
    schemaVersion: 1,
    viewMode: viewMode === '3d' ? '3d' : '2d',
    routeSections,
    activeRouteSectionId,
    plannedMissionDate,
    plannedRoute,
    missionConfig,
    visualConfig,
    missionResult,
  })
  const showProjectDialog = async (mode: 'save-as' | 'open') => {
    if (projectOperation.current) return
    setProjectError(null)
    setProjectDialogMode(mode)
    if (mode === 'open') {
      projectOperation.current = true
      setProjects([])
      setProjectBusy(true)
      try {
        setProjects(await listProjects())
      } catch (error) {
        setProjectError(error instanceof Error ? error.message : String(error))
      } finally {
        projectOperation.current = false
        setProjectBusy(false)
      }
    }
  }
  const saveProjectAs = async (name: string, description: string) => {
    if (projectOperation.current) return
    projectOperation.current = true
    const contentAtSave = projectContent
    setProjectBusy(true)
    setProjectError(null)
    try {
      const stored = await createProject(name, description, currentProjectState())
      logActivity({
        category: 'project',
        action: 'project-created',
        projectId: stored.id,
        values: { revision: stored.revision, routeSectionCount: stored.state.routeSections.length },
        details: { name: stored.name },
      })
      setProjectId(stored.id)
      setProjectName(stored.name)
      setProjectDescription(stored.description)
      setSavedContent(contentAtSave)
      setProjectStatus(`${stored.name} · Revision ${stored.revision} gespeichert`)
      setProjectDialogMode(null)
    } catch (error) {
      logActivity({
        category: 'project',
        action: 'project-create',
        status: 'error',
        message: error instanceof Error ? error.message : String(error),
        details: { name },
      })
      setProjectError(error instanceof Error ? error.message : String(error))
    } finally {
      projectOperation.current = false
      setProjectBusy(false)
    }
  }
  const saveCurrentProject = async () => {
    if (projectOperation.current) return
    if (!projectId) {
      await showProjectDialog('save-as')
      return
    }
    projectOperation.current = true
    const contentAtSave = projectContent
    setProjectBusy(true)
    setProjectError(null)
    try {
      const stored = await updateProject(projectId, projectName, projectDescription, currentProjectState())
      logActivity({
        category: 'project',
        action: 'project-saved',
        projectId: stored.id,
        values: { revision: stored.revision, routeSectionCount: stored.state.routeSections.length },
        details: { name: stored.name },
      })
      setProjectStatus(`${stored.name} · Revision ${stored.revision} gespeichert`)
      setSavedContent(contentAtSave)
    } catch (error) {
      logActivity({
        category: 'project',
        action: 'project-save',
        status: 'error',
        projectId,
        message: error instanceof Error ? error.message : String(error),
      })
      setProjectStatus('')
      setProjectError(error instanceof Error ? error.message : String(error))
    } finally {
      projectOperation.current = false
      setProjectBusy(false)
    }
  }
  const openStoredProject = async (selectedProjectId: string) => {
    if (projectOperation.current) return
    projectOperation.current = true
    setProjectBusy(true)
    setProjectError(null)
    try {
      const stored = await loadProject(selectedProjectId)
      logActivity({
        category: 'project',
        action: 'project-opened',
        projectId: stored.id,
        values: { revision: stored.revision, routeSectionCount: stored.state.routeSections.length },
        details: { name: stored.name },
      })
      setRouteSections(stored.state.routeSections)
      setActiveRouteSectionId(stored.state.activeRouteSectionId || stored.state.routeSections[0]?.id || '')
      setPlannedMissionDate(stored.state.plannedMissionDate)
      setPlannedRoute(stored.state.plannedRoute)
      setMissionConfig(stored.state.missionConfig ?? null)
      setVisualConfig(stored.state.visualConfig ?? null)
      setMissionResult(stored.state.missionResult ?? null)
      setSavedContent(JSON.stringify({
        routeSections: stored.state.routeSections,
        plannedMissionDate: stored.state.plannedMissionDate,
        plannedRoute: stored.state.plannedRoute,
        missionConfig: stored.state.missionConfig ?? null,
        visualConfig: stored.state.visualConfig ?? null,
        missionResult: stored.state.missionResult ?? null,
      }))
      setProjectLoadToken((current) => current + 1)
      setProjectId(stored.id)
      setProjectName(stored.name)
      setProjectDescription(stored.description)
      setProjectStatus(`${stored.name} · Revision ${stored.revision} geöffnet`)
      setViewMode(stored.state.viewMode)
      setProjectDialogMode(null)
    } catch (error) {
      logActivity({
        category: 'project',
        action: 'project-open',
        status: 'error',
        projectId: selectedProjectId,
        message: error instanceof Error ? error.message : String(error),
      })
      setProjectError(error instanceof Error ? error.message : String(error))
    } finally {
      projectOperation.current = false
      setProjectBusy(false)
    }
  }
  const removeStoredProject = async (selectedProjectId: string) => {
    if (projectOperation.current) return
    projectOperation.current = true
    setProjectBusy(true)
    setProjectError(null)
    try {
      await deleteProject(selectedProjectId)
      logActivity({
        category: 'project',
        action: 'project-deleted',
        projectId: selectedProjectId,
      })
      setProjects((current) => current.filter((project) => project.id !== selectedProjectId))
      if (selectedProjectId === projectId) {
        setProjectId('')
        setProjectName('')
        setProjectDescription('')
        setSavedContent(null)
        setProjectStatus('Projekt gelöscht · aktueller Plan ist noch ungespeichert geöffnet')
      }
    } catch (error) {
      logActivity({
        category: 'project',
        action: 'project-delete',
        status: 'error',
        projectId: selectedProjectId,
        message: error instanceof Error ? error.message : String(error),
      })
      setProjectError(error instanceof Error ? error.message : String(error))
    } finally {
      projectOperation.current = false
      setProjectBusy(false)
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">Zum Arbeitsbereich</a>
      <aside className="site-navigation" aria-label="Seitennavigation">
        <div className="site-navigation-head">
          <button className="brand" type="button" onClick={() => navigate('menu')}>
            <span className="brand-mark" aria-hidden="true">SS</span>
            <span>Solar System<small>Missionsplanung</small></span>
          </button>
          <button className="navigation-toggle" type="button" aria-expanded={navigationOpen} aria-controls="workspace-navigation" onClick={() => setNavigationOpen(!navigationOpen)}>
            {navigationOpen ? 'Menü schließen' : 'Menü öffnen'}
          </button>
        </div>
        <nav id="workspace-navigation" className={navigationOpen ? 'workspace-navigation is-open' : 'workspace-navigation'} aria-label="Arbeitsbereiche">
          <h2>Arbeitsbereiche</h2>
          {WORKSPACES.map((workspace) => (
            <button key={workspace.id} type="button" className="nav-item" aria-current={viewMode === workspace.id ? 'page' : undefined} onClick={() => navigate(workspace.id)}>
              <span className="nav-number">{workspace.number}</span>
              <span><strong>{workspace.label}</strong><small>{workspace.detail}</small></span>
            </button>
          ))}
          <button type="button" className="nav-item" aria-haspopup="dialog" onClick={() => { setNavigationOpen(false); setSettingsOpen(true) }}>
            <span className="nav-number">04</span>
            <span><strong>Einstellungen</strong><small>Aktivitäten und Protokolle</small></span>
          </button>
        </nav>
        <div className="navigation-footer"><span>PLANEN · PRÜFEN · ERKUNDEN</span><p>Von der Idee zur Flugbahn.</p></div>
      </aside>
      <div className="site-content">
      <header className="topbar">
        <div className="project-identity">
          <strong>{projectName || 'Unbenannte Mission'}</strong>
          <span>{hasUnsavedChanges ? 'Ungespeicherte Änderungen' : projectId ? 'Alle Änderungen gespeichert' : 'Noch nicht gespeichert'}</span>
        </div>
        <div className="topbar-actions">
          <button className="theme-toggle" type="button" aria-label={theme === 'light' ? 'Dunkles Design aktivieren' : 'Helles Design aktivieren'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>
            <span aria-hidden="true">{theme === 'light' ? '◐' : '◑'}</span>
          </button>
          <button className="shell-button" type="button" disabled={projectBusy} onClick={() => void showProjectDialog('open')}>Öffnen …</button>
          <button className="shell-button copy-project" type="button" disabled={projectBusy} onClick={() => void showProjectDialog('save-as')}>Kopie speichern …</button>
          <button className="shell-button primary" type="button" disabled={projectBusy || Boolean(projectId && !hasUnsavedChanges)} onClick={() => void saveCurrentProject()}>
            {projectBusy ? 'Bitte warten …' : 'Speichern'}
          </button>
        </div>
      </header>
      <main id="workspace" ref={workspaceRef} tabIndex={-1} className="workspace">
      {projectError && !projectDialogMode && <div className="project-feedback error" role="alert"><span>{projectError}</span><button className="shell-button" type="button" disabled={projectBusy} onClick={() => void saveCurrentProject()}>Erneut speichern</button></div>}
      {projectStatus && !projectError && <p className="project-feedback" role="status">{projectStatus}{hasUnsavedChanges ? ' · Neue Änderungen noch nicht gespeichert' : ''}</p>}

      {settingsOpen && (
        <ActivitySettingsDialog
          projectId={projectId}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {projectDialogMode && (
        <ProjectDialog
          mode={projectDialogMode}
          currentName={projectName}
          currentDescription={projectDescription}
          projects={projects}
          busy={projectBusy}
          error={projectError}
          hasUnsavedChanges={hasUnsavedChanges}
          onRetry={() => void showProjectDialog('open')}
          onCancel={() => { if (!projectOperation.current) { setProjectDialogMode(null); setProjectError(null) } }}
          onSave={(name, description) => void saveProjectAs(name, description)}
          onOpen={(selectedProjectId) => void openStoredProject(selectedProjectId)}
          onDelete={(selectedProjectId) => void removeStoredProject(selectedProjectId)}
        />
      )}

      {viewMode === 'menu' ? (
        <section className="chooser" aria-labelledby="chooser-title">
          <p className="eyebrow">Raumfahrt · Orbitalmechanik · Simulation</p>
          <h1 id="chooser-title">Deine nächste Mission.</h1>
          <p className="intro">Plane eine Route durch das Sonnensystem. Prüfe die Flugbahn und erkunde deine Mission aus einer neuen Perspektive.</p>
          <div className="overview-actions">
            <button type="button" className="shell-button primary" onClick={() => navigate('calculation')}>{routeSections.length ? 'Planung fortsetzen' : 'Mission planen'}<span aria-hidden="true">↗</span></button>
            <button type="button" className="shell-button" disabled={projectBusy} onClick={() => void showProjectDialog('open')}>Projekt öffnen …</button>
          </div>
          <div className="overview-heading"><p className="eyebrow">Dein Arbeitsbereich</p><span>Ein Projekt. Alle Perspektiven.</span></div>
          <div className="choice-grid">
            {WORKSPACES.filter((workspace) => workspace.id !== 'menu').map((workspace) => (
              <button className="choice-card" key={workspace.id} type="button" onClick={() => navigate(workspace.id)}>
                <span className="choice-number">{workspace.number}</span>
                <strong>{workspace.label}<span aria-hidden="true">↗</span></strong>
                <span>{workspace.id === 'calculation' ? 'Definiere deine Route, bearbeite Zielkorridore und berechne mögliche Flugbahnen.' : workspace.id === '2d' ? 'Prüfe Bahnen und berechnete Missionsverläufe in Draufsicht und Seitenansicht.' : 'Entdecke Planeten, betrachte deine Route im Raum und spiele berechnete Missionen ab.'}</span>
              </button>
            ))}
          </div>
          <section className="mission-overview" aria-label="Aktueller Missionsstand">
            <div><p className="eyebrow">Aktuelle Mission</p><h2>{projectName || 'Raum für deine Ideen.'}</h2><p>{routeSections.length ? 'Dein Routenplan ist in allen Ansichten verfügbar.' : 'Beginne im Berechnungsbereich mit deinem ersten Routenabschnitt.'}</p></div>
            <dl><div><dt>Routenabschnitte</dt><dd>{String(routeSections.length).padStart(2, '0')}</dd></div><div><dt>Berechnung</dt><dd>{plannedRoute ? 'Vorhanden' : 'Noch offen'}</dd></div></dl>
          </section>
        </section>
      ) : (
        <Suspense fallback={<div className="loading">Ansicht wird geladen …</div>}>
          {viewMode === 'calculation' || viewMode === '2d'
            ? (
              <TwoDView
                projectId={projectId}
                routeSections={routeSections}
                onRouteSectionsChange={updateRouteSections}
                activeRouteSectionId={activeRouteSectionId}
                onActiveRouteSectionChange={setActiveRouteSectionId}
                plannedMissionDate={plannedMissionDate}
                plannedRoute={plannedRoute}
                onApplyPlannedSolution={applyPlannedSolution}
                missionConfig={missionConfig}
                displayOnly={viewMode === '2d'}
              />
            )
            : (
              <ThreeDView
                routeSections={routeSections}
                entryCorridor={activeRouteSection?.corridor ?? DEFAULT_ROUTE_SECTION.corridor}
                onEntryCorridorChange={setEntryCorridor}
                waypointId={activeRouteSection?.targetId ?? ''}
                onWaypointChange={setWaypointId}
                plannedMissionDate={plannedMissionDate}
                onPlannedMissionDateChange={setPlannedMissionDate}
                plannedRoute={plannedRoute}
                onPlannedRouteChange={setPlannedRoute}
                onOpenRoutePlanner={() => setViewMode('calculation')}
                onOpenRouteSelector={() => setViewMode('calculation')}
                restoredMissionConfig={missionConfig}
                restoredVisualConfig={visualConfig}
                restoredMissionResult={missionResult}
                projectLoadToken={projectLoadToken}
                onMissionConfigChange={setMissionConfig}
                onVisualConfigChange={setVisualConfig}
                onMissionResultChange={setMissionResult}
                displayOnly
              />
            )}
        </Suspense>
      )}
      {solverRouteDialogOpen && viewMode === '3d' && (
        <Suspense fallback={<div className="solver-route-launcher"><div>Solver-Auswahl wird geladen …</div></div>}>
          <TwoDView
            projectId={projectId}
            routeSections={routeSections}
            onRouteSectionsChange={updateRouteSections}
            activeRouteSectionId={activeRouteSectionId}
            onActiveRouteSectionChange={setActiveRouteSectionId}
            plannedMissionDate={plannedMissionDate}
            plannedRoute={plannedRoute}
            onApplyPlannedSolution={applyPlannedSolution}
            missionConfig={missionConfig}
            solverDialogOnly
            onSolverDialogClose={() => setSolverRouteDialogOpen(false)}
          />
        </Suspense>
      )}
      </main>
      </div>
    </div>
  )
}
