# Die Skripte in `web/scripts/`

Diese kleinen Node-Skripte testen reine TypeScript-Geometrie ohne Browser und
ohne separates Testframework. Ein fehlgeschlagener Vergleich beendet den
Prozess mit Fehlercode.

## `constellation-graph-consistency.mjs`

Prueft Suchfenster und -budgets des zeitlichen Konstellationsgraphen,
Dijkstra-Distanzen, diverse Kandidatenauswahl und Nachbarschaften fuer die
adaptive Verfeinerung.

```powershell
cd web
npm run test:constellation-graph
```

## `route-geometry-validation.mjs`

Prueft die Frontend-Vorvalidierung einer Route: Zielresiduen, Zeitmonotonie,
Abschnittsreihenfolge, Zustandskontinuitaet und die Trennung zwischen
geometrischer und spaeterer Leistungsbewertung.

```powershell
npm run test:route-geometry
```

## `route-sketch-consistency.mjs`

Prueft die zustandslosen Operationen des 3D-Routenskizzeneditors, darunter
Verschieben eines Linienendes, Entfernen verschiedener Auswahlelemente und
Rueckgaengig-Historie.

```powershell
npm run test:route-sketch
```

## `target-aligned-projection-consistency.mjs`

Prueft die orthonormale Sonne-Ziel-Basis, Einheitslaengen und die verlustfreie
Rueckprojektion eines geneigten 3D-Korridorvektors.

```powershell
npm run test:target-projection
```

