# Analyse des aktuellen Tool-Aufbaus

Zurueck zum [Dokumentationsindex](README.md).

Diese Zusammenfassung beschreibt den aktuellen Aufbau des Solar System Mission
Simulator entlang der Hauptfunktionen. Sie basiert auf der bestehenden
Code-Struktur mit Flask-Backend, React-/Three.js-Frontend, deterministischen
Solvern, AI-Agenten, Persistenz und Audit-Protokollen.

## 1. Generell

Das Tool ist als lokale Webanwendung aufgebaut. `main.py` startet ein
Flask-Backend auf Port `5001`, liefert den gebauten Frontend-Ordner
`web/dist` aus und stellt die JSON-API bereit. Das Frontend liegt in `web/`
und ist ein Vite-Projekt mit React, TypeScript, Three.js,
`@react-three/fiber` und `@react-three/drei`.

Die fachliche Logik ist nach Verantwortlichkeiten getrennt:

| Bereich | Aufgabe |
| --- | --- |
| `models/` | Fachmodelle fuer Satellit, Antrieb und Himmelskoerper |
| `solver/` | Numerische Dynamik, Ephemeriden, Missionssimulation |
| `planner/` | Routenplanung, Lambert-/Flyby-/Solar-Oberth-Logik, Optimierung |
| `services/` | Projekte, Berechnungslaeufe, Aktivitaeten und Audits |
| `ai/` | KI-Dialog, Plausibilitaetspruefung, Vorschlagslogik, Audio |
| `visualization/` | Serverseitige 2D-Daten und Basisdaten fuer Himmelskoerper |
| `web/src/` | Interaktive Bedienoberflaeche, 2D-/3D-Ansichten und UI-Logik |
| `tests/` | Python-Tests fuer Solver, Planner, Services, AI und Audits |
| `scripts/` | Wartungs-, Trainings- und Auswertungsskripte |

Der zentrale Datenfluss ist:

1. Das Frontend sammelt Projektzustand, Routenabschnitte, Missionsparameter
   und Darstellungsoptionen.
2. API-Aufrufe gehen an `main.py`, zum Beispiel fuer Simulation,
   Routenplanung, Optimierung, Projekte, Aktivitaeten und AI.
3. `main.py` delegiert an `solver/`, `planner/`, `services/`,
   `visualization/` und `ai/`.
4. Ergebnisse werden als JSON an das Frontend zurueckgegeben und dort in 2D,
   3D, Tabellen, Dialogen oder Audit-Ansichten dargestellt.
5. Projekte und Berechnungsergebnisse werden ueber SQLite-nahe Services und
   JSONL-Auditdateien nachvollziehbar gespeichert.

Wichtige API-Bereiche in `main.py` sind:

| API | Zweck |
| --- | --- |
| `/api/solar-system` | Himmelskoerper- und Orbitdaten |
| `/api/view/2d` | Serverseitige 2D-Ansicht |
| `/api/mission/defaults` | Standard-Missionskonfiguration |
| `/api/mission/simulate` | Missionssimulation |
| `/api/route/simulate` | Wegpunkt-/Abschnittsroute |
| `/api/trajectory/plan` | Generischer Trajektorienplaner |
| `/api/mission/optimize-launch-window` | Startfenster- und Begegnungssuche |
| `/api/mission/assess-solar-energy` | Oberth-/Energiegrenzen |
| `/api/projects/*` | Projektpersistenz |
| `/api/calculations/*` | Berechnungslaeufe und Varianten |
| `/api/audit/*` | Rechen-, Optimierer- und Playback-Audits |
| `/api/ai/*` | KI-Dialog, Plausibilitaet, Vorschlaege, Audio, ML |

`web/src/App.tsx` ist der UI-Einstieg. Dort werden der globale Zustand,
Projektladen/-speichern, Aktivitaetslogging und der Wechsel zwischen Menu,
Berechnung, 2D und 3D koordiniert. Die 2D- und 3D-Ansichten werden lazy
geladen, damit die Anwendung beim Start nicht sofort alle Visualisierungsteile
laden muss.

