# Markierung 1: Missionschat in der Berechnung

Quelle: Nutzerkommentar vom 02.10.2026 am Panel „Interaktiver KI-Chat fuer die
2D-Planung“, http://127.0.0.1:5001/. Erwartung: nutzbare lokale Antworten und
ehrliche Verfuegbarkeit, statt eines aktiven Labels bei ungueltiger Anbindung.
Vertrag: docs/README_AI.md, bestehende AI-Schemas und Aktions-Allowlist.

Vorbedingungen: isolierte Auditdateien fuer Python-Tests, Ollama mit lokalem
qwen3.8:27b fuer den Browserlauf, Blanko-Projekt ohne gespeicherte Route.

| Kriterium | Datenfluss und Assertion | Ausfuehrbare Abdeckung |
| --- | --- | --- |
| Bereitschaft | .env.local / Modellmetadata -> get_ai_status -> /api/ai/status -> Status und Schaltflaechen | LocalModelTransportTests: lokal, offline, fehlend, Platzhalter, Cloud-Alias, Loopback |
| Freitextantwort | Eingabe / Verlauf / Projektion -> mission-chat -> Ollama JSON-Schema -> validierte Antwort -> Chat | InteractionAgentTests und LocalModelTransportTests; echter Browserlauf |
| Kein erfundener Solverbezug | solverResult null / vorhandene Run-ID -> Referenzvalidierung | test_unprovided_solver_reference_is_rejected; test_blank_project_receives_real_context_without_solver |
| Erlaubte Aktionen | Modellvorschlag -> Allowlist -> expliziter Uebernahmebutton -> Ansicht | test_unknown_action_is_rejected_and_audited; Browser: Draufsicht erst nach Uebernahme |
| Kein Solver im leeren Projekt | leere routeSections -> Ablehnung einer Solver-Aktion | test_blank_project_cannot_start_solver |
| Fehler / Abbruch | Request abgebrochen oder ungueltige Antwort -> Nachricht bleibt erneut sendbar | test_truncated_model_reply_is_recoverable_error; Browser: Abbruch und erneutes Senden |
| Sichtbarkeit | neue Antwort -> Scrollposition am Ende des Chatprotokolls | Browser: scrollTop + clientHeight >= scrollHeight - 2 |
| Audio | lokale Stimme / konfigurierte Audioanbindung -> nutzbare oder deaktivierte Controls | Browser: kein Mikrofon ohne Transkriptionsmodell; keine falsche Bereitschaft |

Keine vorhandene Tool-Checker-Registry, Kampagne oder Intake-Schnittstelle im
Projekt gefunden. Dieses Dokument ist ein Abdeckungsnachweis und keine zweite
Registry. Die bestehenden unittest-Faelle werden gezielt ausgefuehrt;
ein voller Tool-Checker-Kampagnen-PASS wird dadurch nicht behauptet.
