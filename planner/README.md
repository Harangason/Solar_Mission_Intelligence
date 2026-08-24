# Die Skripte in `planner/`

Dieses Paket erzeugt, koppelt und bewertet Missionsrouten. Positionen und
Dynamik kommen aus `solver/`; Persistenz und Nachweise aus `services/`.

## `__init__.py`

Markiert das Paket und beschreibt seine Verantwortung. Oeffentliche Funktionen
werden direkt aus den jeweiligen Modulen importiert.

## `route_planner.py`

Der spezialisierte, umfangreichste Routenplaner. Er berechnet klassische
Solar-Oberth-, Lambert-, Swing-by-, Wegpunkt- und direkte solare Routen. Interne
Hilfen behandeln unter anderem Stumpff-Funktionen, Lambert-Kandidaten,
Hyperbelgeometrie, Eintrittskorridore, Flyby-Austrittsrichtungen und
Unsicherheiten.

Wichtige Einstiege:

- `simulate_waypoint_route(values, include_mission_result=False)` berechnet eine
  Route ueber vorgegebene Wegpunkte.
- `simulate_direct_solar_route(values, include_mission_result=False)` berechnet
  eine direkte solare Variante.

Eingabe und Ausgabe sind API-nahe Dictionaries; Entfernungen werden intern in
Kilometern, Geschwindigkeiten in `km/s` und Zeiten in Sekunden verarbeitet.

## `generic_route_planner.py`

Berechnet frei kombinierte Abschnitte zwischen Sonne, Planeten, Monden und einem
terminalen interstellaren Richtungsziel. Das Modul baut einen Koerperkatalog,
ermittelt zeitabhaengige Zustaende und koppelt Transfers mit lokalen direkten,
Teil- oder Vollumrundungen. `parse_route_passage()` normalisiert die
Passageangaben; `simulate_generic_route_sections()` ist der oeffentliche
Gesamteinstieg.

## `multi_route_planner.py`

Ist die Orchestrierung fuer eine geordnete Liste von Routenabschnitten.
`classify_route_sections()` entscheidet, welche Abschnitte mit welchem
Planermodell verarbeitet werden koennen. `simulate_route_sections()` sucht und
koppelt passende Transfers, prueft Korridore und lokale SOI-Passagen und gibt
eine gemeinsame Trajektorie samt Diagnose- und Bewertungsdaten zurueck.

## `mission_optimizer.py`

Durchsucht Startfenster und bewertet die solare Energiebilanz. Kandidaten werden
zuerst grob und danach genauer ausgewertet. `optimize_launch_window()` liefert
rangierte Starttermine und Routenmetriken; `assess_solar_energy()` vergleicht
verfuegbare und benoetigte Energie beziehungsweise Austrittsgeschwindigkeit.

## `interstellar_targets.py`

Enthaelt den kleinen Katalog fester J2000-Richtungsvektoren fuer interstellare
Ziele. `interstellar_direction(target_id)` liefert einen normierten Vektor oder
`None`. Die Ziele sind Richtungsdarstellungen und keine Koerper mit lokaler
Ephemeride.