## 2. KI

Die KI-Schicht liegt im Paket `ai/` und ist bewusst neben den deterministischen
Solvern platziert. Sie darf erklaeren, Vorschlaege machen und Suchraeume
priorisieren, aber keine physikalischen Solverwerte erfinden oder ueberschreiben.
Der Solver bleibt die Quelle der Wahrheit fuer Flugfaehigkeit, Delta-v,
Zieltreffer, Kollisionen und Bahndaten.

Die wichtigsten Module sind:

| Modul | Rolle |
| --- | --- |
| `ai/interaction_agent.py` | Missionschat und erlaubte UI-Aktionen |
| `ai/plausibility_agent.py` | Plausibilitaetspruefung mit Modellanteil und deterministischen Guardrails |
| `ai/calculation_agent.py` | Vorschlaege fuer Suchfenster, Kandidaten-Seeds und Strategien |
| `ai/audio_agent.py` | Transkription und Sprachsynthese |
| `ai/schemas.py` | Versionierte JSON-Schemas fuer KI-Vertraege |
| `ai/tool_contracts.py` | Allowlist fuer UI-Aktionen |
| `ai/audit_log.py` | Redigierte KI-Auditlogs |
| `ai/evaluation.py` | Training und Bewertung eines Kandidatenrankers |

Die KI-Endpunkte in `main.py` sind:

| API | Funktion |
| --- | --- |
| `/api/ai/mission-chat` | Dialogantworten und strukturierte UI-Aktionen |
| `/api/ai/transcribe` | Audio zu Text |
| `/api/ai/speech` | Text zu Audio |
| `/api/ai/plausibility-check` | Solver-/UI-Zustand pruefen |
| `/api/ai/calculation-suggest` | Suchraeume und Kandidaten vorschlagen |
| `/api/ai/ml/evaluation` | Kandidatenranker auswerten |
| `/api/ai/ml/train` | Kandidatenranker trainieren und speichern |
| `/api/ai/ml/model` | Persistentes ML-Modell lesen |
| `/api/ai/audit/*` | KI-Audit lesen oder herunterladen |

Die Berechnungs-KI arbeitet mit einem strikten Schema. Sie darf nur
Suchfenster, Kandidaten-Seeds, Strategien und Ablehnungshinweise liefern.
Felder wie `feasible`, `collisionFree`, `requiredInjectionDeltaVKmS` oder
`targetCorrectionDeltaVKmS` sind in KI-Vorschlaegen verboten. Jeder Vorschlag
setzt `requiresSolverValidation=true`.

Die Plausibilitaetspruefung kombiniert Modellurteil und deterministische
Pruefungen. Deterministische Befunde koennen durch ein positives Modellurteil
nicht aufgehoben werden. Damit bleibt die KI ein assistiver Navigator und kein
nachtraeglicher Freigabemechanismus.

## 3. Die Berechnungen

Die Berechnungen sind in drei Ebenen organisiert:

1. `solver/` berechnet physikalische Zustaende, Ephemeriden und
   Missionssimulationen.
2. `planner/` baut daraus Routen, Wegpunkte, Lambert-Transfers, Flybys,
   Solar-Oberth-Manoever und Optimierungen.
3. `services/` speichert Berechnungslaeufe, Varianten und Audit-Nachweise.

`solver/trajectory.py` enthaelt die Standard-Missionssimulation. Die
`MissionConfig` beschreibt Startdatum, Parkorbit, Massen, Perihelziel,
Oberth-Delta-v, Brenndauer, Isp, elektrische Segelparameter,
Navigationsrauschen, N-Body-Optionen und theoretische Antriebsmodi. Das
Ergebnis besteht aus Konfiguration, Ephemeridenstatus, Ereignissen,
Trajektorienpunkten und einer Summary mit Flugzeit, Perihel, Fluss,
Geschwindigkeiten, Treibstoff, Unsicherheit und Warnungen.

