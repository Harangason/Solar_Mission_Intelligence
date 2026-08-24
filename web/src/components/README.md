# Die Komponenten in `web/src/components/`

Jede Datei exportiert einen sichtbaren React-Baustein oder die zugehoerigen
Datentypen. Komponenten mit Three.js verwenden `@react-three/fiber`; reine
Bedienoberflaechen rendern normales HTML oder SVG.

## Hauptansichten

### `TwoDView.tsx`

Die zentrale 2D-Arbeitsansicht und groesste Frontendkomponente. Sie zeigt
Planetenbahnen in Drauf- und Seitenprojektion, verwaltet Routenabschnitte und
Korridore, startet adaptive Konstellations- und Solverlaeufe, validiert deren
Geometrie, fuehrt Kandidaten- und ML-Priorisierung zusammen und zeigt Vorschau,
Fortschritt, AI-Dialog und Ergebnisanalyse. Die vielen lokalen Hilfsfunktionen
erzeugen ausschliesslich Vorschaugeometrie; uebernommene Missionsdaten stammen
aus dem Backend.

### `ThreeDView.tsx`

Die zentrale interaktive Three.js-Ansicht. Sie verwaltet Kamera, Zeitablauf,
Mission und Routenwiedergabe, 3D-Routenskizzen, lokale Korridore, Startfenster-
und Energieberechnung sowie Playback-Audits. Die Datei setzt Sonne, Planeten,
Monde, Trajektorien, Zielstrahlen und Editoren zu einer Szene zusammen und kann
zwischen Uebersicht und zielbezogenen Kameras wechseln.

### `TwoDPlanetDetails.tsx`

Zeigt die Stammdaten eines in der 2D-Ansicht gewaehlten Planeten oder Mondes in
einem kompakten Detailpanel.

## Himmelskoerper und Szene

### `Sun.tsx`

Rendert die Sonne als Three.js-Kugel. `sunSceneRadius()` berechnet eine zur
Orbitdarstellung passende, begrenzte visuelle Groesse.

### `PlanetMesh.tsx`

Rendert einen texturierten, rotierenden Planeten an seiner zeitabhaengigen
Position, optional mit Saturnringen, Beschriftung und Klickauswahl.

### `Orbit.tsx`

Zeichnet aus den in `orbitalMath.ts` erzeugten Punkten eine Planetenbahn als
Three.js-Linie.

### `MoonSystem.tsx`

Rendert Mondbahnen und zeitabhaengige Mondpositionen relativ zum bewegten
Planeten und meldet eine Mondauswahl an die Elternkomponente.

### `MilkyWayBackground.tsx`

Legt die Milchstrassentextur auf eine grosse Innenkugel und richtet sie mit
J2000-Referenzrichtungen aus, damit interstellare Ziele zum Hintergrund passen.

### `InterstellarTargets.tsx`

Zeichnet interstellare Zielmarker und optionale Richtungsstrahlen in der
3D-Szene, behandelt Auswahl und zeigt verschiebbare Informationslabels.

## Missionen und berechnete Routen

### `MissionTrajectory.tsx`

Zeichnet die simulierte Mission bis zum aktuellen Wiedergabezeitpunkt, markiert
Ereignisse und Sonde und faerbt Phasen beziehungsweise Unsicherheiten anhand der
Solverdaten.

### `DirectSolarRoute.tsx`

Visualisiert das Ergebnis einer direkten Solarroute mit segmentierten Linien,
Sondenposition, Zielrichtung und verschiebbarem Informationslabel.

### `PlannedWaypointRoute.tsx`

Definiert den umfangreichen Frontendtyp `WaypointRouteResult` und visualisiert
Mehrabschnittsrouten. Es zeichnet aktive und verbleibende Bahnsegmente,
Wegpunkte, Passage- und Diagnosezustaende, Streuung, Navigationshilfe und die
zeitabhaengige Sondenposition.

### `FlybyFocusInset.tsx`

Zeigt fuer eine berechnete Begegnung eine separate, skalierbare 3D-Nahansicht
mit Koerper, Korridorboegen, Ein- und Austrittsrichtung und Kamerasteuerung.

## Routenplanung und Analyse

### `RouteSectionList.tsx`

Listet die vom Nutzer angelegten Routenabschnitte, fasst Passageparameter
lesbar zusammen und oeffnet den Wizard zum Anlegen oder Bearbeiten.

### `RouteSectionWizard.tsx`

Fuehrt schrittweise durch Ursprung, Ziel, Eintritts-/Austrittsverhalten,
direkte oder orbitale Passage, Drehrichtung, Winkel und Korridorzuordnung. Vor
dem Uebernehmen werden Zahlenbereiche und Objektbeziehungen normalisiert.

