"""Audited OpenAI Responses API adapter for the plausibility role."""

from __future__ import annotations

import json
from typing import Any, Callable
from uuid import uuid4

from .audit_log import write_ai_audit
from .interaction_agent import configured_model, _call_responses_api, _extract_output_text
from .schemas import SCHEMA_VERSION, validate_ai_payload


PROMPT_VERSION = "plausibility-v1"

PLAUSIBILITY_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "findings", "requiredFixes", "displaySafe"],
    "properties": {
        "status": {"type": "string", "enum": ["pass", "warning", "fail"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message", "severity", "sourceRefs"],
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "error"]},
                    "sourceRefs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "requiredFixes": {"type": "array", "items": {"type": "string"}},
        "displaySafe": {"type": "boolean"},
    },
}

PLAUSIBILITY_INSTRUCTIONS = """Du bist die Plausibilitaets-KI einer Missionsplanung.
Du bist eine Kontrollinstanz, nicht der kreative Navigator.
Pruefe Solver-Ergebnis, UI-State und View-Metadaten gegen die berechneten Daten.
Der Solver bleibt autoritativ. Schwaeche Solver-Fehler oder deterministische
Findings niemals ab. Melde Widersprueche klar auf Deutsch.
Freitext darf nur erklaeren; status, findings, requiredFixes und displaySafe
sind maschinenrelevante Pruefergebnisse."""


def _severity_rank(severity: str) -> int:
    return {"info": 0, "warning": 1, "error": 2}.get(severity, 0)


def _status_from_findings(findings: list[dict[str, Any]]) -> str:
    highest = max((_severity_rank(str(item.get("severity") or "info")) for item in findings), default=0)
    if highest >= 2:
        return "fail"
    if highest == 1:
        return "warning"
    return "pass"


def _finding(code: str, message: str, severity: str, refs: list[str]) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "severity": severity,
        "sourceRefs": refs,
    }


def _nested_value(root: dict[str, Any], path: list[str]) -> Any:
    current: Any = root
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_value(root: dict[str, Any], paths: list[list[str]]) -> Any:
    for path in paths:
        value = _nested_value(root, path)
        if value is not None:
            return value
    return None


