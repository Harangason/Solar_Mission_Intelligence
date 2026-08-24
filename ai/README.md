# Die Skripte in `ai/`

Dieses optionale Paket setzt eine erklaerende und suchunterstuetzende AI-Schicht
auf die deterministischen Solver. AI-Ausgaben duerfen physikalische Ergebnisse
nicht ersetzen. Fast alle oeffentlichen Aufrufe werden in `main.py` als
`/api/ai/*` bereitgestellt.

## `__init__.py`

Definiert die oeffentliche Paketoberflaeche. Die Datei exportiert die wichtigsten
Agentenfunktionen, Schema-Pruefung und Auditfunktionen, damit Aufrufer nicht die
internen Module kennen muessen.

## `interaction_agent.py`

Fuehrt den Missionsdialog. Es laedt den lokalen API-Schluessel, sendet
Konversation, Missionszustand und erlaubte Werkzeuge an die Responses API und
validiert die zurueckgegebenen UI-Aktionen. Eingabe ist der aktuelle Dialog- und
Missionskontext; Ausgabe ist eine strukturierte Chatantwort mit ausschliesslich
freigegebenen Aktionen.

## `plausibility_agent.py`

Prueft eine berechnete Mission auf Plausibilitaet. Das Skript erzeugt zuerst
deterministische Guardrail-Befunde aus Solver- und UI-Daten und fuegt danach
optionale Modellbefunde hinzu. Kritische deterministische Befunde koennen durch
das Modell nicht heruntergestuft oder entfernt werden.

## `calculation_agent.py`

Erzeugt Vorschlaege fuer Solver-Kandidaten, Startdaten und Suchraeume. Es nimmt
Missionszustand, bekannte Solverreferenzen und einen kompakten Verlauf entgegen,
prueft Datumswerte sowie verbotene Felder und liefert nur validierte Vorschlaege.
Die eigentliche Route wird anschliessend weiterhin vom Planner berechnet.

## `audio_agent.py`

Kapselt Sprache-zu-Text und Text-zu-Sprache. Uploadgroesse und MIME-Typ werden
vor dem API-Aufruf geprueft; API-Fehler werden in verstaendliche Laufzeitfehler
uebersetzt. Audiodaten und Schluessel werden nicht in AI-Audits geschrieben.

## `schemas.py`

Enthaelt die versionierten JSON-Schemas fuer Chat, Plausibilitaet und
Berechnungsvorschlaege sowie einen kleinen rekursiven Validator.
`validate_ai_payload()` gibt eine Liste konkreter Vertragsverletzungen zurueck.

## `tool_contracts.py`

Definiert die Allowlist der Aktionen, die ein Chat im Frontend ausloesen darf,
und prueft deren Parameter. So kann eine Modellantwort nicht beliebige
Clientfunktionen aufrufen.

## `audit_log.py`

Schreibt rollenbezogene JSONL-Nachweise fuer AI-Aufrufe und liest den jeweils
neuesten Eintrag. Vor dem Schreiben werden sensible Schluessel und grosse oder
ungeeignete Werte rekursiv redigiert.

## `evaluation.py`

Bereitet Solver- und Aktivitaetsdaten als `CandidateExample` auf, trainiert einen
lokalen linearen Kandidatenranker, bewertet Rankingmetriken und speichert oder
laedt `data/ml_candidate_ranker.json`. Das Modul besitzt auch eine CLI und wird
von den Trainingsskripten verwendet. Der Ranker priorisiert Kandidaten; er
entscheidet nicht ueber deren physikalische Gueltigkeit.

Typischer Direktaufruf:

```powershell
.\.venv\Scripts\python.exe -m ai.evaluation
```

