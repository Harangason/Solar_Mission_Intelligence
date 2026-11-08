import { useState } from 'react'

import { ROUTE_INTERSTELLAR_SYSTEMS } from '../interstellarTargets'
import type { MoonData, PlanetData } from '../types'
import { routePassage, type RouteSectionDefinition } from '../routeSections'
import { RouteSectionWizard } from './RouteSectionWizard'

interface RouteSectionListProps {
  planets: PlanetData[]
  moons: MoonData[]
  sections: RouteSectionDefinition[]
  activeSectionId: string
  suggestedOriginId: string
  suggestedTargetId: string
  onCreate: (section: RouteSectionDefinition) => void
  onUpdate: (section: RouteSectionDefinition) => void
  onEdit: (sectionId: string) => void
  onPreview: (sectionId: string) => void
  onDelete: (sectionId: string) => void
  onMove: (sectionId: string, direction: -1 | 1) => void
}

function objectName(objectId: string, planets: PlanetData[], moons: MoonData[]) {
  if (objectId === 'sun') return 'Sonne'
  return planets.find((planet) => planet.id === objectId)?.name
    ?? moons.find((moon) => moon.id === objectId)?.name
    ?? ROUTE_INTERSTELLAR_SYSTEMS.find((system) => system.id === objectId)?.name
    ?? objectId
}

export function RouteSectionList({
  planets,
  moons,
  sections,
  activeSectionId,
  suggestedOriginId,
  suggestedTargetId,
  onCreate,
  onUpdate,
  onEdit,
  onPreview,
  onDelete,
  onMove,
}: RouteSectionListProps) {
  const [wizardOpen, setWizardOpen] = useState(false)
  const [editingSection, setEditingSection] = useState<RouteSectionDefinition | null>(null)
  const [editingStep, setEditingStep] = useState<1 | 3>(1)
  const [deleteSectionId, setDeleteSectionId] = useState<string | null>(null)

  return (
    <section className="route-section-list" aria-labelledby="route-sections-title">
      <header>
        <div>
          <p className="eyebrow">Abschnittsplanung</p>
          <h2 id="route-sections-title">Routenabschnitte</h2>
        </div>
        <button type="button" className="route-section-new" onClick={() => setWizardOpen(true)}>+ Neu</button>
      </header>

      <div className="route-section-table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Nr.</th>
              <th scope="col">Verbindung</th>
              <th scope="col">Zielkorridor</th>
              <th scope="col">Passage</th>
              <th scope="col">Δv-Fächer</th>
              <th scope="col"><span className="visually-hidden">Aktionen</span></th>
            </tr>
          </thead>
          <tbody>
            {sections.map((section, index) => {
              const isActive = section.id === activeSectionId
              const passage = routePassage(section)
              return (
                <tr key={section.id} className={isActive ? 'active' : ''}>
                  <td>
                    <div className="route-section-index-cell">
                      <span className="route-section-number">{String(index + 1).padStart(2, '0')}</span>
                      <div className="route-section-order" role="group" aria-label={`Abschnitt ${index + 1} verschieben`}>
                        <button
                          type="button"
                          title="Nach oben"
                          aria-label={`Abschnitt ${index + 1} nach oben verschieben`}
                          disabled={index === 0}
                          onClick={() => onMove(section.id, -1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          title="Nach unten"
                          aria-label={`Abschnitt ${index + 1} nach unten verschieben`}
                          disabled={index === sections.length - 1}
                          onClick={() => onMove(section.id, 1)}
                        >
                          ↓
                        </button>
                      </div>
                    </div>
                  </td>
                  <td>
                    <strong>{objectName(section.originId, planets, moons)} → {objectName(section.targetId, planets, moons)}</strong>
                    {isActive && <small>Aktiv in 2D und 3D</small>}
                  </td>
                  <td>
                    {section.corridor.enabled && section.corridor.blocked
                      ? <span className="route-warning">Gesperrt</span>
                      : section.corridor.enabled
                      ? `±${section.corridor.horizontalHalfAngleDeg.toFixed(0)}° / ±${section.corridor.verticalHalfAngleDeg.toFixed(0)}°`
                      : 'Deaktiviert'}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="route-passage-edit"
                      title="Passage bearbeiten"
                      onClick={() => {
                        onEdit(section.id)
                        setEditingStep(3)
                        setEditingSection(section)
                      }}
                    >
                      {passage.mode === 'full-orbit'
                        ? `Voll · 360° ${passage.orbitDirection === 'prograde' ? 'prograd' : 'retrograd'}`
                        : passage.mode === 'partial-orbit'
                          ? `Teil · ${passage.orbitAngleDeg.toFixed(0)}° ${passage.orbitDirection === 'prograde' ? 'prograd' : 'retrograd'}`
                          : 'Direkt'}
                    </button>
                  </td>
                  <td>−{section.deltaVMinusKmS.toFixed(1)} / +{section.deltaVPlusKmS.toFixed(1)} km/s</td>
                  <td>
                    <div className="route-section-actions">
                      <button type="button" className={isActive ? 'selected' : ''} aria-pressed={isActive} onClick={() => onEdit(section.id)}>
                        {isActive ? 'Ausgewählt' : 'Auswählen'}
                      </button>
                      <button type="button" onClick={() => { onEdit(section.id); setEditingStep(1); setEditingSection(section) }}>Bearbeiten</button>
                      <button type="button" onClick={() => onPreview(section.id)}>Route ansehen</button>
                      {deleteSectionId === section.id ? <span className="route-delete-confirmation" role="group" aria-label={`Abschnitt ${index + 1} wirklich löschen?`}>
                        <span>Abschnitt löschen?</span>
                        <button type="button" autoFocus onClick={() => setDeleteSectionId(null)}>Abbrechen</button>
                        <button type="button" className="danger" onClick={() => { onDelete(section.id); setDeleteSectionId(null) }}>Bestätigen</button>
                      </span> : <button type="button" className="danger" onClick={() => setDeleteSectionId(section.id)}>Löschen …</button>}
                    </div>
                  </td>
                </tr>
              )
            })}
            {sections.length === 0 && (
              <tr className="route-section-empty-row">
                <td colSpan={6}>Noch keine Route geplant. Lege über „+ Neu“ deinen ersten Abschnitt an.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {wizardOpen && (
        <RouteSectionWizard
          planets={planets}
          moons={moons}
          suggestedOriginId={suggestedOriginId}
          suggestedTargetId={suggestedTargetId}
          onCancel={() => setWizardOpen(false)}
          onSubmit={(section) => {
            onCreate(section)
            setWizardOpen(false)
          }}
        />
      )}
      {editingSection && (
        <RouteSectionWizard
          planets={planets}
          moons={moons}
          suggestedOriginId={editingSection.originId}
          suggestedTargetId={editingSection.targetId}
          initialSection={editingSection}
          initialStep={editingStep}
          mode="edit"
          onCancel={() => setEditingSection(null)}
          onSubmit={(section) => {
            onUpdate(section)
            setEditingSection(null)
          }}
        />
      )}
    </section>
  )
}
