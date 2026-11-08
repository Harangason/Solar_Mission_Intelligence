# Web

Zurueck zum [Dokumentationsindex](README.md).

`web/` ist ein Vite-Projekt mit React, TypeScript, Three.js,
`@react-three/fiber` und `@react-three/drei`.

## Wichtige Bereiche

| Pfad | Aufgabe |
| --- | --- |
| `src/App.tsx` | Oberflaechenkomposition und zentraler Zustand |
| `src/components/` | 2D-/3D-Ansichten, Wizards, Editoren und Dialoge |
| `src/components/RouteCalculationDialog.tsx` | Live-Analyse von Solvervarianten, Suchtrichter und Routenprojektion |
| `src/missionSimulation.ts` | Backendaufrufe und Missionsdaten |
| `src/orbitalMath.ts` | Darstellungsbezogene Orbitalmathematik |
| `src/targetAlignedProjection.ts` | Basiswechsel und Rueckprojektion fuer die Sonne-Ziel-Querebene |
| `src/components/SunwardCorridorView.tsx` | Interaktive Frontansicht eines lokalen Zielkorridors |
| `src/routeSections.ts` | Definition und Normalisierung von Routenabschnitten |
| `src/routeGeometryValidation.ts` | Zieltreffer-, Reihenfolge-, Zeit- und Kontinuitaetspruefung vor der Leistungsbewertung |
| `src/constellationGraph.ts` | Adaptives Suchfenster, Suchbudgets und zeitlich diverse Kandidatenauswahl |
| `public/` | Mondkatalog und statische Assets |
| `dist/` | vom Flask-Server ausgelieferter Produktionsbuild |

## Befehle

```powershell
cd web
npm run dev
npm run build
npm run test:constellation-graph
npm run test:route-geometry
npm run test:route-sketch
npm run test:target-projection
```

Der Produktionsbuild fuehrt zuerst `tsc -b` und danach Vite aus. Physikalische
Solverergebnisse kommen per HTTP/JSON aus `main.py`; Frontendmathematik dient
der Darstellung und Konsistenzpruefung.

Das Analyse-Popup unterscheidet `geometry-valid` und `performance-valid`.
Verworfene Diagnosebahnen werden gekennzeichnet und koennen weder automatisch
noch ueber die Variantenauswahl als geplante Loesung uebernommen werden.

Der Zielkorridor bietet Seite, Draufsicht und `Von der Sonne` als Ansichten
desselben raeumlichen Vektors. Der Schalter `3D - raeumliche Kontrolle`
wechselt mit unveraendertem Routen- und Korridorzustand in die Three.js-Szene.


## Oberfläche und Projektaktionen

Die Oberfläche orientiert sich an der Application-Site
(https://application-martin-drexler.vercel.app/): nummerierte Seitennavigation,
IBM Plex Sans, rechteckige Bedienelemente und Carbon-Farben. Die Schrift liegt
mit SIL-OFL-Lizenz in `web/public/fonts/`; ein externer Fontdienst ist nicht nötig.
`ui-harmony.css` enthält die gemeinsame Gestaltung und den Hell-/Dunkelmodus.
Die astronomischen Canvas- und SVG-Flächen behalten ihre wissenschaftliche Farbgebung.

Übersicht, Berechnung, 2D und 3D verwenden denselben Projektzustand.
Auf schmalen Bildschirmen lässt sich die Navigation über einen Menüknopf öffnen.
Projekt öffnen, Speichern und Kopie speichern sind in jeder Ansicht verfügbar.
Der Speicherstatus berücksichtigt Änderungen während einer laufenden Speicherung.
Beim Öffnen über ungespeicherten Änderungen und beim Löschen erscheint eine
Bestätigung. Laufende Projektaktionen sperren Doppelklicks, Auswahl und Dialogschließen;
Serverfehler bleiben mit einer Wiederholungsmöglichkeit sichtbar. Projektanfragen
haben ein Zeitlimit von 20 Sekunden. In der Abschnittsliste sind Auswählen,
Bearbeiten und Löschen getrennte Aktionen.

Zusätzliche Regressionstests:

```powershell
npm run test:project-requests
npm run test:ui-assets
```

Der Assettest prüft alle in `planetTextures.ts` referenzierten JPEGs. Eine fehlende
Jupiter-Textur aus dem zusammengeführten `main`-Stand wurde wiederhergestellt,
da sie die 3D-Ansicht beim Laden abbrechen ließ.
