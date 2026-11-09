# Routen- und Vektorprüfung vom 9. November 2026

Die aktualisierte `data/ml_candidate_ranker.json` ist mit Commit `796c8b1`
(`Update trained mission candidate ranker`) nach `origin/main` gepusht.
Dieser Bericht dokumentiert die anschließend geprüften Routing-Korrekturen.

## Befunde und Reparaturen

Die lokale Vorschau verwendete die Verbindung zwischen zwei Himmelskörpern als
Flugrichtung. Das ist nicht die Geschwindigkeit einer Sonde relativ zum Ziel.
Zusätzlich wurden weit entfernte Eintrittspunkte auf einen kleinen Kreis um den
Zielkörper versetzt. Dadurch konnten die gezeichneten Geraden den Körper schneiden,
obwohl die berechnete Bahn außerhalb verlief.

Die berechneten 2D- und lokalen 3D-Ansichten verwenden jetzt die gespeicherten
Zustände der Sonde und des Zielkörpers zur gleichen Zeit. Sowohl Position als auch
Geschwindigkeit werden voneinander abgezogen. Die Nahansicht bis 2,5 Körperradien
verwendet einen gemeinsamen Maßstab für Zielkörper und Flugbahn. Sie verbindet keine
getrennten Aufenthalte im sichtbaren Bereich. Die Pfeile zeigen die lokalen
Geschwindigkeiten an den sichtbaren Randpunkten; der tatsächliche SOI-Eintritt kann
weit außerhalb dieser Ansicht liegen. Ein Abschnittsende im gebundenen Orbit ist
noch kein Abflug zum nächsten Ziel.

Ohne passende berechnete Zustände bleibt eine ausdrücklich bezeichnete Vorgabeskizze.
Sie erfindet keinen Anflug vom Ursprungsobjekt. Eine angeforderte 45°-Teilumrundung
wird nicht mehr allein aufgrund einer angenommenen Folgezielrichtung verlängert.
Bei einem Mond als Ziel zeigt die lokale Ansicht den Mond selbst statt seines
Mutterplaneten. Animierte Begleitmonde bleiben schematisch.

Unabhängig vom Darstellungsfehler ließ sich ein Fehler der Kollisionsprüfung
reproduzieren: Ein vollständiger Körperdurchflug innerhalb eines Integrationsschritts
konnte unbemerkt bleiben. Ein unabhängiger geradliniger Testfall mit 10 km Körperradius
und 1.000 km/s lieferte vorher `collision=false`; der analytische Erstkontakt ist
bei 123,4467 Sekunden.

Die Prüfung berücksichtigt jetzt auch lokale Abstandsminima. Bei einer Durchdringung
wird auf der kontinuierlichen Integratorlösung der erste Oberflächenkontakt
bestimmt und die Bahn dort beendet. Bewegte Körper verwenden dabei ihre relative
Position und Geschwindigkeit. Anfangszustände innerhalb eines Körpers werden
sofort erkannt. Die Ausgabe behält außerdem adaptive Integrationspunkte bei,
damit die Darstellung nahe der Periapsis keine unnötig langen Verbindungssehnen
zeichnet. Dies ist eine numerische Absicherung mit Regressionstests, kein Beweis
für beliebig oszillierende Ereignisfunktionen oder alle denkbaren Routen.

## Gespeichertes Neptunbeispiel

Untersucht wurde der Lauf `9e393096-644d-4168-ab08-a815cd8e1ebc`, Variante
`95d014d0-37ed-4b5c-b0f4-f32256b74ed6`: Erde → Neptun → Proxima Centauri,
voller Neptunorbit, nominell 100 km Höhe. Die Variante wurde mit der korrigierten
Kollisionsprüfung neu berechnet.

| Messgröße | Ergebnis |
| --- | ---: |
| Kleinste Neptunhöhe | 100,001685 km |
| SOI-Eintrittsradius ab Neptunzentrum | 86.654.495,873 km |
| Relative Geschwindigkeit am SOI-Eintritt | 190,024 km/s |
| Relative Geschwindigkeit am Ende des Neptunabschnitts | 16,628 km/s |
| Δv der lokalen Manöver | 174,846 km/s |
| Δv der gesamten Modellroute | 417,597 km/s |
| Zusammenhängende Zustände | Ja |
| Erkannte Kollision | Nein |
| Mit dem konfigurierten Fahrzeug machbar | Nein |

