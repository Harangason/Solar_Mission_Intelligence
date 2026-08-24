from pathlib import Path
from time import perf_counter

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from services.activity_log import (
    activities_csv,
    flatten_scalar_values,
    read_activities,
    write_activity,
)

from solver.ephemeris import get_ephemeris_status
from services.calculation_audit import (
    METHOD_DOCUMENTATION,
    OPTIMIZER_AUDIT_LOG,
    PLAYBACK_AUDIT_LOG,
    ROUTE_AUDIT_LOG,
    read_latest_optimizer_audit,
    read_latest_playback_audit,
    read_latest_route_audit,
    start_playback_audit,
    write_playback_event,
)
from solver.trajectory import get_default_mission_config, simulate_mission
from planner.route_planner import simulate_waypoint_route
from planner.multi_route_planner import classify_route_sections, simulate_route_sections
from planner.mission_optimizer import assess_solar_energy, optimize_launch_window
from planner.trajectory_planner import calculate_trajectory_plan
from services.calculation_store import CalculationStore
from services.project_store import ProjectStore
from ai.audit_log import AI_AUDIT_LOGS, read_latest_ai_audit
from ai.audio_agent import synthesize_mission_speech, transcribe_mission_audio
from ai.calculation_agent import generate_calculation_suggestion
from ai.evaluation import load_saved_model, train_and_evaluate
from ai.interaction_agent import generate_mission_chat
from ai.plausibility_agent import generate_plausibility_check
from visualization.view_2d_celestials import render_2d_view
from visualization.view_3d_celestials import get_solar_system_data


PORT = 5001

WEB_DIST = Path(__file__).parent / "web" / "dist"
app = Flask(__name__, static_folder=None)
project_store = ProjectStore()
calculation_store = CalculationStore(project_store.database_path)


def _project_id(values: dict | None = None) -> str:
    values = values or {}
    return str(values.get("projectId") or request.headers.get("X-Project-Id") or "")


def _calculation_persistence(values: dict | None) -> dict:
    values = values or {}
    metadata = values.get("calculationPersistence")
    return metadata if isinstance(metadata, dict) else {}


def _record_route_variant(
    values: dict,
    *,
    result: dict | None,
    status: str,
    error_message: str = "",
) -> str:
    metadata = _calculation_persistence(values)
    run_id = str(metadata.get("runId") or "")
    if not run_id:
        return ""
    request_values = dict(values)
    request_values.pop("calculationPersistence", None)
    return calculation_store.record_variant(
        run_id,
        metadata,
        request_values,
        result=result,
        status=status,
        error_message=error_message,
    )


def _vector_delta(left: list[float], right: list[float]) -> list[float]:
    return [float(left[index]) - float(right[index]) for index in range(3)]


def _vector_length(vector: list[float]) -> float:
    return sum(component * component for component in vector) ** 0.5


def _normalize_vector(vector: list[float]) -> list[float]:
    length = _vector_length(vector) or 1.0
    return [component / length for component in vector]


def _planet_name(body_id: str) -> str:
    for planet in get_solar_system_data()["planets"]:
        if planet.get("id") == body_id:
            return str(planet.get("name") or body_id)
    return body_id


