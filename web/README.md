# Das Frontend in `web/`

`web/` ist eine Vite-Anwendung mit React, TypeScript und Three.js. Sie visualisiert
Backenddaten und sendet Missions- und Routenauftraege per HTTP/JSON an
`main.py`. Frontend-Geometrie dient Darstellung und Vorpruefung; verbindliche
physikalische Ergebnisse stammen aus dem Python-Backend.

## Dateien im Ordner

### `package.json`

Definiert React/Three-Abhaengigkeiten sowie die Befehle `dev`, `build`,
`preview` und vier Konsistenztests. `package-lock.json` fixiert den aufgeloesten
Abhaengigkeitsbaum und wird nicht manuell bearbeitet.

### `vite.config.ts`

Konfiguriert Vite mit dem React-Plugin, legt den Entwicklungsserver auf Port
`30001` und leitet `/api` an das Flask-Backend auf Port `5001` weiter.
`vite.config.js` und `vite.config.d.ts` sind erzeugte JavaScript- und
Typdeklarationsvarianten derselben Konfiguration.

### `tsconfig.json` und `tsconfig.node.json`

Konfigurieren TypeScript fuer Browsercode beziehungsweise Vite-Konfiguration.
Sie legen strikte Pruefungen, JSX-Verarbeitung, Module und Buildreferenzen fest.

### `index.html`

Ist die HTML-Huelle mit dem DOM-Einstiegspunkt, in den `src/main.tsx` die
React-Anwendung einhaengt.

## Unterordner

- [`src/README.md`](src/README.md) erklaert jede Zustands-, Mathematik- und
  API-Datei.
- [`src/components/README.md`](src/components/README.md) erklaert jede
  React-Komponente.
- [`scripts/README.md`](scripts/README.md) erklaert die Frontend-Konsistenztests.
- `public/` enthaelt statische Kataloge, Texturen und Hintergruende, keine
  ausfuehrbaren Skripte.
- `dist/` ist der generierte Produktionsbuild und wird nicht manuell geaendert.

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

