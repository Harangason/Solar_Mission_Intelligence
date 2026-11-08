import { useEffect, useMemo, useRef, useState } from 'react'

import {
  activityCsvUrl,
  logActivity,
  requestActivities,
  type ActivityEntry,
} from '../activityLog'

interface ActivitySettingsDialogProps {
  projectId: string
  onClose: () => void
}

const TEMP_API_KEY_STORAGE_KEY = 'solar-system-temporary-api-key'

function activityValues(entry: ActivityEntry) {
  const values = Object.entries(entry.values)
  if (values.length === 0) return '–'
  return values.slice(0, 4).map(([key, value]) => `${key}: ${String(value)}`).join(' · ')
}

export function ActivitySettingsDialog({ projectId, onClose }: ActivitySettingsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [activities, setActivities] = useState<ActivityEntry[]>([])
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('')
  const [onlyCurrentProject, setOnlyCurrentProject] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [temporaryApiKey, setTemporaryApiKey] = useState('')
  const [temporaryApiKeyStored, setTemporaryApiKeyStored] = useState(false)
  const filters = useMemo(() => ({
    category,
    status,
    projectId: onlyCurrentProject ? projectId : '',
  }), [category, onlyCurrentProject, projectId, status])

  const loadActivities = async () => {
    setLoading(true)
    setError('')
    try {
      setActivities(await requestActivities({ ...filters, limit: 500 }))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const dialog = dialogRef.current
    dialog?.showModal()
    logActivity({
      category: 'settings',
      action: 'activity-log-opened',
      projectId,
    })
    return () => {
      if (dialog?.open) dialog.close()
    }
  }, [projectId])

  useEffect(() => {
    setTemporaryApiKeyStored(Boolean(window.sessionStorage.getItem(TEMP_API_KEY_STORAGE_KEY)))
  }, [])

  useEffect(() => {
    void loadActivities()
  }, [filters])

  const saveTemporaryApiKey = () => {
    const trimmedKey = temporaryApiKey.trim()
    if (!trimmedKey) return
    window.sessionStorage.setItem(TEMP_API_KEY_STORAGE_KEY, trimmedKey)
    setTemporaryApiKey('')
    setTemporaryApiKeyStored(true)
    logActivity({
      category: 'settings',
      action: 'temporary-api-key-saved',
      projectId,
      details: { storage: 'sessionStorage' },
    })
  }

  const clearTemporaryApiKey = () => {
    window.sessionStorage.removeItem(TEMP_API_KEY_STORAGE_KEY)
    setTemporaryApiKey('')
    setTemporaryApiKeyStored(false)
    logActivity({
      category: 'settings',
      action: 'temporary-api-key-cleared',
      projectId,
      details: { storage: 'sessionStorage' },
    })
  }

  return (
    <dialog
      ref={dialogRef}
      className="activity-settings-dialog"
      aria-labelledby="activity-settings-title"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <header>
        <div>
          <small>Einstellungen</small>
          <h2 id="activity-settings-title">Aktivitätsprotokoll</h2>
        </div>
        <button type="button" aria-label="Settings schließen" onClick={onClose}>×</button>
      </header>

      <section className="temporary-api-key-panel" aria-labelledby="temporary-api-key-title">
        <div>
          <small>Lokaler API-Key</small>
          <h3 id="temporary-api-key-title">Temporäre API-Key Eingabe</h3>
          <p>Der Key wird nur lokal in diesem Browser-Tab gespeichert und automatisch gelöscht, sobald du den Tab oder das Fenster schließt.</p>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            saveTemporaryApiKey()
          }}
        >
          <label>
            <span>API-Key</span>
            <input
              type="password"
              value={temporaryApiKey}
              onChange={(event) => setTemporaryApiKey(event.target.value)}
              placeholder={temporaryApiKeyStored ? 'Temporärer Key ist gesetzt' : 'API-Key nur für diese Sitzung'}
              autoComplete="off"
              spellCheck={false}
              aria-describedby="temporary-api-key-note"
            />
          </label>
          <div className="temporary-api-key-actions">
            <button type="submit" disabled={!temporaryApiKey.trim()}>Temporär speichern</button>
            <button type="button" disabled={!temporaryApiKeyStored && !temporaryApiKey} onClick={clearTemporaryApiKey}>Jetzt löschen</button>
          </div>
          <output id="temporary-api-key-note" className={temporaryApiKeyStored ? 'stored' : ''}>
            {temporaryApiKeyStored ? 'Temporärer API-Key aktiv. Nicht im Projekt gespeichert.' : 'Kein temporärer API-Key gespeichert.'}
          </output>
        </form>
      </section>

      <div className="activity-settings-toolbar">
        <label>
          <span>Kategorie</span>
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">Alle</option>
            <option value="ui">UI</option>
            <option value="project">Projekt</option>
            <option value="calculation">Berechnung</option>
            <option value="playback">Missionslauf</option>
            <option value="settings">Settings</option>
          </select>
        </label>
        <label>
          <span>Status</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">Alle</option>
            <option value="success">Erfolgreich</option>
            <option value="rejected">Abgelehnt</option>
            <option value="error">Fehler</option>
          </select>
        </label>
        <label className="activity-project-filter">
          <input
            type="checkbox"
            checked={onlyCurrentProject}
            disabled={!projectId}
            onChange={(event) => setOnlyCurrentProject(event.target.checked)}
          />
          <span>Aktuelles Projekt</span>
        </label>
        <button type="button" onClick={() => void loadActivities()}>Aktualisieren</button>
        <a
          className="activity-csv-download"
          href={activityCsvUrl(filters)}
          download="solar-system-activities.csv"
          onClick={() => logActivity({
            category: 'settings',
            action: 'activity-csv-exported',
            projectId,
            details: filters,
          })}
        >
          CSV herunterladen
        </a>
      </div>

      {error && <p className="activity-settings-error" role="alert">{error}</p>}
      <div className="activity-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Zeit</th>
              <th>Quelle</th>
              <th>Kategorie</th>
              <th>Aktion</th>
              <th>Status</th>
              <th>Dauer</th>
              <th>Werte</th>
            </tr>
          </thead>
          <tbody>
            {activities.map((entry) => (
              <tr key={entry.id}>
                <td>{new Date(entry.timestampUtc).toLocaleString('de-DE')}</td>
                <td>{entry.source}</td>
                <td>{entry.category}</td>
                <td title={entry.message || undefined}>{entry.action}</td>
                <td><span className={`activity-status ${entry.status}`}>{entry.status}</span></td>
                <td>{entry.durationMs === null ? '–' : `${entry.durationMs.toFixed(1)} ms`}</td>
                <td title={JSON.stringify(entry.values)}>{activityValues(entry)}</td>
              </tr>
            ))}
            {!loading && activities.length === 0 && (
              <tr><td colSpan={7} className="activity-empty">Noch keine passenden Aktivitäten.</td></tr>
            )}
          </tbody>
        </table>
        {loading && <p className="activity-loading">Aktivitäten werden geladen …</p>}
      </div>

      <footer>
        <output>{activities.length} Einträge</output>
        <button type="button" onClick={onClose}>Schließen</button>
      </footer>
    </dialog>
  )
}
