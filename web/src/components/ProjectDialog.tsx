import { useEffect, useRef, useState } from 'react'

import type { ProjectSummary } from '../projectStore'

interface ProjectDialogProps {
  mode: 'save-as' | 'open'
  currentName: string
  currentDescription: string
  projects: ProjectSummary[]
  busy: boolean
  error: string | null
  hasUnsavedChanges: boolean
  onRetry: () => void
  onCancel: () => void
  onSave: (name: string, description: string) => void
  onOpen: (projectId: string) => void
  onDelete: (projectId: string) => void
}

export function ProjectDialog({ mode, currentName, currentDescription, projects, busy, error,
  hasUnsavedChanges, onRetry, onCancel, onSave, onOpen, onDelete }: ProjectDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const cancelConfirmationRef = useRef<HTMLButtonElement>(null)
  const [name, setName] = useState(() => currentName ? `${currentName} – Kopie` : `Mission ${new Date().toLocaleDateString('de-DE')}`)
  const [description, setDescription] = useState(currentDescription)
  const [selectedProjectId, setSelectedProjectId] = useState('')
  const [confirmation, setConfirmation] = useState<'delete' | 'replace' | null>(null)
  // Resolve against the current list: deletion and async loading cannot leave a stale action target.
  const selected = projects.find((project) => project.id === selectedProjectId) ?? projects[0]

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    dialog.showModal()
    nameRef.current?.focus()
    return () => { if (dialog.open) dialog.close() }
  }, [])

  useEffect(() => {
    if (confirmation) cancelConfirmationRef.current?.focus()
  }, [confirmation])

  const close = () => {
    if (busy) return
    if (confirmation) setConfirmation(null)
    else onCancel()
  }
  const openSelected = () => {
    if (busy || !selected) return
    if (hasUnsavedChanges) setConfirmation('replace')
    else onOpen(selected.id)
  }

  return (
    <dialog ref={dialogRef} className="route-section-wizard project-dialog" aria-labelledby="project-dialog-title" aria-busy={busy}
      onCancel={(event) => { event.preventDefault(); close() }}>
      <header>
        <div><small>Projektverwaltung</small><h2 id="project-dialog-title">{mode === 'save-as' ? currentName ? 'Kopie speichern' : 'Mission speichern' : 'Projekt öffnen'}</h2></div>
        <button type="button" className="wizard-close" aria-label="Dialog schließen" disabled={busy} onClick={close}>×</button>
      </header>
      <form onSubmit={(event) => { event.preventDefault(); if (mode === 'save-as' && !busy && name.trim()) onSave(name.trim(), description.trim()) }}>
        <div className="wizard-content">
          {confirmation && selected ? (
            <section className="project-confirmation" aria-labelledby="confirmation-title">
              <h3 id="confirmation-title">{confirmation === 'delete' ? `„${selected.name}“ wirklich löschen?` : 'Ungespeicherte Änderungen verwerfen?'}</h3>
              <p>{confirmation === 'delete' ? 'Das gespeicherte Projekt wird dauerhaft entfernt. Ein bereits geöffneter Plan bleibt im Arbeitsbereich erhalten.' : `Beim Öffnen von „${selected.name}“ wird der aktuelle Plan ersetzt. Kehre zurück, um ihn zuerst zu speichern.`}</p>
            </section>
          ) : mode === 'save-as' ? (
            <fieldset className="project-save-fields" disabled={busy}>
              <legend>{currentName ? 'Eigenständige Projektkopie' : 'Neues Projekt'}</legend>
              <p>Routenplan, Konfiguration und vorhandene Ergebnisse werden gemeinsam gespeichert.</p>
              <label><span>Projektname</span><input ref={nameRef} value={name} required maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
              <label><span>Beschreibung</span><textarea value={description} maxLength={2000} rows={4} onChange={(event) => setDescription(event.target.value)} /></label>
            </fieldset>
          ) : (
            <fieldset disabled={busy}>
              <legend>Gespeicherte Projekte</legend>
              <div className="project-list" role="radiogroup" aria-label="Gespeicherte Projekte">
                {projects.map((project) => (
                  <label className={selected?.id === project.id ? 'selected' : ''} key={project.id}>
                    <input type="radio" name="project" value={project.id} checked={selected?.id === project.id} onChange={() => setSelectedProjectId(project.id)} />
                    <span><strong>{project.name}</strong><small>{project.routeSectionCount} Abschnitte · Revision {project.revision} · {new Date(project.updatedAtUtc).toLocaleString('de-DE')}</small>{project.description && <span>{project.description}</span>}</span>
                  </label>
                ))}
                {!busy && !error && projects.length === 0 && <p className="project-list-empty">Noch kein Projekt gespeichert. Plane eine Mission und wähle anschließend „Speichern“.</p>}
              </div>
            </fieldset>
          )}
          {busy && <p className="project-dialog-progress" role="status">Vorgang läuft … Bitte warten.</p>}
          {error && <div className="project-dialog-error" role="alert"><p>{error}</p>{mode === 'open' && !projects.length && <button className="shell-button" type="button" disabled={busy} onClick={onRetry}>Erneut laden</button>}</div>}
        </div>
        <footer>
          {confirmation && selected ? <>
            <button ref={cancelConfirmationRef} type="button" className="wizard-cancel" disabled={busy} onClick={() => setConfirmation(null)}>Zurück</button>
            <button type="button" className={confirmation === 'delete' ? 'danger confirm-danger' : 'primary'} disabled={busy} onClick={() => {
              if (confirmation === 'delete') onDelete(selected.id)
              else onOpen(selected.id)
              setConfirmation(null)
            }}>{confirmation === 'delete' ? 'Endgültig löschen' : 'Verwerfen und öffnen'}</button>
          </> : <>
            <button type="button" className="wizard-cancel" disabled={busy} onClick={close}>Abbrechen</button>
            <div>
              {mode === 'open' && selected && <button type="button" className="danger" disabled={busy} onClick={() => setConfirmation('delete')}>Projekt löschen …</button>}
              {mode === 'save-as'
                ? <button type="submit" className="primary" disabled={busy || !name.trim()}>{busy ? 'Speichert …' : currentName ? 'Kopie speichern' : 'Projekt speichern'}</button>
                : <button type="button" className="primary" disabled={busy || !selected} onClick={openSelected}>Projekt öffnen</button>}
            </div>
          </>}
        </footer>
      </form>
    </dialog>
  )
}
