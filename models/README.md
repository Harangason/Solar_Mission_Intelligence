# Die Skripte in `models/`

Hier liegen zustandsarme Fachobjekte. Sie kennen weder Flask noch Datenbank oder
UI und koennen deshalb von Solver, Planner und Visualisierung gemeinsam genutzt
werden.

## `__init__.py`

Markiert den Ordner als Python-Paket. Die Fachklassen werden bewusst aus ihren
konkreten Modulen importiert.

## `universe.py`

Definiert die abstrakte Basisklasse `CelestialBody` sowie `Planet`, `Moon` und
`Star`. Die Objekte tragen physikalische Kerndaten wie Masse, Radius, Position
und Geschwindigkeit. Das Modul bildet damit das gemeinsame Vokabular fuer
Himmelskoerper, fuehrt aber selbst keine Ephemeriden- oder Bahnrechnung aus.

## `satellite.py`

Modelliert das Raumfahrzeug und seinen Zustand:

- Missionsphasen und Leistungsmodi,
- dreidimensionale Vektoren,
- Start-, Kick- und Oberth-Stufen,
- Hitzeschild und Nutzlastsonde,
- Tether und Electric Sail,
- den zusammengesetzten `Satellite` samt `SatelliteState`.

Die Klassen liefern insbesondere Massen, Komponentenstatus und
Phasenuebergaenge, die `solver/trajectory.py` fuer die Simulation benoetigt.

## `propulsion.py`

Definiert einheitliche Antriebsmodule und deren Ergebnisstruktur. Implementiert
sind impulsive, elektrische, nuklear-elektrische, Solar-Sail-, Electric-Sail-,
nuklear-thermische und konzeptionelle Varianten sowie eine rein visuelle
Warp-Variante. `build_propulsion_modules()` erzeugt Module aus JSON-Konfiguration,
`default_propulsion_modules()` liefert Startwerte und `PropulsionSystem`
kombiniert die Beitraege. Technologie-Reifegrade bleiben Teil des Ergebnisses.

