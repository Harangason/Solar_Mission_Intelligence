# Die Skripte in `web/src/`

Dieser Ordner enthaelt den Einstieg, gemeinsame Typen, API-Clients und reine
Geometrie- beziehungsweise Zustandsfunktionen. Die sichtbaren UI-Bausteine sind
separat in [`components/README.md`](components/README.md) beschrieben.

## Anwendungseinstieg

### `main.tsx`

Laedt die globalen Styles und rendert `<App />` im Strict Mode in das Element
`#root`.

### `App.tsx`

Ist die oberste React-Komponente. Sie laedt Sonnen-, Planeten- und Monddaten,
haelt globalen Projekt-, Missions-, Ansichts- und Auswahlzustand und schaltet
zwischen faul geladenem `TwoDView` und `ThreeDView`. Projekt speichern/laden,
Fehleranzeigen und gemeinsame Dialoge werden hier koordiniert.

### `styles.css`

Enthaelt das komplette globale Layout und Styling fuer Werkzeugleisten,
Overlays, Dialoge, Formulare, 2D-Grafiken und responsive Ansichten.

### `types.ts`

Definiert die gemeinsame TypeScript-Datensprache fuer Sonne, Planeten, Monde,
Visualisierung, Antrieb, Missionskonfiguration, Ereignisse, Trajektorien und
Solverergebnisse. Die Interfaces spiegeln die JSON-Vertraege des Backends.

### `vite-env.d.ts`

Bindet Vites Standard-Typdeklarationen ein; die Datei enthaelt keine Logik.

## Backendkommunikation und Zustand

### `missionSimulation.ts`

Enthaelt `DEFAULT_MISSION_CONFIG`, clientseitige Grundvalidierung und den
POST-Aufruf an `/api/mission/simulate`. Aus fehlerhaften HTTP-Antworten werden
lesbare Exceptions.

### `launchOptimizer.ts`

Definiert Ein- und Ausgabetypen fuer Startfenster und solare Energiebewertung
und ruft die entsprechenden Backend-Endpunkte auf.

### `projectStore.ts`

Ist der HTTP-Client fuer Projekte: auflisten, laden, anlegen, aktualisieren und
loeschen. `ProjectState` beschreibt den speicherbaren Frontendzustand.

### `activityLog.ts`

Haelt die aktive Projekt-ID fuer Request-Header, sendet UI-Aktivitaeten ohne den
Bedienfluss zu blockieren, fragt gefilterte Eintraege ab und erzeugt die URL fuer
den CSV-Export.

### `playbackAudit.ts`

Definiert Playback-Ereignisse und Zustands-Snapshots und sendet Start- und
Folgeereignisse an die Audit-API.

### `routeSections.ts`

Definiert Routenabschnitte, Passagearten, Drehrichtung und Verhalten an
Abschnittsgrenzen. Liefert Standardwerte, `createRouteSection()` und eine
rueckwaertskompatible Normalisierung mit `routePassage()`.

### `routeSketchState.ts`

Enthaelt unveraenderliche Zustandsoperationen fuer den 3D-Skizzeneditor:
Linienendpunkt aktualisieren, ausgewaehltes Element entfernen und den letzten
Undo-Stand holen.

## Himmels- und Darstellungsgeometrie

### `orbitalMath.ts`

Berechnet vereinfachte zeitabhaengige Planetenpositionen aus Kepler-Elementen,
erzeugt Orbitpunkte und uebersetzt Kilometer in Three.js-Szenenkoordinaten.
`ANIMATION_DAYS_PER_SECOND` steuert die Standard-Zeitraffung.

### `moonMath.ts`

Berechnet stabile Darstellungsabstaende, zeitabhaengige Mondpositionen und
Orbitpolylinien aus dem Mondkatalog. Ein stabiler Hash verhindert, dass Monde
ohne vollstaendige Elemente bei jedem Rendern springen.

### `celestialCoordinates.ts`

Uebersetzt Rektaszension und Deklination aus J2000-Aequatorkoordinaten in die
ecliptische Three.js-Richtung und liefert Richtungen beziehungsweise
Darstellungspositionen interstellarer Ziele.

### `interstellarTargets.ts`

Definiert den Frontendkatalog interstellarer Richtungsziele sowie gefilterte
Listen fuer bekannte Exoplanetensysteme und die Routenauswahl.

### `planetTextures.ts`

Ordnet Planeten-IDs statischen Texturpfaden zu und liefert eine URL oder einen
leeren Fallback.

### `entryCorridorGeometry.ts`

Definiert den raeumlichen Eintrittskorridor, seine Standardwerte und die
orthogonale Basis. Es berechnet Richtungen und Randboegen und uebersetzt zwischen
physikalischem und Three.js-Koordinatensystem.

### `targetAlignedProjection.ts`

Erzeugt eine orthonormale Basis entlang `Sonne -> Ziel`, projiziert 3D-Vektoren
in diese Querebene und rekonstruiert sie wieder. Diese Funktionen halten 2D-
und 3D-Korridoransichten konsistent.

### `corridorFeasibility.ts`

Bewertet, ob ein Korridor den Zielkoerper schneidet, ausreichend Abstand haelt
und mit der lokalen Gravitationsreserve vereinbar ist. `withCorridorFeasibility()`
ergaenzt eine Korridordefinition um den Diagnosezustand.

### `routeSketchGeometry.ts`

Liefert reine Three.js-Hilfen fuer Punkte, Kreise, Rotationen, Achsen und
Raycast-basierte Verschiebe- beziehungsweise Drehgriffe des Routenskizzeneditors.

## Suche und Validierung

### `constellationGraph.ts`

Leitet adaptives Datumsfenster und Suchbudget aus Route und Zeitraum ab, baut
einen zeitlichen Kandidatengraphen, berechnet Dijkstra-Distanzen und waehlt
zeitlich diverse Kandidaten sowie Verfeinerungsnachbarn aus.

### `routeGeometryValidation.ts`

Prueft Solverresultate vor der Leistungsbewertung auf endliche Punkte,
Zieltreffer, Zeitmonotonie, Abschnittsindizes, Zustandskontinuitaet, Kollisionen
und Korridorverletzungen. Das Ergebnis unterscheidet klar Geometrie- von
Leistungsfehlern.

### `propulsionModels.ts`

Definiert Frontend-Standardmodule und benannte Antriebspresets. Hilfsfunktionen
wenden ein Preset oder eine komplette Modulkonfiguration unveraenderlich auf
eine Missionskonfiguration an.

