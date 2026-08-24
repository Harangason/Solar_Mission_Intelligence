# Die Skripte in `scripts/`

Diese Dateien sind manuell gestartete Wartungs-, Trainings- und
Auswertungswerkzeuge. Aufrufe erfolgen aus dem Projektstamm.

## `download_spice_kernels.py`

Laedt die kompakten generischen NAIF-Kernels `naif0012.tls` und `de440s.bsp`
nach `kernels/`. Ein Download wird zunaechst als `.part` geschrieben, fuer den
Planetenkernel per veroeffentlichtem MD5-Wert geprueft und danach atomar
umbenannt. Abschliessend entsteht `kernels/solar_system.tm` als Meta-Kernel.

```powershell
.\.venv\Scripts\python.exe scripts\download_spice_kernels.py
```

Der Aufruf benoetigt Netzwerkzugriff.

## `update_moons.py`

Laedt oeffentliche JPL-Tabellen zu Mondentdeckungen und Bahnelementen, parst die
HTML-Zeilen, vereinheitlicht Namen und Zahlen und schreibt den Browserkatalog
`web/public/moons.json`. Der Erdmond wird als stabiler Basiseintrag ergaenzt.

```powershell
.\.venv\Scripts\python.exe scripts\update_moons.py
```

Auch dieser Aufruf benoetigt Netzwerkzugriff und ueberschreibt den generierten
Mondkatalog.

## `evaluate_ml_ranker.py`

Ist ein schmaler Kommandozeilen-Wrapper um `ai.evaluation.main()`. Er ergaenzt
den Projektstamm zum Importpfad und reicht alle CLI-Argumente an Training und
Evaluation des lokalen Kandidatenrankers weiter.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_ml_ranker.py
```

## `train_saved_project.py`

Laedt eine gespeicherte Route per Projekt-ID, verteilt eine konfigurierbare Zahl
von Berechnungen ueber ein Datumsfenster und fuehrt sie parallel mit dem
deterministischen Mehrabschnittsplaner aus. Es schreibt kompakte Trainingszeilen
und eine Batch-Zusammenfassung nach `data/ml_training_batches/`, protokolliert
die Aktivitaet und trainiert danach den Ranker neu. Saisonale und
Jupiter-Phasenmerkmale helfen bei der zeitlichen Einordnung.

```powershell
.\.venv\Scripts\python.exe -m scripts.train_saved_project --project-id <UUID> --runs 100
```

## `train_catalog_routes.py`

Erzeugt reproduzierbare Trainingsszenarien fuer alle Planeten, wichtige Monde
und eine Voyager-Grand-Tour. Fuer jedes Szenario werden Startdaten im gewaehlten
Fenster berechnet, Solverergebnisse als Batch gespeichert und der Ranker danach
neu trainiert. `build_scenarios()` kann auch separat verwendet werden, um den
Katalog zu inspizieren.

```powershell
.\.venv\Scripts\python.exe -m scripts.train_catalog_routes --runs-per-scenario 25
```

Beide Trainingsskripte koennen viel CPU-Zeit benoetigen. Der ML-Ranker
priorisiert nur die Suche; die physikalische Gueltigkeit kommt immer vom Solver.