def _deterministic_findings(solver_result: dict[str, Any], ui_state: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    run_id = str(solver_result.get("runId") or "")
    result = solver_result.get("result") if isinstance(solver_result.get("result"), dict) else {}
    validation = solver_result.get("validation") if isinstance(solver_result.get("validation"), dict) else {}
    route_sections = result.get("routeSections") if isinstance(result.get("routeSections"), list) else []
    solver_section_ids = [
        str(section.get("id"))
        for section in route_sections
        if isinstance(section, dict) and section.get("id")
    ]

    if solver_result.get("status") != "success" or validation.get("solverValid") is not True:
        findings.append(_finding(
            "solver-not-flight-ready",
            "Der Solver hat diese Route nicht als eindeutig flugfaehig freigegeben.",
            "error",
            [run_id, "solver.validation"],
        ))
    elif validation.get("warnings"):
        findings.append(_finding(
            "solver-warnings-visible",
            "Der Solver liefert Warnungen; sie muessen in der Anzeige sichtbar bleiben.",
            "warning",
            [run_id, "solver.validation.warnings"],
        ))

    if validation.get("nBodyValid") is False:
        findings.append(_finding(
            "nbody-validation-failed",
            "Die N-Koerper-Validierung widerspricht einer Flugfreigabe.",
            "error",
            [run_id, "solver.validation.nBodyValid"],
        ))

    displayed_run_id = str(ui_state.get("displayedSolverRunId") or "")
    if displayed_run_id and displayed_run_id != run_id:
        findings.append(_finding(
            "ui-run-id-mismatch",
            "Die UI zeigt einen anderen Solver-Lauf als das gepruefte Ergebnis.",
            "error",
            [displayed_run_id, run_id],
        ))

    active_section_id = str(ui_state.get("activeRouteSectionId") or "")
    if active_section_id and solver_section_ids and active_section_id not in solver_section_ids:
        findings.append(_finding(
            "ui-active-section-missing",
            "Der aktive UI-Routenabschnitt kommt im Solver-Ergebnis nicht vor.",
            "warning",
            [active_section_id, run_id],
        ))

    ui_section_ids = ui_state.get("routeSectionIds")
    if isinstance(ui_section_ids, list) and solver_section_ids:
        clean_ui_ids = [str(item) for item in ui_section_ids if item]
        if clean_ui_ids and clean_ui_ids != solver_section_ids:
            findings.append(_finding(
                "ui-route-sections-mismatch",
                "Die UI-Routenabschnitte stimmen nicht mit den Solver-Routenabschnitten ueberein.",
                "error",
                [run_id, "ui.routeSectionIds", "solver.result.routeSections"],
            ))

    displayed_start_date = ui_state.get("displayedStartDate")
    if displayed_start_date and result.get("startDate") and displayed_start_date != result.get("startDate"):
        findings.append(_finding(
            "start-date-mismatch",
            "Missionsstart in UI und Solver-Ergebnis stimmen nicht ueberein.",
            "warning",
            [run_id, "ui.displayedStartDate", "solver.result.startDate"],
        ))

    displayed_days = ui_state.get("displayedTotalFlightDays")
    solver_days = result.get("totalFlightDays")
    if isinstance(displayed_days, (int, float)) and isinstance(solver_days, (int, float)):
        if abs(float(displayed_days) - float(solver_days)) > 0.01:
            findings.append(_finding(
                "flight-days-mismatch",
                "Angezeigte Flugtage und Solver-Flugtage stimmen nicht ueberein.",
                "warning",
                [run_id, "ui.displayedTotalFlightDays", "solver.result.totalFlightDays"],
            ))

    displayed_encounter_day = ui_state.get("displayedEncounterDay")
    solver_encounter_day = _first_value(result, [
        ["optimizedEncounterDay"],
        ["encounterDay"],
        ["waypoint", "encounterDay"],
        ["summary", "encounterDay"],
    ])
    if isinstance(displayed_encounter_day, (int, float)) and isinstance(solver_encounter_day, (int, float)):
        if abs(float(displayed_encounter_day) - float(solver_encounter_day)) > 0.01:
            findings.append(_finding(
                "encounter-day-mismatch",
                "Angezeigter Encounter-Missionstag und Solver-Encounter-Day stimmen nicht ueberein.",
                "warning",
                [run_id, "ui.displayedEncounterDay", "solver.result.encounterDay"],
            ))

    displayed_encounter_date = ui_state.get("displayedEncounterDate")
    solver_encounter_date = _first_value(result, [
        ["optimizedEncounterDate"],
        ["encounterDate"],
        ["waypoint", "encounterDate"],
        ["summary", "encounterDate"],
    ])
    if displayed_encounter_date and solver_encounter_date and displayed_encounter_date != solver_encounter_date:
        findings.append(_finding(
            "encounter-date-mismatch",
            "Angezeigtes Encounter-Datum und Solver-Encounter-Datum stimmen nicht ueberein.",
            "warning",
            [run_id, "ui.displayedEncounterDate", "solver.result.encounterDate"],
        ))

    displayed_optimized_start_date = ui_state.get("displayedOptimizedStartDate")
    solver_optimized_start_date = _first_value(result, [
        ["optimizedStartDate"],
        ["startDate"],
    ])
    if (
        displayed_optimized_start_date
        and solver_optimized_start_date
        and displayed_optimized_start_date != solver_optimized_start_date
    ):
        findings.append(_finding(
            "optimized-start-date-mismatch",
            "Optimiertes Startdatum in UI und Solver-Ergebnis stimmen nicht ueberein.",
            "warning",
            [run_id, "ui.displayedOptimizedStartDate", "solver.result.optimizedStartDate"],
        ))

    if ui_state.get("displayedFlightReady") is True and (
        solver_result.get("status") != "success" or validation.get("solverValid") is not True
    ):
        findings.append(_finding(
            "unsafe-flight-ready-label",
            "Die UI darf eine nicht freigegebene Route nicht als flugfaehig darstellen.",
            "error",
            [run_id, "ui.displayedFlightReady", "solver.validation"],
        ))

    if not findings:
        findings.append(_finding(
            "solver-ui-consistent",
            "Solver-Ergebnis und uebergebener UI-State sind plausibel kompatibel.",
            "info",
            [run_id],
        ))
    return findings


def _merge_findings(model_findings: object, guardrail_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(model_findings, list):
        for item in model_findings:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            message = str(item.get("message") or "").strip()
            severity = str(item.get("severity") or "info")
            refs = item.get("sourceRefs") if isinstance(item.get("sourceRefs"), list) else []
            if code and message and severity in {"info", "warning", "error"}:
                merged.append(_finding(code, message, severity, [str(ref) for ref in refs]))
                seen.add(code)
    for item in guardrail_findings:
        if item["code"] not in seen:
            merged.append(item)
    return merged[:12]


def generate_plausibility_check(
    payload: dict[str, Any],
    *,
    api_caller: Callable[[dict[str, Any]], dict[str, Any]] = _call_responses_api,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Der Plausibilitaetsauftrag muss ein JSON-Objekt sein.")
    mission_state = payload.get("missionState")
    if not isinstance(mission_state, dict):
        raise ValueError("Der Missionszustand fehlt.")
    mission_errors = validate_ai_payload("mission-state", mission_state)
    if mission_errors:
        raise ValueError("Ungueltiger Missionszustand: " + "; ".join(mission_errors))

    solver_result = payload.get("solverResult")
    if not isinstance(solver_result, dict):
        raise ValueError("Das Solver-Ergebnis fehlt.")
    solver_errors = validate_ai_payload("solver-result", solver_result)
    if solver_errors:
        raise ValueError("Ungueltiges Solver-Ergebnis: " + "; ".join(solver_errors))

    ui_state = payload.get("uiState") if isinstance(payload.get("uiState"), dict) else {}
    guardrail_findings = _deterministic_findings(solver_result, ui_state)
    model = configured_model("plausibility")
    context = {
        "missionState": mission_state,
        "solverResult": solver_result,
        "uiState": ui_state,
        "deterministicFindings": guardrail_findings,
    }
    request_payload = {
        "model": model,
        "instructions": PLAUSIBILITY_INSTRUCTIONS,
        "input": [{
            "role": "user",
            "content": "Pruefe diesen Missionszustand auf Plausibilitaet:\n"
            + json.dumps(context, ensure_ascii=False, allow_nan=False),
        }],
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "plausibility_check_response",
                "strict": True,
                "schema": PLAUSIBILITY_RESPONSE_SCHEMA,
            },
        },
        "max_output_tokens": 1_500,
        "store": False,
    }
    solver_run_id = str(solver_result["runId"])
    audit_input = {
        "missionState": mission_state,
        "solverResult": solver_result,
        "uiState": ui_state,
        "deterministicFindings": guardrail_findings,
    }
    try:
        api_response = api_caller(request_payload)
        structured = json.loads(_extract_output_text(api_response))
        findings = _merge_findings(structured.get("findings"), guardrail_findings)
        required_fixes = structured.get("requiredFixes")
        if not isinstance(required_fixes, list):
            required_fixes = []
        required_fixes = [str(item).strip() for item in required_fixes if str(item).strip()]
        if any(item["severity"] == "error" for item in guardrail_findings):
            required_fixes.append("Solver/UI-Widerspruch beheben, bevor die Route als freigegeben dargestellt wird.")
        status = _status_from_findings(findings)
        display_safe = status != "fail" and bool(structured.get("displaySafe", True))
        if any(item["severity"] == "error" for item in guardrail_findings):
            display_safe = False
        output = {
            "schemaVersion": SCHEMA_VERSION,
            "reportId": f"plausibility-{uuid4().hex[:10]}",
            "solverRunId": solver_run_id,
            "status": status,
            "findings": findings,
            "requiredFixes": required_fixes[:8],
            "displaySafe": display_safe,
            "model": str(api_response.get("model") or model),
            "responseId": str(api_response.get("id") or ""),
        }
        report_errors = validate_ai_payload("plausibility-report", {
            key: output[key]
            for key in [
                "schemaVersion",
                "reportId",
                "solverRunId",
                "status",
                "findings",
                "requiredFixes",
                "displaySafe",
            ]
        })
        if report_errors:
            raise ValueError("Ungueltiger Plausibilitaetsbericht: " + "; ".join(report_errors))
        audit = write_ai_audit(
            role="plausibility",
            model_name=output["model"],
            input_payload=audit_input,
            output_payload=output,
            status="success",
            prompt_version=PROMPT_VERSION,
            solver_run_ids=[solver_run_id],
        )
        return {**output, "auditRunId": audit["runId"]}
    except Exception as error:
        write_ai_audit(
            role="plausibility",
            model_name=model,
            input_payload=audit_input,
            output_payload=None,
            status="rejected" if isinstance(error, (ValueError, json.JSONDecodeError)) else "error",
            prompt_version=PROMPT_VERSION,
            solver_run_ids=[solver_run_id],
            error=str(error),
        )
        raise
