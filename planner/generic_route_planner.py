"""Generic patched-conic baseline for freely connected route sections.

The specialised solar-Oberth planner remains available for its original
Sun-to-planet mission.  This module handles user-defined starts at the Sun,
any catalogued planet, and catalogued moons without silently replacing the
selected origin with Earth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import acos, atan2, cos, exp, log, pi, sin, sqrt
from pathlib import Path

from scipy.optimize import least_squares

from planner.route_planner import (
    G_KM3_KG_S2,
    _cross,
    _dot,
    _lambert_candidates,
    _parse_entry_corridor,
    _propagate_lambert_segment,
    _subtract,
)
from planner.interstellar_targets import (
    HYPOTHETICAL_ASYMPTOTE_DISTANCE_AU,
    INTERSTELLAR_ROUTE_TARGETS,
    interstellar_direction,
)
from solver.trajectory import (
    AU_KM,
    DAY_SECONDS,
    J2000,
    MU_SUN,
    PLANET_EPHEMERIDES,
    _add,
    _magnitude,
    _mission_epoch_days,
    _normalize,
    _planet_state_at,
    simulate_mission,
)
from visualization.view_3d_celestials import PLANET_DATA
from solver.ephemeris import body_gm, planet_state, EPHEMERIS, SPICE_TARGETS


SUN_RADIUS_KM = 696_340.0
SUN_MASS_KG = MU_SUN / G_KM3_KG_S2
MOON_CATALOG = Path(__file__).resolve().parents[1] / "web" / "public" / "moons.json"

# Physical radii are deliberately separate from the orbital-elements catalog.
# Unknown small moons remain routable as point targets with a conservative
# 10 km keep-out radius instead of being rejected by an ID allow-list.
KNOWN_MOON_GM_KM3_S2 = {
    "earth-moon": 4902.800118, "mars-phobos": .0007087, "mars-deimos": .0000962,
    "jupiter-io": 5959.916, "jupiter-europa": 3202.739, "jupiter-ganymede": 9887.834,
    "jupiter-callisto": 7179.289, "saturn-titan": 8978.1382,
    "saturn-enceladus": 7.21037, "uranus-titania": 226.9,
    "uranus-oberon": 205.3, "neptune-triton": 1427.598,
}
KNOWN_MOON_RADII_KM = {
    "earth-moon": 1_737.4,
    "mars-phobos": 11.267,
    "mars-deimos": 6.2,
    "jupiter-io": 1_821.6,
    "jupiter-europa": 1_560.8,
    "jupiter-ganymede": 2_634.1,
    "jupiter-callisto": 2_410.3,
    "saturn-titan": 2_574.7,
    "saturn-enceladus": 252.1,
    "uranus-titania": 788.9,
    "uranus-oberon": 761.4,
    "neptune-triton": 1_353.4,
}

PASSAGE_MODES = {"direct", "partial-orbit", "full-orbit"}
PASSAGE_DIRECTIONS = {"prograde", "retrograde"}
MAX_PARTIAL_ORBIT_ANGLE_DEG = 1080.0
BOUNDARY_BEHAVIORS = {
    "ballistic",
    "tangential-prograde",
    "tangential-retrograde",
    "tangential-accelerate",
    "radial",
}


def parse_route_passage(raw: object) -> dict:
    """Normalize the wizard's target-passage definition for every planner."""
    source = raw if isinstance(raw, dict) else {}
    mode = str(source.get("mode") or "direct")
    if mode not in PASSAGE_MODES:
        raise ValueError(f"Unbekannter Passagemodus '{mode}'.")

    direction = str(source.get("orbitDirection") or "prograde")
    if direction not in PASSAGE_DIRECTIONS:
        raise ValueError(f"Unbekannte Umlaufrichtung '{direction}'.")

    entry_behavior = str(source.get("entryBehavior") or "ballistic")
    exit_behavior = str(source.get("exitBehavior") or "ballistic")
    if entry_behavior not in BOUNDARY_BEHAVIORS:
        raise ValueError(f"Unbekanntes Eintrittsverhalten '{entry_behavior}'.")
    if exit_behavior not in BOUNDARY_BEHAVIORS:
        raise ValueError(f"Unbekanntes Austrittsverhalten '{exit_behavior}'.")

    requested_angle = float(source.get("orbitAngleDeg") or 0.0)
    orbit_angle = (
        0.0
        if mode == "direct"
        else 360.0
        if mode == "full-orbit"
        else max(1.0, min(MAX_PARTIAL_ORBIT_ANGLE_DEG, requested_angle or 45.0))
    )
    return {
        "mode": mode,
        "orbitAngleDeg": orbit_angle,
        "orbitDirection": direction,
        "entryBehavior": entry_behavior,
        "exitBehavior": exit_behavior,
    }


def _rotate_vector(vector: tuple, axis: tuple, angle_rad: float) -> tuple:
    axis = _normalize(axis)
    parallel = tuple(_dot(vector, axis) * component for component in axis)
    perpendicular = _subtract(vector, parallel)
    cross = _cross(axis, vector)
    return tuple(
        parallel[index]
        + perpendicular[index] * cos(angle_rad)
        + cross[index] * sin(angle_rad)
        for index in range(3)
    )


@dataclass(frozen=True)
class RouteBody:
    id: str
    name: str
    kind: str
    radius_km: float
    mass_kg: float
    parent_id: str | None = None
    moon_elements: dict | None = None


