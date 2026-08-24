# Die Skripte in `visualization/`

Dieses kleine Backend-Paket stellt Daten und eine statische Grafik bereit. Die
interaktive Darstellung liegt unter `web/`.

## `__init__.py`

Markiert das Paket fuer serverseitige Ansichts- und Katalogfunktionen.

## `view_3d_celestials.py`

Definiert Sonne und Planeten samt Massen-, Radius-, Rotations-, Farb- und
vereinfachten Orbitaldaten. `get_solar_system_objects()` liefert Python-Objekte;
`get_solar_system_data()` serialisiert sie fuer `/api/solar-system`. Trotz des
Namens rendert die Datei keine Three.js-Szene, sondern liefert deren Datenbasis.

## `view_2d_celestials.py`

Erzeugt mit Matplotlib eine statische Draufsicht der Planetenbahnen und gibt sie
als PNG-Bytestrom zurueck. `main.py` stellt das Ergebnis unter `/api/view/2d`
bereit. Die Darstellung verwendet visuelle Skalierung und ersetzt keine
physikalische Trajektorie.