def _direct_lambert_route(values: dict) -> dict | None:
    if not values.get("preferLambertBodyToBody"):
        return None
    route_sections = values.get("routeSections")
    if not isinstance(route_sections, list) or len(route_sections) < 1:
        return None
    if not all(isinstance(section, dict) for section in route_sections):
        return None
    planet_ids = {planet["id"] for planet in get_solar_system_data()["planets"]}
    if any(
        str(section.get("originId") or "") not in planet_ids
        or str(section.get("targetId") or "") not in planet_ids
        or str(section.get("originId") or "") == str(section.get("targetId") or "")
        for section in route_sections
    ):
        return None

    mission = values.get("mission") if isinstance(values.get("mission"), dict) else {}
    start_date = str(mission.get("startDate") or "")
    if not start_date:
        raise ValueError("mission.startDate ist fuer direkte Lambert-Routen erforderlich.")
    constraints = values.get("lambertConstraints") if isinstance(values.get("lambertConstraints"), dict) else {}
    plans = []
    current_start_date = start_date
    for section in route_sections:
        origin_id = str(section.get("originId") or "")
        target_id = str(section.get("targetId") or "")
        plan = calculate_trajectory_plan({
            "start": {
                "type": "body",
                "bodyId": origin_id,
                "orbitAltitudeKm": mission.get("parkingOrbitAltitudeKm", 400),
                "startDate": current_start_date,
            },
            "target": {
                "type": "body",
                "bodyId": target_id,
                "arrivalMode": "flyby",
                "entryCorridor": section.get("corridor") or {},
            },
            "waypoints": [],
            "searchWindow": {
                "departureStartDate": current_start_date,
                "departureEndDate": current_start_date,
                "departureStepDays": 1,
                "arrivalStepDays": constraints.get("arrivalStepDays", 20),
            },
            "constraints": {
                "minFlightDays": constraints.get("minFlightDays", 120),
                "maxFlightDays": constraints.get("maxFlightDays", 1_200),
                **(values.get("constraints") if isinstance(values.get("constraints"), dict) else {}),
            },
            "optimizationMode": values.get("optimizationMode") or "balanced",
            "simulation": {
                "sampleTrajectoryPoints": 160,
                "includeUncertainty": False,
                "includeAudit": False,
                "propagationYears": constraints.get("propagationYears", 5),
            },
        })
        plans.append((section, plan))
        current_start_date = str(plan["summary"].get("targetReachedDate") or plan["bestCandidate"]["arrivalDate"])

    trajectory = []
    segments = []
    route_section_payloads = []
    elapsed_offset = 0.0
    total_flight_days = 0.0
    required_leg_delta_vs = []
    arrival_excess_speeds = []
    last_target_id = str(route_sections[-1].get("targetId") or "")
    last_target_center = None
    last_target_position = None
    last_incoming_direction = [1.0, 0.0, 0.0]
    maximum_endpoint_residual = 0.0
    min_solar_radius = None
    for section_index, (section, plan) in enumerate(plans):
        leg_trajectory = plan.get("trajectory") or []
        if len(leg_trajectory) < 2:
            raise ValueError("Lambert-Route enthaelt zu wenige Stützpunkte.")
        start_index = len(trajectory)
        for point_index, point in enumerate(leg_trajectory):
            if section_index > 0 and point_index == 0:
                continue
            trajectory.append({
                **point,
                "elapsedDays": float(point["elapsedDays"]) + elapsed_offset,
            })
            radius = _vector_length(point["positionKm"])
            min_solar_radius = radius if min_solar_radius is None else min(min_solar_radius, radius)
        end_index = len(trajectory) - 1
        leg_flight_days = float(plan["summary"]["totalFlightDays"])
        total_flight_days = elapsed_offset + leg_flight_days
        segments.append({
            "id": f"lambert-transfer-{section_index + 1}",
            "label": plan.get("segments", [{}])[0].get(
                "label",
                f"{section.get('originId')} -> {section.get('targetId')}",
            ),
            "startIndex": start_index,
            "endIndex": end_index,
        })
        target_position = plan.get("target", {}).get("entryPositionKm") or leg_trajectory[-1]["positionKm"]
        target_center = plan.get("target", {}).get("positionKm") or target_position
        incoming_direction = _normalize_vector(_vector_delta(target_position, target_center))
        if _vector_length(incoming_direction) == 0:
            incoming_direction = _normalize_vector(_vector_delta(
                leg_trajectory[-1]["positionKm"], leg_trajectory[-2]["positionKm"],
            ))
        required_delta_v = float(plan["summary"].get("requiredInjectionDeltaVKmS", 0) or 0)
        arrival_v_inf = float(plan["summary"].get("arrivalVInfinityKmS", 0) or 0)
        endpoint_residual = float(plan["summary"].get("endpointResidualKm", 0) or 0)
        maximum_endpoint_residual = max(maximum_endpoint_residual, endpoint_residual)
        corridor_payload = plan.get("entryCorridor") or {
            "enabled": False,
            "centerDirection": incoming_direction,
            "horizontalHalfAngleDeg": 0,
            "verticalHalfAngleDeg": 0,
            "rotationDeg": 0,
            "entryInsideCorridor": True,
        }
        route_section_payloads.append({
            "id": section.get("id", f"route-section-{section_index + 1}"),
            "sectionType": "lambert-body-to-body",
            "originId": str(section.get("originId") or ""),
            "targetId": str(section.get("targetId") or ""),
            "targetName": _planet_name(str(section.get("targetId") or "")),
            "entryIndex": end_index,
            "periapsisIndex": end_index,
            "exitIndex": end_index,
            "entryDay": elapsed_offset + leg_flight_days,
            "periapsisDay": elapsed_offset + leg_flight_days,
            "exitDay": elapsed_offset + leg_flight_days,
            "entryPositionKm": target_position,
            "entryDirection": incoming_direction,
            "entryLatitudeDeg": 0,
            "minimumAltitudeKm": 0,
            "requiredTransitionDeltaVKmS": required_delta_v,
            "availableTransitionDeltaVKmS": float(mission.get("oberthDeltaVKmS", required_delta_v) or required_delta_v),
            "transitionDeltaVDeficitKmS": max(0, required_delta_v - float(mission.get("oberthDeltaVKmS", required_delta_v) or required_delta_v)),
            "transferDurationDays": leg_flight_days,
            "corridorInsertionDeltaVKmS": 0,
            "entryVelocityPreserved": True,
            "lookaheadTargetId": str(route_sections[section_index + 1].get("targetId")) if section_index + 1 < len(route_sections) else None,
            "lookaheadAlignmentDeg": 0,
            "predictedPassiveTurnDeg": 0,
            "desiredDepartureDirection": incoming_direction,
            "predictedOutgoingDirection": incoming_direction,
            "requestedPassageAngleDeg": 0,
            "selectedPassageAngleDeg": 0,
            "lambertEndpointResidualKm": endpoint_residual,
            "corridor": {
                **corridor_payload,
                "entryInsideCorridor": corridor_payload.get("entryInsideCorridor", True),
            },
        })
        required_leg_delta_vs.append(required_delta_v)
        arrival_excess_speeds.append(arrival_v_inf)
        last_target_id = str(section.get("targetId") or "")
        last_target_center = target_center
        last_target_position = target_position
        last_incoming_direction = incoming_direction
        elapsed_offset += leg_flight_days

    if len(trajectory) < 2:
        raise ValueError("Lambert-Route enthaelt zu wenige Stützpunkte.")
    last_index = len(trajectory) - 1
    target_center = last_target_center or trajectory[-1]["positionKm"]
    available_delta_v = float(mission.get("oberthDeltaVKmS", max(required_leg_delta_vs or [0])) or 0)
    required_delta_v = max(required_leg_delta_vs or [0.0])
    arrival_v_inf = arrival_excess_speeds[-1] if arrival_excess_speeds else 0.0
    all_corridors_inside = all(
        section["corridor"].get("entryInsideCorridor", True)
        for section in route_section_payloads
    )
    return {
        "startDate": start_date,
        "totalFlightDays": total_flight_days,
        "warnings": [warning for _, plan in plans for warning in (plan.get("warnings") or [])],
        "waypoint": {
            "id": last_target_id,
            "name": _planet_name(last_target_id),
            "encounterDay": total_flight_days,
            "flybyAltitudeKm": 0,
            "trajectoryIndex": last_index,
            "positionKm": target_center,
        },
        "trajectory": trajectory,
        "segments": segments,
        "routeSections": route_section_payloads,
        "stateChain": {
            "continuousPosition": True,
            "exitStateFeedsNextSection": True,
        },
        "validation": {
            "collisionFree": True,
            "minimumSolarRadiusKm": min_solar_radius or 0,
            "sunRadiusKm": 696_340,
            "minimumSolarAltitudeKm": (min_solar_radius or 0) - 696_340,
        },
        "outgoingDirection": last_incoming_direction,
        "entryCorridor": route_section_payloads[-1]["corridor"],
        "summary": {
            "flybyMode": "multi-section",
            "requiredInjectionDeltaVKmS": required_delta_v,
            "availableInjectionDeltaVKmS": available_delta_v,
            "solarDepartureInjectionApplied": required_delta_v <= available_delta_v,
            "incomingExcessSpeedKmS": arrival_v_inf,
            "turnAngleDeg": 0,
            "heliocentricSpeedBeforeKmS": plans[-1][1]["summary"].get("finalHeliocentricSpeedKmS", 0),
            "heliocentricSpeedAfterKmS": plans[-1][1]["summary"].get("finalHeliocentricSpeedKmS", 0),
            "speedGainKmS": 0,
            "targetCorrectionDeltaVKmS": 0,
            "targetInjectionApplied": False,
            "passiveTargeting": True,
            "actualTargetAlignmentDeg": 0,
            "courseChangeDeg": 0,
            "periapsisSpeedKmS": arrival_v_inf,
            "observationWindowHours": 0,
            "targetAlignmentDeg": 0,
            "feasibleWithConfiguredBurn": (
                all(bool(plan["summary"].get("feasible")) for _, plan in plans)
                and required_delta_v <= available_delta_v
                and all_corridors_inside
            ),
            "entryCorridorTargeted": any(bool(plan["summary"].get("entryCorridorTargeted")) for _, plan in plans),
            "entryInsideCorridor": all_corridors_inside,
            "warnings": [warning for _, plan in plans for warning in (plan.get("warnings") or [])],
            "model": "multi-leg body-to-body Lambert route bound to launch windows and entry corridors",
        },
        "genericTrajectoryPlan": plans[0][1],
        "genericTrajectoryPlans": [plan for _, plan in plans],
    }


