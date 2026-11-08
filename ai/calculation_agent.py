"""Audited OpenAI Responses API adapter for calculation search guidance."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from services.activity_log import read_activities

from .audit_log import write_ai_audit
from .evaluation import train_and_evaluate
from .interaction_agent import configured_model, _call_responses_api, _extract_output_text
from .schemas import SCHEMA_VERSION, validate_ai_payload


PROMPT_VERSION = "calculation-v1"
MAX_HISTORY_ITEMS = 24

CALCULATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "strategy",
        "searchWindows",
        "candidateSeeds",
        "rejectionHints",
        "expectedImprovement",
        "basedOnHistoricalRunIds",
        "requiresSolverValidation",
    ],
    "properties": {
        "strategy": {
            "type": "string",
            "enum": ["gravity-assist", "solar-oberth", "hybrid", "corridor-refinement"],
        },
        "searchWindows": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "startDate", "endDate", "priority", "reason"],
                "properties": {
                    "label": {"type": "string"},
                    "startDate": {"type": "string"},
                    "endDate": {"type": "string"},
                    "priority": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
            },
        },
        "candidateSeeds": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "startDate",
                    "encounterDay",
                    "routeMode",
                    "priority",
                    "rationale",
                    "routeSectionIds",
                ],
                "properties": {
                    "startDate": {"type": "string"},
                    "encounterDay": {"type": ["number", "null"], "minimum": 0},
                    "routeMode": {
                        "type": "string",
                        "enum": ["gravity-assist", "solar-oberth", "direct", "hybrid"],
                    },
                    "priority": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    "routeSectionIds": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "rejectionHints": {"type": "array", "items": {"type": "string"}},
        "expectedImprovement": {"type": "string"},
        "basedOnHistoricalRunIds": {"type": "array", "items": {"type": "string"}},
        "requiresSolverValidation": {"type": "boolean", "const": True},
    },
}

CALCULATION_INSTRUCTIONS = """Du bist die Berechnungs-KI einer Missionsplanung.
Du darfst niemals physikalische Endergebnisse behaupten oder eine Route als
flugfaehig markieren. Der klassische Solver bleibt die einzige Quelle der
Wahrheit. Deine Aufgabe ist nur, Suchraeume, Startdaten, Encounter-Tage,
Korridor-/Routenstrategie und Kandidaten-Seeds vorzuschlagen.
Nutze uebergebene historische Solver-/Aktivitaetslogs nur zur Priorisierung.
Jeder Kandidat muss requiresSolverValidation=true voraussetzen. Antworte auf
Deutsch in Begruendungen, aber halte alle maschinenrelevanten Felder strikt im
Schema."""

FORBIDDEN_PROPOSAL_KEYS = {
    "actualtargetalignmentdeg",
    "availableinjectiondeltavkms",
    "collisionfree",
    "displayfree",
    "displaysafe",
    "feasible",
    "feasiblewithconfiguredburn",
    "flightready",
    "nbodyvalid",
    "plausibilitystatus",
    "requiredinjectiondeltavkms",
    "solvervalid",
    "targetcorrectiondeltavkms",
    "totalflightdays",
}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _compact_history(limit: int = MAX_HISTORY_ITEMS) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for record in read_activities(limit=limit, category="calculation"):
        values = record.get("values") if isinstance(record.get("values"), dict) else {}
        details = record.get("details") if isinstance(record.get("details"), dict) else {}
        history.append({
            "id": record.get("id"),
            "action": record.get("action"),
            "status": record.get("status"),
            "message": record.get("message"),
            "searchRunId": details.get("searchRunId"),
            "startDate": values.get("startDate") or values.get("bestDate"),
            "quality": values.get("quality"),
            "rejectionKind": details.get("rejectionKind"),
            "targetAlignmentDeg": values.get("targetAlignmentDeg"),
            "deltaVDeficitKmS": values.get("deltaVDeficitKmS"),
        })
    return history


def _contains_forbidden_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").replace("-", "").lower()
            if normalized in FORBIDDEN_PROPOSAL_KEYS:
                return str(key)
            child = _contains_forbidden_key(item)
            if child:
                return child
    if isinstance(value, list):
        for item in value:
            child = _contains_forbidden_key(item)
            if child:
                return child
    return None


def _validate_date(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Berechnungs-KI hat kein gueltiges Datum fuer {field_name} geliefert.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError(f"Berechnungs-KI hat ein ungueltiges Datum fuer {field_name} geliefert.") from error
    return value


def _validate_calculation_output(structured: dict[str, Any], mission_state: dict[str, Any]) -> dict[str, Any]:
    forbidden = _contains_forbidden_key(structured)
    if forbidden:
        raise ValueError(f"Berechnungs-KI-Ausgabe enthaelt verbotenes Ergebnisfeld: {forbidden}")
    if structured.get("requiresSolverValidation") is not True:
        raise ValueError("Berechnungs-KI-Vorschlaege muessen Solver-Validierung verlangen.")

    route_section_ids = {
        str(section.get("id"))
        for section in mission_state.get("routeSections", [])
        if isinstance(section, dict) and section.get("id")
    }
    search_windows: list[dict[str, Any]] = []
    for item in structured.get("searchWindows", [])[:6]:
        if not isinstance(item, dict):
            continue
        start_date = _validate_date(item.get("startDate"), "searchWindows.startDate")
        end_date = _validate_date(item.get("endDate"), "searchWindows.endDate")
        if end_date < start_date:
            raise ValueError("Berechnungs-KI-Suchfenster endet vor seinem Start.")
        search_windows.append({
            "label": str(item.get("label") or "Suchfenster").strip()[:120],
            "startDate": start_date,
            "endDate": end_date,
            "priority": max(0.0, min(1.0, float(item.get("priority") or 0))),
            "reason": str(item.get("reason") or "").strip()[:1_000],
        })

    candidate_seeds: list[dict[str, Any]] = []
    for item in structured.get("candidateSeeds", [])[:12]:
        if not isinstance(item, dict):
            continue
        seed_ids = [str(value) for value in item.get("routeSectionIds", []) if str(value)]
        if route_section_ids and any(seed_id not in route_section_ids for seed_id in seed_ids):
            raise ValueError("Berechnungs-KI referenziert einen unbekannten Routenabschnitt.")
        encounter_day = item.get("encounterDay")
        if encounter_day is not None:
            encounter_day = max(0.0, float(encounter_day))
        candidate_seeds.append({
            "startDate": _validate_date(item.get("startDate"), "candidateSeeds.startDate"),
            "encounterDay": encounter_day,
            "routeMode": str(item.get("routeMode") or "hybrid"),
            "priority": max(0.0, min(1.0, float(item.get("priority") or 0))),
            "rationale": str(item.get("rationale") or "").strip()[:1_000],
            "routeSectionIds": seed_ids,
        })

    if not search_windows and not candidate_seeds:
        start_date = str(mission_state.get("startDate") or _today())
        search_windows.append({
            "label": "Fallback-Suchfenster",
            "startDate": start_date,
            "endDate": start_date,
            "priority": 0.25,
            "reason": "Die KI hat keine verwertbaren Kandidaten geliefert; der Solver nutzt den aktuellen Missionsstart.",
        })
    return {
        "strategy": structured.get("strategy"),
        "searchWindows": search_windows,
        "candidateSeeds": candidate_seeds,
        "rejectionHints": [
            str(item).strip()[:500]
            for item in structured.get("rejectionHints", [])
            if str(item).strip()
        ][:8],
        "expectedImprovement": str(structured.get("expectedImprovement") or "").strip()[:1_000],
        "basedOnHistoricalRunIds": [
            str(item).strip()
            for item in structured.get("basedOnHistoricalRunIds", [])
            if str(item).strip()
        ][:12],
        "requiresSolverValidation": True,
    }


def generate_calculation_suggestion(
    payload: dict[str, Any],
    *,
    api_caller: Callable[[dict[str, Any]], dict[str, Any]] = _call_responses_api,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Der Berechnungs-KI-Auftrag muss ein JSON-Objekt sein.")
    mission_state = payload.get("missionState")
    if not isinstance(mission_state, dict):
        raise ValueError("Der Missionszustand fehlt.")
    mission_errors = validate_ai_payload("mission-state", mission_state)
    if mission_errors:
        raise ValueError("Ungueltiger Missionszustand: " + "; ".join(mission_errors))

    solver_result = payload.get("solverResult")
    solver_run_ids: list[str] = []
    if solver_result is not None:
        if not isinstance(solver_result, dict):
            raise ValueError("Das Solver-Ergebnis ist ungueltig.")
        solver_errors = validate_ai_payload("solver-result", solver_result)
        if solver_errors:
            raise ValueError("Ungueltiges Solver-Ergebnis: " + "; ".join(solver_errors))
        solver_run_ids = [str(solver_result["runId"])]

    ui_state = payload.get("uiState") if isinstance(payload.get("uiState"), dict) else {}
    recent_solver_history = payload.get("recentSolverHistory")
    if not isinstance(recent_solver_history, list):
        recent_solver_history = _compact_history()
    else:
        recent_solver_history = recent_solver_history[:MAX_HISTORY_ITEMS]
    ml_prioritization = train_and_evaluate()

    model = configured_model("calculation")
    context = {
        "missionState": mission_state,
        "solverResult": solver_result,
        "uiState": ui_state,
        "recentSolverHistory": recent_solver_history,
        "mlPrioritization": {
            "dataset": ml_prioritization["dataset"],
            "evaluation": ml_prioritization["evaluation"],
            "verdict": ml_prioritization["verdict"],
            "useOnlyForPrioritization": True,
        },
    }
    request_payload = {
        "model": model,
        "instructions": CALCULATION_INSTRUCTIONS,
        "input": [{
            "role": "user",
            "content": "Erzeuge nur Suchraeume und Kandidaten-Seeds fuer den Solver:\n"
            + json.dumps(context, ensure_ascii=False, allow_nan=False),
        }],
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "calculation_suggestion_response",
                "strict": True,
                "schema": CALCULATION_RESPONSE_SCHEMA,
            },
        },
        "max_output_tokens": 1_800,
        "store": False,
    }
    audit_input = {
        "missionState": mission_state,
        "solverResult": solver_result,
        "uiState": ui_state,
        "recentSolverHistory": recent_solver_history,
        "mlPrioritization": context["mlPrioritization"],
    }
    try:
        api_response = api_caller(request_payload)
        structured = json.loads(_extract_output_text(api_response))
        if not isinstance(structured, dict):
            raise ValueError("Berechnungs-KI hat kein JSON-Objekt geliefert.")
        proposal = _validate_calculation_output(structured, mission_state)
        output = {
            "schemaVersion": SCHEMA_VERSION,
            "suggestionId": f"calc-{uuid4().hex[:10]}",
            "role": "calculation",
            "modelName": str(api_response.get("model") or model),
            "basedOnSolverRunIds": solver_run_ids,
            "proposal": proposal,
            "rationale": proposal["expectedImprovement"],
            "requiresUserConfirmation": True,
            "responseId": str(api_response.get("id") or ""),
        }
        suggestion_errors = validate_ai_payload("ai-suggestion", {
            key: output[key]
            for key in [
                "schemaVersion",
                "suggestionId",
                "role",
                "modelName",
                "basedOnSolverRunIds",
                "proposal",
                "rationale",
                "requiresUserConfirmation",
            ]
        })
        if suggestion_errors:
            raise ValueError("Ungueltiger Berechnungs-KI-Vorschlag: " + "; ".join(suggestion_errors))
        audit = write_ai_audit(
            role="calculation",
            model_name=output["modelName"],
            input_payload=audit_input,
            output_payload=output,
            status="success",
            prompt_version=PROMPT_VERSION,
            solver_run_ids=solver_run_ids,
        )
        return {**output, "auditRunId": audit["runId"]}
    except Exception as error:
        write_ai_audit(
            role="calculation",
            model_name=model,
            input_payload=audit_input,
            output_payload=None,
            status="rejected" if isinstance(error, (ValueError, json.JSONDecodeError)) else "error",
            prompt_version=PROMPT_VERSION,
            solver_run_ids=solver_run_ids,
            error=str(error),
        )
        raise
