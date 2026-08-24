# Die Skripte in `services/`

Die Services kapseln lokale SQLite-Persistenz sowie append-only JSONL-Protokolle.
Sie pruefen Daten an ihrer Grenze, fuehren aber keine Bahnrechnung durch.

## `__init__.py`

Markiert das Paket fuer Persistenz-, Aktivitaets- und Auditdienste.

## `project_store.py`

Implementiert `ProjectStore` fuer versionierte Projektsnapshots in
`data/solar_simulator.db`. Der Service validiert Name, Beschreibung und
Projektzustand und bietet Anlegen, Laden, Auflisten, Aktualisieren und Loeschen.
Zeitstempel werden in UTC geschrieben.

## `calculation_store.py`

Implementiert `CalculationStore`, die normalisierte Persistenz aller
Berechnungslaeufe und Varianten. Das Modul validiert UUIDs, zerlegt Vektoren und
umfangreiche Ergebnisse, speichert Geometrie- und Leistungsstatus getrennt und
kann komplette Laeufe oder einzelne Varianten wieder zusammensetzen. Schwere
Trajektorien werden fuer kompakte Uebersichten entfernt, bleiben aber in den
zugehoerigen Detailtabellen erhalten.

## `activity_log.py`

Schreibt bereinigte Aktivitaetseintraege nach `logs/activities.jsonl`, filtert
sie nach Projekt, Kategorie und Zeit und exportiert flache Skalarwerte als CSV.
Textlaengen und Mappinggroessen sind begrenzt, damit beliebige UI-Daten das Log
nicht unkontrolliert aufblaehen.

## `calculation_audit.py`

Schreibt getrennte JSONL-Nachweise fuer Routen, Startfensteroptimierung und
Playback. Neben Schreib- und Lesefunktionen enthaelt das Modul
`METHOD_DOCUMENTATION`, die ueber die API erklaert, welche Rechenmethode einen
Nachweis erzeugt hat. Auditlogs dokumentieren Ergebnisse, sind aber keine
alternative Quelle fuer Solverwerte.