def _catalog() -> dict[str, RouteBody]:
    result = {
        "sun": RouteBody("sun", "Sonne", "sun", SUN_RADIUS_KM, SUN_MASS_KG),
    }
    for row in PLANET_DATA:
        result[row[0]] = RouteBody(
            id=row[0],
            name=row[1],
            kind="planet",
            radius_km=row[3] / 1_000,
            mass_kg=(body_gm(row[0]) or row[2]*G_KM3_KG_S2)/G_KM3_KG_S2,
            parent_id="sun",
        )
    try:
        moon_rows = json.loads(MOON_CATALOG.read_text(encoding="utf-8"))["moons"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Mondkatalog konnte nicht geladen werden: {error}") from error
    for row in moon_rows:
        if not row.get("semiMajorAxisKm") or not row.get("orbitalPeriodDays"):
            continue
        SPICE_TARGETS.setdefault(row["id"],(row["name"].upper(),))
        result[row["id"]] = RouteBody(
            id=row["id"],
            name=row["name"],
            kind="moon",
            radius_km=KNOWN_MOON_RADII_KM.get(row["id"], 10.0),
            mass_kg=(body_gm(row["id"]) or KNOWN_MOON_GM_KM3_S2.get(row["id"], 0.0)) / G_KM3_KG_S2,
            parent_id=row["parentId"],
            moon_elements=row,
        )
    return result


def _planet_records(planet_id: str) -> tuple[tuple, tuple]:
    ephemeris = next(row for row in PLANET_EPHEMERIDES if row[0] == planet_id)
    data = next(row for row in PLANET_DATA if row[0] == planet_id)
    return ephemeris, data


def _epoch_days(epoch: str | None) -> float:
    if not epoch:
        return 0.0
    value = epoch.strip()
    if value.endswith(".5") and len(value) == 12:
        parsed = datetime.strptime(value[:-2], "%Y-%m-%d").replace(
            hour=12, tzinfo=timezone.utc
        )
    elif value.endswith(".0") and len(value) == 12:
        parsed = datetime.strptime(value[:-2], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    else:
        from solver.orbital import utc
        parsed = utc(value)
    return (parsed - J2000).total_seconds() / DAY_SECONDS


def _moon_relative_state(body: RouteBody, absolute_days: float) -> tuple[tuple, tuple]:
    elements = body.moon_elements or {}
    semi_major = float(elements["semiMajorAxisKm"])
    eccentricity = float(elements.get("eccentricity") or 0.0)
    period_days = float(elements["orbitalPeriodDays"])
    mean_anomaly = (
        float(elements.get("meanAnomalyEpochDeg") or 0.0) * pi / 180
        + 2 * pi * (absolute_days - _epoch_days(elements.get("epoch"))) / period_days
    ) % (2 * pi)
    eccentric_anomaly = mean_anomaly
    for _ in range(12):
        eccentric_anomaly -= (
            eccentric_anomaly - eccentricity * sin(eccentric_anomaly) - mean_anomaly
        ) / max(1e-12, 1 - eccentricity * cos(eccentric_anomaly))
    x_orbit = semi_major * (cos(eccentric_anomaly) - eccentricity)
    y_orbit = semi_major * sqrt(max(0.0, 1 - eccentricity**2)) * sin(eccentric_anomaly)
    mean_motion = 2 * pi / (period_days * DAY_SECONDS)
    denominator = max(1e-12, 1 - eccentricity * cos(eccentric_anomaly))
    vx_orbit = -semi_major * mean_motion * sin(eccentric_anomaly) / denominator
    vy_orbit = (
        semi_major * mean_motion * sqrt(max(0.0, 1 - eccentricity**2))
        * cos(eccentric_anomaly) / denominator
    )
    argument = float(elements.get("argumentPeriapsisDeg") or 0.0) * pi / 180
    inclination = float(elements.get("inclinationDeg") or 0.0) * pi / 180
    node = float(elements.get("ascendingNodeDeg") or 0.0) * pi / 180

    def rotate(x_value: float, y_value: float) -> tuple:
        peri_x = x_value * cos(argument) - y_value * sin(argument)
        peri_y = x_value * sin(argument) + y_value * cos(argument)
        return (
            peri_x * cos(node) - peri_y * cos(inclination) * sin(node),
            peri_x * sin(node) + peri_y * cos(inclination) * cos(node),
            peri_y * sin(inclination),
        )

    return rotate(x_orbit, y_orbit), rotate(vx_orbit, vy_orbit)


def _body_state(
    body: RouteBody,
    absolute_days: float,
    catalog: dict[str, RouteBody],
) -> tuple[tuple, tuple]:
    if body.kind == "sun":
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    if body.kind == "planet":
        ephemeris, _ = _planet_records(body.id)
        return _planet_state_at(ephemeris, absolute_days)
    if body.id in SPICE_TARGETS and EPHEMERIS.ensure_loaded():
        try:
            resolved = planet_state(body.id, absolute_days * DAY_SECONDS)
            if resolved is not None:
                raw, _ = resolved
                return tuple(raw[:3]), tuple(raw[3:])
        except RuntimeError:
            if EPHEMERIS.strict:
                raise
    parent = catalog[body.parent_id]
    parent_position, parent_velocity = _body_state(parent, absolute_days, catalog)
    relative_position, relative_velocity = _moon_relative_state(body, absolute_days)
    return (
        _add(parent_position, relative_position),
        _add(parent_velocity, relative_velocity),
    )


def _local_central_body(
    origin: RouteBody,
    target: RouteBody,
    catalog: dict[str, RouteBody],
) -> RouteBody | None:
    if origin.kind == "planet" and target.parent_id == origin.id:
        return origin
    if target.kind == "planet" and origin.parent_id == target.id:
        return target
    if origin.kind == "moon" and target.kind == "moon":
        if origin.parent_id == target.parent_id:
            return catalog[origin.parent_id]
    return None


def _parking_radius(body: RouteBody) -> float:
    if body.kind == "sun":
        return body.radius_km * 1.12
    return body.radius_km + max(100.0, body.radius_km * 0.08)


def _entry_radius(body: RouteBody, catalog: dict[str, RouteBody]) -> float:
    if body.kind == "sun":
        return _parking_radius(body)
    if body.kind == "planet":
        ephemeris, _ = _planet_records(body.id)
        soi = ephemeris[2] * AU_KM * (body.mass_kg / SUN_MASS_KG) ** (2 / 5)
        return max(_parking_radius(body), soi)
    parent = catalog[body.parent_id]
    semi_major = float((body.moon_elements or {})["semiMajorAxisKm"])
    # A moon without a catalogued mass still receives a finite navigation
    # boundary.  This is not presented as a physical SOI.
    return max(_parking_radius(body), semi_major * (body.mass_kg / parent.mass_kg) ** .4) if body.mass_kg > 0 else max(_parking_radius(body), min(semi_major * .02, body.radius_km * 20))


def _transfer_seconds(
    start: tuple,
    end: tuple,
    gravitational_parameter: float,
    *,
    local: bool,
    reference_speed_km_s: float | None = None,
) -> float:
    start_radius = max(_magnitude(start), 1.0)
    end_radius = max(_magnitude(end), 1.0)
    hohmann = pi * sqrt(((start_radius + end_radius) / 2) ** 3 / gravitational_parameter)
    minimum = 0.2 * DAY_SECONDS if local else 20 * DAY_SECONDS
    maximum = 120 * DAY_SECONDS if local else 20 * 365.25 * DAY_SECONDS
    if (
        not local
        and reference_speed_km_s is not None
        and reference_speed_km_s > 0
    ):
        specific_energy = (
            reference_speed_km_s**2 / 2
            - gravitational_parameter / start_radius
        )
        if specific_energy > 0:
            # A post-Oberth or gravity-assist departure is hyperbolic.  A
            # Hohmann half-period then overestimates the flight time by years
            # and makes the following Lambert leg invent a huge braking burn.
            # The asymptotic speed supplies the appropriate first-order time
            # scale; Lambert still performs the actual endpoint solution.
            asymptotic_speed = sqrt(2 * specific_energy)
            chord_distance = _magnitude(_subtract(end, start))
            hyperbolic = chord_distance / max(asymptotic_speed, 1e-6)
            return max(minimum, min(maximum, hyperbolic))
    return max(minimum, min(maximum, hohmann))


def _candidate(
    start: tuple,
    end: tuple,
    duration_seconds: float,
    reference_velocity: tuple,
    gravitational_parameter: float,
    minimum_central_radius_km: float = 0.0,
) -> dict:
    candidates = _lambert_candidates(
        start, end, duration_seconds, gravitational_parameter
    )
    assessed = []
    for item in candidates:
        clearance = float("inf")
        if minimum_central_radius_km > 0:
            propagated, _, _ = _propagate_lambert_segment(
                start,
                item["departure"],
                0.0,
                duration_seconds,
                120,
                gravitational_parameter,
            )
            clearance = min(
                _magnitude(tuple(point["positionKm"])) for point in propagated
            )
        assessed.append((item, clearance))
    safe = [
        item for item, clearance in assessed
        if clearance >= minimum_central_radius_km - 1.0
    ]
    if minimum_central_radius_km > 0 and not safe:
        best_clearance = max(clearance for _, clearance in assessed)
        raise ValueError(
            "Kein kollisionsfreier Lambert-Zweig gefunden: "
            f"Mindestabstand {minimum_central_radius_km:.0f} km, "
            f"bester Abstand {best_clearance:.0f} km."
        )
    pool = safe or [item for item, _ in assessed]
    return min(
        pool,
        key=lambda item: _magnitude(_subtract(item["departure"], reference_velocity)),
    )


def _angle_deg(first: tuple, second: tuple) -> float:
    return acos(max(-1.0, min(1.0, _dot(_normalize(first), _normalize(second))))) * 180 / pi


def _passage_basis(entry_direction: tuple, incoming_relative_velocity: tuple) -> tuple[tuple, tuple]:
    tangential = _subtract(
        incoming_relative_velocity,
        tuple(_dot(incoming_relative_velocity, entry_direction) * component for component in entry_direction),
    )
    if _magnitude(tangential) < 1e-9:
        reference = (0.0, 0.0, 1.0) if abs(entry_direction[2]) < 0.9 else (0.0, 1.0, 0.0)
        tangential = _cross(reference, entry_direction)
    tangent = _normalize(tangential)
    normal = _normalize(_cross(entry_direction, tangent))
    return tangent, normal


def _targeted_interstellar_passage_angle_deg(
    *,
    passage: dict,
    target: RouteBody,
    interstellar_target_id: str,
    catalog: dict[str, RouteBody],
    epoch_days: float,
    entry_day: float,
    entry_radius: float,
    passage_speed: float,
    entry_direction: tuple,
    normal: tuple,
    direction_sign: float,
) -> tuple[float, dict]:
    """Choose the exit phase whose heliocentric velocity best faces the star.

    The editor may still create the two coarse legs independently.  This
    look-ahead pass couples them afterwards without inventing a velocity turn
    on the outbound interstellar leg.
    """
    desired_direction = interstellar_direction(interstellar_target_id)
    requested_angle = float(passage["orbitAngleDeg"])
    minimum_angle = 360.0 if passage["mode"] == "full-orbit" else requested_angle
    candidates: list[dict] = []
    # At solar-perihelion speeds even half a degree can imply several km/s of
    # artificial terminal correction.  Resolve the exit phase finely enough
    # that a genuinely target-coupled passage can remain passive.
    for step in range(7201):
        selected_angle = minimum_angle + step * 0.05
        duration_seconds = (
            selected_angle * pi / 180 * entry_radius / passage_speed
            if passage_speed > 0
            else 0.0
        )
        _, target_velocity = _body_state(
            target,
            epoch_days + entry_day + duration_seconds / DAY_SECONDS,
            catalog,
        )
        radial = _normalize(_rotate_vector(
            entry_direction,
            normal,
            direction_sign * selected_angle * pi / 180,
        ))
        tangent = _normalize(_cross(normal, radial))
        if direction_sign < 0:
            tangent = tuple(-component for component in tangent)
        relative_velocity = tuple(passage_speed * component for component in tangent)
        heliocentric_velocity = _add(target_velocity, relative_velocity)
        candidates.append({
            "angleDeg": selected_angle,
            "alignmentDeg": _angle_deg(heliocentric_velocity, desired_direction),
            "exitDirection": radial,
            "heliocentricVelocity": heliocentric_velocity,
            "targetVelocity": target_velocity,
        })
    selected = min(
        candidates,
        key=lambda item: (item["alignmentDeg"], item["angleDeg"]),
    )
    return selected["angleDeg"], {
        "method": "heliocentric next-target velocity alignment",
        "lookaheadTargetId": interstellar_target_id,
        "requestedAngleDeg": requested_angle,
        "selectedAngleDeg": selected["angleDeg"],
        "autoExtendedAngleDeg": max(0.0, selected["angleDeg"] - requested_angle),
        "desiredExitDirection": list(desired_direction),
        "desiredExitRadialDirection": list(selected["exitDirection"]),
        "predictedHeliocentricExitDirection": list(_normalize(
            selected["heliocentricVelocity"]
        )),
        "predictedAlignmentDeg": selected["alignmentDeg"],
        "lineOfSightClear": True,
        "bestApproximation": selected["alignmentDeg"] > 0.5,
        "requiresCurvedTransfer": False,
        "keepOutRadiusKm": entry_radius,
        "departureClearanceKm": entry_radius - target.radius_km,
    }


def _targeted_body_passage_angle_deg(
    *,
    passage: dict,
    target: RouteBody,
    lookahead_target: RouteBody,
    lookahead_entry_direction: tuple | None,
    lookahead_entry_radius: float,
    catalog: dict[str, RouteBody],
    epoch_days: float,
    entry_day: float,
    entry_radius: float,
    passage_speed: float,
    entry_direction: tuple,
    normal: tuple,
    direction_sign: float,
) -> tuple[float, dict]:
    """Match a passive passage exit to the next body's Lambert departure."""
    requested_angle = float(passage["orbitAngleDeg"])
    minimum_angle = 360.0 if passage["mode"] == "full-orbit" else requested_angle

    def assess(
        selected_angle: float,
        passage_normal: tuple = normal,
        forced_transfer_days: float | None = None,
    ) -> dict | None:
        passage_seconds = selected_angle * pi / 180 * entry_radius / passage_speed
        exit_day = entry_day + passage_seconds / DAY_SECONDS
        target_position, target_velocity = _body_state(
            target, epoch_days + exit_day, catalog
        )
        radial = _normalize(_rotate_vector(
            entry_direction,
            passage_normal,
            direction_sign * selected_angle * pi / 180,
        ))
        tangent = _normalize(_cross(passage_normal, radial))
        if direction_sign < 0:
            tangent = tuple(-component for component in tangent)
        exit_position = _add(
            target_position,
            tuple(entry_radius * component for component in radial),
        )
        exit_velocity = _add(
            target_velocity,
            tuple(passage_speed * component for component in tangent),
        )
        provisional_position, _ = _body_state(
            lookahead_target, epoch_days + exit_day, catalog
        )
        provisional_seconds = (
            max(0.05, forced_transfer_days) * DAY_SECONDS
            if forced_transfer_days is not None
            else _transfer_seconds(
                exit_position,
                provisional_position,
                MU_SUN,
                local=False,
                reference_speed_km_s=_magnitude(exit_velocity),
            )
        )
        provisional_position, _ = _body_state(
            lookahead_target,
            epoch_days + exit_day + provisional_seconds / DAY_SECONDS,
            catalog,
        )
        endpoint_direction = lookahead_entry_direction
        if endpoint_direction is None:
            endpoint_direction = _normalize(_subtract(
                exit_position, provisional_position
            ))
        provisional_endpoint = _add(
            provisional_position,
            tuple(
                lookahead_entry_radius * component
                for component in endpoint_direction
            ),
        )
        transfer_seconds = (
            provisional_seconds
            if forced_transfer_days is not None
            else _transfer_seconds(
                exit_position,
                provisional_endpoint,
                MU_SUN,
                local=False,
                reference_speed_km_s=_magnitude(exit_velocity),
            )
        )
        future_position, _ = _body_state(
            lookahead_target,
            epoch_days + exit_day + transfer_seconds / DAY_SECONDS,
            catalog,
        )
        endpoint = _add(
            future_position,
            tuple(
                lookahead_entry_radius * component
                for component in endpoint_direction
            ),
        )
        try:
            departures = _lambert_candidates(
                exit_position, endpoint, transfer_seconds, MU_SUN
            )
        except ValueError:
            return None
        selected_departure = min(
            departures,
            key=lambda item: _magnitude(_subtract(
                item["departure"], exit_velocity
            )),
        )
        mismatch = _magnitude(_subtract(
            selected_departure["departure"], exit_velocity
        ))
        return {
            "angleDeg": selected_angle,
            "mismatchKmS": mismatch,
            "transferDays": transfer_seconds / DAY_SECONDS,
            "exitDirection": radial,
            "exitVelocity": exit_velocity,
            "requiredDepartureVelocity": selected_departure["departure"],
            "futureTargetPosition": future_position,
            "targetVelocity": target_velocity,
            "passageNormal": passage_normal,
        }

    # First locate a useful phase basin cheaply. The requested passage angle
    # is a lower bound: one additional revolution remains available to phase
    # the passive exit for the following body.
    candidates = [
        item for item in (
            assess(minimum_angle + step * 5.0) for step in range(73)
        ) if item is not None
    ]
    if not candidates:
        raise ValueError("Keine planetare Lambert-Kopplung gefunden.")
    selected = min(candidates, key=lambda item: item["mismatchKmS"])

    # Phase, passage plane and encounter time are one coupled targeting
    # problem. Optimising them one after another leaves a false correction
    # burn because every later variable moves the optimum of the earlier one.
    # Solve the three velocity residuals together instead. Logarithmic time
    # keeps short and multi-year transfers numerically well conditioned.
    seed_normal = selected["passageNormal"]
    lower_days = max(10.0, selected["transferDays"] * 0.15)
    upper_days = min(20 * 365.25, max(365.25, selected["transferDays"] * 8.0))
    evaluated: list[dict] = []

    def joint_residual(parameters) -> tuple:
        angle_deg, plane_offset, log_transfer_days = (
            float(value) for value in parameters
        )
        candidate_normal = _normalize(_rotate_vector(
            seed_normal, entry_direction, plane_offset,
        ))
        candidate = assess(
            angle_deg, candidate_normal, exp(log_transfer_days)
        )
        if candidate is None:
            return (1_000.0, 1_000.0, 1_000.0)
        evaluated.append(candidate)
        return _subtract(
            candidate["requiredDepartureVelocity"], candidate["exitVelocity"]
        )

    joint = least_squares(
        joint_residual,
        (
            selected["angleDeg"],
            0.0,
            log(selected["transferDays"]),
        ),
        bounds=(
            (minimum_angle, -pi, log(lower_days)),
            (minimum_angle + 360.0, pi, log(upper_days)),
        ),
        x_scale=(90.0, 1.0, 1.0),
        max_nfev=80,
        ftol=1e-9,
        xtol=1e-9,
        gtol=1e-9,
    )
    joint_residual(joint.x)
    if evaluated:
        selected = min(
            [selected, *evaluated], key=lambda item: item["mismatchKmS"]
        )
    plane_coupling_iterations = [{
        "angleDeg": selected["angleDeg"],
        "mismatchKmS": selected["mismatchKmS"],
        "normal": list(selected["passageNormal"]),
        "transferDays": selected["transferDays"],
        "solverEvaluations": int(joint.nfev),
    }]
    desired_direction = _normalize(_subtract(
        selected["futureTargetPosition"],
        tuple(0.0 for _ in range(3)),
    ))
    return selected["angleDeg"], {
        "method": "passive exit to next-body Lambert velocity coupling",
        "lookaheadTargetId": lookahead_target.id,
        "requestedAngleDeg": requested_angle,
        "selectedAngleDeg": selected["angleDeg"],
        "autoExtendedAngleDeg": max(0.0, selected["angleDeg"] - requested_angle),
        "transferPreviewDays": selected["transferDays"],
        "desiredExitDirection": list(desired_direction),
        "desiredExitRadialDirection": list(selected["exitDirection"]),
        "predictedHeliocentricExitDirection": list(_normalize(
            selected["exitVelocity"]
        )),
        "predictedTransitionDeltaVKmS": selected["mismatchKmS"],
        "requiredDepartureVelocityKmS": list(
            selected["requiredDepartureVelocity"]
        ),
        "optimizedPassageNormal": list(selected["passageNormal"]),
        "planeCouplingIterations": plane_coupling_iterations,
        "lineOfSightClear": True,
        "bestApproximation": selected["mismatchKmS"] > 0.5,
        "requiresCurvedTransfer": True,
        "keepOutRadiusKm": entry_radius,
        "departureClearanceKm": entry_radius - target.radius_km,
    }


def _targeted_passage_angle_deg(
    *,
    passage: dict,
    target: RouteBody,
    lookahead_target: RouteBody | None,
    lookahead_entry_direction: tuple | None,
    lookahead_entry_radius: float,
    lookahead_interstellar_target_id: str | None,
    catalog: dict[str, RouteBody],
    epoch_days: float,
    entry_day: float,
    entry_radius: float,
    passage_speed: float,
    entry_direction: tuple,
    normal: tuple,
    direction_sign: float,
    optimize_transfer_velocity: bool,
) -> tuple[float, dict | None]:
    if passage["mode"] == "direct":
        return passage["orbitAngleDeg"], None

    if lookahead_interstellar_target_id is not None:
        return _targeted_interstellar_passage_angle_deg(
            passage=passage,
            target=target,
            interstellar_target_id=lookahead_interstellar_target_id,
            catalog=catalog,
            epoch_days=epoch_days,
            entry_day=entry_day,
            entry_radius=entry_radius,
            passage_speed=passage_speed,
            entry_direction=entry_direction,
            normal=normal,
            direction_sign=direction_sign,
        )

    if lookahead_target is None:
        return passage["orbitAngleDeg"], None

    if (
        optimize_transfer_velocity
        and _local_central_body(target, lookahead_target, catalog) is None
    ):
        return _targeted_body_passage_angle_deg(
            passage=passage,
            target=target,
            lookahead_target=lookahead_target,
            lookahead_entry_direction=lookahead_entry_direction,
            lookahead_entry_radius=lookahead_entry_radius,
            catalog=catalog,
            epoch_days=epoch_days,
            entry_day=entry_day,
            entry_radius=entry_radius,
            passage_speed=passage_speed,
            entry_direction=entry_direction,
            normal=normal,
            direction_sign=direction_sign,
        )

    requested_angle = float(passage["orbitAngleDeg"])
    requested_duration_days = (
        abs(requested_angle) * pi / 180 * entry_radius / passage_speed / DAY_SECONDS
        if passage_speed > 0
        else 0.0
    )
    target_position, target_velocity = _body_state(
        target,
        epoch_days + entry_day + requested_duration_days,
        catalog,
    )
    lookahead_position, _ = _body_state(
        lookahead_target,
        epoch_days + entry_day + requested_duration_days,
        catalog,
    )
    requested_radial = _normalize(_rotate_vector(
        entry_direction,
        normal,
        direction_sign * requested_angle * pi / 180,
    ))
    requested_tangent = _normalize(_cross(normal, requested_radial))
    if direction_sign < 0:
        requested_tangent = tuple(-component for component in requested_tangent)
    preview_departure_position = _add(
        target_position,
        tuple(entry_radius * component for component in requested_radial),
    )
    preview_departure_velocity = _add(
        target_velocity,
        tuple(passage_speed * component for component in requested_tangent),
    )
    transfer_seconds = _transfer_seconds(
        preview_departure_position,
        lookahead_position,
        MU_SUN,
        local=False,
        reference_speed_km_s=_magnitude(preview_departure_velocity),
    )
    future_day = entry_day + requested_duration_days + transfer_seconds / DAY_SECONDS
    target_position, _ = _body_state(target, epoch_days + future_day, catalog)
    lookahead_position, _ = _body_state(
        lookahead_target,
        epoch_days + future_day,
        catalog,
    )
    relative_target = _subtract(lookahead_position, target_position)
    projected_target = _subtract(
        relative_target,
        tuple(_dot(relative_target, normal) * component for component in normal),
    )
    projected_distance = _magnitude(projected_target)
    if projected_distance < 1e-6:
        return requested_angle, {
            "method": "future target projection degenerate",
            "lookaheadTargetId": lookahead_target.id,
            "requestedAngleDeg": requested_angle,
            "selectedAngleDeg": requested_angle,
            "autoExtendedAngleDeg": 0.0,
            "lineOfSightClear": False,
            "bestApproximation": True,
            "futureTargetDistanceKm": projected_distance,
            "keepOutRadiusKm": entry_radius,
        }
    projected_direction = _normalize(projected_target)
    has_external_tangency = projected_distance > entry_radius + 1e-6
    target_mu = target.mass_kg * G_KM3_KG_S2
    conic_eccentricity = (
        entry_radius * passage_speed**2 / target_mu - 1.0
        if target_mu > 0
        else 0.0
    )
    uses_hyperbolic_conic = (
        target.kind == "sun"
        and conic_eccentricity > 1.0
        and has_external_tangency
    )
    if uses_hyperbolic_conic:
        # At solar perihelion the departure velocity is tangential, but the
        # Sun keeps bending the outbound hyperbola long after the local arc.
        # Use the conic true anomaly at the future target radius instead of a
        # straight 90-degree tangent, otherwise Lambert has to invent that
        # entire passive turn as a powered plane change.
        semi_latus_rectum = entry_radius * (1.0 + conic_eccentricity)
        conic_cosine = (
            semi_latus_rectum / projected_distance - 1.0
        ) / conic_eccentricity
        tangency_offset = acos(max(-1.0, min(1.0, conic_cosine)))
    else:
        tangency_offset = (
            acos(max(-1.0, min(1.0, entry_radius / projected_distance)))
            if has_external_tangency
            else pi / 2
        )
    desired_exit_radial = _normalize(_rotate_vector(
        projected_direction,
        normal,
        -direction_sign * tangency_offset,
    ))
    cross = _cross(entry_direction, desired_exit_radial)
    signed_angle = (
        atan2(_dot(normal, cross), _dot(entry_direction, desired_exit_radial))
        * 180
        / pi
    )
    directed_angle = signed_angle if direction_sign > 0 else -signed_angle
    directed_angle = directed_angle % 360.0
    minimum_angle = 360.0 if passage["mode"] == "full-orbit" else requested_angle
    selected_angle = directed_angle
    while selected_angle + 1e-6 < minimum_angle:
        selected_angle += 360.0
    departure_clearance = entry_radius - target.radius_km
    return selected_angle, {
        "method": (
            "future target hyperbolic conic with minimum passage"
            if uses_hyperbolic_conic
            else "future target tangency with minimum passage"
            if has_external_tangency
            else "best tangential departure for curved internal transfer"
        ),
        "lookaheadTargetId": lookahead_target.id,
        "requestedAngleDeg": requested_angle,
        "selectedAngleDeg": selected_angle,
        "autoExtendedAngleDeg": max(0.0, selected_angle - requested_angle),
        "transferPreviewDays": transfer_seconds / DAY_SECONDS,
        "departureConicEccentricity": conic_eccentricity,
        "departureConicTrueAnomalyDeg": tangency_offset * 180 / pi,
        "usesHyperbolicDepartureConic": uses_hyperbolic_conic,
        "desiredExitDirection": list(projected_direction),
        "desiredExitRadialDirection": list(desired_exit_radial),
        "lineOfSightClear": has_external_tangency,
        "bestApproximation": not has_external_tangency,
        "requiresCurvedTransfer": not has_external_tangency,
        "futureTargetDistanceKm": projected_distance,
        "keepOutRadiusKm": entry_radius,
        "departureClearanceKm": departure_clearance,
        "straightLineClearanceDeficitKm": max(0.0, entry_radius - projected_distance),
    }


def _local_passage(
    *,
    section: dict,
    target: RouteBody,
    lookahead_target: RouteBody | None,
    lookahead_entry_direction: tuple | None,
    lookahead_entry_radius: float,
    lookahead_interstellar_target_id: str | None,
    catalog: dict[str, RouteBody],
    epoch_days: float,
    entry_day: float,
    entry_direction: tuple,
    entry_radius: float,
    entry_position: tuple,
    arrival_velocity: tuple,
    configured_oberth_delta_v_km_s: float = 0.0,
    geometry_only: bool = False,
) -> dict:
    passage = section["passage"]
    if passage["mode"] == "direct":
        return {
            "trajectory": [],
            "exitDay": entry_day,
            "exitPosition": entry_position,
            "exitVelocity": arrival_velocity,
            "exitDirection": entry_direction,
            "exitAngleDeg": 0.0,
            "passageDurationDays": 0.0,
            "passageDeltaVKmS": 0.0,
            "minimumRadiusKm": entry_radius,
            "periapsisOffsetIndex": 0,
            "exitAngleSelection": None,
        }

    target_position, target_velocity = _body_state(target, epoch_days + entry_day, catalog)
    incoming_relative_velocity = _subtract(arrival_velocity, target_velocity)
    incoming_speed = max(_magnitude(incoming_relative_velocity), 1e-6)
    _, normal = _passage_basis(entry_direction, incoming_relative_velocity)
    desired_lookahead_direction = (
        interstellar_direction(lookahead_interstellar_target_id)
        if lookahead_interstellar_target_id is not None
        else None
    )
    if desired_lookahead_direction is None and lookahead_target is not None:
        lookahead_position, _ = _body_state(
            lookahead_target,
            epoch_days + entry_day,
            catalog,
        )
        relative_lookahead = _subtract(lookahead_position, target_position)
        if _magnitude(relative_lookahead) > 1e-9:
            desired_lookahead_direction = _normalize(relative_lookahead)
    target_coupled_plane = False
    if desired_lookahead_direction is not None:
        coupled_normal = _cross(entry_direction, desired_lookahead_direction)
        if _magnitude(coupled_normal) > 1e-9:
            # The selected 3D corridor defines one vector in the passage
            # plane; the following stellar direction defines the other.  The
            # same target-coupled construction is valid at the Sun, planets,
            # and moons instead of trapping planetary assists in the ecliptic.
            normal = _normalize(coupled_normal)
            target_coupled_plane = True
    direction_sign = 1.0 if passage["orbitDirection"] == "prograde" else -1.0
    target_mu = target.mass_kg * G_KM3_KG_S2
    circular_speed = sqrt(target_mu / entry_radius) if target_mu > 0 else incoming_speed
    # During route geometry the ideal arc must not be distorted by the
    # configured propulsion budget.  The required impulse is still measured,
    # but feasibility is evaluated only after every requested section reaches
    # its target in one continuous chain.
    passage_speed = max(0.01, max(incoming_speed, circular_speed)) if geometry_only else max(
        0.01,
        min(
            max(incoming_speed, circular_speed),
            incoming_speed + section["deltaVPlusKmS"],
        ),
    )
    applied_oberth_delta_v = (
        max(0.0, configured_oberth_delta_v_km_s)
        if target.kind == "sun"
        else 0.0
    )
    outbound_passage_speed = passage_speed + applied_oberth_delta_v
    selected_angle_deg, exit_angle_selection = _targeted_passage_angle_deg(
        passage=passage,
        target=target,
        lookahead_target=lookahead_target,
        lookahead_entry_direction=lookahead_entry_direction,
        lookahead_entry_radius=lookahead_entry_radius,
        lookahead_interstellar_target_id=lookahead_interstellar_target_id,
        catalog=catalog,
        epoch_days=epoch_days,
        entry_day=entry_day,
        entry_radius=entry_radius,
        passage_speed=outbound_passage_speed,
        entry_direction=entry_direction,
        normal=normal,
        direction_sign=direction_sign,
        optimize_transfer_velocity=not geometry_only,
    )
    if (
        exit_angle_selection is not None
        and exit_angle_selection.get("optimizedPassageNormal")
    ):
        normal = _normalize(tuple(
            exit_angle_selection["optimizedPassageNormal"]
        ))
        target_coupled_plane = True
    orbit_angle_rad = direction_sign * selected_angle_deg * pi / 180
    entry_tangent = _normalize(_cross(normal, entry_direction))
    if direction_sign < 0:
        entry_tangent = tuple(-component for component in entry_tangent)
    desired_entry_relative_velocity = tuple(passage_speed * component for component in entry_tangent)
    entry_delta_v = (
        0.0
        if passage["entryBehavior"] == "ballistic"
        else _magnitude(_subtract(
            desired_entry_relative_velocity,
            incoming_relative_velocity,
        ))
    )

    arc_length = abs(orbit_angle_rad) * entry_radius
    duration_seconds = arc_length / passage_speed if passage_speed > 0 else 0.0
    sample_count = max(16, min(540, int(abs(selected_angle_deg) // 2) + 32))
    points = []
    minimum_radius = float("inf")
    periapsis_index = 0
    for sample_index in range(sample_count + 1):
        fraction = sample_index / sample_count
        angle = orbit_angle_rad * fraction
        elapsed_seconds = duration_seconds * fraction
        day = entry_day + elapsed_seconds / DAY_SECONDS
        current_target_position, current_target_velocity = _body_state(
            target,
            epoch_days + day,
            catalog,
        )
        radial = _normalize(_rotate_vector(entry_direction, normal, angle))
        tangent = _normalize(_cross(normal, radial))
        if direction_sign < 0:
            tangent = tuple(-component for component in tangent)
        relative_position = tuple(entry_radius * component for component in radial)
        point_speed = passage_speed + (
            applied_oberth_delta_v if fraction >= 0.5 else 0.0
        )
        relative_velocity = tuple(point_speed * component for component in tangent)
        absolute_position = _add(current_target_position, relative_position)
        absolute_velocity = _add(current_target_velocity, relative_velocity)
        radius = _magnitude(relative_position)
        if radius < minimum_radius:
            minimum_radius = radius
            periapsis_index = sample_index
        points.append({
            "elapsedDays": day,
            "positionKm": list(absolute_position),
            "velocityKmS": list(absolute_velocity),
            "waypointPositionKm": list(current_target_position),
            "waypointRelativePositionKm": list(relative_position),
        })

    exit_direction = _normalize(_rotate_vector(entry_direction, normal, orbit_angle_rad))
    exit_velocity = tuple(points[-1]["velocityKmS"])
    return {
        "trajectory": points,
        "exitDay": points[-1]["elapsedDays"],
        "exitPosition": tuple(points[-1]["positionKm"]),
        "exitVelocity": exit_velocity,
        "exitDirection": exit_direction,
        "exitAngleDeg": selected_angle_deg * direction_sign,
        "exitAngleSelection": exit_angle_selection,
        "lookaheadAlignmentDeg": (
            _angle_deg(exit_velocity, desired_lookahead_direction)
            if desired_lookahead_direction is not None
            else 0.0
        ),
        "courseChangeDeg": _angle_deg(arrival_velocity, exit_velocity),
        "heliocentricSpeedGainKmS": (
            _magnitude(exit_velocity) - _magnitude(arrival_velocity)
        ),
        "passageDurationDays": duration_seconds / DAY_SECONDS,
        "passageDeltaVKmS": entry_delta_v,
        "appliedOberthDeltaVKmS": applied_oberth_delta_v,
        "targetCoupledPlane": target_coupled_plane,
        "minimumRadiusKm": minimum_radius,
        "periapsisOffsetIndex": periapsis_index,
    }


def _legacy_schematic_route_sections(values: dict | None) -> dict:
    values = values or {}
    raw_sections = values.get("routeSections")
    geometry_only = str(values.get("calculationStage") or "") == "geometry"
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("Mindestens ein 2D-Routenabschnitt ist erforderlich.")
    catalog = _catalog()
    parsed: list[dict] = []
    previous_target = None
    for index, raw in enumerate(raw_sections):
        if not isinstance(raw, dict):
            raise ValueError(f"Routenabschnitt {index + 1} ist ungültig.")
        origin_id = str(raw.get("originId") or "")
        target_id = str(raw.get("targetId") or "")
        stellar_record = INTERSTELLAR_ROUTE_TARGETS.get(target_id)
        if origin_id not in catalog:
            raise ValueError(f"Ursprung '{origin_id}' besitzt keine lokale Ephemeride.")
        if stellar_record is not None and index != len(raw_sections) - 1:
            raise ValueError(
                f"Interstellares Richtungsziel '{target_id}' darf nur der letzte "
                "Routenabschnitt sein."
            )
        if target_id not in catalog and stellar_record is None:
            raise ValueError(f"Ziel '{target_id}' besitzt keine lokale Ephemeride.")
        if origin_id == target_id:
            raise ValueError(f"Abschnitt {index + 1} verbindet ein Objekt mit sich selbst.")
        if previous_target is not None and origin_id != previous_target:
            raise ValueError(
                f"Abschnitt {index + 1} beginnt bei '{origin_id}', erwartet wird "
                f"der Endpunkt '{previous_target}' des vorherigen Abschnitts."
            )
        raw_corridor = dict(raw.get("corridor") or {})
        # The schematic editor can only assess a straight approach.  Curved
        # transfer propagation below is authoritative, so cached UI metadata
        # must not veto a newly calculated body-to-body route.
        raw_corridor["blocked"] = False
        raw_corridor.pop("blockReasons", None)
        parsed.append({
            "id": str(raw.get("id") or f"route-section-{index + 1}"),
            "origin": catalog[origin_id],
            "target": catalog.get(target_id),
            "interstellarTargetId": target_id if stellar_record is not None else None,
            "corridor": _parse_entry_corridor(raw_corridor),
            "passage": parse_route_passage(raw.get("passage")),
            "deltaVPlusKmS": max(0.0, float(raw.get("deltaVPlusKmS", 0.0))),
        })
        previous_target = target_id

    mission_values = dict(values.get("mission") or {})
    start_date = str(mission_values.get("startDate") or datetime.now().date().isoformat())
    propulsion_modules = mission_values.get("propulsionModules")
    solar_oberth_module = next(
        (
            module for module in propulsion_modules
            if isinstance(module, dict) and module.get("type") == "solar_oberth"
        ),
        None,
    ) if isinstance(propulsion_modules, list) else None
    solar_oberth_enabled = (
        bool(mission_values.get("carrierEnabled", True))
        and bool(mission_values.get("kickStageEnabled", True))
        and (
            solar_oberth_module is None
            or bool(solar_oberth_module.get("enabled", True))
        )
    )
    configured_oberth_delta_v = (
        max(0.0, float(mission_values.get("oberthDeltaVKmS", 8.0)))
        if solar_oberth_enabled
        else 0.0
    )
    spacecraft_integration = None
    if bool(values.get("integrateSpacecraft")):
        # Route discovery deliberately stays independent of a particular
        # spacecraft.  The interactive 3D hand-off, however, must validate the
        # selected geometry with the actual masses, stages and propulsion
        # modules.  The mission model applies the rocket equation and may cap
        # the requested Oberth burn when propellant or configuration is
        # insufficient.  Feed that achieved value back into the same route
        # instead of silently constructing a different unconstrained route.
        spacecraft_mission = simulate_mission({
            **mission_values,
            "missionYears": min(1.0, max(1.0, float(mission_values.get("missionYears", 1.0)))),
        })
        configured_oberth_delta_v = min(
            configured_oberth_delta_v,
            spacecraft_mission.summary.achieved_burn_delta_v_km_s,
        )
        enabled_modules = [
            report for report in spacecraft_mission.summary.propulsion_report
            if report.get("enabled")
        ]
        config = spacecraft_mission.config
        spacecraft_integration = {
            "validated": True,
            "routeGeometryPreserved": True,
            "wetMassKg": (
                config.payload_mass_kg
                + config.carrier_mass_kg
                + config.heatshield_mass_kg
                + config.propellant_mass_kg
            ),
            "payloadMassKg": config.payload_mass_kg,
            "requestedOberthDeltaVKmS": config.oberth_delta_v_km_s,
            "achievedOberthDeltaVKmS": spacecraft_mission.summary.achieved_burn_delta_v_km_s,
            "propellantUsedKg": spacecraft_mission.summary.propellant_used_kg,
            "enabledPropulsionModules": [report.get("name") for report in enabled_modules],
            "warnings": list(spacecraft_mission.summary.warnings),
        }
    sundiver_transfer_provided = (
        len(parsed) > 0
        and parsed[0]["origin"].id == "earth"
        and parsed[0]["target"] is not None
        and parsed[0]["target"].id == "sun"
        and solar_oberth_enabled
    )
    epoch_days = _mission_epoch_days(start_date)
    start_day = 0.0
    first_origin = parsed[0]["origin"]
    first_target = parsed[0]["target"]
    origin_position, origin_velocity = _body_state(first_origin, epoch_days, catalog)
    first_stellar_id = parsed[0]["interstellarTargetId"]
    if first_stellar_id is not None:
        initial_direction = interstellar_direction(first_stellar_id)
    else:
        target_position, _ = _body_state(first_target, epoch_days, catalog)
        initial_direction = _normalize(_subtract(target_position, origin_position))
        if first_origin.kind == "sun":
            initial_direction = _normalize(target_position)
    start_position = _add(
        origin_position,
        tuple(component * _parking_radius(first_origin) for component in initial_direction),
    )
    start_velocity = origin_velocity
    trajectory = [{
        "elapsedDays": 0.0,
        "positionKm": list(start_position),
        "velocityKmS": list(start_velocity),
    }]
    segments = []
    calculated = []
    total_delta_v = 0.0
    hypothetical_asymptote_direction = None

    for index, section in enumerate(parsed):
        origin = section["origin"]
        target = section["target"]
        corridor = section["corridor"]
        stellar_target_id = section["interstellarTargetId"]
        if stellar_target_id is not None:
            stellar_record = INTERSTELLAR_ROUTE_TARGETS[stellar_target_id]
            direction = interstellar_direction(stellar_target_id)
            outbound_speed = max(_magnitude(start_velocity), 1e-9)
            desired_velocity = tuple(
                outbound_speed * component for component in direction
            )
            transition_delta_v = _magnitude(
                _subtract(desired_velocity, start_velocity)
            )
            alignment_deg = _angle_deg(start_velocity, direction)
            transition_applied = (
                transition_delta_v <= section["deltaVPlusKmS"] + 1e-9
            )
            asymptote_velocity = (
                desired_velocity if transition_applied else start_velocity
            )
            segment_start_index = len(trajectory) - 1
            visualization_length_km = HYPOTHETICAL_ASYMPTOTE_DISTANCE_AU * AU_KM
            endpoint = _add(
                start_position,
                tuple(component * visualization_length_km for component in direction),
            )
            trajectory.append({
                "elapsedDays": start_day,
                "positionKm": list(endpoint),
                "velocityKmS": list(asymptote_velocity),
                "phase": "HYPOTHETICAL_INTERSTELLAR_ASYMPTOTE",
            })
            endpoint_index = len(trajectory) - 1
            calculated.append({
                "id": section["id"],
                "originId": origin.id,
                "targetId": stellar_target_id,
                "targetName": stellar_record[0],
                "sectionType": "interstellar-asymptote",
                "hypothetical": True,
                "visualizationDistanceAu": HYPOTHETICAL_ASYMPTOTE_DISTANCE_AU,
                "noLocalEphemeris": True,
                "transferStartIndex": segment_start_index,
                "entryIndex": endpoint_index,
                "periapsisIndex": endpoint_index,
                "exitIndex": endpoint_index,
                "entryDay": start_day,
                "periapsisDay": start_day,
                "exitDay": start_day,
                "entryPositionKm": list(endpoint),
                "entryDirection": list(direction),
                "entryLatitudeDeg": atan2(
                    direction[2], sqrt(direction[0] ** 2 + direction[1] ** 2)
                ) * 180 / pi,
                "exitPositionKm": list(endpoint),
                "exitVelocityKmS": list(asymptote_velocity),
                "minimumAltitudeKm": 0.0,
                "sphereOfInfluenceRadiusKm": 0.0,
                "requiredTransitionDeltaVKmS": transition_delta_v,
                "availableTransitionDeltaVKmS": section["deltaVPlusKmS"],
                "transitionDeltaVDeficitKmS": max(
                    0.0, transition_delta_v - section["deltaVPlusKmS"]
                ),
                "requiredPassageDeltaVKmS": 0.0,
                "requiredSectionDeltaVKmS": transition_delta_v,
                "corridorInsertionDeltaVKmS": 0.0,
                "entryVelocityPreserved": transition_delta_v < 1e-9,
                "lookaheadTargetId": None,
                "lookaheadAlignmentDeg": alignment_deg,
                "predictedPassiveTurnDeg": 0.0,
                "desiredDepartureDirection": list(direction),
                "predictedOutgoingDirection": list(_normalize(start_velocity)),
                "courseChangeDeg": alignment_deg if transition_applied else 0.0,
                "heliocentricSpeedGainKmS": 0.0,
                "passage": section["passage"],
                "requestedPassageAngleDeg": 0.0,
                "selectedPassageAngleDeg": 0.0,
                "corridor": {
                    "enabled": corridor["enabled"],
                    "centerDirection": list(direction),
                    "horizontalHalfAngleDeg": corridor["horizontalHalfAngleDeg"],
                    "verticalHalfAngleDeg": corridor["verticalHalfAngleDeg"],
                    "rotationDeg": corridor["rotationDeg"],
                    "actualHorizontalOffsetDeg": 0.0,
                    "actualVerticalOffsetDeg": 0.0,
                    "entryInsideCorridor": True,
                    "exitDirection": list(direction),
                    "passageSignedAngleDeg": 0.0,
                    "exitAngleSelection": None,
                },
                "relativeTrajectory": [],
                "lambertEndpointResidualKm": 0.0,
                "lambertVelocityResidualKmS": 0.0,
            })
            segments.append({
                "id": f"{section['id']}-hypothetical-50-au",
                "label": f"Hypothetische Richtung -> {stellar_record[0]} (50 AE)",
                "startIndex": segment_start_index,
                "endIndex": endpoint_index,
            })
            start_position = endpoint
            start_velocity = asymptote_velocity
            total_delta_v += transition_delta_v
            hypothetical_asymptote_direction = direction
            continue
        central = _local_central_body(origin, target, catalog)
        gravitational_parameter = (
            central.mass_kg * G_KM3_KG_S2 if central is not None else MU_SUN
        )
        central_position, central_velocity = (
            _body_state(central, epoch_days + start_day, catalog)
            if central is not None else ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        )
        frame_start = _subtract(start_position, central_position)
        frame_velocity = _subtract(start_velocity, central_velocity)

        previous_exit_selection = (
            calculated[-1].get("corridor", {}).get("exitAngleSelection")
            if calculated else None
        )
        coupled_transfer_seconds = (
            float(previous_exit_selection["transferPreviewDays"]) * DAY_SECONDS
            if (
                previous_exit_selection
                and previous_exit_selection.get("lookaheadTargetId") == target.id
                and float(previous_exit_selection.get("transferPreviewDays", 0.0)) > 0
            )
            else None
        )

        provisional_target_position, _ = _body_state(
            target, epoch_days + start_day, catalog
        )
        provisional_frame_target = _subtract(
            provisional_target_position, central_position
        )
        provisional_duration = coupled_transfer_seconds or _transfer_seconds(
            frame_start,
            provisional_frame_target,
            gravitational_parameter,
            local=central is not None,
            reference_speed_km_s=_magnitude(frame_velocity),
        )
        arrival_day = start_day + provisional_duration / DAY_SECONDS
        arrival_target_position, arrival_target_velocity = _body_state(
            target, epoch_days + arrival_day, catalog
        )
        arrival_central_position, arrival_central_velocity = (
            _body_state(central, epoch_days + arrival_day, catalog)
            if central is not None else ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        )
        direction = _normalize(tuple(corridor["centerDirection"]))
        if not corridor["enabled"]:
            # A disabled corridor means that geometry may choose the natural
            # near-side entry point.  Reusing an arbitrary editor direction
            # here can introduce a huge plane change before the user has
            # constrained the passage at all.
            natural_approach = _subtract(start_position, arrival_target_position)
            if target.kind == "sun" and _magnitude(frame_start) > 1e-9:
                inward = tuple(-component for component in _normalize(frame_start))
                tangential_velocity = _subtract(
                    frame_velocity,
                    tuple(
                        _dot(frame_velocity, inward) * component
                        for component in inward
                    ),
                )
                tangent = (
                    _normalize(tangential_velocity)
                    if _magnitude(tangential_velocity) > 1e-9
                    else _normalize(_cross((0.0, 0.0, 1.0), inward))
                )
                direction = _normalize(
                    tuple(
                        inward[index] + 0.04 * tangent[index]
                        for index in range(3)
                    )
                )
            elif _magnitude(natural_approach) > 1e-9:
                direction = _normalize(natural_approach)
        entry_radius = _entry_radius(target, catalog)
        entry_position = _add(
            arrival_target_position,
            tuple(component * entry_radius for component in direction),
        )
        frame_end = _subtract(entry_position, arrival_central_position)
        duration_seconds = coupled_transfer_seconds or _transfer_seconds(
            frame_start,
            frame_end,
            gravitational_parameter,
            local=central is not None,
            reference_speed_km_s=_magnitude(frame_velocity),
        )
        arrival_day = start_day + duration_seconds / DAY_SECONDS
        arrival_target_position, arrival_target_velocity = _body_state(
            target, epoch_days + arrival_day, catalog
        )
        arrival_central_position, arrival_central_velocity = (
            _body_state(central, epoch_days + arrival_day, catalog)
            if central is not None else ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        )
        entry_position = _add(
            arrival_target_position,
            tuple(component * entry_radius for component in direction),
        )
        frame_end = _subtract(entry_position, arrival_central_position)
        selected = _candidate(
            frame_start,
            frame_end,
            duration_seconds,
            frame_velocity,
            gravitational_parameter,
            _parking_radius(central if central is not None else catalog["sun"]),
        )
        transition_delta_v = _magnitude(
            _subtract(selected["departure"], frame_velocity)
        )
        segment_start_index = len(trajectory) - 1
        relative_trajectory, propagated_end, propagated_velocity = (
            _propagate_lambert_segment(
                frame_start,
                selected["departure"],
                start_day,
                duration_seconds,
                180,
                gravitational_parameter,
            )
        )
        emitted = []
        for point in relative_trajectory:
            point_day = float(point["elapsedDays"])
            point_central_position = (
                _body_state(central, epoch_days + point_day, catalog)[0]
                if central is not None else (0.0, 0.0, 0.0)
            )
            emitted.append({
                "elapsedDays": point_day,
                "positionKm": list(_add(tuple(point["positionKm"]), point_central_position)),
            })
        emitted[-1]["positionKm"] = list(entry_position)
        trajectory.extend(emitted[1:])
        segment_end_index = len(trajectory) - 1
        arrival_velocity = _add(propagated_velocity, arrival_central_velocity)
        trajectory[-1]["velocityKmS"] = list(arrival_velocity)
        passage_result = _local_passage(
            section=section,
            target=target,
            lookahead_target=(
                parsed[index + 1]["target"] if index + 1 < len(parsed) else None
            ),
            lookahead_entry_direction=(
                tuple(parsed[index + 1]["corridor"]["centerDirection"])
                if (
                    index + 1 < len(parsed)
                    and parsed[index + 1]["target"] is not None
                    and parsed[index + 1]["corridor"]["enabled"]
                )
                else None
            ),
            lookahead_entry_radius=(
                _entry_radius(parsed[index + 1]["target"], catalog)
                if index + 1 < len(parsed)
                and parsed[index + 1]["target"] is not None
                else 0.0
            ),
            lookahead_interstellar_target_id=(
                parsed[index + 1]["interstellarTargetId"]
                if index + 1 < len(parsed)
                else None
            ),
            catalog=catalog,
            epoch_days=epoch_days,
            entry_day=arrival_day,
            entry_direction=direction,
            entry_radius=entry_radius,
            entry_position=entry_position,
            arrival_velocity=arrival_velocity,
            configured_oberth_delta_v_km_s=(
                configured_oberth_delta_v if target.kind == "sun" else 0.0
            ),
            geometry_only=geometry_only,
        )
        if passage_result["trajectory"]:
            trajectory.extend(passage_result["trajectory"][1:])
        exit_index = len(trajectory) - 1
        periapsis_index = segment_end_index + passage_result["periapsisOffsetIndex"]
        exit_day = passage_result["exitDay"]
        exit_position = passage_result["exitPosition"]
        exit_velocity = passage_result["exitVelocity"]
        passage_delta_v = passage_result["passageDeltaVKmS"]
        horizontal = atan2(direction[1], direction[0]) * 180 / pi
        latitude = atan2(direction[2], sqrt(direction[0]**2 + direction[1]**2)) * 180 / pi
        calculated.append({
            "id": section["id"],
            "originId": origin.id,
            "targetId": target.id,
            "targetName": target.name,
            "sectionType": (
                f"{central.name}-zentrierter Transfer"
                if central is not None else "heliozentrischer Transfer"
            ),
            "transferStartIndex": segment_start_index,
            "entryIndex": segment_end_index,
            "periapsisIndex": periapsis_index,
            "exitIndex": exit_index,
            "entryDay": arrival_day,
            "periapsisDay": trajectory[periapsis_index]["elapsedDays"],
            "exitDay": exit_day,
            "entryPositionKm": list(entry_position),
            "entryDirection": list(direction),
            "entryLatitudeDeg": latitude,
            "exitPositionKm": list(exit_position),
            "exitVelocityKmS": list(exit_velocity),
            "minimumAltitudeKm": passage_result["minimumRadiusKm"] - target.radius_km,
            "sphereOfInfluenceRadiusKm": entry_radius,
            "requiredTransitionDeltaVKmS": transition_delta_v,
            "requiredPassageDeltaVKmS": passage_delta_v,
            "appliedOberthDeltaVKmS": passage_result.get(
                "appliedOberthDeltaVKmS", 0.0
            ),
            "targetCoupledPassagePlane": passage_result.get(
                "targetCoupledPlane", False
            ),
            "sundiverTransferProvidedByMissionModel": (
                sundiver_transfer_provided and index == 0
            ),
            "requiredSectionDeltaVKmS": transition_delta_v + passage_delta_v,
            "corridorInsertionDeltaVKmS": passage_delta_v,
            "entryVelocityPreserved": passage_delta_v < 1e-9,
            "lookaheadTargetId": (
                (
                    parsed[index + 1]["target"].id
                    if parsed[index + 1]["target"] is not None
                    else parsed[index + 1]["interstellarTargetId"]
                )
                if index + 1 < len(parsed) else None
            ),
            "lookaheadAlignmentDeg": passage_result.get("lookaheadAlignmentDeg", 0.0),
            "predictedPassiveTurnDeg": passage_result.get("courseChangeDeg", 0.0),
            "courseChangeDeg": passage_result.get("courseChangeDeg", 0.0),
            "heliocentricSpeedGainKmS": passage_result.get(
                "heliocentricSpeedGainKmS", 0.0
            ),
            "heliocentricSpeedBeforeKmS": _magnitude(arrival_velocity),
            "heliocentricSpeedAfterKmS": _magnitude(exit_velocity),
            "passage": section["passage"],
            "requestedPassageAngleDeg": section["passage"]["orbitAngleDeg"],
            "selectedPassageAngleDeg": abs(passage_result["exitAngleDeg"]),
            "corridor": {
                "enabled": corridor["enabled"],
                "centerDirection": list(direction),
                "horizontalHalfAngleDeg": corridor["horizontalHalfAngleDeg"],
                "verticalHalfAngleDeg": corridor["verticalHalfAngleDeg"],
                "rotationDeg": corridor["rotationDeg"],
                "actualHorizontalOffsetDeg": 0.0,
                "actualVerticalOffsetDeg": 0.0,
                "entryInsideCorridor": True,
                "exitDirection": list(passage_result["exitDirection"]),
                "passageSignedAngleDeg": passage_result["exitAngleDeg"],
                "exitAngleSelection": passage_result["exitAngleSelection"],
            },
            "relativeTrajectory": [
                {
                    "elapsedDays": point["elapsedDays"],
                    "positionKm": point["waypointRelativePositionKm"],
                }
                for point in passage_result["trajectory"]
            ],
            "lambertEndpointResidualKm": _magnitude(
                _subtract(propagated_end, frame_end)
            ),
            "lambertVelocityResidualKmS": _magnitude(
                _subtract(propagated_velocity, selected["arrival"])
            ),
        })
        segments.append({
            "id": f"{section['id']}-transfer",
            "label": f"{origin.name} → {target.name}",
            "startIndex": segment_start_index,
            "endIndex": exit_index,
        })
        total_delta_v += transition_delta_v + passage_delta_v
        start_position = exit_position
        start_velocity = exit_velocity
        start_day = exit_day

    first = calculated[0]
    first_definition = parsed[0]
    requested_waypoint_id = str(values.get("waypointId") or "")
    focus_index = next(
        (
            index for index in range(len(calculated) - 1, -1, -1)
            if calculated[index]["targetId"] == requested_waypoint_id
        ),
        len(calculated) - 1,
    )
    focus = calculated[focus_index]
    focus_definition = parsed[focus_index]
    def required_for_configured_budget(index: int, section: dict) -> float:
        if sundiver_transfer_provided and index == 0:
            # The configured Sundiver mission supplies the Earth-to-Sun
            # transfer.  Only an additional corridor insertion impulse is
            # charged to the section's local delta-v fan.
            return section["requiredPassageDeltaVKmS"]
        return section["requiredSectionDeltaVKmS"]

    warnings = [
        (
            f"Abschnitt {index + 1} benötigt am Übergang Δv "
            f"{section['requiredSectionDeltaVKmS']:.2f} km/s."
        )
        for index, section in enumerate(calculated)
        if required_for_configured_budget(index, section)
        > parsed[index]["deltaVPlusKmS"] + 1e-9
    ]
    warnings.extend(
        (
            f"Abschnitt {index + 1} besitzt keine direkte Außentangente zum "
            "Folgeziel. Der kollisionsgeprüfte gekrümmte Transfer wird als "
            "beste Annäherung verwendet."
        )
        for index, section in enumerate(calculated)
        if (
            section["corridor"].get("exitAngleSelection")
            and section["corridor"]["exitAngleSelection"].get("bestApproximation")
        )
    )
    final_velocity = tuple(trajectory[-1].get("velocityKmS", start_velocity))
    minimum_solar_radius_km = min(
        _magnitude(tuple(point["positionKm"])) for point in trajectory
    )
    if minimum_solar_radius_km < SUN_RADIUS_KM - 1.0:
        raise ValueError(
            "Die propagierte Route schneidet den Sonnenkörper "
            f"(Minimum {minimum_solar_radius_km:.0f} km)."
        )
    feasible_with_configured_burn = all(
        required_for_configured_budget(index, section)
        <= parsed[index]["deltaVPlusKmS"] + 1e-9
        for index, section in enumerate(calculated)
    )
    target_transition_section = (
        calculated[-1]
        if calculated[-1].get("sectionType") == "interstellar-asymptote"
        else calculated[1]
        if len(calculated) > 1
        else None
    )
    target_correction_delta_v = (
        target_transition_section["requiredTransitionDeltaVKmS"]
        if target_transition_section is not None
        else 0.0
    )
    return {
        "startDate": start_date,
        "calculationStage": "geometry" if geometry_only else "performance",
        "spacecraftIntegration": spacecraft_integration,
        "totalFlightDays": start_day,
        "warnings": warnings,
        "trajectory": trajectory,
        "segments": segments,
        "routeSections": calculated,
        "stateChain": {
            "continuousPosition": True,
            "exitStateFeedsNextSection": True,
            "transitionImpulsesExplicit": True,
            "sundiverTransferProvidedByMissionModel": sundiver_transfer_provided,
            "targetCoupledSolarPassagePlane": first.get(
                "targetCoupledPassagePlane", False
            ),
            "coordinateConvention": "x=Breite, y=Tiefe, z=Höhe (ECLIPJ2000)",
            "referenceFramesSelectedPerSection": True,
        },
        "validation": {
            "collisionFree": True,
            "minimumSolarRadiusKm": minimum_solar_radius_km,
            "sunRadiusKm": SUN_RADIUS_KM,
            "minimumSolarAltitudeKm": minimum_solar_radius_km - SUN_RADIUS_KM,
        },
        "waypoint": {
            "id": focus["targetId"],
            "name": focus["targetName"],
            "encounterDay": focus["entryDay"],
            "entryDay": focus["entryDay"],
            "exitDay": focus["exitDay"],
            "flybyAltitudeKm": focus["minimumAltitudeKm"],
            "minimumFlybyAltitudeKm": 0.0,
            "trajectoryIndex": focus["entryIndex"],
            "positionKm": focus["entryPositionKm"],
        },
        "entryCorridor": {
            "enabled": focus_definition["corridor"]["enabled"],
            "surface": "körperbezogene Navigationsgrenze",
            "selectionStrategy": "körperbasierte Bezugssystemwahl mit Lambert-Basislösung",
            "centerDirection": list(focus_definition["corridor"]["centerDirection"]),
            "horizontalHalfAngleDeg": focus_definition["corridor"]["horizontalHalfAngleDeg"],
            "verticalHalfAngleDeg": focus_definition["corridor"]["verticalHalfAngleDeg"],
            "rotationDeg": focus_definition["corridor"]["rotationDeg"],
            "selectedDirection": focus["entryDirection"],
            "evaluatedTargetCount": 1,
            "actualEntryDirection": focus["entryDirection"],
            "actualHorizontalOffsetDeg": 0.0,
            "actualVerticalOffsetDeg": 0.0,
            "actualEntryPositionKm": focus["entryPositionKm"],
            "entryInsideCorridor": True,
        },
        "outgoingDirection": list(
            hypothetical_asymptote_direction or _normalize(final_velocity)
        ),
        "summary": {
            "flybyMode": "multi-section",
            "requiredInjectionDeltaVKmS": (
                first["requiredPassageDeltaVKmS"]
                if sundiver_transfer_provided
                else first["requiredTransitionDeltaVKmS"]
            ),
            "availableInjectionDeltaVKmS": first_definition["deltaVPlusKmS"],
            "solarDepartureInjectionApplied": (
                required_for_configured_budget(0, first)
                <= first_definition["deltaVPlusKmS"] + 1e-9
            ),
            "sundiverTransferProvidedByMissionModel": sundiver_transfer_provided,
            "modeledSundiverTransferDeltaVKmS": (
                first["requiredTransitionDeltaVKmS"]
                if sundiver_transfer_provided else 0.0
            ),
            "appliedOberthDeltaVKmS": first.get("appliedOberthDeltaVKmS", 0.0),
            "targetCoupledPassagePlane": first.get(
                "targetCoupledPassagePlane", False
            ),
            "incomingExcessSpeedKmS": 0.0,
            "turnAngleDeg": focus.get("courseChangeDeg", 0.0),
            "heliocentricSpeedBeforeKmS": focus.get(
                "heliocentricSpeedBeforeKmS",
                _magnitude(tuple(trajectory[0]["velocityKmS"])),
            ),
            "heliocentricSpeedAfterKmS": focus.get(
                "heliocentricSpeedAfterKmS", _magnitude(final_velocity)
            ),
            "speedGainKmS": focus.get("heliocentricSpeedGainKmS", 0.0),
            "targetCorrectionDeltaVKmS": target_correction_delta_v,
            "targetInjectionApplied": (
                feasible_with_configured_burn and len(calculated) > 1
            ),
            "passiveTargeting": (
                feasible_with_configured_burn and len(calculated) == 1
            ),
            "courseChangeDeg": focus.get("courseChangeDeg", 0.0),
            "periapsisSpeedKmS": focus.get(
                "heliocentricSpeedAfterKmS", _magnitude(final_velocity)
            ),
            "observationWindowHours": 0.0,
            "targetAlignmentDeg": calculated[-1].get("lookaheadAlignmentDeg", 0.0),
            "actualTargetAlignmentDeg": calculated[-1].get(
                "lookaheadAlignmentDeg", 0.0
            ),
            "feasibleWithConfiguredBurn": feasible_with_configured_burn,
            "hypotheticalInterstellarAsymptote": (
                hypothetical_asymptote_direction is not None
            ),
            "interstellarVisualizationDistanceAu": (
                HYPOTHETICAL_ASYMPTOTE_DISTANCE_AU
                if hypothetical_asymptote_direction is not None else None
            ),
            "entryCorridorTargeted": first_definition["corridor"]["enabled"],
            "entryInsideCorridor": True,
            "warnings": warnings,
            "model": (
                "target-coupled 3D solar-Oberth patched-conic route"
                if sundiver_transfer_provided
                and first.get("targetCoupledPassagePlane", False)
                else "körperbasierte Patched-Conic-Basisroute"
            ),
            "totalTransitionDeltaVKmS": total_delta_v,
        },
    }


def simulate_generic_route_sections(values: dict | None) -> dict:
    """Single audited planner; legacy section output is an adapter only."""
    from planner.trajectory_planner import calculate_section_route
    return calculate_section_route(values or {})