def _write_calculation_activity(
    action: str,
    started_at: float,
    *,
    status: str,
    values: dict,
    result: object | None = None,
    message: str = "",
    details: dict | None = None,
) -> None:
    calculation_values = {
        **flatten_scalar_values(values, "input", limit=30),
        **flatten_scalar_values(result or {}, "result", limit=70),
    }
    if isinstance(result, dict):
        for index, section in enumerate(result.get("routeSections") or []):
            corridor = section.get("corridor") or {}
            prefix = f"audit.routeSections.{index}"
            calculation_values[f"{prefix}.targetId"] = section.get("targetId")
            calculation_values[f"{prefix}.entryInsideCorridor"] = corridor.get(
                "entryInsideCorridor"
            )
            calculation_values[f"{prefix}.requiredTransitionDeltaVKmS"] = section.get(
                "requiredTransitionDeltaVKmS"
            )
            calculation_values[f"{prefix}.corridorInsertionDeltaVKmS"] = section.get(
                "corridorInsertionDeltaVKmS"
            )
            calculation_values[f"{prefix}.lookaheadAlignmentDeg"] = section.get(
                "lookaheadAlignmentDeg"
            )
            calculation_values[f"{prefix}.availableTransitionDeltaVKmS"] = section.get(
                "availableTransitionDeltaVKmS"
            )
            calculation_values[f"{prefix}.transitionDeltaVDeficitKmS"] = section.get(
                "transitionDeltaVDeficitKmS"
            )
            calculation_values[f"{prefix}.departureRadialSpeedKmS"] = section.get(
                "departureRadialSpeedKmS"
            )
            calculation_values[f"{prefix}.departureDirectionChangeDeg"] = section.get(
                "departureDirectionChangeDeg"
            )
            calculation_values[f"{prefix}.backtracksFromOuterTarget"] = section.get(
                "backtracksFromOuterTarget"
            )
            calculation_values[f"{prefix}.transferDurationDays"] = section.get(
                "transferDurationDays"
            )
    write_activity(
        source="backend",
        category="calculation",
        action=action,
        status=status,
        project_id=_project_id(values),
        duration_ms=(perf_counter() - started_at) * 1_000,
        message=message,
        values=calculation_values,
        details={
            "routeSectionCount": len(values.get("routeSections") or []),
            "waypointId": values.get("waypointId"),
            **(details or {}),
        },
    )