Für diese gespeicherte Variante ist ein Durchflug durch Neptun somit nicht bestätigt.
Die Darstellung war irreführend; der zusätzlich gefundene Kollisionsfehler war real.
Die sehr kurze Transferzeit von 260 Tagen erzeugt hohe Geschwindigkeiten und einen
untragbaren Treibstoffbedarf. Der Optimierungsstatus der Variante hatte außerdem
den angeforderten Korridor gelockert. Die gespeicherte Originalvariante hatte keine
vollständige Ephemeridenbestätigung, unter anderem wegen eines Mars-Baryzentrums
bei den Kollisionskörpern. Die aktuelle Neuberechnung bestätigt die Ephemeriden;
der Fahrzeugnachweis bleibt negativ. Diese Modellroute ist ausdrücklich keine
flugfähige Mission.
Die Optimierungspolitik für Transferzeiten und gelockerte Korridore wurde in dieser
Reparatur nicht geändert.

## Verifikation

- 4 neue numerische Regressionstests: schneller Durchflug mit und ohne
  Geschwindigkeitsableitung, bewegter neptungroßer Körper, knapper Vorbeiflug und
  Start innerhalb eines Körpers.
- 6 bestehende Routenvertragstests erfolgreich.
- 7 bestehende physikalische Tests erfolgreich, einschließlich lokaler gebundener
  Bahnen bei Mars, Erdmond und Io, Mondrückkehr sowie Referenzdynamik.
- Neuberechnung der gespeicherten Neptunvariante erfolgreich; Kollisionsfreiheit,
  Zustandskontinuität und Mindesthöhe geprüft.
- Frontend-Prüfungen für relative Zustände bei Erde, Mond und Neptun, fehlende
  Ephemeriden und getrennte Nahbereichssegmente erfolgreich. Bestehende
  Richtungs- und Geometrietests ebenfalls erfolgreich.
- TypeScript und Produktionsbuild erfolgreich. Die bekannte Warnung zu einem
  JavaScript-Paket über 500 kB bleibt bestehen.
- Browserprüfung: berechneter Neptunverlauf in 2D und 3D, Geschwindigkeiten,
  Einfangmarkierung und Machbarkeitsstatus; unberechnete Mondansicht mit 45°.
  Keine Browserkonsolenfehler in der Prüfansicht erfasst.

Die lokalen Belege liegen unter `logs/route-vector-review/` (nicht versioniert).
Die erste Browserprüfung erfolgte auf `http://127.0.0.1:5018/` mit eigener
Testdatenbank. Nach ausdrücklicher Zustimmung des Nutzers wurde die Hauptinstanz
auf `http://127.0.0.1:5001/` am 9. November um 12:57 Uhr neu gestartet. Der laufende
Build `314ba5148b3fc466` entspricht dem geprüften Stand; auch das ausgelieferte
Frontend stimmt überein. Die Neptun-Nachrechnung über die Hauptinstanz bestätigt
Kollisionsfreiheit, Zustandskontinuität und denselben Δv-Bedarf. Die vorhandenen
Projekte sind erreichbar. Der alte Berechnungsprozess wurde für den Neustart
beendet. Bestehende gespeicherte Routen erhalten erst durch Neuberechnung die
verbesserte Kollisionsprüfung und zusätzliche Abtastpunkte.

## Technische Referenzen

- [SciPy: solve_ivp](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html)
  dokumentiert, dass mehrere Ereignisnullstellen innerhalb eines Schritts bei der
  Suche nach Vorzeichenwechseln übersehen werden können.
- [NASA/JPL SPICE: spkgeo](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/cspice/spkgeo_c.html)
  beschreibt geometrische Zustände aus Position und Geschwindigkeit relativ zum
  Beobachter im angegebenen Bezugssystem.
