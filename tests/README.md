# Die Tests in `tests/`

Die Suite verwendet Pythons `unittest`. Jeder Test isoliert schreibende Pfade
mit temporaeren Verzeichnissen oder Mocks, soweit Laufzeitdaten betroffen sind.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## AI und Audio

### `test_ai_phase1.py`

Prueft versionierte JSON-Schemas, gueltige und ungueltige Missionszustaende sowie
rollenbezogene, geheimnisbereinigte AI-Audits.

### `test_ai_phase2.py`

Prueft den Missionsdialog: Solverkontext und erlaubte UI-Aktionen muessen
weitergereicht, unbekannte Aktionen und erfundene Solverreferenzen abgewiesen
und auditiert werden.

### `test_ai_phase3.py`

Prueft die Plausibilitaetsstufe. Deterministische Solverfehler muessen ein zu
positives Modellurteil ueberstimmen; Datums- und Begegnungsinkonsistenzen werden
erkannt.

### `test_ai_phase4.py`

Prueft Berechnungsvorschlaege fuer Suchraeume und Seeds. Das Modell darf weder
Solverergebnisse erfinden noch unbekannte Routenabschnitte referenzieren.

### `test_ai_phase5.py`

Prueft Datennormalisierung, Training, Rankingqualitaet, Mindestdatenmenge sowie
Speichern und erneutes Laden des lokalen Kandidatenrankers.

### `test_ai_audio.py`

Prueft Transkription und Sprachsynthese mit gemockter API. Nicht unterstuetzte
Formate und zu lange Texte werden vor einem Netzwerkaufruf verworfen; Audits
enthalten nur Metadaten.

## Planner und Solver

### `test_generic_route_planner.py`

Die groesste Regressionstestsuite fuer freie Mehrabschnittsrouten. Sie deckt
Planeten-, Mond-, Sonnen- und interstellare Abschnitte, lokale Referenzrahmen,
Teil- und Vollumrundungen, Korridorkopplung, Kollisionsfreiheit, tangentiale
Austritte sowie die Trennung von Geometrie- und Leistungsbewertung ab.

### `test_multi_route_planner.py`

Prueft Klassifikation und Kopplung von Routenabschnitten, echte 3D-Korridore,
unverbundene Ketten, interstellare Endziele, fehlerhafte Eingaben und passive
Austritte ohne kuenstlichen Geschwindigkeitsgewinn.

### `test_entry_corridor_blocking.py`

Prueft, dass ein aktivierter und physikalisch blockierter Eintrittskorridor eine
Route verwirft, ein deaktivierter Korridor aber nicht angewendet wird.

### `test_mission_optimizer_energy.py`

Prueft die solare Energiebewertung fuer erreichbare, zu hohe und ungueltige
Austrittsgeschwindigkeiten.

### `test_nbody_propagation.py`

Prueft endliche N-Body-Beschleunigung, dichte kontinuierliche Propagation,
gedrehte Hyperbelrahmen und die Hin-und-zurueck-Abbildung von Korridorwinkeln.

### `test_ephemeris.py`

Prueft den kernelunabhaengigen Kepler-Modus, den Fehler im strikten SPICE-Modus
ohne Meta-Kernel und - falls lokale Standardkernel vorhanden sind - einen realen
Erde-Zustand.

## Persistenz, API und Protokolle

### `test_project_store.py`

Prueft Anlegen, Auflisten, Laden, versioniertes Aktualisieren, Validierung und
gezieltes Loeschen von Projekten.

### `test_calculation_store.py`

Prueft UUID- und Fremdschluessel-Schema, normalisierten Ergebnis-Roundtrip,
Projektloeschung mit erhaltenem Rechenlauf, adaptive Budgets und kaskadierendes
Loeschen eines Laufs.

### `test_calculation_api.py`

Prueft den HTTP-Roundtrip von Rechenlauf, Variante und normalisiertem Ergebnis
sowie Fehler fuer ungueltige UUIDs und unbekannte Projekte.

### `test_activity_log.py`

Prueft Schreiben, Filtern und Abfragen von Aktivitaeten, den CSV-Export mit
UTF-8-BOM und dynamischen Wertespalten sowie begrenztes Abflachen tiefer Daten.

### `test_playback_audit.py`

Prueft Aufbau und Rekonstruktion eines vollstaendigen Playback-Ereignisstroms
sowie die Ablehnung unbekannter Wiedergabe-IDs.

## Trainingswerkzeuge

### `test_train_catalog_routes.py`

Prueft den erwarteten Szenariokatalog aus sieben Planeten, zwoelf Monden und
einer chemischen Voyager-Route sowie die Datumsgrenze des Trainingsfensters.