`solver/ephemeris.py` liefert Planetenzustaende. Der Modus wird ueber
`SOLAR_SIM_EPHEMERIS_MODE` gesteuert und kann `auto`, `spice` oder `kepler`
sein. Wenn SPICE verfuegbar ist, werden lokale Kernel verwendet; sonst greift
das System auf Kepler-nahe J2000-Elemente zurueck.

`solver/nbody_propagation.py` validiert bei Bedarf eine durchgaengige
N-Koerper-Bahn. Dabei wird nicht an der Einflusssphaere das Kraftmodell
gewechselt, sondern heliocentrisch mit Sonne und Planeten propagiert.

`planner/trajectory_planner.py` ist der einheitliche Einstieg fuer neue
Trajektorienplaene. Je nach Start, Ziel und Wegpunkten verzweigt er auf:

| Zieltyp | Behandlung |
| --- | --- |
| Koerper / Orbit | Body-to-body-Transfer oder Multi-Leg-Transfer |
| Flyby | Flyby-Zielroute |
| Richtung | Ausflug in eine Zielrichtung |
| Zone | Distanzereignis einer heliozentrischen Bahn |
| Grenze | Grenz-/Boundary-Ereignis |
| Zustandsvektor | Transfer zu freiem kartesischem Zielzustand |

`planner/route_planner.py` deckt Solar-Oberth-, Lambert-, Swing-by- und
Direktrouten ab. `planner/multi_route_planner.py` klassifiziert geordnete
Routenabschnitte und koppelt sie. `planner/generic_route_planner.py` verarbeitet
freie Abschnitte zwischen Sonne, Planeten, Monden und interstellaren Zielen.
`planner/mission_optimizer.py` sucht Startfenster, Begegnungstage und
Energiegrenzen.

Die Rechenkette trennt bewusst Geometrie und Leistungsbewertung:

1. Route und Abschnitte erfassen.
2. Konstellationsraum grob durchsuchen.
3. Abschnittsfolge, Zieltreffer, Zeitmonotonie, Kontinuitaet und Kollisionen
   pruefen.
4. Eintrittskorridore, Passageboegen und Austritte bestimmen.
5. Erst danach Delta-v, Antriebsbudget und Flugfaehigkeit bewerten.

Die internen Einheiten sind Kilometer, Kilometer pro Sekunde, Sekunden und
Tage. Das physikalische Koordinatensystem ist heliocentrisch, ekliptikal und
kartesisch. Visuelle Skalierungen duerfen die Solverwerte nicht veraendern.

## 4. 2D Darstellung

Die 2D-Darstellung besteht aus zwei Teilen:

1. `visualization/view_2d_celestials.py` erzeugt eine einfache serverseitige
   Matplotlib-Ansicht fuer `/api/view/2d`.
2. `web/src/components/TwoDView.tsx` ist die eigentliche interaktive
   2D-Oberflaeche im Browser.

Die 2D-Ansicht dient nicht nur als Grafik, sondern auch als Arbeitsoberflaeche
fuer Routenabschnitte, Eintrittskorridore, lokale Zielgeometrie und
Berechnungsdialoge. Sie verwendet Daten aus dem globalen React-Zustand und aus
den Planner-/Solver-Antworten.

Wichtige Frontend-Bausteine fuer die 2D-Arbeit sind:

| Datei | Aufgabe |
| --- | --- |
| `web/src/components/TwoDView.tsx` | Hauptansicht fuer Planeten, Route, Korridore und Dialoge |
| `web/src/components/TwoDPlanetDetails.tsx` | Detailinformationen zu Koerpern |
| `web/src/components/RouteSectionWizard.tsx` | Anlegen und Bearbeiten von Routenabschnitten |
| `web/src/components/RouteSectionList.tsx` | Abschnittsliste |
| `web/src/components/RoutePlanPreview.tsx` | Vorschau geplanter Routen |
| `web/src/components/RouteCalculationDialog.tsx` | Solvervarianten, Suchtrichter und Uebernahme |
| `web/src/components/SunwardCorridorView.tsx` | Zielkoerperbezogene Querebene aus Sonnensicht |
| `web/src/components/EntryCorridorEditor.tsx` | Bearbeitung des Eintrittskorridors |