def _write_optimizer_trace(values: dict, result: dict) -> None:
    audit = result.get("audit") or {}
    search_run_id = str(audit.get("runId") or "")
    project_id = _project_id(values)
    for item in result.get("history") or []:
        write_activity(
            source="backend",
            category="calculation",
            action="optimizer-iteration",
            status="success",
            project_id=project_id,
            values=flatten_scalar_values(item, limit=40),
            details={
                "searchRunId": search_run_id,
                "stage": "adaptive-refinement",
            },
        )
    for candidate in result.get("fullValidationCandidates") or []:
        reasons = candidate.get("rejectionReasons") or []
        write_activity(
            source="backend",
            category="calculation",
            action="optimizer-full-validation",
            status="success" if candidate.get("plausible") else "rejected",
            project_id=project_id,
            message=" | ".join(str(reason) for reason in reasons),
            values=flatten_scalar_values(candidate, limit=60),
            details={
                "searchRunId": search_run_id,
                "stage": "full-model-validation",
                "role": candidate.get("role"),
                "rejectionKind": "" if candidate.get("plausible") else "constraint",
            },
        )


@app.after_request
def disable_local_browser_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/solar-system")
def solar_system_data():
    return jsonify(get_solar_system_data())


@app.get("/api/ephemeris/status")
def ephemeris_status():
    try:
        return jsonify(get_ephemeris_status())
    except RuntimeError as error:
        return jsonify({"error": str(error), "active": False}), 503


