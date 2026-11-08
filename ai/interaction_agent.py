"""Audited interaction role with a shared, explicitly configured model transport."""

from __future__ import annotations

import json
import os
import ipaddress
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .audit_log import write_ai_audit
from .schemas import validate_ai_payload
from .tool_contracts import validate_interaction_actions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gpt-5.6-sol"
RESPONSES_URL = "https://api.openai.com/v1/responses"
PROMPT_VERSION = "interaction-v1"

INTERACTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "basedOnSolverRunIds", "proposedActions"],
    "properties": {
        "reply": {"type": "string"},
        "basedOnSolverRunIds": {
            "type": "array",
            "items": {"type": "string"},
        },
        "proposedActions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "type",
                    "sectionId",
                    "projection",
                    "requiresConfirmation",
                ],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "focus-route-section",
                            "set-projection",
                            "run-route-solver",
                        ],
                    },
                    "sectionId": {"type": ["string", "null"]},
                    "projection": {
                        "type": ["string", "null"],
                        "enum": ["corridor", "side", "top", None],
                    },
                    "requiresConfirmation": {"type": "boolean", "const": True},
                },
            },
        },
    },
}

INTERACTION_INSTRUCTIONS = """Du bist die Interaktions-KI einer Missionsplanung.
Antworte auf Deutsch, direkt und verstaendlich, moeglichst in zwei bis vier Saetzen.
Sprich den Nutzer direkt an. Verwende im Antworttext die sichtbaren deutschen
Namen der Ansichten und Schaltflaechen. Interne Feldnamen und Aktionsnamen wie
routeSections, corridor, top oder set-projection gehoeren nur in die JSON-Felder,
nicht in den Antworttext.
Der physikalische Solver ist die einzige Quelle fuer Missionswerte. Nenne konkrete
Zahlen nur, wenn sie im Feld solverResult stehen, und fuehre dabei die zugehoerige
Solver-Run-ID in basedOnSolverRunIds auf. Erfinde keine Missionswerte. Wenn kein
Solver-Ergebnis vorliegt, sage das klar.
Du darfst nur die im Ausgabeschema erlaubten UI-Aktionen vorschlagen. Aktionen
werden nie automatisch ausgefuehrt und muessen immer Nutzerbestaetigung verlangen.
Erklaere die aktuelle Projektion: corridor = Zielkorridor am Zielobjekt,
side = Kantenansicht der Bahnneigung, top = Draufsicht auf die Bahnen.
Bei leerer routeSections-Liste ist das Projekt leer: erklaere dem Nutzer, dass
er ueber die Schaltflaeche '+ Neu' den Routenassistenten oeffnen kann.
Schlage keinen Solverlauf vor.
Bei einem Wunsch nach einer anderen Ansicht schlage set-projection vor.
Verstecke keine Warnungen und bezeichne best-effort oder ungueltige Ergebnisse
nicht als flugfaehig."""


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    env_path = PROJECT_ROOT / ".env.local"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("'\"")
    return default


def _provider() -> str:
    if _setting("SOLAR_SYSTEM_AI_DISABLED", "0").lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("Die Solar-KI ist auf Nutzerwunsch ausgeschaltet.")
    provider = _setting("SOLAR_SYSTEM_AI_PROVIDER", "openai").lower()
    if provider not in {"openai", "ollama"}:
        raise RuntimeError("Unbekannte KI-Anbindung in SOLAR_SYSTEM_AI_PROVIDER.")
    return provider


def configured_model(role: str) -> str:
    if _provider() == "ollama":
        return _setting("OLLAMA_MODEL", "qwen3.8:27b")
    return _setting(f"OPENAI_{role.upper()}_MODEL", DEFAULT_MODEL)