Die 2D-Geometrie wird durch mehrere Hilfsdateien gestuetzt:

| Datei | Aufgabe |
| --- | --- |
| `web/src/orbitalMath.ts` | Darstellungsbezogene Orbitalmathematik |
| `web/src/targetAlignedProjection.ts` | Projektion in die Sonne-Ziel-Querebene |
| `web/src/entryCorridorGeometry.ts` | Korridorvektoren und lokale Geometrie |
| `web/src/routeGeometryValidation.ts` | Ziel-, Reihenfolge-, Zeit- und Kontinuitaetschecks |
| `web/src/routeSketchGeometry.ts` | Manuelle Skizzengeometrie |
| `web/src/routeSectionValidation.ts` | Validierung von Routenabschnitten |

Ein lokaler Zielkorridor kann in Draufsicht, Seitenansicht und einer
zielorientierten Querebene angezeigt werden. Die Querebene nutzt eine
orthonormale Basis aus der Richtung Sonne -> Ziel, Querachse und Hochachse.
Damit bleibt sichtbar, ob ein Eintrittspunkt wirklich aus allen drei
Vektorkomponenten passt.

Die 2D-Ansicht kann manuelle Routenskizzen speichern. Diese Skizzen werden im
Audit als visuelle Zusatzdaten gefuehrt und duerfen die Dynamik nicht heimlich
veraendern.

## 5. 3D Darstellung

Die 3D-Darstellung ist eine Three.js-Szene im React-Frontend. Zentrale Datei
ist `web/src/components/ThreeDView.tsx`; weitere Komponenten bauen Planeten,
Orbits, Monde, Trajektorien, Korridore und Fokusansichten ein.

Wichtige 3D-Komponenten sind:

| Datei | Aufgabe |
| --- | --- |
| `web/src/components/ThreeDView.tsx` | Hauptszene und 3D-Orchestrierung |
| `web/src/components/PlanetMesh.tsx` | Planetengeometrie und Texturen |
| `web/src/components/Orbit.tsx` | Orbitlinien |
| `web/src/components/MoonSystem.tsx` | Monde und lokale Mondsysteme |
| `web/src/components/MissionTrajectory.tsx` | Missionsbahn |
| `web/src/components/PlannedWaypointRoute.tsx` | Geplante Wegpunktroute |
| `web/src/components/DirectSolarRoute.tsx` | Direkte Solarroute |
| `web/src/components/EntryCorridorMarker.tsx` | Korridormarker in 3D |
| `web/src/components/FlybyFocusInset.tsx` | Lokaler Flyby-Fokus |
| `web/src/components/PlanetCameraControls.tsx` | Kameramodi und Zielkoerpersteuerung |
| `web/src/components/MilkyWayBackground.tsx` | Hintergrundasset |
| `web/src/components/InterstellarTargets.tsx` | Katalogziele und Richtungsstrahlen |

Die physikalischen Koordinaten kommen aus Solver und Planner in Kilometer und
werden nur fuer die Anzeige skaliert. Die Three.js-Achsenabbildung ist eine
Darstellungsabbildung, keine Aenderung der Rechendaten. Planetentexturen liegen
unter `web/public/assets/planets/`; die Herkunft ist in
`web/public/assets/planets/SOURCES.md` dokumentiert.

