# Die Skripte in `solver/`

Hier liegt die numerische Dynamik. Solver duerfen Fachmodelle verwenden, kennen
aber keine HTTP-Endpunkte und importieren keine Planner.

## `__init__.py`

Markiert das Paket fuer Trajektorien-, N-Body- und Ephemeridenberechnungen.

## `trajectory.py`

Definiert die zentralen Datenklassen `MissionConfig`, `MissionEvent`,
`TrajectoryPoint`, `MissionSummary` und `MissionResult`. `simulate_mission()`
baut aus der Konfiguration ein Raumfahrzeug, integriert seine Bewegung mit RK4,
wendet Gravitations- und Antriebsbeitraege an und erzeugt Ereignisse, Bahnproben
und Zusammenfassung. Ein vereinfachtes Kalman-Navigationsmodell verfolgt die
Zustandsunsicherheit. `get_default_mission_config()` liefert die API-Startwerte;
`validate_mission_config()` meldet ungueltige Parameter.

Dies ist die einzige `trajectory.py` im Projekt. Der verbindliche Import lautet
`from solver.trajectory import ...`.

## `nbody_propagation.py`

Propagiert einen Zustand kontinuierlich unter dem Einfluss mehrerer Koerper.
`continuous_n_body_acceleration()` bildet die Beschleunigung, waehrend
`propagate_continuous_n_body()` die Integration und Stichproben erzeugt.
`validate_continuous_waypoint_route()` fuehrt eine differentielle Korrektur aus
und prueft, ob eine mehrteilige Route ihre Wegpunkte mit stetigen Zustaenden und
vertretbaren Residuen trifft.

## `ephemeris.py`

Kapselt die Planetenephemeriden. `SpiceEphemeris` laedt einen Meta-Kernel und
liefert SPICE-Zustaende; `planet_state()` verwendet je nach
`SOLAR_SIM_EPHEMERIS_MODE` SPICE, Kepler oder den automatischen Fallback.
`utc_to_ephemeris_seconds()` uebersetzt UTC fuer SPICE und
`get_ephemeris_status()` macht Modus, Verfuegbarkeit und Fehler diagnostizierbar.
