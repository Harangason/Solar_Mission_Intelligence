# Skriptfuehrer

Diese Datei erklaert die Python-Dateien im Projektstamm. Ausfuehrlichere
Erklaerungen stehen direkt in den jeweiligen Codeordnern.

## Der eigentliche Einstiegspunkt

### `main.py`

Startet das Flask-Backend auf Port `5001`, verbindet die Fachpakete und liefert
den gebauten Webclient aus `web/dist/` aus. Die Datei enthaelt die HTTP-Endpunkte
fuer:

- Sonnensystem- und Ephemeridendaten,
- Projekte und gespeicherte Berechnungslaeufe,
- Missionen, Routen, Startfenster und Energiebewertungen,
- Aktivitaets- und Berechnungsaudits,
- optionale AI-, Audio- und ML-Funktionen.

`main.py` rechnet die Bahnen nicht selbst. Die Endpunkte validieren und
uebersetzen HTTP/JSON-Daten und rufen anschliessend `planner/`, `solver/`,
`services/`, `visualization/` oder `ai/` auf. Direkter Start:

```powershell
.\.venv\Scripts\python.exe main.py
```

## Kompatibilitaetsmodule

Die folgenden Dateien bestehen absichtlich nur aus wenigen Zeilen. Sie leiten
alte Imports auf die neue Paketstruktur um. Neuer Code sollte den Zielpfad aus
der rechten Spalte importieren.

| Datei | Eigentliche Implementierung |
| --- | --- |
| `activity_log.py` | `services.activity_log` |
| `calculation_audit.py` | `services.calculation_audit` |
| `ephemeris.py` | `solver.ephemeris` |
| `generic_route_planner.py` | `planner.generic_route_planner` |
| `mission_optimizer.py` | `planner.mission_optimizer` |
| `multi_route_planner.py` | `planner.multi_route_planner` |
| `nbody_propagation.py` | `solver.nbody_propagation` |
| `project_store.py` | `services.project_store` |
| `propulsion.py` | `models.propulsion` |
| `route_planner.py` | `planner.route_planner` |
| `satellite.py` | `models.satellite` |
| `universe.py` | `models.universe` |
| `view_2d_celestials.py` | `visualization.view_2d_celestials` |
| `view_3d_celestials.py` | `visualization.view_3d_celestials` |

Fuer Trajektorien gibt es keinen alten Alias mehr. Verwende ausschliesslich:

```python
from solver.trajectory import simulate_mission
```

## Dokumentation nach Ordner

- [`ai/README.md`](ai/README.md): AI-Agenten, Vertraege und Evaluation
- [`models/README.md`](models/README.md): Fach- und Antriebsmodelle
- [`planner/README.md`](planner/README.md): Routenplanung und Optimierung
- [`services/README.md`](services/README.md): Persistenz und Auditlogs
- [`solver/README.md`](solver/README.md): Numerische Berechnungen
- [`visualization/README.md`](visualization/README.md): Backend-Darstellung
- [`scripts/README.md`](scripts/README.md): Manuell gestartete Werkzeuge
- [`tests/README.md`](tests/README.md): Python-Tests
- [`web/README.md`](web/README.md): Frontend und seine Unterordner