Die globale 3D-Ansicht bleibt heliocentrisch. Flybys werden in der
Hauptansicht als Teil der echten Trajektorie dargestellt. Fuer lokale
Nahbegegnungen gibt es zusaetzlich den `FlybyFocusInset`, der
planetenzentriert arbeitet und die SOI- oder Perizentrumsumgebung linear
skaliert. Dadurch lassen sich Kollisionsreserve, Eintritt, Perizentrum,
Austritt und Tangenten pruefen, ohne die globale Bahn zu verfremden.

Interstellare Ziele besitzen keine lokale Ephemeride. Sie werden als
hypothetische 50-AE-Richtungsstrahlen ab dem letzten realen Zustand angezeigt.
Das Tool trennt damit reale Teilroute und hypothetische Zielrichtung.

## 6. Simulation

Die Simulation ist der Laufzeitverbund aus UI-Zustand, Solveraufruf,
Berechnungsergebnis, Visualisierung und Audit.

Im Frontend stellt `web/src/missionSimulation.ts` die Standardkonfiguration,
Validierung und den API-Aufruf `/api/mission/simulate` bereit.
`web/src/launchOptimizer.ts` ruft `/api/mission/optimize-launch-window` und
`/api/mission/assess-solar-energy` auf. Der generische Trajektorienplaner wird
aus UI-Komponenten wie `TrajectoryPlannerPanel.tsx` ueber
`/api/trajectory/plan` angesprochen.

Eine typische Simulation laeuft so:

1. Nutzer legt Mission, Antrieb, Route, Korridore und Darstellungsoptionen im
   Frontend fest.
2. Das Frontend validiert Grundparameter, zum Beispiel Perihelabstand und
   elektrische Segelwerte.
3. Ein API-Aufruf geht an `main.py`.
4. `main.py` startet den passenden Solver oder Planner.
5. Ergebnis, Warnungen, Zusammenfassung und Trajektorie kommen als JSON
   zurueck.
6. Die UI aktualisiert 2D- und 3D-Ansicht, Detaildialoge und geplante Route.
7. Services schreiben Aktivitaeten, Berechnungsvarianten und Audits.

Es gibt mehrere Simulationsmodi:

| Modus | Einstieg |
| --- | --- |
| Standardmission | `/api/mission/simulate` -> `solver.simulate_mission()` |
| Wegpunktroute | `/api/route/simulate` -> `planner.simulate_waypoint_route()` oder Abschnittssimulation |
| Generischer Trajektorienplan | `/api/trajectory/plan` -> `planner.calculate_trajectory_plan()` |
| Startfensteroptimierung | `/api/mission/optimize-launch-window` -> `planner.optimize_launch_window()` |
| Solare Energiebewertung | `/api/mission/assess-solar-energy` -> `planner.assess_solar_energy()` |
| Playback-/Auditlauf | `/api/audit/playback/*` und JSONL-Protokolle |

Die Simulation unterscheidet Ergebnisqualitaeten. Eine geometrisch plausible
Route ist nicht automatisch leistungsfaehig. Delta-v-Budgets,
Zielinjektionen, Kollisionen, N-Body-Reste, Zielwinkel und Kontinuitaet werden
separat ausgewiesen. Energetisch oder geometrisch unmoegliche Kandidaten
koennen als Diagnose gespeichert werden, duerfen aber nicht als flugfaehige
Loesung uebernommen werden.

Die wichtigsten Nachweisdateien sind:

| Pfad | Inhalt |
| --- | --- |
| `logs/route_calculations.jsonl` | Routenberechnungen und Randdaten |
| `logs/mission_optimizer.jsonl` | Optimierersuche, Kandidaten und Ablehnungsgruende |
| `logs/mission_playback.jsonl` | Playback-Ereignisse |
| AI-spezifische Logs | KI-Aufrufe nach Rolle, redigiert und schemaorientiert |

Damit ist die Simulation nicht nur eine Animation, sondern eine nachvollziehbare
Rechenkette: Eingaben, Solverpfad, Modellgrenzen, Validierungen und angezeigte
Trajektorien bleiben miteinander verknuepft.