### `RouteCalculationDialog.tsx`

Zeigt einen laufenden oder abgeschlossenen Suchlauf. Das Dialogfenster
visualisiert Stufen, Budgets, Kandidatenstatus, Defizite, Shortlist,
Qualitaetsmetriken und projizierte Geometrie. Nur als gueltig gekennzeichnete
Varianten koennen ausgewaehlt und uebernommen werden.

### `RoutePlanPreview.tsx`

Ist ein 3D-Skizzeneditor fuer noch nicht berechnete Routen. Nutzer koennen
Punkte, Linien und orientierte Kreise anlegen, auswaehlen, verschieben und
drehen. `createRouteSketch()` erzeugt die Anfangsskizze; Gizmos arbeiten mit
Raycasting und melden atomare Aenderungen fuer Undo/Redo nach aussen.

## Eintrittskorridore und lokale Passage

### `PlanetCorridorPlanner.tsx`

Der uebergeordnete Korridor- und Passageeditor. Er verbindet globale Drauf- und
Seitenprojektion, sonnenorientierte Querebene und lokale 3D-Ansicht. Nutzer
waehlen Zielkoerper, Zentrum, Oeffnungswinkel, Rotation und lokale
Passageparameter; Machbarkeit und Zielrichtung werden laufend angezeigt.

### `SunwardCorridorView.tsx`

Zeigt und bearbeitet den Eintrittskorridor in der Querebene aus Richtung der
Sonne. Pointer- und Tastaturbewegungen werden ueber die Zielbasis in denselben
raeumlichen Vektor zurueckgerechnet.

### `EntryCorridorEditor.tsx`

Eigenstaendige Three.js-Nahansicht zum Drehen und Skalieren eines
Korridorkegels. Kamera und Ziehpunkte erlauben die direkte Bearbeitung von
Zentrum, horizontaler/vertikaler Halbbreite und Rotation.

### `EntryCorridorMarker.tsx`

Zeichnet den Korridorkegel beziehungsweise sein Drahtgitter an einem Zielkoerper
in der Hauptszene, einschliesslich Mittelachse und Beschriftung.

### `LocalPlanetThreeD.tsx`

Rendert eine lokale, nicht heliocentrisch skalierte Planetenansicht mit Monden,
Ein- und Austrittskorridoren und berechneter Passagebahn. Hilfsfunktionen bauen
lokale Rahmen, Randgeschwindigkeiten und direkte, Teil- oder Vollumrundungen.

## Bedienoberflaeche

### `ParameterPanel.tsx`

Stellt Missions-, Darstellungs- und Optimierungsparameter als Zahlenfelder,
Slider und Schalter bereit. Es startet Simulation, Startfenstersuche und
Energiebewertung und bindet den Antriebswizard ein.

### `PropulsionWizard.tsx`

Bearbeitet Antriebspresets und einzelne Module mit Technologie-Reifegrad,
Trocken- und Treibstoffmasse sowie modulspezifischen Parametern. Eine lokale
Kopie verhindert, dass Abbrechen die aktive Missionskonfiguration veraendert.

### `ProjectDialog.tsx`

Dialog zum Anlegen, Laden, Umbenennen, Beschreiben und Loeschen gespeicherter
Projekte. Fokus- und Escape-Behandlung machen ihn tastaturbedienbar.

### `ActivitySettingsDialog.tsx`

Zeigt filterbare Aktivitaetsprotokolle, CSV-Export und die temporaere lokale
Eingabe eines AI-API-Schluessels. Der Schluessel bleibt im Browserzustand und
wird nicht mit den Aktivitaetseintraegen angezeigt.

### `DraggableOverlayPanel.tsx`

Wiederverwendbare HTML-Huelle fuer verschiebbare Panels. Sie begrenzt die
Position auf den Viewport, respektiert interaktive Kindelemente und unterstuetzt
Pointer- sowie Tastaturbedienung.

### `DraggableInfoLabel.tsx`

Wiederverwendbares Three.js-HTML-Label mit Fuehrungslinie. Es kann relativ zu
seinem 3D-Anker verschoben werden, verhindert Ueberdeckung der Quelle und
begrenzt sich auf den sichtbaren Bereich.

### `PlanetCameraControls.tsx`

Kapselt OrbitControls und animierte Kamerafahrten. Es unterstuetzt
Systemuebersichten, Fokus auf einen Koerper und aus dessen Position abgeleitete
Ansichten wie `Von Sonne zum Ziel`, `Sonne dahinter` und Querachse.