@app.get("/api/view/2d")
def two_dimensional_view():
    return send_file(render_2d_view(), mimetype="image/png", max_age=0)


@app.get("/api/mission/defaults")
def mission_defaults():
    return jsonify(get_default_mission_config())


@app.get("/api/projects")
def list_projects():
    return jsonify({"projects": project_store.list_projects()})


@app.get("/api/projects/<project_id>")
def load_project(project_id: str):
    try:
        return jsonify(project_store.get_project(project_id))
    except LookupError as error:
        return jsonify({"error": str(error)}), 404


@app.post("/api/projects")
def create_project():
    try:
        return jsonify(project_store.create_project(request.get_json(silent=True) or {})), 201
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.put("/api/projects/<project_id>")
def update_project(project_id: str):
    try:
        return jsonify(project_store.update_project(project_id, request.get_json(silent=True) or {}))
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.delete("/api/projects/<project_id>")
def delete_project(project_id: str):
    try:
        project_store.delete_project(project_id)
        return "", 204
    except LookupError as error:
        return jsonify({"error": str(error)}), 404


@app.get("/api/calculations/runs")
def calculation_runs():
    try:
        runs = calculation_store.list_runs(
            project_id=str(request.args.get("projectId") or _project_id()),
            limit=int(request.args.get("limit", 25)),
        )
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"runs": runs})


@app.post("/api/calculations/runs")
def start_calculation_run():
    values = request.get_json(silent=True) or {}
    try:
        run = calculation_store.start_run(values, project_id=_project_id(values))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    return jsonify(run), 201


