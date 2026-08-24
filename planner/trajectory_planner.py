"""Target-independent trajectory planning facade.

This module deliberately contains orchestration and result normalization only.
Lambert propagation, body ephemerides, flybys and Solar-Oberth dynamics remain
implemented by the established solver modules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import acos, asin, atan2, cos, pi, sin, sqrt
from typing import Any
from uuid import uuid4

from planner.generic_route_planner import (
    SUN_RADIUS_KM,
    _body_state,
    _catalog,
    _entry_radius,
)
from planner.multi_route_planner import simulate_route_sections
from planner.route_planner import (
    G_KM3_KG_S2,
    _lambert_candidates,
    _parse_entry_corridor,
    _propagate_lambert_segment,
    _select_entry_corridor_target,
    simulate_direct_solar_route,
    simulate_waypoint_route,
)
from services.calculation_audit import write_route_audit
from solver.trajectory import (
    AU_KM,
    DAY_SECONDS,
    MU_SUN,
    _magnitude,
    _mission_epoch_days,
    _normalize,
)


ZONE_DEFINITIONS = {
    "asteroid_belt": {"name": "Asteroidengürtel", "innerRadiusAU": 2.1, "outerRadiusAU": 3.3},
    "kuiper_belt": {"name": "Kuipergürtel", "innerRadiusAU": 30.0, "outerRadiusAU": 50.0},
    "scattered_disk": {"name": "Scattered Disk", "innerRadiusAU": 50.0, "outerRadiusAU": 1_000.0},
    "oort_cloud": {"name": "Oortsche Wolke", "innerRadiusAU": 2_000.0, "outerRadiusAU": 100_000.0},
}

BOUNDARY_DEFINITIONS = {
    "100_au": {"name": "100-AE-Grenze", "radiusAU": 100.0},
    "termination_shock": {"name": "Termination Shock", "radiusAU": 94.0},
    "heliopause": {"name": "Heliopause", "radiusAU": 120.0},
    "voyager_1_distance": {"name": "Voyager-1-Distanz", "radiusAU": 170.0},
}

TARGET_TYPES = {
    "body", "body_orbit", "flyby", "zone", "boundary", "direction", "state_vector",
}
OPTIMIZATION_MODES = {
    "minimum_energy", "minimum_time", "minimum_arrival_speed",
    "maximum_exit_speed", "minimum_delta_v", "balanced", "custom",
}


def _number(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result


def _date(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} muss ein ISO-Datum sein.") from error
    return parsed.replace(tzinfo=timezone.utc)


def _date_range(start: object, end: object, step_days: object, field: str) -> list[datetime]:
    first = _date(start, f"{field}.start")
    last = _date(end, f"{field}.end")
    step = max(1, int(_number(step_days, 1)))
    if last < first:
        raise ValueError(f"{field}: Enddatum liegt vor dem Startdatum.")
    count = int((last - first).days / step) + 1
    if count > 400:
        raise ValueError(f"{field} enthält mehr als 400 Rasterpunkte.")
    return [first + timedelta(days=index * step) for index in range(count)]


def _vector(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{field} muss drei Komponenten besitzen.")
    result = tuple(_number(component, float("nan")) for component in value)
    if any(component != component for component in result):
        raise ValueError(f"{field} enthält ungültige Komponenten.")
    return result


def _direction(target: dict) -> tuple[float, float, float]:
    if target.get("positionKm") is not None:
        return _normalize(_vector(target["positionKm"], "target.positionKm"))
    if target.get("eclipticLongitudeDeg") is not None:
        longitude = _number(target.get("eclipticLongitudeDeg"), 0.0) * pi / 180
        latitude = _number(target.get("eclipticLatitudeDeg"), 0.0) * pi / 180
        return _normalize((cos(latitude) * cos(longitude), cos(latitude) * sin(longitude), sin(latitude)))
    right_ascension = _number(target.get("rightAscensionDeg"), 0.0) * pi / 180
    declination = _number(target.get("declinationDeg"), 0.0) * pi / 180
    obliquity = 23.43928 * pi / 180
    equatorial = (
        cos(declination) * cos(right_ascension),
        cos(declination) * sin(right_ascension),
        sin(declination),
    )
    return _normalize((
        equatorial[0],
        equatorial[1] * cos(obliquity) + equatorial[2] * sin(obliquity),
        -equatorial[1] * sin(obliquity) + equatorial[2] * cos(obliquity),
    ))


def calculate_candidate_score(
    candidate: dict,
    optimization_mode: str,
    weights: dict | None = None,
) -> float:
    """Return a dimensionless lower-is-better score for every target type."""
    if optimization_mode not in OPTIMIZATION_MODES:
        raise ValueError(f"Unbekannter Optimierungsmodus '{optimization_mode}'.")
    energy = max(0.0, _number(candidate.get("c3Km2S2"), 0.0)) / 400.0
    flight_time = max(0.0, _number(candidate.get("flightDays"), 0.0)) / 3_650.0
    arrival = max(0.0, _number(candidate.get("arrivalVInfinityKmS"), 0.0)) / 30.0
    risk = max(0.0, _number(candidate.get("risk"), len(candidate.get("warnings") or []))) / 10.0
    delta_v = max(0.0, _number(candidate.get("totalDeltaVKmS"), 0.0)) / 30.0
    exit_speed = max(0.0, _number(candidate.get("finalHeliocentricSpeedKmS"), 0.0)) / 100.0
    if optimization_mode == "minimum_energy":
        return energy
    if optimization_mode == "minimum_time":
        return flight_time
    if optimization_mode == "minimum_arrival_speed":
        return arrival
    if optimization_mode == "maximum_exit_speed":
        return -exit_speed
    if optimization_mode == "minimum_delta_v":
        return delta_v
    selected = weights or {
        "energy": 0.35, "flightTime": 0.25, "arrivalSpeed": 0.20,
        "risk": 0.10, "deltaV": 0.10,
    }
    return (
        energy * _number(selected.get("energy"), 0.35)
        + flight_time * _number(selected.get("flightTime"), 0.25)
        + arrival * _number(selected.get("arrivalSpeed"), 0.20)
        + risk * _number(selected.get("risk"), 0.10)
        + delta_v * _number(selected.get("deltaV"), 0.10)
    )


def _normalized_input(values: dict) -> dict:
    start = dict(values.get("start") or {})
    target = dict(values.get("target") or {})
    waypoints = list(values.get("waypoints") or [])
    search = dict(values.get("searchWindow") or {})
    constraints = dict(values.get("constraints") or {})
    simulation = dict(values.get("simulation") or {})
    start_type = str(start.get("type") or "body")
    target_type = str(target.get("type") or "")
    if start_type not in {"body", "orbit", "state_vector"}:
        raise ValueError(f"Unbekannter Starttyp '{start_type}'.")
    if target_type not in TARGET_TYPES:
        raise ValueError(f"Unbekannter Zieltyp '{target_type}'.")
    start_date = str(start.get("startDate") or search.get("departureStartDate") or "")
    if not start_date:
        raise ValueError("start.startDate ist erforderlich.")
    departure_start = str(search.get("departureStartDate") or start_date)
    departure_end = str(search.get("departureEndDate") or departure_start)
    optimization_mode = str(values.get("optimizationMode") or "balanced")
    if optimization_mode not in OPTIMIZATION_MODES:
        raise ValueError(f"Unbekannter Optimierungsmodus '{optimization_mode}'.")
    normalized = {
        **values,
        "start": {**start, "type": start_type, "startDate": start_date},
        "target": {**target, "type": target_type},
        "waypoints": waypoints,
        "searchWindow": {
            **search,
            "departureStartDate": departure_start,
            "departureEndDate": departure_end,
            "departureStepDays": max(1, int(_number(search.get("departureStepDays"), 10))),
        },
        "constraints": constraints,
        "optimizationMode": optimization_mode,
        "simulation": {
            **simulation,
            "sampleTrajectoryPoints": max(24, min(2_000, int(_number(simulation.get("sampleTrajectoryPoints"), 240)))),
            "includeUncertainty": bool(simulation.get("includeUncertainty", False)),
            "includeAudit": bool(simulation.get("includeAudit", True)),
            "propagationYears": max(0.1, min(50_000.0, _number(simulation.get("propagationYears"), 20.0))),
        },
    }
    return normalized


def _start_state(input_values: dict, date: datetime) -> tuple[tuple, tuple, str | None]:
    start = input_values["start"]
    if start["type"] == "state_vector":
        return (
            _vector(start.get("positionKm"), "start.positionKm"),
            _vector(start.get("velocityKmS"), "start.velocityKmS"),
            None,
        )
    body_id = str(start.get("bodyId") or "")
    catalog = _catalog()
    if body_id not in catalog:
        raise ValueError(f"Startkörper '{body_id}' besitzt keine Ephemeride.")
    body = catalog[body_id]
    position, velocity = _body_state(body, _mission_epoch_days(date.date().isoformat()), catalog)
    if start.get("orbitAltitudeKm") is not None and body.kind != "sun":
        altitude = max(0.0, _number(start.get("orbitAltitudeKm"), 0.0))
        orbit_radius = body.radius_km + altitude
        radial = _normalize(position)
        tangent_component = tuple(
            velocity[index] - sum(velocity[axis] * radial[axis] for axis in range(3)) * radial[index]
            for index in range(3)
        )
        tangent = _normalize(tangent_component)
        circular_speed = sqrt(max(0.0, body.mass_kg * G_KM3_KG_S2 / max(orbit_radius, 1.0)))
        position = tuple(position[index] + radial[index] * orbit_radius for index in range(3))
        velocity = tuple(velocity[index] + tangent[index] * circular_speed for index in range(3))
    return position, velocity, body_id


def _constraints(candidate: dict, values: dict) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    checks = [
        ("minFlightDays", lambda actual, limit: actual >= limit, "Mindestflugzeit unterschritten"),
        ("maxFlightDays", lambda actual, limit: actual <= limit, "Maximalflugzeit überschritten"),
        ("maxC3Km2S2", lambda actual, limit: actual <= limit, "C3-Grenze überschritten"),
        ("maxDepartureVInfinityKmS", lambda actual, limit: actual <= limit, "Start-v∞ überschritten"),
        ("maxArrivalVInfinityKmS", lambda actual, limit: actual <= limit, "Ankunfts-v∞ überschritten"),
        ("maxTotalDeltaVKmS", lambda actual, limit: actual <= limit, "Gesamt-Δv überschritten"),
    ]
    mapping = {
        "minFlightDays": "flightDays", "maxFlightDays": "flightDays",
        "maxC3Km2S2": "c3Km2S2",
        "maxDepartureVInfinityKmS": "departureVInfinityKmS",
        "maxArrivalVInfinityKmS": "arrivalVInfinityKmS",
        "maxTotalDeltaVKmS": "totalDeltaVKmS",
    }
    for key, predicate, message in checks:
        if values.get(key) is None:
            continue
        actual = _number(candidate.get(mapping[key]), 0.0)
        if not predicate(actual, _number(values[key], 0.0)):
            warnings.append(message)
    return not warnings, warnings


def calculate_body_to_body_transfer(values: dict) -> dict:
    input_values = _normalized_input(values)
    target = input_values["target"]
    target_id = str(target.get("bodyId") or "")
    catalog = _catalog()
    if target_id not in catalog:
        raise ValueError(f"Zielkörper '{target_id}' besitzt keine Ephemeride.")
    target_body = catalog[target_id]
    entry_corridor = _parse_entry_corridor(target.get("entryCorridor") or {})
    search = input_values["searchWindow"]
    departures = _date_range(
        search["departureStartDate"], search["departureEndDate"],
        search["departureStepDays"], "searchWindow.departure",
    )
    explicit_arrivals: list[datetime] | None = None
    flight_day_options: list[int] | None = None
    if search.get("arrivalStartDate") and search.get("arrivalEndDate"):
        explicit_arrivals = _date_range(
            search["arrivalStartDate"], search["arrivalEndDate"],
            search.get("arrivalStepDays", search["departureStepDays"]),
            "searchWindow.arrival",
        )
    elif target.get("targetDate"):
        explicit_arrivals = [_date(target["targetDate"], "target.targetDate")]
    else:
        minimum = max(1.0, _number(input_values["constraints"].get("minFlightDays"), 120.0))
        maximum = max(minimum, _number(input_values["constraints"].get("maxFlightDays"), 1_200.0))
        arrival_step = max(1, int(_number(search.get("arrivalStepDays"), max(20, (maximum - minimum) / 12))))
        flight_day_options = list(range(int(minimum), int(maximum) + 1, arrival_step))
    arrival_count_per_departure = len(explicit_arrivals or flight_day_options or [])
    if len(departures) * arrival_count_per_departure > 4_000:
        raise ValueError("Das kombinierte Start-/Ankunftsraster überschreitet 4.000 Datumspaare.")
    candidates: list[dict] = []
    for departure_date in departures:
        start_position, start_velocity, start_body_id = _start_state(input_values, departure_date)
        arrival_dates = explicit_arrivals or [
            departure_date + timedelta(days=flight_days)
            for flight_days in (flight_day_options or [])
        ]
        for arrival_date in arrival_dates:
            flight_days = (arrival_date - departure_date).total_seconds() / DAY_SECONDS
            if flight_days <= 0:
                continue
            target_center_position, target_velocity = _body_state(
                target_body, _mission_epoch_days(arrival_date.date().isoformat()), catalog,
            )
            target_position = target_center_position
            corridor_selection = None
            if entry_corridor["enabled"]:
                corridor_selection = _select_entry_corridor_target(
                    entry_corridor,
                    burn_position=start_position,
                    reference_velocity=start_velocity,
                    planet_position=target_center_position,
                    sphere_of_influence_km=_entry_radius(target_body, catalog),
                    flight_seconds=flight_days * DAY_SECONDS,
                )
                target_position = corridor_selection["position"]
            try:
                branches = _lambert_candidates(start_position, target_position, flight_days * DAY_SECONDS)
            except ValueError:
                continue
            for branch_index, branch in enumerate(branches):
                departure_v_inf = _magnitude(tuple(
                    branch["departure"][index] - start_velocity[index] for index in range(3)
                ))
                arrival_v_inf = _magnitude(tuple(
                    branch["arrival"][index] - target_velocity[index] for index in range(3)
                ))
                total_delta_v = departure_v_inf + (
                    arrival_v_inf if target.get("arrivalMode") in {"orbit_insertion", "rendezvous"} else 0.0
                )
                candidate = {
                    "id": f"candidate-{uuid4().hex[:12]}",
                    "departureDate": departure_date.date().isoformat(),
                    "arrivalDate": arrival_date.date().isoformat(),
                    "flightDays": flight_days,
                    "startBodyId": start_body_id,
                    "targetBodyId": target_id,
                    "waypointIds": [],
                    "c3Km2S2": departure_v_inf**2,
                    "departureVInfinityKmS": departure_v_inf,
                    "arrivalVInfinityKmS": arrival_v_inf,
                    "requiredDeltaVKmS": total_delta_v,
                    "totalDeltaVKmS": total_delta_v,
                    "finalHeliocentricSpeedKmS": _magnitude(branch["arrival"]),
                    "branch": {key: item for key, item in branch.items() if key not in {"departure", "arrival"}},
                    "_departureVelocity": branch["departure"],
                    "_arrivalVelocity": branch["arrival"],
                    "_startPosition": start_position,
                    "_targetPosition": target_position,
                    "_targetCenterPosition": target_center_position,
                    "_corridorSelection": corridor_selection,
                    "warnings": [],
                }
                candidate["feasible"], candidate["warnings"] = _constraints(candidate, input_values["constraints"])
                candidate["score"] = calculate_candidate_score(
                    candidate, input_values["optimizationMode"], values.get("scoreWeights"),
                ) + (0.0 if candidate["feasible"] else 1_000.0)
                candidates.append(candidate)
    if not candidates:
        raise ValueError("Im gewählten Datumsfenster wurde keine Lambert-Lösung gefunden.")
    candidates.sort(key=lambda item: item["score"])
    best = candidates[0]
    trajectory, propagated_end, propagated_velocity = _propagate_lambert_segment(
        best["_startPosition"], best["_departureVelocity"], 0.0,
        best["flightDays"] * DAY_SECONDS,
        input_values["simulation"]["sampleTrajectoryPoints"],
    )
    endpoint_residual = _magnitude(tuple(
        propagated_end[index] - best["_targetPosition"][index] for index in range(3)
    ))
    corridor_selection = best.get("_corridorSelection")
    public_candidates = []
    for candidate in candidates[:250]:
        public_candidates.append({key: item for key, item in candidate.items() if not key.startswith("_")})
    best_public = public_candidates[0]
    last_velocity = tuple(trajectory[-1].get("velocityKmS") or propagated_velocity)
    return {
        "mode": "body-to-body",
        "input": input_values,
        "start": {
            "type": input_values["start"]["type"], "bodyId": best.get("startBodyId"),
            "date": best["departureDate"], "positionKm": list(best["_startPosition"]),
            "velocityKmS": list(_start_state(input_values, _date(best["departureDate"], "departureDate"))[1]),
        },
        "target": {
            "type": target["type"], "bodyId": target_id, "date": best["arrivalDate"],
            "positionKm": list(best["_targetCenterPosition"]),
            **({"entryPositionKm": list(best["_targetPosition"])} if corridor_selection else {}),
        },
        "bestCandidate": best_public,
        "candidates": public_candidates,
        "guide": {
            "mode": "start-target",
            "nodes": [
                {"id": "start", "kind": "start", "positionKm": list(best["_startPosition"]), "date": best["departureDate"]},
                {"id": "target", "kind": "body", "bodyId": target_id, "positionKm": list(best["_targetCenterPosition"]), "date": best["arrivalDate"]},
            ],
            "legs": [{"id": "start-to-target", "from": "start", "to": "target", "physicalSegments": ["lambert-transfer"]}],
        },
        "segments": [{"id": "lambert-transfer", "label": f"{best.get('startBodyId') or 'state'} → {target_id}", "startIndex": 0, "endIndex": len(trajectory) - 1}],
        "trajectory": trajectory,
        "summary": {
            "totalFlightDays": best["flightDays"],
            "totalDeltaVKmS": best["totalDeltaVKmS"],
            "requiredInjectionDeltaVKmS": best["departureVInfinityKmS"],
            "c3Km2S2": best["c3Km2S2"],
            "departureVInfinityKmS": best["departureVInfinityKmS"],
            "arrivalVInfinityKmS": best["arrivalVInfinityKmS"],
            "finalHeliocentricSpeedKmS": _magnitude(last_velocity),
            "targetReached": endpoint_residual <= max(100.0, _entry_radius(target_body, catalog)),
            "targetReachedDate": best["arrivalDate"],
            "targetReachedDistanceAU": _magnitude(best["_targetCenterPosition"]) / AU_KM,
            "endpointResidualKm": endpoint_residual,
            "feasible": best["feasible"],
            "entryCorridorTargeted": bool(corridor_selection),
            "entryInsideCorridor": True if corridor_selection else None,
            "model": "universal-variable Lambert transfer with heliocentric DOP853 propagation",
        },
        **({
            "entryCorridor": {
                **entry_corridor,
                "centerDirection": list(entry_corridor["centerDirection"]),
                "selectedDirection": list(corridor_selection["direction"]),
                "selectedHorizontalOffsetDeg": corridor_selection["horizontalOffsetDeg"],
                "selectedVerticalOffsetDeg": corridor_selection["verticalOffsetDeg"],
                "selectedRequiredInjectionDeltaVKmS": corridor_selection["requiredInjectionDeltaVKmS"],
                "evaluatedTargetCount": corridor_selection["evaluatedTargetCount"],
                "actualEntryDirection": list(corridor_selection["direction"]),
                "actualHorizontalOffsetDeg": corridor_selection["horizontalOffsetDeg"],
                "actualVerticalOffsetDeg": corridor_selection["verticalOffsetDeg"],
                "actualEntryPositionKm": list(best["_targetPosition"]),
                "entryInsideCorridor": True,
            }
        } if corridor_selection else {}),
        "warnings": list(best["warnings"]),
    }


def _legacy_route_result(input_values: dict, legacy: dict, mode: str) -> dict:
    trajectory = legacy.get("trajectory") or []
    sections = legacy.get("routeSections") or []
    summary = legacy.get("summary") or {}
    target_input = input_values["target"]
    final_position = trajectory[-1].get("positionKm", [0.0, 0.0, 0.0]) if trajectory else [0.0, 0.0, 0.0]
    final_velocity = trajectory[-1].get("velocityKmS", [0.0, 0.0, 0.0]) if trajectory else [0.0, 0.0, 0.0]
    total_flight_days = _number(legacy.get("totalFlightDays"), _number(trajectory[-1].get("elapsedDays") if trajectory else 0, 0))
    total_delta_v = _number(summary.get("totalTransitionDeltaVKmS"), _number(summary.get("requiredInjectionDeltaVKmS"), 0.0))
    feasible = bool(summary.get("feasibleWithConfiguredBurn", True))
    target_id = str(target_input.get("bodyId") or target_input.get("zoneId") or target_input.get("boundaryId") or "direction")
    nodes = [{"id": "start", "kind": "start", "positionKm": trajectory[0].get("positionKm") if trajectory else [0, 0, 0]}]
    for index, waypoint in enumerate(input_values["waypoints"]):
        nodes.append({"id": str(waypoint.get("id") or f"waypoint-{index + 1}"), "kind": waypoint.get("type"), "bodyId": waypoint.get("bodyId")})
    nodes.append({"id": "target", "kind": target_input["type"], "bodyId": target_input.get("bodyId"), "direction": legacy.get("targetDirection")})
    legs = [{
        "id": f"leg-{index + 1}", "from": nodes[index]["id"], "to": nodes[index + 1]["id"],
        "physicalSegments": [segment.get("id") for segment in (legacy.get("segments") or [])[index:index + 1]],
    } for index in range(len(nodes) - 1)]
    return {
        "mode": mode,
        "input": input_values,
        "start": {"type": input_values["start"]["type"], "bodyId": input_values["start"].get("bodyId"), "date": input_values["start"]["startDate"], "positionKm": trajectory[0].get("positionKm") if trajectory else [0, 0, 0], "velocityKmS": trajectory[0].get("velocityKmS", [0, 0, 0]) if trajectory else [0, 0, 0]},
        "target": {"type": target_input["type"], "bodyId": target_input.get("bodyId"), "direction": legacy.get("targetDirection"), "date": target_input.get("targetDate")},
        "bestCandidate": {"id": f"candidate-{uuid4().hex[:12]}", "departureDate": input_values["start"]["startDate"], "flightDays": total_flight_days, "startBodyId": input_values["start"].get("bodyId"), "targetBodyId": target_input.get("bodyId"), "waypointIds": [node["id"] for node in nodes[1:-1]], "requiredDeltaVKmS": total_delta_v, "totalDeltaVKmS": total_delta_v, "score": 0.0, "feasible": feasible, "warnings": legacy.get("warnings") or summary.get("warnings") or []},
        "candidates": [],
        "guide": {"mode": "multi-leg", "nodes": nodes, "legs": legs},
        "segments": legacy.get("segments") or [],
        "trajectory": trajectory,
        "summary": {"totalFlightDays": total_flight_days, "totalDeltaVKmS": total_delta_v, "requiredInjectionDeltaVKmS": _number(summary.get("requiredInjectionDeltaVKmS"), total_delta_v), "finalHeliocentricSpeedKmS": _magnitude(tuple(final_velocity)), "targetReached": feasible, "targetReachedDistanceAU": _magnitude(tuple(final_position)) / AU_KM, "feasible": feasible, "model": str(summary.get("model") or mode)},
        "warnings": legacy.get("warnings") or summary.get("warnings") or [],
        "legacyRoute": legacy,
    }


def calculate_multi_leg_transfer(values: dict) -> dict:
    input_values = _normalized_input(values)
    if any(
        isinstance(waypoint.get("aimpoint"), dict)
        and waypoint["aimpoint"].get("enabled") is True
        for waypoint in input_values["waypoints"]
    ):
        raise ValueError(
            "Ein aktiver Flyby-Aimpoint benötigt derzeit die gekoppelte "
            "Solar-Oberth/Flyby-Berechnung; er wird niemals nur visuell übernommen."
        )
    start_id = str(input_values["start"].get("bodyId") or "")
    if not start_id:
        raise ValueError("Multi-Leg-Routen benötigen einen Startkörper.")
    nodes: list[tuple[str, dict | None]] = [(start_id, None)]
    for waypoint in input_values["waypoints"]:
        waypoint_type = str(waypoint.get("type") or "")
        body_id = "sun" if waypoint_type == "solar_oberth" else str(waypoint.get("bodyId") or "")
        if not body_id:
            raise ValueError(f"Wegpunkt '{waypoint.get('id') or waypoint_type}' benötigt einen Körper.")
        nodes.append((body_id, waypoint))
    target = input_values["target"]
    if target["type"] not in {"body", "body_orbit", "flyby"}:
        raise ValueError("Diese Multi-Leg-Kombination benötigt derzeit ein Körper- oder Flyby-Ziel.")
    nodes.append((str(target.get("bodyId") or ""), None))
    sections = []
    for index in range(len(nodes) - 1):
        waypoint = nodes[index + 1][1]
        is_flyby_target = index == len(nodes) - 2 and target["type"] == "flyby"
        passage_mode = "partial-orbit" if is_flyby_target or waypoint and waypoint.get("type") in {"body_flyby", "solar_oberth"} else "direct"
        sections.append({
            "id": str((waypoint or {}).get("id") or f"trajectory-leg-{index + 1}"),
            "originId": nodes[index][0], "targetId": nodes[index + 1][0],
            "corridor": {"enabled": False},
            "passage": {"mode": passage_mode, "orbitAngleDeg": 45 if passage_mode != "direct" else 0, "orbitDirection": "prograde", "entryBehavior": "ballistic", "exitBehavior": "tangential-accelerate" if passage_mode != "direct" else "ballistic"},
            "deltaVPlusKmS": max(0.0, _number((waypoint or {}).get("burnDeltaVKmS"), _number(input_values["constraints"].get("maxTotalDeltaVKmS"), 30.0))),
        })
    legacy = simulate_route_sections({
        "routeSections": sections,
        "mission": {**dict(values.get("mission") or {}), "startDate": input_values["start"]["startDate"]},
        "integrateSpacecraft": bool(values.get("integrateSpacecraft", False)),
    })
    return _legacy_route_result(input_values, legacy, "multi-leg")


def calculate_flyby_target_route(values: dict) -> dict:
    input_values = _normalized_input(values)
    target = input_values["target"]
    solar_waypoint = next((item for item in input_values["waypoints"] if item.get("type") == "solar_oberth"), None)
    if solar_waypoint is not None:
        aimpoint = dict(target.get("aimpoint") or {})
        legacy = simulate_waypoint_route({
            "mission": {**dict(values.get("mission") or {}), "startDate": input_values["start"]["startDate"]},
            "waypointId": target.get("bodyId"),
            "encounterDay": _number(target.get("encounterDay"), _number(target.get("flybyDay"), 730.0)),
            "flybyAltitudeKm": _number(target.get("flybyAltitudeKm"), 100_000.0),
            "flybyMode": target.get("flybyMode") or "acceleration",
            "flybyAimpoint": aimpoint,
            "desiredSolarExitSpeedKmS": input_values["constraints"].get("desiredSolarExitSpeedKmS"),
        })
        return _legacy_route_result(input_values, legacy, "flyby")
    if isinstance(target.get("aimpoint"), dict) and target["aimpoint"].get("enabled") is True:
        raise ValueError(
            "Ein aktiver Flyby-Aimpoint benötigt derzeit einen Solar-Oberth-Wegpunkt, "
            "damit die vorhandene physikalische Hyperbelberechnung verwendet wird."
        )
    return calculate_multi_leg_transfer({**input_values, "waypoints": input_values["waypoints"], "target": {**target, "type": "flyby"}})


def _outbound_route(input_values: dict, radius_au: float, *, target_meta: dict, exit_radius_au: float | None = None) -> dict:
    departure = _date(input_values["start"]["startDate"], "start.startDate")
    position, body_velocity, start_body_id = _start_state(input_values, departure)
    radius = max(_magnitude(position), SUN_RADIUS_KM * 1.1)
    radial = _normalize(position)
    desired_exit = max(0.0, _number(input_values["constraints"].get("desiredSolarExitSpeedKmS"), 15.0))
    escape_speed = sqrt(2 * MU_SUN / radius)
    tangential_radial_component = sum(body_velocity[index] * radial[index] for index in range(3))
    required_radial_speed = max(0.0, sqrt(max(0.0, escape_speed**2 + desired_exit**2 - max(0.0, _magnitude(body_velocity)**2 - tangential_radial_component**2))))
    departure_velocity = tuple(body_velocity[index] + radial[index] * required_radial_speed for index in range(3))
    years = input_values["simulation"]["propagationYears"]
    trajectory, _, final_velocity = _propagate_lambert_segment(
        position, departure_velocity, 0.0, years * 365.25 * DAY_SECONDS,
        input_values["simulation"]["sampleTrajectoryPoints"],
    )
    def first_crossing(requested_radius_au: float) -> dict | None:
        for point in trajectory:
            distance_au = _magnitude(tuple(point["positionKm"])) / AU_KM
            if distance_au >= requested_radius_au:
                return {**point, "distanceAU": distance_au}
        return None
    entry = first_crossing(radius_au)
    exit_crossing = first_crossing(exit_radius_au) if exit_radius_au is not None else None
    target_reached = entry is not None
    total_delta_v = _magnitude(tuple(departure_velocity[index] - body_velocity[index] for index in range(3)))
    reached_date = (
        (departure + timedelta(days=float(entry["elapsedDays"]))).date().isoformat()
        if entry else None
    )
    warnings = [] if target_reached else [f"Zielradius {radius_au:g} AE wurde innerhalb von {years:g} Jahren nicht erreicht."]
    target = {**target_meta, "distanceAU": radius_au}
    result = {
        "mode": target_meta["type"], "input": input_values,
        "start": {"type": input_values["start"]["type"], "bodyId": start_body_id, "date": departure.date().isoformat(), "positionKm": list(position), "velocityKmS": list(body_velocity)},
        "target": target,
        "bestCandidate": {"id": f"candidate-{uuid4().hex[:12]}", "departureDate": departure.date().isoformat(), "flightDays": float(entry["elapsedDays"]) if entry else years * 365.25, "startBodyId": start_body_id, "requiredDeltaVKmS": total_delta_v, "totalDeltaVKmS": total_delta_v, "finalHeliocentricSpeedKmS": _magnitude(final_velocity), "score": 0.0, "feasible": target_reached, "warnings": warnings},
        "candidates": [],
        "guide": {"mode": "radial-boundary-crossing", "nodes": [{"id": "start", "kind": "start", "positionKm": list(position)}, {"id": "target", "kind": target_meta["type"], **target}], "legs": [{"id": "outbound", "from": "start", "to": "target", "physicalSegments": ["heliocentric-outbound"]}]},
        "segments": [{"id": "heliocentric-outbound", "label": f"{start_body_id or 'Zustandsvektor'} → {target_meta.get('name')}", "startIndex": 0, "endIndex": len(trajectory) - 1}],
        "trajectory": trajectory,
        "summary": {"totalFlightDays": float(entry["elapsedDays"]) if entry else years * 365.25, "totalDeltaVKmS": total_delta_v, "requiredInjectionDeltaVKmS": total_delta_v, "finalHeliocentricSpeedKmS": _magnitude(final_velocity), "targetReached": target_reached, "targetReachedDate": reached_date, "targetReachedDistanceAU": entry["distanceAU"] if entry else _magnitude(tuple(trajectory[-1]["positionKm"])) / AU_KM, "feasible": target_reached, "model": "heliocentric two-body DOP853 boundary-crossing propagation"},
        "warnings": warnings,
        "events": ([{"type": "ZONE_ENTRY" if target_meta["type"] == "zone" else "BOUNDARY_REACHED", "elapsedDays": entry["elapsedDays"], "distanceAU": entry["distanceAU"]}] if entry else []),
    }
    if target_meta["type"] == "zone":
        result.update({
            "zoneEntryDate": reached_date,
            "zoneExitDate": (departure + timedelta(days=float(exit_crossing["elapsedDays"]))).date().isoformat() if exit_crossing else None,
            "zoneEntryDistanceAU": entry["distanceAU"] if entry else None,
            "zoneExitDistanceAU": exit_crossing["distanceAU"] if exit_crossing else None,
        })
        if exit_crossing:
            result["events"].append({"type": "ZONE_EXIT", "elapsedDays": exit_crossing["elapsedDays"], "distanceAU": exit_crossing["distanceAU"]})
    return result


def calculate_zone_target_route(values: dict) -> dict:
    input_values = _normalized_input(values)
    zone_id = str(input_values["target"].get("zoneId") or "")
    if zone_id not in ZONE_DEFINITIONS:
        raise ValueError(f"Unbekannte Sonnensystem-Zone '{zone_id}'.")
    zone = ZONE_DEFINITIONS[zone_id]
    crossing_radius = zone["outerRadiusAU"] if input_values["target"].get("arrivalMode") == "crossing" and input_values["target"].get("crossingEdge") == "outer" else zone["innerRadiusAU"]
    return _outbound_route(input_values, crossing_radius, target_meta={"type": "zone", "zoneId": zone_id, "name": zone["name"], **zone}, exit_radius_au=zone["outerRadiusAU"])


def calculate_boundary_target_route(values: dict) -> dict:
    input_values = _normalized_input(values)
    boundary_id = str(input_values["target"].get("boundaryId") or "")
    if boundary_id == "custom":
        radius = _number(input_values["target"].get("distanceAU"), 0.0)
        if radius <= 0:
            raise ValueError("Eine eigene Grenze benötigt target.distanceAU > 0.")
        boundary = {"name": "Eigene Distanzgrenze", "radiusAU": radius}
    else:
        if boundary_id not in BOUNDARY_DEFINITIONS:
            raise ValueError(f"Unbekannte Sonnensystem-Grenze '{boundary_id}'.")
        boundary = BOUNDARY_DEFINITIONS[boundary_id]
    return _outbound_route(input_values, boundary["radiusAU"], target_meta={"type": "boundary", "boundaryId": boundary_id, **boundary})


def calculate_direction_target_route(values: dict) -> dict:
    input_values = _normalized_input(values)
    direction = _direction(input_values["target"])
    solar_oberth = any(item.get("type") == "solar_oberth" for item in input_values["waypoints"])
    body_waypoints = [item for item in input_values["waypoints"] if item.get("type") == "body_flyby"]
    if body_waypoints:
        last_flyby = body_waypoints[-1]
        preceding = input_values["waypoints"][:input_values["waypoints"].index(last_flyby)]
        flyby_input = {
            **input_values,
            "waypoints": preceding,
            "target": {
                "type": "flyby",
                "bodyId": last_flyby.get("bodyId"),
                "flybyAltitudeKm": last_flyby.get("flybyAltitudeKm", 100_000.0),
                "flybyMode": last_flyby.get("flybyMode", "acceleration"),
                "encounterDay": last_flyby.get("encounterDay", 730.0),
                "aimpoint": last_flyby.get("aimpoint") or {},
            },
        }
        base = calculate_flyby_target_route(flyby_input)
        legacy = base.get("legacyRoute") or {}
        trajectory = list(base.get("trajectory") or [])
        if not trajectory:
            raise ValueError("Der planetare Wegpunkt lieferte keinen ausgehenden Zustand.")
        last_point = trajectory[-1]
        start_position = tuple(last_point["positionKm"])
        legacy_summary = legacy.get("summary") if isinstance(legacy, dict) else {}
        outgoing = tuple(legacy.get("outgoingDirection") or direction) if isinstance(legacy, dict) else direction
        outbound_speed = max(
            1.0,
            _number((legacy_summary or {}).get("heliocentricSpeedAfterKmS"), 0.0),
            _magnitude(tuple(last_point.get("velocityKmS") or (0.0, 0.0, 0.0))),
        )
        current_velocity = tuple(component * outbound_speed for component in _normalize(outgoing))
        desired_velocity = tuple(component * outbound_speed for component in direction)
        correction_delta_v = _magnitude(tuple(
            desired_velocity[index] - current_velocity[index] for index in range(3)
        ))
        start_day = _number(last_point.get("elapsedDays"), base["summary"]["totalFlightDays"])
        outbound, _, final_velocity = _propagate_lambert_segment(
            start_position, desired_velocity, start_day,
            input_values["simulation"]["propagationYears"] * 365.25 * DAY_SECONDS,
            input_values["simulation"]["sampleTrajectoryPoints"],
        )
        segment_start = len(trajectory) - 1
        trajectory.extend(outbound[1:])
        alignment = calculate_vector_angle_deg(final_velocity, direction)
        tolerance = _number(input_values["constraints"].get("targetToleranceDeg"), 5.0)
        base.update({
            "mode": "multi-leg-direction",
            "input": input_values,
            "target": {"type": "direction", "direction": list(direction), "distanceAU": input_values["target"].get("distanceAU", 50)},
            "trajectory": trajectory,
            "segments": [
                *base.get("segments", []),
                {"id": "post-flyby-direction", "label": f"{last_flyby.get('bodyId')} → freie Zielrichtung", "startIndex": segment_start, "endIndex": len(trajectory) - 1},
            ],
        })
        base.pop("legacyRoute", None)
        flyby_node = base["guide"]["nodes"][-1]
        flyby_node.update({
            "id": str(last_flyby.get("id") or "planetary-flyby"),
            "kind": "body_flyby",
            "bodyId": last_flyby.get("bodyId"),
        })
        base["guide"]["legs"][-1]["to"] = flyby_node["id"]
        base["guide"]["nodes"].append({
            "id": "target", "kind": "direction", "direction": list(direction),
        })
        base["guide"]["legs"].append({
            "id": "post-flyby-direction-leg",
            "from": flyby_node["id"], "to": "target",
            "physicalSegments": ["post-flyby-direction"],
        })
        base["summary"].update({
            "totalFlightDays": trajectory[-1]["elapsedDays"],
            "totalDeltaVKmS": _number(base["summary"].get("totalDeltaVKmS"), 0.0) + correction_delta_v,
            "finalHeliocentricSpeedKmS": _magnitude(final_velocity),
            "targetAlignmentDeg": alignment,
            "targetReached": alignment <= tolerance,
            "feasible": bool(base["summary"].get("feasible")) and alignment <= tolerance,
            "model": f"{base['summary'].get('model')} + propagated post-flyby target-vector leg",
        })
        return base
    if solar_oberth and str(input_values["start"].get("bodyId") or "") == "earth":
        obliquity = 23.43928 * pi / 180
        equatorial = (
            direction[0],
            direction[1] * cos(obliquity) - direction[2] * sin(obliquity),
            direction[1] * sin(obliquity) + direction[2] * cos(obliquity),
        )
        legacy = simulate_direct_solar_route({
            "mission": {**dict(values.get("mission") or {}), "startDate": input_values["start"]["startDate"], "missionYears": input_values["simulation"]["propagationYears"]},
            "targetRightAscensionDeg": atan2(equatorial[1], equatorial[0]) * 180 / pi,
            "targetDeclinationDeg": asin(max(-1.0, min(1.0, equatorial[2]))) * 180 / pi,
        })
        result = _legacy_route_result(input_values, legacy, "direction")
        result["target"]["direction"] = list(direction)
        result["summary"]["targetReached"] = legacy["summary"]["finalTargetAlignmentDeg"] <= _number(input_values["constraints"].get("targetToleranceDeg"), 5.0)
        result["summary"]["targetAlignmentDeg"] = legacy["summary"]["finalTargetAlignmentDeg"]
        return result
    distance = max(1.0, _number(input_values["target"].get("distanceAU"), 50.0))
    outbound = _outbound_route(input_values, distance, target_meta={"type": "direction", "name": "Freie 3D-Zielrichtung"})
    outbound["target"]["direction"] = list(direction)
    position = tuple(outbound["start"]["positionKm"])
    body_velocity = tuple(outbound["start"]["velocityKmS"])
    desired_speed = max(sqrt(2 * MU_SUN / max(_magnitude(position), 1.0)) + 1.0, _number(input_values["constraints"].get("desiredSolarExitSpeedKmS"), 45.0))
    desired_velocity = tuple(component * desired_speed for component in direction)
    trajectory, _, final_velocity = _propagate_lambert_segment(position, desired_velocity, 0.0, input_values["simulation"]["propagationYears"] * 365.25 * DAY_SECONDS, input_values["simulation"]["sampleTrajectoryPoints"])
    alignment = calculate_vector_angle_deg(final_velocity, direction)
    outbound["trajectory"] = trajectory
    outbound["segments"][0]["endIndex"] = len(trajectory) - 1
    outbound["summary"].update({"totalDeltaVKmS": _magnitude(tuple(desired_velocity[index] - body_velocity[index] for index in range(3))), "requiredInjectionDeltaVKmS": _magnitude(tuple(desired_velocity[index] - body_velocity[index] for index in range(3))), "finalHeliocentricSpeedKmS": _magnitude(final_velocity), "targetAlignmentDeg": alignment, "targetReached": alignment <= _number(input_values["constraints"].get("targetToleranceDeg"), 5.0), "feasible": alignment <= _number(input_values["constraints"].get("targetToleranceDeg"), 5.0), "model": "target-vector injection + heliocentric DOP853 propagation"})
    return outbound


def calculate_vector_angle_deg(first: tuple, second: tuple) -> float:
    first_normalized, second_normalized = _normalize(first), _normalize(second)
    cosine = max(-1.0, min(1.0, sum(first_normalized[index] * second_normalized[index] for index in range(3))))
    return acos(cosine) * 180 / pi


def calculate_state_vector_target_route(values: dict) -> dict:
    input_values = _normalized_input(values)
    target = input_values["target"]
    target_date = _date(target.get("targetDate"), "target.targetDate")
    departure = _date(input_values["start"]["startDate"], "start.startDate")
    flight_days = (target_date - departure).total_seconds() / DAY_SECONDS
    if flight_days <= 0:
        raise ValueError("target.targetDate muss nach dem Startdatum liegen.")
    start_position, start_velocity, start_body_id = _start_state(input_values, departure)
    target_position = _vector(target.get("positionKm"), "target.positionKm")
    target_velocity = _vector(target.get("velocityKmS"), "target.velocityKmS") if target.get("velocityKmS") is not None else (0.0, 0.0, 0.0)
    branches = _lambert_candidates(start_position, target_position, flight_days * DAY_SECONDS)
    selected = min(branches, key=lambda branch: _magnitude(tuple(branch["departure"][index] - start_velocity[index] for index in range(3))))
    trajectory, propagated_end, final_velocity = _propagate_lambert_segment(start_position, selected["departure"], 0.0, flight_days * DAY_SECONDS, input_values["simulation"]["sampleTrajectoryPoints"])
    departure_delta_v = _magnitude(tuple(selected["departure"][index] - start_velocity[index] for index in range(3)))
    arrival_delta_v = _magnitude(tuple(selected["arrival"][index] - target_velocity[index] for index in range(3)))
    residual = _magnitude(tuple(propagated_end[index] - target_position[index] for index in range(3)))
    return {
        "mode": "state-vector", "input": input_values,
        "start": {"type": input_values["start"]["type"], "bodyId": start_body_id, "date": departure.date().isoformat(), "positionKm": list(start_position), "velocityKmS": list(start_velocity)},
        "target": {"type": "state_vector", "date": target_date.date().isoformat(), "positionKm": list(target_position), "velocityKmS": list(target_velocity)},
        "bestCandidate": {"id": f"candidate-{uuid4().hex[:12]}", "departureDate": departure.date().isoformat(), "arrivalDate": target_date.date().isoformat(), "flightDays": flight_days, "requiredDeltaVKmS": departure_delta_v + arrival_delta_v, "totalDeltaVKmS": departure_delta_v + arrival_delta_v, "score": 0.0, "feasible": residual < 100.0, "warnings": []},
        "candidates": [], "guide": {"mode": "state-vector", "nodes": [{"id": "start", "kind": "start", "positionKm": list(start_position)}, {"id": "target", "kind": "state_vector", "positionKm": list(target_position)}], "legs": [{"id": "state-transfer", "from": "start", "to": "target", "physicalSegments": ["state-vector-lambert"]}]},
        "segments": [{"id": "state-vector-lambert", "label": "Zustandsvektor-Transfer", "startIndex": 0, "endIndex": len(trajectory) - 1}], "trajectory": trajectory,
        "summary": {"totalFlightDays": flight_days, "totalDeltaVKmS": departure_delta_v + arrival_delta_v, "requiredInjectionDeltaVKmS": departure_delta_v, "arrivalVInfinityKmS": arrival_delta_v, "finalHeliocentricSpeedKmS": _magnitude(final_velocity), "targetReached": residual < 100.0, "targetReachedDate": target_date.date().isoformat(), "targetReachedDistanceAU": _magnitude(target_position) / AU_KM, "endpointResidualKm": residual, "feasible": residual < 100.0, "model": "state-vector Lambert transfer + DOP853 propagation"}, "warnings": [],
    }


def calculate_trajectory_plan(values: dict | None = None, include_mission_result: bool = False) -> dict:
    """Plan any supported target through one stable public entry point."""
    raw_values = dict(values or {})
    input_values = _normalized_input(raw_values)
    target_type = input_values["target"]["type"]
    waypoints = input_values["waypoints"]
    if target_type in {"body", "body_orbit"} and not waypoints:
        result = calculate_body_to_body_transfer(input_values)
    elif target_type in {"body", "body_orbit"} and waypoints:
        result = calculate_multi_leg_transfer(input_values)
    elif target_type == "flyby":
        result = calculate_flyby_target_route(input_values)
    elif target_type == "direction":
        result = calculate_direction_target_route(input_values)
    elif target_type == "zone":
        result = calculate_zone_target_route(input_values)
    elif target_type == "boundary":
        result = calculate_boundary_target_route(input_values)
    elif target_type == "state_vector":
        result = calculate_state_vector_target_route(input_values)
    else:
        raise ValueError("Unbekannter Zieltyp.")
    audit_payload = {
        "inputs": input_values,
        "constants": {"AU_KM": AU_KM, "MU_SUN_KM3_S2": MU_SUN, "zones": ZONE_DEFINITIONS, "boundaries": BOUNDARY_DEFINITIONS},
        "coordinateTransform": {"frame": "ECLIPJ2000", "units": {"position": "km", "velocity": "km/s", "time": "days"}},
        "segments": result.get("segments") or [],
        "continuity": {"stateChain": True, "trajectoryPointCount": len(result.get("trajectory") or [])},
        "validation": {"targetReached": result["summary"]["targetReached"], "feasible": result["summary"]["feasible"], "warnings": result.get("warnings") or []},
        "summary": result["summary"],
    }
    if input_values["simulation"]["includeAudit"]:
        result["audit"] = write_route_audit(audit_payload)
    if include_mission_result and result.get("legacyRoute", {}).get("mission"):
        result["mission"] = result["legacyRoute"]["mission"]
    return result