def _ollama_url() -> str:
    url = _setting("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlparse(url)
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if parsed.scheme != "http" or not loopback or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Die lokale KI benoetigt eine HTTP-Loopback-Adresse in OLLAMA_URL.")
    return url


def _load_local_key() -> str:
    key = _setting("OPENAI_API_KEY")
    if not key or not key.startswith("sk-") or any(word in key.lower() for word in ("dein", "your", "placeholder", "example", "replace")):
        raise RuntimeError("Kein gueltiger OPENAI_API_KEY eingerichtet.")
    return key


def _check_local_model(model: str) -> None:
    if model.endswith(":cloud"):
        raise RuntimeError("Fuer die lokale KI muss ein lokal installiertes Modell gewaehlt werden.")
    query = Request(_ollama_url() + "/api/show", method="POST",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps({"model": model}).encode("utf-8"))
    with urlopen(query, timeout=3) as response:
        metadata = json.loads(response.read().decode("utf-8"))
    if metadata.get("remote_host") or metadata.get("remote_model"):
        raise RuntimeError("Das konfigurierte Modell verwendet einen Cloud-Dienst. Bitte ein lokales Modell waehlen.")
    if "completion" not in metadata.get("capabilities", []):
        raise RuntimeError("Das konfigurierte Modell unterstuetzt keinen Textchat.")


def get_ai_status() -> dict[str, Any]:
    """Check configuration and local model availability without generating text."""
    status: dict[str, Any] = {"ready": False, "provider": None, "model": None, "message": ""}
    try:
        status.update(provider=_provider(), model=configured_model("interaction"))
        if status["provider"] == "ollama":
            _check_local_model(status["model"])
            status.update(ready=True, message="Lokales Modell bereit.")
        else:
            _load_local_key()
            status.update(ready=True, message="OpenAI konfiguriert; Verbindung wird beim Senden geprueft.")
    except HTTPError as error:
        status["message"] = "Das konfigurierte lokale Modell ist nicht installiert." if error.code == 404 else f"Ollama antwortet mit HTTP {error.code}."
    except (URLError, TimeoutError, OSError):
        status["message"] = "Ollama ist nicht erreichbar. Starte den lokalen Modellserver und pruefe erneut."
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        status["message"] = str(error) if isinstance(error, RuntimeError) else "Die KI-Konfiguration konnte nicht gelesen werden."
    status["audio"] = {"transcription": False, "speech": False}
    if status["provider"] == "openai" and status["ready"]:
        status["audio"] = {"transcription": True, "speech": True}
    return status


def _call_ollama(request_payload: dict[str, Any]) -> dict[str, Any]:
    model = configured_model("interaction")
    schema = deepcopy(request_payload["text"]["format"]["schema"])
    instructions = request_payload["instructions"]
    if request_payload["text"]["format"].get("name") == "calculation_suggestion_response":
        # A smaller local proposal is still validated against the canonical
        # contract. Avoid exhausting CPU inference on twelve verbose seeds.
        for field in ("searchWindows", "candidateSeeds"):
            schema["properties"][field]["maxItems"] = 1
            schema["properties"][field]["minItems"] = 1
        schema["properties"]["rejectionHints"]["maxItems"] = 2
        instructions += "\nErzeuge genau ein Suchfenster und genau einen Kandidaten. Begruendungen jeweils hoechstens ein kurzer Satz. expectedImprovement hoechstens ein Satz."
    messages = [{"role": "system", "content": instructions
                 + "\nAntworte ausschliesslich als JSON nach diesem Schema:\n"
                 + json.dumps(schema, ensure_ascii=False)}]
    messages.extend({"role": "system" if item["role"] == "developer" else item["role"],
                     "content": item["content"]} for item in request_payload["input"])
    local_request = Request(
        _ollama_url() + "/api/chat",
        data=json.dumps({"model": model, "messages": messages, "format": schema,
                         "stream": False, "think": False,
                         "options": {"temperature": 0, "num_ctx": 4096,
                                     "num_predict": request_payload.get("max_output_tokens", 1200)}},
                        ensure_ascii=False, allow_nan=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        _check_local_model(model)
        with urlopen(local_request, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip() or result.get("done_reason") == "length":
            raise RuntimeError("Das lokale Modell hat keine vollstaendige Antwort geliefert. Bitte erneut versuchen.")
        return {"id": "", "model": result.get("model") or model,
                "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}]}
    except HTTPError as error:
        raise RuntimeError(f"Ollama antwortet mit HTTP {error.code}. Pruefe das lokal installierte Modell.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError("Das lokale Modell ist nicht erreichbar oder antwortet zu langsam. Bitte erneut versuchen.") from error
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Ollama hat eine ungueltige Antwort geliefert.") from error


def _call_responses_api(request_payload: dict[str, Any]) -> dict[str, Any]:
    if _provider() == "ollama":
        return _call_ollama(request_payload)
    api_key = _load_local_key()
    encoded = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    api_request = Request(
        os.getenv("OPENAI_RESPONSES_URL", RESPONSES_URL),
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(api_request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(
            f"OpenAI Responses API antwortet mit HTTP {error.code}"
            + ". Bitte Zugang und Modellkonfiguration pruefen."
        ) from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("OpenAI Responses API ist derzeit nicht erreichbar.") from error


def _validate_history(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Der Chatverlauf muss eine Liste sein.")
    history: list[dict[str, str]] = []
    for item in value[-12:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            raise ValueError("Der Chatverlauf enthaelt eine ungueltige Rolle.")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 4_000:
            raise ValueError("Der Chatverlauf enthaelt einen ungueltigen Text.")
        history.append({"role": item["role"], "content": content.strip()})
    return history


def _extract_output_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text:
                    return text
    raise RuntimeError("OpenAI hat keine auswertbare Textantwort geliefert.")


def generate_mission_chat(
    payload: dict[str, Any],
    *,
    api_caller: Callable[[dict[str, Any]], dict[str, Any]] = _call_responses_api,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Der KI-Auftrag muss ein JSON-Objekt sein.")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 4_000:
        raise ValueError("Die Chatnachricht ist leer oder zu lang.")

    mission_state = payload.get("missionState")
    if not isinstance(mission_state, dict):
        raise ValueError("Der Missionszustand fehlt.")
    mission_errors = validate_ai_payload("mission-state", mission_state)
    if mission_errors:
        raise ValueError("Ungueltiger Missionszustand: " + "; ".join(mission_errors))

    solver_result = payload.get("solverResult")
    if solver_result is not None:
        if not isinstance(solver_result, dict):
            raise ValueError("Das Solver-Ergebnis ist ungueltig.")
        solver_errors = validate_ai_payload("solver-result", solver_result)
        if solver_errors:
            raise ValueError("Ungueltiges Solver-Ergebnis: " + "; ".join(solver_errors))

    history = _validate_history(payload.get("history"))
    view_state = payload.get("viewState") if isinstance(payload.get("viewState"), dict) else {}
    context = {
        "missionState": mission_state,
        "solverResult": solver_result,
        "viewState": {
            "projection": view_state.get("projection"),
            "activeRouteSectionId": view_state.get("activeRouteSectionId"),
        },
    }
    model = configured_model("interaction")
    request_payload = {
        "model": model,
        "instructions": INTERACTION_INSTRUCTIONS,
        "input": [
            {
                "role": "developer",
                "content": "Aktueller Anwendungszustand (JSON):\n"
                + json.dumps(context, ensure_ascii=False, allow_nan=False),
            },
            *history,
            {"role": "user", "content": message.strip()},
        ],
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "mission_chat_response",
                "strict": True,
                "schema": INTERACTION_RESPONSE_SCHEMA,
            },
        },
        "max_output_tokens": 600,
        "store": False,
    }
    solver_run_ids = [solver_result["runId"]] if solver_result else []
    audit_input = {
        "message": message.strip(),
        "history": history,
        "context": context,
    }
    try:
        api_response = api_caller(request_payload)
        structured = json.loads(_extract_output_text(api_response))
        if not isinstance(structured, dict):
            raise ValueError("Die KI-Antwort muss ein JSON-Objekt sein.")
        if not isinstance(structured.get("reply"), str) or not structured["reply"].strip() or len(structured["reply"]) > 4_000:
            raise ValueError("Die KI-Antwort enthaelt keinen Antworttext.")
        cited_ids = structured.get("basedOnSolverRunIds")
        if not isinstance(cited_ids, list) or any(item not in solver_run_ids for item in cited_ids):
            raise ValueError("Die KI referenziert einen nicht uebergebenen Solver-Lauf.")
        actions = validate_interaction_actions(
            structured.get("proposedActions"),
            mission_state,
        )
        output = {
            "reply": structured["reply"].strip(),
            "basedOnSolverRunIds": cited_ids,
            "proposedActions": actions,
            "model": str(api_response.get("model") or model),
            "responseId": str(api_response.get("id") or ""),
        }
        audit = write_ai_audit(
            role="interaction",
            model_name=output["model"],
            input_payload=audit_input,
            output_payload=output,
            status="success",
            prompt_version=PROMPT_VERSION,
            solver_run_ids=cited_ids,
        )
        return {**output, "auditRunId": audit["runId"]}
    except Exception as error:
        write_ai_audit(
            role="interaction",
            model_name=model,
            input_payload=audit_input,
            output_payload=None,
            status="rejected" if isinstance(error, (ValueError, json.JSONDecodeError)) else "error",
            prompt_version=PROMPT_VERSION,
            solver_run_ids=solver_run_ids,
            error=str(error),
        )
        raise