@app.get("/api/calculations/runs/<run_id>")
def load_calculation_run(run_id: str):
    try:
        run = calculation_store.get_run(run_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    return jsonify(run)


@app.patch("/api/calculations/runs/<run_id>")
def update_calculation_run(run_id: str):
    values = request.get_json(silent=True) or {}
    try:
        run = calculation_store.update_run(run_id, values)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    return jsonify(run)


@app.delete("/api/calculations/runs/<run_id>")
def delete_calculation_run(run_id: str):
    try:
        calculation_store.delete_run(run_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    return "", 204


@app.get("/api/calculations/variants/<variant_id>")
def load_calculation_variant(variant_id: str):
    try:
        variant = calculation_store.get_variant(variant_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    return jsonify(variant)


@app.patch("/api/calculations/runs/<run_id>/variants/<variant_id>")
def update_calculation_variant(run_id: str, variant_id: str):
    values = request.get_json(silent=True) or {}
    try:
        variant = calculation_store.update_variant(run_id, variant_id, values)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    return jsonify(variant)


@app.post("/api/mission/simulate")
def mission_simulation():
    started_at = perf_counter()
    values = request.get_json(silent=True) or {}
    try:
        result = simulate_mission(values)
    except ValueError as error:
        _write_calculation_activity(
            "mission-simulate",
            started_at,
            status="rejected",
            values=values,
            message=str(error),
        )
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        _write_calculation_activity(
            "mission-simulate",
            started_at,
            status="error",
            values=values,
            message=str(error),
        )
        return jsonify({"error": str(error)}), 422
    payload = result.to_dict()
    _write_calculation_activity(
        "mission-simulate",
        started_at,
        status="success",
        values=values,
        result=payload,
    )
    return jsonify(payload)


@app.post("/api/route/simulate")
def waypoint_route_simulation():
    started_at = perf_counter()
    values = request.get_json(silent=True) or {}
    route_classification = (
        classify_route_sections(values.get("routeSections"))
        if values.get("routeSections")
        else {"solver": "waypoint", "reason": "legacy-waypoint-request"}
    )
    try:
        lambert_route = _direct_lambert_route(values)
        if lambert_route is not None:
            result = lambert_route
        elif values.get("routeSections"):
            result = simulate_route_sections(values)
        else:
            result = simulate_waypoint_route(values)
    except ValueError as error:
        variant_id = _record_route_variant(
            values,
            result=None,
            status="rejected",
            error_message=str(error),
        )
        _write_calculation_activity(
            "route-simulate",
            started_at,
            status="rejected",
            values=values,
            message=str(error),
            details={
                **route_classification,
                "rejectionKind": "structural-or-constraint",
            },
        )
        return jsonify(
            {"error": str(error), "calculationVariantId": variant_id}
        ), 400
    except RuntimeError as error:
        variant_id = _record_route_variant(
            values,
            result=None,
            status="error",
            error_message=str(error),
        )
        _write_calculation_activity(
            "route-simulate",
            started_at,
            status="error",
            values=values,
            message=str(error),
            details={
                **route_classification,
                "rejectionKind": "numerical",
            },
        )
        return jsonify(
            {"error": str(error), "calculationVariantId": variant_id}
        ), 422
    variant_id = _record_route_variant(
        values,
        result=result if isinstance(result, dict) else None,
        status="solver-completed",
    )
    _write_calculation_activity(
        "route-simulate",
        started_at,
        status="success",
        values=values,
        result=result,
        details=route_classification,
    )
    if variant_id and isinstance(result, dict):
        return jsonify(
            {
                **result,
                "calculationPersistence": {
                    "runId": _calculation_persistence(values).get("runId"),
                    "variantId": variant_id,
                },
            }
        )
    return jsonify(result)


@app.post("/api/trajectory/plan")
def generic_trajectory_plan():
    started_at = perf_counter()
    values = request.get_json(silent=True) or {}
    try:
        result = calculate_trajectory_plan(values)
    except ValueError as error:
        _write_calculation_activity(
            "trajectory-plan", started_at, status="rejected",
            values=values, message=str(error),
            details={"targetType": (values.get("target") or {}).get("type")},
        )
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        _write_calculation_activity(
            "trajectory-plan", started_at, status="error",
            values=values, message=str(error),
            details={"targetType": (values.get("target") or {}).get("type")},
        )
        return jsonify({"error": str(error)}), 422
    _write_calculation_activity(
        "trajectory-plan", started_at, status="success",
        values=values, result=result,
        details={"mode": result.get("mode"), "targetType": result.get("target", {}).get("type")},
    )
    return jsonify(result)


@app.post("/api/activity")
def append_activity():
    values = request.get_json(silent=True) or {}
    try:
        record = write_activity(
            source=str(values.get("source") or "frontend"),
            category=str(values.get("category") or "ui"),
            action=str(values.get("action") or "unknown"),
            status=str(values.get("status") or "success"),
            project_id=str(values.get("projectId") or ""),
            duration_ms=values.get("durationMs"),
            message=str(values.get("message") or ""),
            values=values.get("values"),
            details=values.get("details"),
        )
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(record), 201


@app.get("/api/activity")
def activity_entries():
    try:
        records = read_activities(
            limit=int(request.args.get("limit", 250)),
            category=request.args.get("category", ""),
            status=request.args.get("status", ""),
            project_id=request.args.get("projectId", ""),
        )
    except ValueError:
        return jsonify({"error": "Das Abfragelimit ist ungültig."}), 400
    return jsonify({"activities": records, "count": len(records)})


@app.get("/api/activity/export.csv")
def activity_csv_export():
    try:
        records = read_activities(
            limit=int(request.args.get("limit", 5_000)),
            category=request.args.get("category", ""),
            status=request.args.get("status", ""),
            project_id=request.args.get("projectId", ""),
        )
    except ValueError:
        return jsonify({"error": "Das Abfragelimit ist ungültig."}), 400
    return Response(
        activities_csv(records),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=solar-system-activities.csv"},
    )


@app.get("/api/audit/latest-route")
def latest_route_audit():
    record = read_latest_route_audit()
    if record is None:
        return jsonify({"error": "Noch keine Routenberechnung protokolliert."}), 404
    return jsonify(record)


@app.get("/api/audit/route-log")
def route_audit_log():
    if not ROUTE_AUDIT_LOG.exists():
        return jsonify({"error": "Noch keine Routenberechnung protokolliert."}), 404
    return send_file(ROUTE_AUDIT_LOG, mimetype="application/x-ndjson", as_attachment=True)


@app.get("/api/audit/latest-optimizer")
def latest_optimizer_audit():
    record = read_latest_optimizer_audit()
    if record is None:
        return jsonify({"error": "Noch keine Navigatorsuche protokolliert."}), 404
    return jsonify(record)


@app.get("/api/audit/optimizer-log")
def optimizer_audit_log():
    if not OPTIMIZER_AUDIT_LOG.exists():
        return jsonify({"error": "Noch keine Navigatorsuche protokolliert."}), 404
    return send_file(OPTIMIZER_AUDIT_LOG, mimetype="application/x-ndjson", as_attachment=True)


@app.post("/api/audit/playback/start")
def start_playback_log():
    values = request.get_json(silent=True) or {}
    try:
        result = start_playback_audit(values)
    except (TypeError, ValueError) as error:
        write_activity(
            source="backend",
            category="playback",
            action="playback-start",
            status="error",
            project_id=_project_id(values),
            message=str(error),
        )
        return jsonify({"error": str(error)}), 400
    write_activity(
        source="backend",
        category="playback",
        action="playback-start",
        project_id=_project_id(values),
        values={
            "playbackEndDay": values.get("playbackEndDay"),
            "routeSectionCount": len(values.get("routeSections") or []),
        },
        details={
            "playbackId": result.get("playbackId"),
            "originId": values.get("originId"),
            "targetId": values.get("targetId"),
        },
    )
    return jsonify(result)


@app.post("/api/audit/playback/event")
def append_playback_log_event():
    values = request.get_json(silent=True) or {}
    try:
        result = write_playback_event(values)
    except (TypeError, ValueError) as error:
        write_activity(
            source="backend",
            category="playback",
            action=str(values.get("eventType") or "playback-event"),
            status="error",
            project_id=_project_id(values),
            message=str(error),
        )
        return jsonify({"error": str(error)}), 400
    write_activity(
        source="backend",
        category="playback",
        action=str(values.get("eventType") or "playback-event"),
        project_id=_project_id(values),
        values={
            "sequence": values.get("sequence"),
            "missionDay": values.get("missionDay"),
            **flatten_scalar_values(values.get("state") or {}, "state", limit=12),
        },
        details={
            "playbackId": values.get("playbackId"),
            "sectionId": values.get("sectionId"),
            "sectionLabel": values.get("sectionLabel"),
        },
    )
    return jsonify(result)


@app.get("/api/audit/latest-playback")
def latest_playback_audit():
    record = read_latest_playback_audit()
    if record is None:
        return jsonify({"error": "Noch kein Missionslauf protokolliert."}), 404
    return jsonify(record)


@app.get("/api/audit/playback-log")
def playback_audit_log():
    if not PLAYBACK_AUDIT_LOG.exists():
        return jsonify({"error": "Noch kein Missionslauf protokolliert."}), 404
    return send_file(
        PLAYBACK_AUDIT_LOG,
        mimetype="application/x-ndjson",
        as_attachment=True,
        download_name="mission_playback.jsonl",
    )


@app.get("/api/audit/methods")
def calculation_methods():
    return send_file(METHOD_DOCUMENTATION, mimetype="text/markdown; charset=utf-8")


@app.post("/api/ai/mission-chat")
def ai_mission_chat():
    values = request.get_json(silent=True)
    try:
        return jsonify(generate_mission_chat(values))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503


@app.post("/api/ai/transcribe")
def ai_transcribe_audio():
    upload = request.files.get("audio")
    if upload is None:
        return jsonify({"error": "Keine Audioaufnahme uebergeben."}), 400
    try:
        payload = transcribe_mission_audio(
            file_bytes=upload.read(),
            filename=upload.filename or "recording.webm",
            mime_type=upload.mimetype or upload.content_type or "",
        )
        return jsonify(payload)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503


@app.post("/api/ai/speech")
def ai_speech_audio():
    values = request.get_json(silent=True) or {}
    try:
        audio_bytes, content_type, audit = synthesize_mission_speech(
            text=str(values.get("text") or ""),
        )
        return Response(
            audio_bytes,
            mimetype=content_type,
            headers={
                "X-AI-Audit-Run-Id": str(audit.get("runId") or ""),
                "Content-Disposition": 'inline; filename="mission-chat.mp3"',
            },
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503


@app.post("/api/ai/plausibility-check")
def ai_plausibility_check():
    values = request.get_json(silent=True)
    try:
        return jsonify(generate_plausibility_check(values))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503


@app.post("/api/ai/calculation-suggest")
def ai_calculation_suggest():
    values = request.get_json(silent=True)
    try:
        return jsonify(generate_calculation_suggestion(values))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503


@app.get("/api/ai/ml/evaluation")
def ai_ml_evaluation():
    return jsonify(train_and_evaluate())


@app.post("/api/ai/ml/train")
def ai_ml_train():
    values = request.get_json(silent=True)
    project_id = _project_id(values)
    report = train_and_evaluate(persist_model=True, project_id=project_id)
    write_activity(
        source="ml-ranker",
        category="ai",
        action="ml-candidate-ranker-trained",
        status="success" if report["verdict"] == "ready" else "rejected",
        project_id=project_id,
        values={
            "trainingRows": report["dataset"]["rows"],
            "positiveRows": report["dataset"]["positiveRows"],
            "negativeRows": report["dataset"]["negativeRows"],
            "userCorrectionRows": report["dataset"]["userCorrectionRows"],
            "groups": report["evaluation"]["groups"],
            "pairwiseAccuracy": report["evaluation"]["pairwiseAccuracy"],
        },
        details={"modelPath": report.get("modelPath", "")},
    )
    return jsonify(report)


@app.get("/api/ai/ml/model")
def ai_ml_model():
    model = load_saved_model()
    if model is None:
        return jsonify({"error": "Noch kein persistentes ML-Modell trainiert."}), 404
    return jsonify(model)


@app.get("/api/ai/audit/latest")
def latest_ai_audit():
    role = request.args.get("role", "interaction")
    try:
        record = read_latest_ai_audit(role)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if record is None:
        return jsonify({"error": "Noch kein KI-Aufruf protokolliert."}), 404
    return jsonify(record)


@app.get("/api/ai/audit/log")
def ai_audit_log():
    role = request.args.get("role", "interaction")
    if role not in AI_AUDIT_LOGS:
        return jsonify({"error": "Unbekannte KI-Rolle."}), 400
    path = AI_AUDIT_LOGS[role]
    if not path.exists():
        return jsonify({"error": "Noch kein KI-Aufruf protokolliert."}), 404
    return send_file(path, mimetype="application/x-ndjson", as_attachment=True)


@app.post("/api/mission/optimize-launch-window")
def launch_window_optimization():
    started_at = perf_counter()
    values = request.get_json(silent=True) or {}
    try:
        result = optimize_launch_window(values)
    except ValueError as error:
        _write_calculation_activity(
            "optimize-launch-window",
            started_at,
            status="rejected",
            values=values,
            message=str(error),
        )
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        _write_calculation_activity(
            "optimize-launch-window",
            started_at,
            status="error",
            values=values,
            message=str(error),
        )
        return jsonify({"error": str(error)}), 422
    _write_optimizer_trace(values, result)
    _write_calculation_activity(
        "optimize-launch-window",
        started_at,
        status="success",
        values=values,
        result=result,
        details={
            "searchRunId": (result.get("audit") or {}).get("runId"),
            "resultKind": "flight-ready" if result.get("plausible") else "best-effort",
            "stopReason": result.get("stopReason"),
            "iterations": result.get("iterations"),
            "evaluations": result.get("evaluations"),
        },
    )
    return jsonify(result)


@app.post("/api/mission/assess-solar-energy")
def solar_energy_assessment():
    started_at = perf_counter()
    values = request.get_json(silent=True) or {}
    try:
        result = assess_solar_energy(values)
    except ValueError as error:
        _write_calculation_activity(
            "assess-solar-energy",
            started_at,
            status="rejected",
            values=values,
            message=str(error),
        )
        return jsonify({"error": str(error)}), 400
    _write_calculation_activity(
        "assess-solar-energy",
        started_at,
        status="success",
        values=values,
        result=result,
    )
    return jsonify(result)


@app.get("/")
@app.get("/<path:requested_path>")
def browser_application(requested_path=""):
    requested_file = WEB_DIST / requested_path
    if requested_path and requested_file.is_file():
        return send_from_directory(WEB_DIST, requested_path)
    return send_from_directory(WEB_DIST, "index.html")


def main():
    if not WEB_DIST.exists():
        raise RuntimeError("Frontend fehlt. Bitte zuerst im Ordner 'web' den Befehl 'npm run build' ausführen.")
    print(f"Sonnensystem wird unter http://127.0.0.1:{PORT} bereitgestellt")
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
