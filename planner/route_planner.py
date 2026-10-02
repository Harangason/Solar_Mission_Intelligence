"""Lambert transfer and patched-conic planetary flyby planning."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from math import acosh, acos, asin, atan2, cos, cosh, pi, sin, sinh, sqrt, tan

from scipy import constants
from scipy.integrate import solve_ivp
from scipy.optimize import minimize

from services.calculation_audit import write_route_audit
from solver.nbody_propagation import validate_continuous_waypoint_route
from solver.trajectory import (
    AU_KM,
    DAY_SECONDS,
    J2000,
    MU_SUN,
    PLANET_EPHEMERIDES,
    KalmanNavigationSystem,
    _add,
    _magnitude,
    _mission_epoch_days,
    _normalize,
    _planet_position_at,
    _planet_state_at,
    _rk4,
    simulate_mission,
)
from visualization.view_3d_celestials import PLANET_DATA


G_KM3_KG_S2 = constants.G / 1_000**3
CHI_SQUARE_RADIUS_95_3D = 2.795


def _dot(first: tuple, second: tuple) -> float:
    return sum(first[index] * second[index] for index in range(3))


def _cross(first: tuple, second: tuple) -> tuple:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )

def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _corridor_basis(center_direction: tuple, rotation_deg: float) -> tuple[tuple, tuple]:
    """Return rotated tangent axes for a planet-centred spherical corridor."""
    center = _normalize(center_direction)
    reference = (0.0, 0.0, 1.0) if abs(_dot(center, (0.0, 0.0, 1.0))) < 0.9 else (0.0, 1.0, 0.0)
    right = _normalize(_cross(reference, center))
    up = _normalize(_cross(center, right))
    rotation = rotation_deg * pi / 180.0
    return (
        _normalize(tuple(
            right[index] * cos(rotation) + up[index] * sin(rotation)
            for index in range(3)
        )),
        _normalize(tuple(
            -right[index] * sin(rotation) + up[index] * cos(rotation)
            for index in range(3)
        )),
    )


def _corridor_direction(
    center_direction: tuple,
    horizontal_offset_deg: float,
    vertical_offset_deg: float,
    rotation_deg: float = 0.0,
) -> tuple:
    """Map gnomonic angular offsets onto the unit sphere."""
    center = _normalize(center_direction)
    right, up = _corridor_basis(center, rotation_deg)
    horizontal = tan(horizontal_offset_deg * pi / 180.0)
    vertical = tan(vertical_offset_deg * pi / 180.0)
    return _normalize(tuple(
        center[index] + right[index] * horizontal + up[index] * vertical
        for index in range(3)
    ))


def _corridor_coordinates_deg(
    direction: tuple,
    center_direction: tuple,
    rotation_deg: float = 0.0,
) -> tuple[float, float]:
    """Return gnomonic horizontal and vertical offsets from corridor centre."""
    center = _normalize(center_direction)
    candidate = _normalize(direction)
    right, up = _corridor_basis(center, rotation_deg)
    forward = _dot(candidate, center)
    if forward <= 0.0:
        return 180.0, 180.0
    return (
        atan2(_dot(candidate, right), forward) * 180.0 / pi,
        atan2(_dot(candidate, up), forward) * 180.0 / pi,
    )


def _parse_entry_corridor(values: dict) -> dict:
    enabled = values.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("entryCorridor.enabled muss ein boolescher Wert sein.")
    blocked = values.get("blocked", False)
    if not isinstance(blocked, bool):
        raise ValueError("entryCorridor.blocked muss ein boolescher Wert sein.")
    if enabled and blocked:
        raw_reasons = values.get("blockReasons") or ()
        reasons = raw_reasons if isinstance(raw_reasons, (list, tuple)) else ()
        detail = " ".join(str(reason) for reason in reasons if reason)
        suffix = f" {detail}" if detail else ""
        raise ValueError(f"Zielkorridor ist gesperrt.{suffix}")
    raw_center = values.get("centerDirection") or (1.0, 0.0, 0.0)
    if not isinstance(raw_center, (list, tuple)) or len(raw_center) != 3:
        raise ValueError("entryCorridor.centerDirection muss drei Komponenten besitzen.")
    center = _normalize(tuple(float(component) for component in raw_center))
    if _magnitude(center) == 0.0:
        raise ValueError("Der Mittelpunkt des Eintrittskorridors darf kein Nullvektor sein.")
    horizontal = float(values.get("horizontalHalfAngleDeg", 8.0))
    vertical = float(values.get("verticalHalfAngleDeg", 5.0))
    rotation = float(values.get("rotationDeg", 0.0))
    if not 0.1 <= horizontal <= 80.0 or not 0.1 <= vertical <= 80.0:
        raise ValueError("Die Korridor-Halbwinkel müssen zwischen 0,1° und 80° liegen.")
    return {
        "enabled": enabled,
        "centerDirection": center,
        "horizontalHalfAngleDeg": horizontal,
        "verticalHalfAngleDeg": vertical,
        "rotationDeg": rotation,
    }


def _select_entry_corridor_target(
    corridor: dict,
    *,
    burn_position: tuple,
    reference_velocity: tuple,
    planet_position: tuple,
    sphere_of_influence_km: float,
    flight_seconds: float,
) -> dict:
    """Select the lowest-injection Lambert target from a 3x3 corridor grid."""
    horizontal_limit = corridor["horizontalHalfAngleDeg"]
    vertical_limit = corridor["verticalHalfAngleDeg"]
    candidates = []
    for horizontal_factor in (-1.0, 0.0, 1.0):
        for vertical_factor in (-1.0, 0.0, 1.0):
            horizontal = horizontal_factor * horizontal_limit
            vertical = vertical_factor * vertical_limit
            direction = _corridor_direction(
                corridor["centerDirection"],
                horizontal,
                vertical,
                corridor["rotationDeg"],
            )
            position = _add(
                planet_position,
                tuple(component * sphere_of_influence_km for component in direction),
            )
            try:
                departure, _, diagnostics = _select_lambert(
                    burn_position,
                    position,
                    flight_seconds,
                    reference_velocity,
                )
            except ValueError:
                continue
            candidates.append({
                "direction": direction,
                "position": position,
                "horizontalOffsetDeg": horizontal,
                "verticalOffsetDeg": vertical,
                "requiredInjectionDeltaVKmS": _magnitude(
                    _subtract(departure, reference_velocity)
                ),
                "lambertCandidateCount": diagnostics["candidateCount"],
            })
    if not candidates:
        raise ValueError("Kein Lambert-Zielpunkt innerhalb des Eintrittskorridors ist erreichbar.")
    selected = min(candidates, key=lambda candidate: candidate["requiredInjectionDeltaVKmS"])
    selected["evaluatedTargetCount"] = len(candidates)
    return selected


def _aimpoint_direction_from_clock(
    clock_angle_deg: float,
    screen_radius_norm: float,
    view_direction: tuple,
    screen_up: tuple,
    screen_right: tuple,
) -> tuple:
    """Convert a projected planet-disc clock position into a planet-relative unit vector.

    Convention:
    - 0°   = 12 Uhr / screen_up
    - 90°  = 3 Uhr / screen_right
    - 180° = 6 Uhr
    - 270° = 9 Uhr
    - screen_radius_norm = 0 means disc center
    - screen_radius_norm = 1 means visible limb/rim
    """
    angle = clock_angle_deg * pi / 180.0
    p = _clamp(screen_radius_norm, 0.0, 1.0)

    up = _normalize(screen_up)
    right = _normalize(screen_right)
    view = _normalize(view_direction)

    screen_direction = _normalize(tuple(
        up[index] * cos(angle) + right[index] * sin(angle)
        for index in range(3)
    ))

    depth = sqrt(max(0.0, 1.0 - p * p))

    return _normalize(tuple(
        view[index] * depth + screen_direction[index] * p
        for index in range(3)
    ))
def _align_hyperbola_periapsis_aimpoint(
    axis_x: tuple,
    axis_y: tuple,
    desired_periapsis_direction: tuple | None,
) -> tuple[tuple, tuple]:
    """Rotate the hyperbola frame so axis_x points to the requested periapsis aimpoint."""
    if desired_periapsis_direction is None:
        return axis_x, axis_y

    current = _normalize(axis_x)
    desired = _normalize(desired_periapsis_direction)

    angle = acos(max(-1.0, min(1.0, _dot(current, desired))))
    rotation_axis = _cross(current, desired)

    if angle < 1e-12 or _magnitude(rotation_axis) < 1e-12:
        return axis_x, axis_y

    new_axis_x = _normalize(_rotate_about_axis(axis_x, rotation_axis, angle))
    new_axis_y = _normalize(_rotate_about_axis(axis_y, rotation_axis, angle))

    return new_axis_x, new_axis_y


def _align_hyperbola_aimpoint(
    axis_x: tuple,
    axis_y: tuple,
    anomaly: float,
    semi_major_axis: float,
    eccentricity: float,
    planet_mu: float,
    desired_periapsis_direction: tuple | None,
) -> tuple[tuple, tuple]:
    if desired_periapsis_direction is None:
        return axis_x, axis_y
    position, _, _ = _hyperbola_relative_state(
        anomaly,
        semi_major_axis,
        eccentricity,
        planet_mu,
        axis_x,
        axis_y,
    )
    current_direction = _normalize(position)
    desired_direction = _normalize(desired_periapsis_direction)

    cosine_error = max(-1.0, min(1.0, _dot(current_direction, desired_direction)))
    correction_angle = acos(cosine_error)
    correction_axis = _cross(current_direction, desired_direction)

    if correction_angle < 1e-12 or _magnitude(correction_axis) < 1e-12:
        return axis_x, axis_y

    return (
        _normalize(_rotate_about_axis(axis_x, correction_axis, correction_angle)),
        _normalize(_rotate_about_axis(axis_y, correction_axis, correction_angle)),
    )

def _subtract(first: tuple, second: tuple) -> tuple:
    return tuple(first[index] - second[index] for index in range(3))


def _stumpff_c(z: float) -> float:
    if z > 1e-8:
        root = sqrt(z)
        return (1 - cos(root)) / z
    if z < -1e-8:
        root = sqrt(-z)
        return (cosh(root) - 1) / -z
    return 0.5


def _stumpff_s(z: float) -> float:
    if z > 1e-8:
        root = sqrt(z)
        return (root - sin(root)) / root**3
    if z < -1e-8:
        root = sqrt(-z)
        return (sinh(root) - root) / root**3
    return 1 / 6


def _lambert_candidates(
    start: tuple,
    end: tuple,
    flight_seconds: float,
    gravitational_parameter: float = MU_SUN,
) -> list[dict]:
    """Return zero-revolution Lambert solutions for both transfer sides."""
    start_radius, end_radius = _magnitude(start), _magnitude(end)
    cosine = max(-1.0, min(1.0, _dot(start, end) / (start_radius * end_radius)))
    denominator = 1 - cosine
    if denominator < 1e-12:
        raise ValueError("Lambert-Geometrie ist nahezu kollinear.")
    sine_magnitude = sqrt(max(0.0, 1 - cosine**2))
    candidates: list[dict] = []
    for transfer_side in (1.0, -1.0):
        parameter_a = transfer_side * sine_magnitude * sqrt(start_radius * end_radius / denominator)
        if abs(parameter_a) < 1e-9:
            continue

        def residual(z: float) -> float | None:
            c_value, s_value = _stumpff_c(z), _stumpff_s(z)
            if c_value <= 0:
                return None
            y_value = start_radius + end_radius + parameter_a * (z * s_value - 1) / sqrt(c_value)
            if y_value <= 0:
                return None
            calculated = (y_value / c_value) ** 1.5 * s_value + parameter_a * sqrt(y_value)
            return calculated - sqrt(gravitational_parameter) * flight_seconds

        previous: tuple[float, float] | None = None
        brackets: list[tuple[tuple[float, float], tuple[float, float]]] = []
        z_minimum, z_maximum = -4 * pi**2, 64 * pi**2
        sample_count = 5000
        for index in range(sample_count + 1):
            z_value = z_minimum + index * (z_maximum - z_minimum) / sample_count
            value = residual(z_value)
            if value is None:
                previous = None
                continue
            current = (z_value, value)
            if previous is not None and (previous[1] == 0 or previous[1] * value < 0):
                brackets.append((previous, current))
            previous = current
        if not brackets:
            continue
        for bracket in brackets:
            lower, upper = bracket[0][0], bracket[1][0]
            for _ in range(80):
                middle = (lower + upper) / 2
                lower_value, middle_value = residual(lower), residual(middle)
                if lower_value is None or middle_value is None:
                    lower = middle
                    continue
                if lower_value * middle_value <= 0:
                    upper = middle
                else:
                    lower = middle
            z_value = (lower + upper) / 2
            side_label = "positive-sine" if transfer_side > 0 else "negative-sine"
            if any(candidate["transferSide"] == side_label and abs(candidate["universalVariableZ"] - z_value) < 1e-7 for candidate in candidates):
                continue
            c_value, s_value = _stumpff_c(z_value), _stumpff_s(z_value)
            y_value = start_radius + end_radius + parameter_a * (z_value * s_value - 1) / sqrt(c_value)
            f_value = 1 - y_value / start_radius
            g_value = parameter_a * sqrt(y_value / gravitational_parameter)
            if abs(g_value) < 1e-12:
                continue
            g_dot = 1 - y_value / end_radius
            departure = tuple((end[index] - f_value * start[index]) / g_value for index in range(3))
            arrival = tuple((g_dot * end[index] - start[index]) / g_value for index in range(3))
            angular_momentum_z = _cross(start, departure)[2]
            candidates.append({
                "departure": departure,
                "arrival": arrival,
                "transferSide": side_label,
                "motion": "prograde" if angular_momentum_z >= 0 else "retrograde",
                "revolutionFamily": max(0, int(sqrt(max(0.0, z_value)) / (2 * pi))),
                "universalVariableZ": z_value,
            })
    if not candidates:
        raise ValueError("Keine Lambert-Lösung für Datum und Wegpunkt gefunden.")
    return candidates


def _select_lambert(start: tuple, end: tuple, flight_seconds: float, reference_velocity: tuple) -> tuple[tuple, tuple, dict]:
    candidates = _lambert_candidates(start, end, flight_seconds)
    for candidate in candidates:
        departure = candidate["departure"]
        candidate["injectionDeltaVKmS"] = _magnitude(_subtract(departure, reference_velocity))
        candidate["directionChangeDeg"] = acos(max(-1.0, min(1.0, _dot(
            _normalize(departure), _normalize(reference_velocity),
        )))) * 180 / pi
    selected = min(candidates, key=lambda item: item["injectionDeltaVKmS"])
    diagnostics = {key: value for key, value in selected.items() if key not in {"departure", "arrival"}}
    diagnostics["candidateCount"] = len(candidates)
    diagnostics["candidates"] = [{
        key: value for key, value in candidate.items() if key not in {"departure", "arrival"}
    } for candidate in candidates]
    return selected["departure"], selected["arrival"], diagnostics


def _lambert(start: tuple, end: tuple, flight_seconds: float, reference_velocity: tuple | None = None) -> tuple[tuple, tuple]:
    """Select the Lambert branch closest to the supplied boundary velocity."""
    if reference_velocity is None:
        candidate = min(_lambert_candidates(start, end, flight_seconds), key=lambda item: _magnitude(item["departure"]))
        return candidate["departure"], candidate["arrival"]
    departure, arrival, _ = _select_lambert(start, end, flight_seconds, reference_velocity)
    return departure, arrival


def _propagate_lambert_segment(
    start_position: tuple,
    start_velocity: tuple,
    start_day: float,
    flight_seconds: float,
    sample_count: int,
    gravitational_parameter: float = MU_SUN,
) -> tuple[list[dict], tuple, tuple]:
    """Adaptively propagate the heliocentric Lambert arc for display and audit."""
    from solver.nbody_propagation import propagate_conic
    result = propagate_conic((start_position, start_velocity), flight_seconds,
                             gravitational_parameter, sample_count=sample_count)
    trajectory = [{"elapsedDays": start_day + p["elapsedSeconds"] / DAY_SECONDS,
                   "positionKm": p["positionKm"], "velocityKmS": p["velocityKmS"]}
                  for p in result["trajectory"]]
    return trajectory, tuple(result["finalPositionKm"]), tuple(result["finalVelocityKmS"])


def _solar_asymptote_direction(position: tuple, velocity: tuple) -> tuple | None:
    """Return the outgoing two-body hyperbolic asymptote direction."""
    angular_momentum = _cross(position, velocity)
    angular_momentum_magnitude = _magnitude(angular_momentum)
    radius = _magnitude(position)
    if angular_momentum_magnitude < 1e-12 or radius < 1.0:
        return None
    eccentricity_vector = _subtract(
        tuple(component / MU_SUN for component in _cross(velocity, angular_momentum)),
        tuple(component / radius for component in position),
    )
    eccentricity = _magnitude(eccentricity_vector)
    if eccentricity <= 1.0 + 1e-10:
        return None
    periapsis_axis = _normalize(eccentricity_vector)
    normal = tuple(component / angular_momentum_magnitude for component in angular_momentum)
    transverse_axis = _normalize(_cross(normal, periapsis_axis))
    asymptotic_true_anomaly = acos(max(-1.0, min(1.0, -1 / eccentricity)))
    return _normalize(tuple(
        periapsis_axis[index] * cos(asymptotic_true_anomaly)
        + transverse_axis[index] * sin(asymptotic_true_anomaly)
        for index in range(3)
    ))


def _solar_speed_at_radius(position: tuple, velocity: tuple, radius_km: float = AU_KM) -> float:
    """Return the two-body speed at a requested outbound solar radius.

    A value of zero means that the osculating orbit does not reach the
    requested radius.  The default 1-AU boundary is the navigator's explicit
    definition of "Sonnenaustritt"; it avoids confusing perihelion speed with
    the much lower speed after climbing out of the solar gravity well.
    """
    specific_energy = _magnitude(velocity) ** 2 / 2 - MU_SUN / max(_magnitude(position), 1.0)
    squared_speed = 2 * (specific_energy + MU_SUN / max(radius_km, 1.0))
    return sqrt(max(0.0, squared_speed))


def _backward_target_state(
    position: tuple,
    planet_velocity: tuple,
    excess_speed: float,
    target_direction: tuple,
) -> dict:
    """Solve the outbound Jupiter state from the target back to the flyby.

    The magnitude of the planet-relative hyperbolic excess is supplied by the
    forward Lambert leg and is conserved by an unpowered swing-by.  This
    backward solve chooses its direction independently so that the resulting
    solar hyperbola approaches the interstellar target direction.
    """
    target_direction = _normalize(target_direction)
    target_longitude = atan2(target_direction[1], target_direction[0])
    target_latitude = asin(max(-1.0, min(1.0, target_direction[2])))
    relative_hint = _normalize(_subtract(
        tuple(component * max(_magnitude(planet_velocity) + excess_speed, excess_speed) for component in target_direction),
        planet_velocity,
    ))
    hint_longitude = atan2(relative_hint[1], relative_hint[0])
    hint_latitude = asin(max(-1.0, min(1.0, relative_hint[2])))

    def state_from_angles(angles) -> tuple[tuple, tuple, tuple | None, float]:
        longitude = float(angles[0])
        latitude = max(-pi / 2, min(pi / 2, float(angles[1])))
        cosine_latitude = cos(latitude)
        relative_direction = (
            cosine_latitude * cos(longitude),
            cosine_latitude * sin(longitude),
            sin(latitude),
        )
        outgoing_velocity = _add(
            planet_velocity,
            tuple(component * excess_speed for component in relative_direction),
        )
        asymptote = _solar_asymptote_direction(position, outgoing_velocity)
        alignment = (
            acos(max(-1.0, min(1.0, _dot(asymptote, target_direction))))
            if asymptote is not None
            else pi
        )
        return relative_direction, outgoing_velocity, asymptote, alignment

    solutions: list[dict] = []
    starts = (
        (hint_longitude, hint_latitude),
        (target_longitude, target_latitude),
        (hint_longitude + pi / 2, hint_latitude),
        (hint_longitude - pi / 2, hint_latitude),
    )
    for start in starts:
        def objective(angles) -> float:
            _, outgoing_velocity, _, alignment = state_from_angles(angles)
            # Alignment is the hard backward boundary condition.  A tiny
            # energy preference breaks numerically equivalent solutions.  A
            # negative target projection is forbidden by the mission rule
            # that the post-Jupiter path may never initially run away from
            # the target.
            target_progress_rate = _dot(outgoing_velocity, target_direction)
            return (
                alignment**2 * 1_000_000
                + max(0.0, -target_progress_rate) ** 2 * 10_000
                - _magnitude(outgoing_velocity) * 1e-5
            )

        result = minimize(
            objective,
            start,
            method="Nelder-Mead",
            options={"maxiter": 100, "xatol": 1e-9, "fatol": 1e-14},
        )
        relative_direction, outgoing_velocity, asymptote, alignment = state_from_angles(result.x)
        if asymptote is None:
            continue
        solutions.append({
            "relativeDirection": relative_direction,
            "velocity": outgoing_velocity,
            "asymptoteDirection": asymptote,
            "alignmentRad": alignment,
            "initialTargetProgressKmS": _dot(outgoing_velocity, target_direction),
            "iterations": int(getattr(result, "nit", 0)),
            "converged": bool(getattr(result, "success", False)),
        })
    if not solutions:
        raise RuntimeError("Rückwärtsrechnung findet bei dieser Überschussgeschwindigkeit keine solare Fluchtbahn.")
    return min(solutions, key=lambda solution: (
        0 if solution["initialTargetProgressKmS"] > 0 else 1,
        solution["alignmentRad"],
        -solution["initialTargetProgressKmS"],
    ))


def _solve_bidirectional_flyby(
    position: tuple,
    planet_velocity: tuple,
    incoming_excess: tuple,
    target_direction: tuple,
    maximum_turn_angle: float,
    flyby_mode: str,
) -> dict:
    """Match a forward Lambert arrival with a backward target boundary."""
    excess_speed = _magnitude(incoming_excess)
    incoming_direction = _normalize(incoming_excess)
    backward = _backward_target_state(position, planet_velocity, excess_speed, target_direction)
    required_direction = backward["relativeDirection"]
    demanded_turn = acos(max(-1.0, min(1.0, _dot(incoming_direction, required_direction))))
    used_turn = min(demanded_turn, maximum_turn_angle)
    outgoing_excess = _rotate_towards(incoming_excess, required_direction, used_turn)
    outgoing_velocity = _add(planet_velocity, outgoing_excess)
    actual_asymptote = _solar_asymptote_direction(position, outgoing_velocity)
    actual_alignment = (
        acos(max(-1.0, min(1.0, _dot(actual_asymptote, _normalize(target_direction)))))
        if actual_asymptote is not None
        else pi
    )
    angular_residual = max(0.0, demanded_turn - maximum_turn_angle)
    velocity_residual = 2 * excess_speed * sin(angular_residual / 2)
    return {
        "outgoingExcess": outgoing_excess,
        "outgoingVelocity": outgoing_velocity,
        "usedTurnRad": used_turn,
        "maximumTurnRad": maximum_turn_angle,
        "demandedTurnRad": demanded_turn,
        "turnClosureResidualRad": angular_residual,
        "boundaryVelocityResidualKmS": velocity_residual,
        "backwardRequiredRelativeDirection": required_direction,
        "backwardRequiredVelocityKmS": backward["velocity"],
        "backwardAsymptoteDirection": backward["asymptoteDirection"],
        "backwardAlignmentRad": backward["alignmentRad"],
        "initialTargetProgressKmS": _dot(outgoing_velocity, _normalize(target_direction)),
        "actualAsymptoteDirection": actual_asymptote,
        "actualAlignmentRad": actual_alignment,
        "backwardIterations": backward["iterations"],
        "backwardConverged": backward["converged"],
        "passiveMatch": (
            angular_residual <= 1e-8
            and backward["alignmentRad"] <= 0.01 * pi / 180
            and actual_alignment <= 0.01 * pi / 180
            and _dot(outgoing_velocity, _normalize(target_direction)) > 0
        ),
        "mode": flyby_mode,
    }


def _solve_solar_target_injection(position: tuple, gravity_velocity: tuple, target_direction: tuple) -> dict:
    """Find the minimum tested SOI-exit correction whose solar asymptote hits the target."""
    target_direction = _normalize(target_direction)
    escape_speed = sqrt(2 * MU_SUN / max(_magnitude(position), 1.0))
    target_longitude = atan2(target_direction[1], target_direction[0])
    target_latitude = asin(max(-1.0, min(1.0, target_direction[2])))
    gravity_speed = _magnitude(gravity_velocity)
    tested_speeds = sorted({
        max(escape_speed * factor, escape_speed * 1.001)
        for factor in (1.001, 1.03, 1.08, 1.16, 1.3, 1.55, 2.0)
    } | {max(gravity_speed, escape_speed * 1.001)})
    solutions: list[dict] = []

    for departure_speed in tested_speeds:
        def objective(angles) -> float:
            longitude = float(angles[0])
            latitude = max(-pi / 2, min(pi / 2, float(angles[1])))
            cosine_latitude = cos(latitude)
            direction = (cosine_latitude * cos(longitude), cosine_latitude * sin(longitude), sin(latitude))
            velocity = tuple(component * departure_speed for component in direction)
            asymptote = _solar_asymptote_direction(position, velocity)
            if asymptote is None:
                return 1_000.0
            alignment = acos(max(-1.0, min(1.0, _dot(asymptote, target_direction))))
            target_progress_rate = _dot(velocity, target_direction)
            return alignment**2 + max(0.0, -target_progress_rate) ** 2

        result = minimize(
            objective,
            (target_longitude, target_latitude),
            method="Nelder-Mead",
            options={"maxiter": 90, "xatol": 1e-8, "fatol": 1e-14},
        )
        longitude = float(result.x[0])
        latitude = max(-pi / 2, min(pi / 2, float(result.x[1])))
        cosine_latitude = cos(latitude)
        direction = (cosine_latitude * cos(longitude), cosine_latitude * sin(longitude), sin(latitude))
        velocity = tuple(component * departure_speed for component in direction)
        asymptote = _solar_asymptote_direction(position, velocity)
        if asymptote is None:
            continue
        alignment = acos(max(-1.0, min(1.0, _dot(asymptote, target_direction))))
        solutions.append({
            "velocity": velocity,
            "injectionDirection": direction,
            "asymptoteDirection": asymptote,
            "departureSpeedKmS": departure_speed,
            "escapeSpeedKmS": escape_speed,
            "correctionDeltaVKmS": _magnitude(_subtract(velocity, gravity_velocity)),
            "alignmentRad": alignment,
            "initialTargetProgressKmS": _dot(velocity, target_direction),
            "iterations": int(getattr(result, "nit", 0)),
            "converged": bool(getattr(result, "success", False)),
        })
    if not solutions:
        raise RuntimeError("Keine hyperbolische solare Zielasymptote gefunden.")
    return min(solutions, key=lambda solution: (
        0 if solution["initialTargetProgressKmS"] > 0 else 1,
        0 if solution["alignmentRad"] <= 0.01 * pi / 180 else 1,
        solution["correctionDeltaVKmS"] + solution["alignmentRad"] * 1_000,
    ))


def _rotate_towards(vector: tuple, target: tuple, maximum_angle: float) -> tuple:
    source = _normalize(vector)
    destination = _normalize(target)
    separation = acos(max(-1.0, min(1.0, _dot(source, destination))))
    angle = min(maximum_angle, separation)
    axis = _cross(source, destination)
    if _magnitude(axis) < 1e-12:
        return vector
    axis = _normalize(axis)
    rotated = tuple(
        source[index] * cos(angle)
        + _cross(axis, source)[index] * sin(angle)
        + axis[index] * _dot(axis, source) * (1 - cos(angle))
        for index in range(3)
    )
    return tuple(value * _magnitude(vector) for value in rotated)


def _rotate_about_axis(vector: tuple, axis: tuple, angle: float) -> tuple:
    axis = _normalize(axis)
    cross_term = _cross(axis, vector)
    projection = _dot(axis, vector)
    return tuple(
        vector[index] * cos(angle)
        + cross_term[index] * sin(angle)
        + axis[index] * projection * (1 - cos(angle))
        for index in range(3)
    )


def _align_hyperbola_entry_velocity(
    axis_x: tuple,
    axis_y: tuple,
    hyperbolic_limit: float,
    semi_major_axis: float,
    eccentricity: float,
    planet_mu: float,
    required_entry_direction: tuple,
) -> tuple[tuple, tuple]:
    """Rotate the complete conic frame onto the finite Lambert SOI velocity."""
    _, calculated_velocity, _ = _hyperbola_relative_state(
        -hyperbolic_limit,
        semi_major_axis,
        eccentricity,
        planet_mu,
        axis_x,
        axis_y,
    )
    calculated_direction = _normalize(calculated_velocity)
    required_direction = _normalize(required_entry_direction)
    cosine_error = max(-1.0, min(1.0, _dot(calculated_direction, required_direction)))
    correction_angle = acos(cosine_error)
    correction_axis = _cross(calculated_direction, required_direction)
    if correction_angle < 1e-14 or _magnitude(correction_axis) < 1e-14:
        return axis_x, axis_y
    return (
        _normalize(_rotate_about_axis(axis_x, correction_axis, correction_angle)),
        _normalize(_rotate_about_axis(axis_y, correction_axis, correction_angle)),
    )


def _select_flyby_outgoing(
    incoming_excess: tuple,
    planet_velocity: tuple,
    target_direction: tuple,
    turn_angle: float,
    flyby_mode: str,
) -> tuple[tuple, tuple, float]:
    """Select a reachable B-plane turn and return the actually used turn angle.

    The exact condition ``|s*t - v_planet| = v_infinity`` gives heliocentric
    speeds on the requested target ray. A reachable root permits exact target
    pointing by raising periapsis above the configured safe minimum.
    """
    excess_speed = _magnitude(incoming_excess)
    incoming_direction = _normalize(incoming_excess)
    target_direction = _normalize(target_direction)
    target_projection = _dot(target_direction, planet_velocity)
    discriminant = target_projection**2 - (_dot(planet_velocity, planet_velocity) - excess_speed**2)
    exact_candidates: list[tuple[float, float, tuple, tuple]] = []
    if discriminant >= 0:
        root = sqrt(discriminant)
        for heliocentric_speed in (target_projection - root, target_projection + root):
            if heliocentric_speed <= 0:
                continue
            candidate_velocity = tuple(component * heliocentric_speed for component in target_direction)
            candidate_excess = _subtract(candidate_velocity, planet_velocity)
            candidate_turn = acos(max(-1.0, min(1.0, _dot(incoming_direction, _normalize(candidate_excess)))))
            if candidate_turn <= turn_angle + 1e-10:
                exact_candidates.append((heliocentric_speed, candidate_turn, candidate_excess, candidate_velocity))
    if exact_candidates:
        chosen = (max if flyby_mode == "acceleration" else min)(
            exact_candidates,
            key=lambda candidate: candidate[0] if flyby_mode == "acceleration" else candidate[1],
        )
        return chosen[2], chosen[3], chosen[1]

    if flyby_mode == "observation":
        outgoing_excess = _rotate_towards(incoming_excess, target_direction, turn_angle)
        used_turn = acos(max(-1.0, min(1.0, _dot(incoming_direction, _normalize(outgoing_excess)))))
        return outgoing_excess, _add(planet_velocity, outgoing_excess), used_turn

    planet_direction = _normalize(planet_velocity)
    candidates: list[tuple[float, tuple, tuple]] = []
    for target_weight_index in range(21):
        target_weight = target_weight_index / 20
        desired = _normalize(tuple(
            planet_direction[index] * (1 - target_weight) + target_direction[index] * target_weight
            for index in range(3)
        ))
        candidate_excess = _rotate_towards(incoming_excess, desired, turn_angle)
        candidate_velocity = _add(planet_velocity, candidate_excess)
        alignment = acos(max(-1.0, min(1.0, _dot(_normalize(candidate_velocity), target_direction))))
        # Radians and km/s are intentionally combined as a mission-design
        # objective: target course dominates, energy breaks near ties.
        score = alignment * 24 - _magnitude(candidate_velocity)
        candidates.append((score, candidate_excess, candidate_velocity))
    _, outgoing_excess, outgoing_velocity = min(candidates, key=lambda candidate: candidate[0])
    used_turn = acos(max(-1.0, min(1.0, _dot(incoming_direction, _normalize(outgoing_excess)))))
    return outgoing_excess, outgoing_velocity, used_turn


def _planet_velocity(ephemeris: tuple, days_j2000: float) -> tuple:
    return _planet_state_at(ephemeris, days_j2000)[1]


def _route_uncertainty(trajectory: list[dict], config, waypoint_id: str) -> dict:
    """Propagate Cartesian navigation covariance and create deterministic samples."""
    navigation = KalmanNavigationSystem.create(
        config.position_measurement_noise_km,
        config.velocity_measurement_noise_km_s,
    )
    navigation_cycle = max(60.0, config.navigation_cycle_hours * 3_600.0)
    since_update = 0.0
    previous_day = float(trajectory[0]["elapsedDays"])
    covariance: list[dict] = []

    for point in trajectory:
        elapsed_day = float(point["elapsedDays"])
        remaining = max(0.0, (elapsed_day - previous_day) * DAY_SECONDS)
        while remaining > 1e-9:
            step = remaining if not config.kalman_enabled else min(remaining, navigation_cycle - since_update)
            for axis in navigation.axes:
                axis.predict(step, acceleration_sigma_km_s2=1e-9)
            remaining -= step
            since_update += step
            if config.kalman_enabled and since_update >= navigation_cycle - 1e-6:
                for axis in navigation.axes:
                    axis.update(
                        config.position_measurement_noise_km,
                        config.velocity_measurement_noise_km_s,
                    )
                navigation.cycles += 1
                since_update = 0.0
        position_sigma = navigation.position_uncertainty_km
        velocity_sigma = navigation.velocity_uncertainty_km_s
        covariance.append({
            "elapsedDays": elapsed_day,
            "positionSigmaKm": position_sigma,
            "velocitySigmaKmS": velocity_sigma,
            "radius95Km": CHI_SQUARE_RADIUS_95_3D * position_sigma,
        })
        previous_day = elapsed_day

    generator = random.Random(f"{config.start_date}:{waypoint_id}:kalman-route-v1")
    samples: list[dict] = []
    correlation = 0.94
    innovation_scale = sqrt(1 - correlation**2)
    for sample_index in range(14):
        normalized_error = [generator.gauss(0.0, 1.0) for _ in range(3)]
        sample_points: list[dict] = []
        for nominal, uncertainty in zip(trajectory, covariance):
            for axis_index in range(3):
                normalized_error[axis_index] = (
                    correlation * normalized_error[axis_index]
                    + innovation_scale * generator.gauss(0.0, 1.0)
                )
            per_axis_sigma = uncertainty["positionSigmaKm"] / sqrt(3)
            perturbed = [
                nominal["positionKm"][axis_index] + normalized_error[axis_index] * per_axis_sigma
                for axis_index in range(3)
            ]
            sample_points.append({
                "elapsedDays": nominal["elapsedDays"],
                "positionKm": perturbed,
            })
        samples.append({"id": sample_index + 1, "trajectory": sample_points})

    return {
        "confidenceLevelPct": 95.0,
        "model": "3D-Kalman-Kovarianz + korrelierte deterministische Stichproben",
        "kalmanEnabled": config.kalman_enabled,
        "navigationCycleHours": config.navigation_cycle_hours,
        "positionMeasurementNoiseKm": config.position_measurement_noise_km,
        "velocityMeasurementNoiseKmS": config.velocity_measurement_noise_km_s,
        "covariance": covariance,
        "samples": samples,
        "summary": {
            "startRadius95Km": covariance[0]["radius95Km"],
            "waypointRadius95Km": covariance[-1]["radius95Km"],
            "maximumRadius95Km": max(point["radius95Km"] for point in covariance),
            "navigationCycles": navigation.cycles,
        },
    }


def _simulate_waypoint_route_legacy(values: dict | None, include_mission_result: bool = False) -> dict:
    values = values or {}
    mission_values = dict(values.get("mission") or {})
    mission_values["missionYears"] = 1
    result = simulate_mission(mission_values)
    burn_point = next(point for point in result.trajectory if point.phase.value == "SOLAR_OBERTH_BURN")
    waypoint_id = str(values.get("waypointId") or "jupiter")
    ephemeris = next((item for item in PLANET_EPHEMERIDES if item[0] == waypoint_id), None)
    planet_row = next((row for row in PLANET_DATA if row[0] == waypoint_id), None)
    if ephemeris is None or planet_row is None or waypoint_id == "earth":
        raise ValueError("Dieser planetare Wegpunkt wird nicht unterstützt.")
    encounter_day = float(values.get("encounterDay", 1500.0))
    altitude_km = float(values.get("flybyAltitudeKm", 100_000.0))
    flyby_mode = str(values.get("flybyMode") or "acceleration")
    if flyby_mode not in {"acceleration", "observation"}:
        raise ValueError("Unbekanntes Vorbeiflugprofil.")
    if encounter_day <= burn_point.elapsed_days + 5:
        raise ValueError("Der Begegnungstag muss mindestens fünf Tage nach dem Perihel liegen.")
    radius_km = planet_row[3] / 1_000
    if altitude_km <= 0:
        raise ValueError("Die Vorbeiflughöhe muss positiv sein.")

    epoch_days = _mission_epoch_days(result.config.start_date)
    encounter_days_j2000 = epoch_days + encounter_day
    planet_position = _planet_position_at(ephemeris, encounter_days_j2000)
    flight_seconds = (encounter_day - burn_point.elapsed_days) * DAY_SECONDS
    departure_velocity, arrival_velocity = _lambert(
        burn_point.position_km, planet_position, flight_seconds, burn_point.velocity_km_s,
    )
    injection_delta_v = _magnitude(_subtract(departure_velocity, burn_point.velocity_km_s))
    planet_velocity = _planet_velocity(ephemeris, encounter_days_j2000)
    incoming_excess = _subtract(arrival_velocity, planet_velocity)
    excess_speed = _magnitude(incoming_excess)
    planet_mu = G_KM3_KG_S2 * planet_row[2]
    minimum_periapsis_radius = radius_km + altitude_km
    periapsis_radius = minimum_periapsis_radius
    turn_angle = 2 * asin(1 / (1 + periapsis_radius * excess_speed**2 / planet_mu))

    target_ra = float(values.get("targetRightAscensionDeg", 217.43)) * pi / 180
    target_dec = float(values.get("targetDeclinationDeg", -62.68)) * pi / 180
    obliquity = 23.43928 * pi / 180
    equatorial_x = cos(target_dec) * cos(target_ra)
    equatorial_y = cos(target_dec) * sin(target_ra)
    equatorial_z = sin(target_dec)
    target_direction = (
        equatorial_x,
        equatorial_y * cos(obliquity) + equatorial_z * sin(obliquity),
        -equatorial_y * sin(obliquity) + equatorial_z * cos(obliquity),
    )
    outgoing_excess, outgoing_velocity, turn_angle = _select_flyby_outgoing(
        incoming_excess,
        planet_velocity,
        target_direction,
        turn_angle,
        flyby_mode,
    )
    outgoing_direction = _normalize(outgoing_velocity)
    target_alignment = acos(max(-1.0, min(1.0, _dot(outgoing_direction, _normalize(target_direction)))))
    periapsis_speed = sqrt(excess_speed**2 + 2 * planet_mu / periapsis_radius)
    observation_radius = max(periapsis_radius, radius_km + 1_000_000.0)
    observation_half_chord = sqrt(max(0.0, observation_radius**2 - periapsis_radius**2))
    observation_window_hours = 2 * observation_half_chord / max(excess_speed, 1e-9) / 3_600

    sample_count = 180
    sample_step = flight_seconds / sample_count
    sample_state = (burn_point.position_km, departure_velocity)
    lambert_trajectory = [{
        "elapsedDays": burn_point.elapsed_days,
        "positionKm": list(sample_state[0]),
    }]
    for index in range(sample_count):
        sample_state = _rk4(sample_state, sample_step)
        lambert_trajectory.append({
            "elapsedDays": burn_point.elapsed_days + (index + 1) * sample_step / DAY_SECONDS,
            "positionKm": list(sample_state[0]),
        })

    inbound = [point for point in result.trajectory if point.elapsed_days <= burn_point.elapsed_days]
    # Keep the complete solar-approach sampling. Decimating this high-curvature
    # segment to roughly 70 points produces visible polygon corners even when
    # the propagated state itself is smooth.
    inbound_route: list[dict] = []
    for point in inbound:
        state = {
            "elapsedDays": point.elapsed_days,
            "positionKm": list(point.position_km),
        }
        if inbound_route:
            previous = inbound_route[-1]
            same_time = abs(state["elapsedDays"] - previous["elapsedDays"]) <= 1e-12
            same_position = _magnitude(_subtract(
                tuple(state["positionKm"]), tuple(previous["positionKm"]),
            )) <= 1e-6
            if same_time and same_position:
                continue
        inbound_route.append(state)
    if inbound and (not inbound_route or inbound_route[-1]["elapsedDays"] != inbound[-1].elapsed_days):
        inbound_route.append({
            "elapsedDays": inbound[-1].elapsed_days,
            "positionKm": list(inbound[-1].position_km),
        })
    trajectory = [*inbound_route, *lambert_trajectory[1:]]
    requested_mission_years = float((values.get("mission") or {}).get("missionYears", 10.0))
    outbound_preview_days = min(5 * 365.25, max(2 * 365.25, requested_mission_years * 365.25))
    outbound_sample_count = 300
    outbound_step = outbound_preview_days * DAY_SECONDS / outbound_sample_count
    outbound_state = (planet_position, outgoing_velocity)
    post_flyby_trajectory = [{
        "elapsedDays": encounter_day,
        "positionKm": list(outbound_state[0]),
    }]
    for index in range(outbound_sample_count):
        outbound_state = _rk4(outbound_state, outbound_step)
        post_flyby_trajectory.append({
            "elapsedDays": encounter_day + (index + 1) * outbound_step / DAY_SECONDS,
            "positionKm": list(outbound_state[0]),
        })
    trajectory.extend(post_flyby_trajectory[1:])
    course_change = acos(max(-1.0, min(1.0, _dot(_normalize(arrival_velocity), outgoing_direction))))

    payload = {
        "waypoint": {
            "id": waypoint_id,
            "name": planet_row[1],
            "encounterDay": encounter_day,
            "trajectoryIndex": len(inbound_route) + len(lambert_trajectory) - 2,
            "flybyAltitudeKm": altitude_km,
            "positionKm": list(planet_position),
        },
        "trajectory": trajectory,
        "uncertainty": _route_uncertainty(trajectory, result.config, waypoint_id),
        "outgoingDirection": list(outgoing_direction),
        "flybyGeometry": {
            "incomingExcessDirection": list(_normalize(incoming_excess)),
            "outgoingExcessDirection": list(_normalize(outgoing_excess)),
            "incomingHeliocentricDirection": list(_normalize(arrival_velocity)),
            "outgoingHeliocentricDirection": list(outgoing_direction),
            "periapsisRadiusKm": periapsis_radius,
            "planetRadiusKm": radius_km,
            "hyperbolaEccentricity": 1 + periapsis_radius * excess_speed**2 / planet_mu,
            "semiMajorAxisMagnitudeKm": planet_mu / max(excess_speed**2, 1e-12),
        },
        "summary": {
            "flybyMode": flyby_mode,
            "requiredInjectionDeltaVKmS": injection_delta_v,
            "incomingExcessSpeedKmS": excess_speed,
            "turnAngleDeg": turn_angle * 180 / pi,
            "heliocentricSpeedBeforeKmS": _magnitude(arrival_velocity),
            "heliocentricSpeedAfterKmS": _magnitude(outgoing_velocity),
            "speedGainKmS": _magnitude(outgoing_velocity) - _magnitude(arrival_velocity),
            "courseChangeDeg": course_change * 180 / pi,
            "periapsisSpeedKmS": periapsis_speed,
            "observationWindowHours": observation_window_hours,
            "targetAlignmentDeg": target_alignment * 180 / pi,
            "feasibleWithConfiguredBurn": injection_delta_v <= result.config.oberth_delta_v_km_s,
            "model": "Lambert + B-plane target/energy search + patched conics + post-flyby propagation",
        },
    }
    if include_mission_result:
        payload["mission"] = result.to_dict()
    return payload


def _hyperbola_relative_state(
    anomaly: float,
    semi_major_axis: float,
    eccentricity: float,
    planet_mu: float,
    axis_x: tuple,
    axis_y: tuple,
) -> tuple[tuple, tuple, float]:
    root = sqrt(max(0.0, eccentricity**2 - 1))
    x = semi_major_axis * (eccentricity - cosh(anomaly))
    y = semi_major_axis * root * sinh(anomaly)
    denominator = max(eccentricity * cosh(anomaly) - 1, 1e-12)
    anomaly_rate = sqrt(planet_mu / semi_major_axis**3) / denominator
    velocity_x = -semi_major_axis * sinh(anomaly) * anomaly_rate
    velocity_y = semi_major_axis * root * cosh(anomaly) * anomaly_rate
    position = tuple(axis_x[index] * x + axis_y[index] * y for index in range(3))
    velocity = tuple(axis_x[index] * velocity_x + axis_y[index] * velocity_y for index in range(3))
    relative_seconds = sqrt(semi_major_axis**3 / planet_mu) * (eccentricity * sinh(anomaly) - anomaly)
    return position, velocity, relative_seconds


def _legacy_waypoint_route(values: dict | None, include_mission_result: bool = False) -> dict:
    """Calculate state-continuous heliocentric/SOI/hyperbolic route segments."""
    values = values or {}
    requested_mission_values = dict(values.get("mission") or {})
    mission_values = dict(requested_mission_values)
    mission_values["missionYears"] = 1
    result = simulate_mission(mission_values)
    burn_point = next(point for point in result.trajectory if point.phase.value == "SOLAR_OBERTH_BURN")
    pre_burn_event = next(event for event in result.events if event.name == "SOLAR_OBERTH_BURN_STARTED")
    pre_burn_velocity = pre_burn_event.velocity_km_s
    available_oberth_delta_v = result.config.oberth_delta_v_km_s
    desired_solar_exit_raw = values.get("desiredSolarExitSpeedKmS")
    desired_solar_exit_speed = (
        float(desired_solar_exit_raw) if desired_solar_exit_raw is not None else None
    )
    if desired_solar_exit_speed is not None and not 1.0 <= desired_solar_exit_speed <= 1_000.0:
        raise ValueError("Die Zielgeschwindigkeit am 1-AE-Sonnenaustritt muss zwischen 1 und 1.000 km/s liegen.")
    waypoint_id = str(values.get("waypointId") or "jupiter")
    ephemeris = next((item for item in PLANET_EPHEMERIDES if item[0] == waypoint_id), None)
    planet_row = next((row for row in PLANET_DATA if row[0] == waypoint_id), None)
    if ephemeris is None or planet_row is None or waypoint_id == "earth":
        raise ValueError("Dieser planetare Wegpunkt wird nicht unterstützt.")

    periapsis_day = float(values.get("encounterDay", 730.0))
    altitude_km = float(values.get("flybyAltitudeKm", 100_000.0))
    flyby_mode = str(values.get("flybyMode") or "acceleration")
    high_fidelity_raw = values.get("highFidelityNBody", False)
    if not isinstance(high_fidelity_raw, bool):
        raise ValueError("highFidelityNBody muss ein boolescher Wert sein.")
    high_fidelity_n_body = high_fidelity_raw
    aimpoint_values = values.get("flybyAimpoint") or {}
    entry_corridor = _parse_entry_corridor(values.get("entryCorridor") or {})

    aimpoint_enabled = bool(aimpoint_values.get("enabled", False))
    aimpoint_clock_deg = float(aimpoint_values.get("clockAngleDeg", 0.0))
    aimpoint_screen_radius = float(aimpoint_values.get("screenRadiusNorm", 1.0))
    aimpoint_role = str(aimpoint_values.get("role", "periapsis")).strip().lower()
    if aimpoint_role not in {"entry", "periapsis", "exit", "periapsis_point"}:
        aimpoint_role = "periapsis"
    if aimpoint_role == "periapsis_point":
        aimpoint_role = "periapsis"
    aimpoint_altitude_km = (
        float(aimpoint_values.get("altitudeKm", altitude_km))
        if aimpoint_enabled
        else altitude_km
    )
    
    if flyby_mode not in {"acceleration", "observation"}:
        raise ValueError("Unbekanntes Vorbeiflugprofil.")
    if periapsis_day <= burn_point.elapsed_days + 5:
        raise ValueError("Der Begegnungstag muss mindestens fünf Tage nach dem Perihel liegen.")
    if altitude_km <= 0:
        raise ValueError("Die Vorbeiflughöhe muss positiv sein.")
    if aimpoint_enabled and aimpoint_altitude_km < 0:
        raise ValueError("Die Aimpoint-Höhe muss nicht-negativ sein.")
    if aimpoint_enabled and entry_corridor["enabled"]:
        raise ValueError(
            "Ein einzelner Flyby-Aimpoint und ein Eintrittskorridor können "
            "nicht gleichzeitig aktiv sein."
        )

    radius_km = planet_row[3] / 1_000
    minimum_periapsis_radius = radius_km + altitude_km
    if aimpoint_enabled and aimpoint_role == "periapsis":
        minimum_periapsis_radius = max(minimum_periapsis_radius, radius_km + aimpoint_altitude_km)
    periapsis_radius = minimum_periapsis_radius
    
    desired_periapsis_direction = None
    desired_periapsis_relative_position = None
    aimpoint_position_relative = None
    aimpoint_position = None
    aimpoint_alignment_after_rad = None
    aimpoint_alignment_before_rad = None

    if aimpoint_enabled:
        desired_periapsis_direction = _aimpoint_direction_from_clock(
            clock_angle_deg=aimpoint_clock_deg,
            screen_radius_norm=aimpoint_screen_radius,
            view_direction=(0.0, 0.0, 1.0),
            screen_up=(0.0, 1.0, 0.0),
            screen_right=(1.0, 0.0, 0.0),
        )
        desired_periapsis_relative_position = tuple(
            component * (radius_km + aimpoint_altitude_km)
            for component in desired_periapsis_direction
        )
    
    planet_mu = G_KM3_KG_S2 * planet_row[2]
    sun_mass_kg = MU_SUN / G_KM3_KG_S2
    planet_orbit_radius_km = ephemeris[2] * AU_KM
    sphere_of_influence_km = planet_orbit_radius_km * (planet_row[2] / sun_mass_kg) ** (2 / 5)
    epoch_days = _mission_epoch_days(result.config.start_date)

    target_ra = float(values.get("targetRightAscensionDeg", 217.43)) * pi / 180
    target_dec = float(values.get("targetDeclinationDeg", -62.68)) * pi / 180
    obliquity = 23.43928 * pi / 180
    equatorial_x = cos(target_dec) * cos(target_ra)
    equatorial_y = cos(target_dec) * sin(target_ra)
    equatorial_z = sin(target_dec)
    target_direction = _normalize((
        equatorial_x,
        equatorial_y * cos(obliquity) + equatorial_z * sin(obliquity),
        -equatorial_y * sin(obliquity) + equatorial_z * cos(obliquity),
    ))

    entry_day = periapsis_day
    corridor_selection = None
    selected_corridor_direction = None
    if entry_corridor["enabled"]:
        corridor_selection = _select_entry_corridor_target(
            entry_corridor,
            burn_position=burn_point.position_km,
            reference_velocity=burn_point.velocity_km_s,
            planet_position=_planet_position_at(ephemeris, epoch_days + entry_day),
            sphere_of_influence_km=sphere_of_influence_km,
            flight_seconds=(entry_day - burn_point.elapsed_days) * DAY_SECONDS,
        )
        selected_corridor_direction = corridor_selection["direction"]
        entry_position = corridor_selection["position"]
    else:
        entry_position = _planet_position_at(ephemeris, epoch_days + entry_day)
    departure_velocity = burn_point.velocity_km_s
    arrival_velocity = burn_point.velocity_km_s
    outgoing_excess = (0.0, 0.0, 0.0)
    outgoing_velocity_asymptotic = (0.0, 0.0, 0.0)
    axis_x = (1.0, 0.0, 0.0)
    axis_y = (0.0, 1.0, 0.0)
    eccentricity = 1.1
    semi_major_axis = 1.0
    hyperbolic_limit = 1.0
    turn_angle = 0.0
    excess_speed = 0.0
    targeting_planet_velocity = _planet_velocity(ephemeris, epoch_days + periapsis_day)
    bidirectional_solution: dict | None = None

    # Entry time and B-plane orientation are mutually dependent. Iterate the
    # Lambert arrival and planet-centred hyperbola until their SOI state agrees.
    for _ in range(7):
        flight_seconds = (entry_day - burn_point.elapsed_days) * DAY_SECONDS
        departure_velocity, arrival_velocity = _lambert(
            burn_point.position_km,
            entry_position,
            flight_seconds,
            burn_point.velocity_km_s,
        )
        planet_entry_velocity = _planet_velocity(ephemeris, epoch_days + entry_day)
        incoming_excess = _subtract(arrival_velocity, planet_entry_velocity)
        entry_relative_speed = _magnitude(incoming_excess)
        excess_speed = sqrt(max(1e-12, entry_relative_speed**2 - 2 * planet_mu / sphere_of_influence_km))
        incoming_asymptote = tuple(value * excess_speed for value in _normalize(incoming_excess))
        maximum_turn_angle = 2 * asin(1 / (1 + minimum_periapsis_radius * excess_speed**2 / planet_mu))
        bidirectional_solution = _solve_bidirectional_flyby(
            _planet_position_at(ephemeris, epoch_days + periapsis_day),
            targeting_planet_velocity,
            incoming_asymptote,
            target_direction,
            maximum_turn_angle,
            flyby_mode,
        )
        outgoing_excess = bidirectional_solution["outgoingExcess"]
        outgoing_velocity_asymptotic = bidirectional_solution["outgoingVelocity"]
        turn_angle = bidirectional_solution["usedTurnRad"]
        if turn_angle > 1e-9:
            targeted_periapsis = planet_mu / max(excess_speed**2, 1e-12) * (1 / sin(turn_angle / 2) - 1)
            periapsis_radius = max(minimum_periapsis_radius, targeted_periapsis)
        incoming_direction = _normalize(incoming_excess)
        outgoing_direction_relative = _normalize(outgoing_excess)
        axis_x = _normalize(_subtract(incoming_direction, outgoing_direction_relative))
        axis_y = _normalize(_add(incoming_direction, outgoing_direction_relative))
        semi_major_axis = planet_mu / max(excess_speed**2, 1e-12)
        eccentricity = 1 + periapsis_radius / semi_major_axis
        hyperbolic_limit = acosh(max(1.0, (sphere_of_influence_km / semi_major_axis + 1) / eccentricity))
        axis_x, axis_y = _align_hyperbola_entry_velocity(
            axis_x,
            axis_y,
            hyperbolic_limit,
            semi_major_axis,
            eccentricity,
            planet_mu,
            incoming_direction,
        )
        if selected_corridor_direction is not None:
            axis_x, axis_y = _align_hyperbola_aimpoint(
                axis_x,
                axis_y,
                -hyperbolic_limit,
                semi_major_axis,
                eccentricity,
                planet_mu,
                selected_corridor_direction,
            )
        _, _, estimated_exit_seconds = _hyperbola_relative_state(
            hyperbolic_limit, semi_major_axis, eccentricity, planet_mu, axis_x, axis_y,
        )
        targeting_planet_velocity = _planet_velocity(
            ephemeris, epoch_days + periapsis_day + estimated_exit_seconds / DAY_SECONDS,
        )
        relative_entry, _, entry_relative_seconds = _hyperbola_relative_state(
            -hyperbolic_limit,
            semi_major_axis,
            eccentricity,
            planet_mu,
            axis_x,
            axis_y,
        )
        next_entry_day = periapsis_day + entry_relative_seconds / DAY_SECONDS
        planet_entry_position = _planet_position_at(ephemeris, epoch_days + next_entry_day)
        next_entry_position = _add(planet_entry_position, relative_entry)
        if abs(next_entry_day - entry_day) < 1e-5 and _magnitude(_subtract(next_entry_position, entry_position)) < 1.0:
            entry_day, entry_position = next_entry_day, next_entry_position
            break
        entry_day, entry_position = next_entry_day, next_entry_position

    # Re-solve once at the converged SOI boundary and rebuild the final frame.
    flight_seconds = (entry_day - burn_point.elapsed_days) * DAY_SECONDS
    departure_velocity, arrival_velocity, lambert_selection = _select_lambert(
        burn_point.position_km, entry_position, flight_seconds, burn_point.velocity_km_s,
    )
    planet_entry_velocity = _planet_velocity(ephemeris, epoch_days + entry_day)
    incoming_excess = _subtract(arrival_velocity, planet_entry_velocity)
    entry_relative_speed = _magnitude(incoming_excess)
    excess_speed = sqrt(max(1e-12, entry_relative_speed**2 - 2 * planet_mu / sphere_of_influence_km))
    incoming_asymptote = tuple(value * excess_speed for value in _normalize(incoming_excess))
    maximum_turn_angle = 2 * asin(1 / (1 + minimum_periapsis_radius * excess_speed**2 / planet_mu))
    bidirectional_solution = _solve_bidirectional_flyby(
        _planet_position_at(ephemeris, epoch_days + periapsis_day),
        targeting_planet_velocity,
        incoming_asymptote,
        target_direction,
        maximum_turn_angle,
        flyby_mode,
    )
    outgoing_excess = bidirectional_solution["outgoingExcess"]
    outgoing_velocity_asymptotic = bidirectional_solution["outgoingVelocity"]
    turn_angle = bidirectional_solution["usedTurnRad"]
    if turn_angle > 1e-9:
        targeted_periapsis = planet_mu / max(excess_speed**2, 1e-12) * (1 / sin(turn_angle / 2) - 1)
        periapsis_radius = max(minimum_periapsis_radius, targeted_periapsis)
    incoming_direction = _normalize(incoming_excess)
    outgoing_direction_relative = _normalize(outgoing_excess)
    axis_x = _normalize(_subtract(incoming_direction, outgoing_direction_relative))
    axis_y = _normalize(_add(incoming_direction, outgoing_direction_relative))
    semi_major_axis = planet_mu / max(excess_speed**2, 1e-12)
    eccentricity = 1 + periapsis_radius / semi_major_axis
    hyperbolic_limit = acosh(max(1.0, (sphere_of_influence_km / semi_major_axis + 1) / eccentricity))
    axis_x, axis_y = _align_hyperbola_entry_velocity(
        axis_x,
        axis_y,
        hyperbolic_limit,
        semi_major_axis,
        eccentricity,
        planet_mu,
        incoming_direction,
    )
    if selected_corridor_direction is not None:
        axis_x, axis_y = _align_hyperbola_aimpoint(
            axis_x,
            axis_y,
            -hyperbolic_limit,
            semi_major_axis,
            eccentricity,
            planet_mu,
            selected_corridor_direction,
        )
    if aimpoint_enabled and desired_periapsis_direction is not None:
        aimpoint_anomaly = (
            -hyperbolic_limit
            if aimpoint_role == "entry"
            else hyperbolic_limit
            if aimpoint_role == "exit"
            else 0.0
        )
        aimpoint_before_position, _, _ = _hyperbola_relative_state(
            aimpoint_anomaly,
            semi_major_axis,
            eccentricity,
            planet_mu,
            axis_x,
            axis_y,
        )
        aimpoint_alignment_before_rad = acos(max(-1.0, min(1.0, _dot(
            _normalize(aimpoint_before_position),
            desired_periapsis_direction,
        ))))

        axis_x, axis_y = _align_hyperbola_aimpoint(
            axis_x,
            axis_y,
            aimpoint_anomaly,
            semi_major_axis,
            eccentricity,
            planet_mu,
            desired_periapsis_direction,
        )
    hyperbola_points: list[dict] = []
    relative_hyperbola_points: list[dict] = []
    exit_relative_position = (0.0, 0.0, 0.0)
    exit_relative_velocity = (0.0, 0.0, 0.0)
    exit_day = periapsis_day
    flyby_sample_count = 300
    for index in range(flyby_sample_count + 1):
        anomaly = -hyperbolic_limit + index * 2 * hyperbolic_limit / flyby_sample_count
        relative_position, relative_velocity, relative_seconds = _hyperbola_relative_state(
            anomaly,
            semi_major_axis,
            eccentricity,
            planet_mu,
            axis_x,
            axis_y,
        )
        point_day = periapsis_day + relative_seconds / DAY_SECONDS
        planet_position = _planet_position_at(ephemeris, epoch_days + point_day)
        hyperbola_points.append({
            "elapsedDays": point_day,
            "positionKm": list(_add(planet_position, relative_position)),
        })
        relative_hyperbola_points.append({
            "elapsedDays": point_day,
            "anomaly": anomaly,
            "positionKm": list(relative_position),
            "velocityKmS": list(relative_velocity),
        })
        if index == flyby_sample_count:
            exit_relative_position = relative_position
            exit_relative_velocity = relative_velocity
            exit_day = point_day

    # Report the direction produced by the final rotated conic frame, not the
    # pre-alignment B-plane candidate.  They are close, but only this vector is
    # exactly consistent with the rendered and propagated SOI-exit state.
    outgoing_direction_relative = _normalize(exit_relative_velocity)

    planet_periapsis_position = _planet_position_at(ephemeris, epoch_days + periapsis_day)
    if aimpoint_enabled and desired_periapsis_direction is not None:
        aimpoint_role_anomaly = (
            -hyperbolic_limit
            if aimpoint_role == "entry"
            else hyperbolic_limit
            if aimpoint_role == "exit"
            else 0.0
        )
        aimpoint_position_relative, _, _ = _hyperbola_relative_state(
            aimpoint_role_anomaly,
            semi_major_axis,
            eccentricity,
            planet_mu,
            axis_x,
            axis_y,
        )
        aimpoint_alignment_after_rad = acos(max(-1.0, min(1.0, _dot(
            _normalize(aimpoint_position_relative),
            desired_periapsis_direction,
        ))))
        aimpoint_position = _add(planet_periapsis_position, aimpoint_position_relative)
    hyperbola_entry_position = tuple(hyperbola_points[0]["positionKm"])
    _, hyperbola_entry_relative_velocity, _ = _hyperbola_relative_state(
        -hyperbolic_limit,
        semi_major_axis,
        eccentricity,
        planet_mu,
        axis_x,
        axis_y,
    )
    hyperbola_entry_velocity = _add(planet_entry_velocity, hyperbola_entry_relative_velocity)
    entry_position_patch_residual = _magnitude(_subtract(entry_position, hyperbola_entry_position))
    entry_velocity_patch_residual = _magnitude(_subtract(arrival_velocity, hyperbola_entry_velocity))
    burn_to_lambert_direction_change = acos(max(-1.0, min(1.0, _dot(
        _normalize(pre_burn_velocity), _normalize(departure_velocity),
    )))) * 180 / pi
    lambert_to_hyperbola_direction_change = acos(max(-1.0, min(1.0, _dot(
        _normalize(arrival_velocity), _normalize(hyperbola_entry_velocity),
    )))) * 180 / pi
    planet_exit_position = _planet_position_at(ephemeris, epoch_days + exit_day)
    planet_exit_velocity = _planet_velocity(ephemeris, epoch_days + exit_day)
    exit_position = _add(planet_exit_position, exit_relative_position)
    gravity_exit_velocity = _add(planet_exit_velocity, exit_relative_velocity)
    gravity_only_direction = _normalize(gravity_exit_velocity)
    passive_asymptote = _solar_asymptote_direction(exit_position, gravity_exit_velocity)
    passive_alignment = (
        acos(max(-1.0, min(1.0, _dot(passive_asymptote, target_direction))))
        if passive_asymptote is not None
        else pi
    )
    solar_escape_speed = sqrt(2 * MU_SUN / max(_magnitude(exit_position), 1.0))
    if passive_asymptote is not None and passive_alignment <= 0.01 * pi / 180:
        # The backward branch and forward branch close at Jupiter without an
        # artificial velocity discontinuity.  Jupiter alone supplies the
        # speed and steering in this case.
        target_solution = {
            "velocity": gravity_exit_velocity,
            "injectionDirection": _normalize(gravity_exit_velocity),
            "asymptoteDirection": passive_asymptote,
            "departureSpeedKmS": _magnitude(gravity_exit_velocity),
            "escapeSpeedKmS": solar_escape_speed,
            "correctionDeltaVKmS": 0.0,
            "alignmentRad": passive_alignment,
            "iterations": bidirectional_solution["backwardIterations"],
            "converged": bidirectional_solution["backwardConverged"],
        }
    else:
        # Preserve a separately budgeted correction as an explicit fallback;
        # it is never hidden inside the gravity-assist geometry.
        target_solution = _solve_solar_target_injection(exit_position, gravity_exit_velocity, target_direction)
    target_departure_speed = target_solution["departureSpeedKmS"]
    target_injection_direction = target_solution["injectionDirection"]
    target_correction_delta_v = target_solution["correctionDeltaVKmS"]
    target_asymptote_alignment = target_solution["alignmentRad"]
    targeting_iterations = target_solution["iterations"]
    targeting_converged = target_solution["converged"]
    outgoing_direction = target_solution["asymptoteDirection"]
    target_injection_applied = (
        target_correction_delta_v > 1e-9
        and target_correction_delta_v <= available_oberth_delta_v
    )
    exit_velocity = target_solution["velocity"] if target_injection_applied else gravity_exit_velocity
    actual_solar_asymptote = _solar_asymptote_direction(exit_position, exit_velocity)
    actual_target_alignment = (
        acos(max(-1.0, min(1.0, _dot(actual_solar_asymptote, target_direction))))
        if actual_solar_asymptote is not None
        else None
    )
    passive_targeting = target_correction_delta_v <= 1e-9 and passive_alignment <= 0.01 * pi / 180

    inbound = [point for point in result.trajectory if point.elapsed_days <= burn_point.elapsed_days]
    # The active waypoint solver must retain the propagated samples around the
    # solar Oberth arc.  Decimating this segment to ~70 vertices makes the
    # physically smooth state history look like a chain of sharp manoeuvres.
    inbound_route: list[dict] = []
    for point in inbound:
        state = {
            "elapsedDays": point.elapsed_days,
            "positionKm": list(point.position_km),
        }
        if inbound_route:
            previous = inbound_route[-1]
            same_time = abs(state["elapsedDays"] - previous["elapsedDays"]) <= 1e-12
            same_position = _magnitude(_subtract(
                tuple(state["positionKm"]), tuple(previous["positionKm"]),
            )) <= 1e-6
            if same_time and same_position:
                continue
        inbound_route.append(state)
    if inbound and (not inbound_route or inbound_route[-1]["elapsedDays"] != inbound[-1].elapsed_days):
        inbound_route.append({"elapsedDays": inbound[-1].elapsed_days, "positionKm": list(inbound[-1].position_km)})

    lambert_sample_count = 360
    lambert_trajectory, propagated_lambert_position, propagated_lambert_velocity = _propagate_lambert_segment(
        burn_point.position_km,
        departure_velocity,
        burn_point.elapsed_days,
        flight_seconds,
        lambert_sample_count,
    )
    lambert_propagation_position_residual = _magnitude(_subtract(propagated_lambert_position, hyperbola_entry_position))
    lambert_propagation_velocity_residual = _magnitude(_subtract(propagated_lambert_velocity, arrival_velocity))
    # Use the exact same boundary point in both adjacent segments. The velocity
    # residual is reported separately because patched-conic force switching is
    # still an approximation rather than a full simultaneous N-body solve.
    lambert_trajectory[-1] = {"elapsedDays": entry_day, "positionKm": list(hyperbola_entry_position)}

    requested_mission_years = float(requested_mission_values.get("missionYears", 10.0))
    outbound_preview_days = min(5 * 365.25, max(2 * 365.25, requested_mission_years * 365.25))
    outbound_sample_count = 300
    outbound_step = outbound_preview_days * DAY_SECONDS / outbound_sample_count
    outbound_state = (exit_position, exit_velocity)
    post_flyby_trajectory = [{"elapsedDays": exit_day, "positionKm": list(exit_position)}]
    for index in range(outbound_sample_count):
        outbound_state = _rk4(outbound_state, outbound_step)
        post_flyby_trajectory.append({
            "elapsedDays": exit_day + (index + 1) * outbound_step / DAY_SECONDS,
            "positionKm": list(outbound_state[0]),
        })

    target_progress_km = [
        _dot(_subtract(tuple(point["positionKm"]), exit_position), target_direction)
        for point in post_flyby_trajectory
    ]
    progress_rates_km_s = [
        (target_progress_km[index] - target_progress_km[index - 1])
        / max(
            (post_flyby_trajectory[index]["elapsedDays"] - post_flyby_trajectory[index - 1]["elapsedDays"])
            * DAY_SECONDS,
            1e-9,
        )
        for index in range(1, len(post_flyby_trajectory))
    ]
    minimum_target_progress_rate = min(progress_rates_km_s, default=0.0)
    target_progress_monotonic = minimum_target_progress_rate >= -1e-6

    trajectory = list(inbound_route)
    inbound_end = len(trajectory) - 1
    trajectory.extend(lambert_trajectory[1:])
    lambert_end = len(trajectory) - 1
    trajectory.extend(hyperbola_points[1:])
    flyby_end = len(trajectory) - 1
    periapsis_index = lambert_end + flyby_sample_count // 2
    trajectory.extend(post_flyby_trajectory[1:])
    post_end = len(trajectory) - 1
    # Keep the inertial state and the co-moving planet-relative state on every
    # sample. The renderer can then apply one continuous focus transform to
    # the complete route instead of stitching a second, enlarged hyperbola to
    # a heliocentric line.
    for point in trajectory:
        planet_position_at_point = _planet_position_at(
            ephemeris,
            epoch_days + float(point["elapsedDays"]),
        )
        point["waypointPositionKm"] = list(planet_position_at_point)
        point["waypointRelativePositionKm"] = list(_subtract(
            tuple(point["positionKm"]),
            planet_position_at_point,
        ))

    # The solar manoeuvre is one vector change from the propagated pre-burn
    # state to the Lambert departure state.  Comparing against an already
    # burned nominal state would count the configured Oberth impulse twice.
    injection_delta_v = _magnitude(_subtract(departure_velocity, pre_burn_velocity))
    solar_exit_speed = _solar_speed_at_radius(burn_point.position_km, departure_velocity)
    solar_speed_residual = (
        abs(solar_exit_speed - desired_solar_exit_speed)
        if desired_solar_exit_speed is not None
        else 0.0
    )
    solar_speed_tolerance = (
        max(0.25, desired_solar_exit_speed * 0.005)
        if desired_solar_exit_speed is not None
        else float("inf")
    )
    solar_speed_boundary_reached = solar_speed_residual <= solar_speed_tolerance
    # Calendar changes can rotate the transfer geometry, but they cannot add
    # orbital energy.  Publish an analytical upper bound so the optimizer and
    # UI can distinguish an unfortunate date from an insufficient burn.
    burn_radius = max(_magnitude(burn_point.position_km), 1.0)
    pre_burn_speed = _magnitude(pre_burn_velocity)
    maximum_perihelion_speed = pre_burn_speed + available_oberth_delta_v
    maximum_energy_velocity = tuple(
        component * maximum_perihelion_speed
        for component in _normalize(pre_burn_velocity)
    )
    maximum_exit_speed_with_available_burn = _solar_speed_at_radius(
        burn_point.position_km,
        maximum_energy_velocity,
    )
    if desired_solar_exit_speed is None:
        minimum_oberth_delta_v = 0.0
        additional_oberth_delta_v = 0.0
        solar_energy_reachable = True
    else:
        required_perihelion_speed = sqrt(max(
            0.0,
            desired_solar_exit_speed**2
            + 2 * MU_SUN * (1 / burn_radius - 1 / AU_KM),
        ))
        minimum_oberth_delta_v = max(0.0, required_perihelion_speed - pre_burn_speed)
        additional_oberth_delta_v = max(
            0.0,
            minimum_oberth_delta_v - available_oberth_delta_v,
        )
        solar_energy_reachable = (
            desired_solar_exit_speed
            <= maximum_exit_speed_with_available_burn + solar_speed_tolerance
        )
    burn_datetime = datetime.fromisoformat(result.config.start_date) + timedelta(days=burn_point.elapsed_days)
    target_alignment = target_asymptote_alignment
    course_change = acos(max(-1.0, min(1.0, _dot(_normalize(arrival_velocity), _normalize(exit_velocity)))))
    entry_relative_position = tuple(relative_hyperbola_points[0]["positionKm"])
    actual_entry_direction = _normalize(entry_relative_position)
    corridor_horizontal_deg, corridor_vertical_deg = _corridor_coordinates_deg(
        actual_entry_direction,
        entry_corridor["centerDirection"],
        entry_corridor["rotationDeg"],
    )
    entry_inside_corridor = (
        not entry_corridor["enabled"]
        or (
            abs(corridor_horizontal_deg) <= entry_corridor["horizontalHalfAngleDeg"] + 1e-6
            and abs(corridor_vertical_deg) <= entry_corridor["verticalHalfAngleDeg"] + 1e-6
        )
    )
    entry_corridor_payload = {
        "enabled": entry_corridor["enabled"],
        "surface": "planetary sphere of influence",
        "selectionStrategy": "minimum departure injection delta-v on a 3x3 angular grid",
        "centerDirection": list(entry_corridor["centerDirection"]),
        "horizontalHalfAngleDeg": entry_corridor["horizontalHalfAngleDeg"],
        "verticalHalfAngleDeg": entry_corridor["verticalHalfAngleDeg"],
        "rotationDeg": entry_corridor["rotationDeg"],
        "selectedDirection": (
            list(selected_corridor_direction)
            if selected_corridor_direction is not None
            else None
        ),
        "selectedHorizontalOffsetDeg": (
            corridor_selection["horizontalOffsetDeg"]
            if corridor_selection is not None
            else None
        ),
        "selectedVerticalOffsetDeg": (
            corridor_selection["verticalOffsetDeg"]
            if corridor_selection is not None
            else None
        ),
        "evaluatedTargetCount": (
            corridor_selection["evaluatedTargetCount"]
            if corridor_selection is not None
            else 0
        ),
        "selectedRequiredInjectionDeltaVKmS": (
            corridor_selection["requiredInjectionDeltaVKmS"]
            if corridor_selection is not None
            else None
        ),
        "actualEntryDirection": list(actual_entry_direction),
        "actualHorizontalOffsetDeg": corridor_horizontal_deg,
        "actualVerticalOffsetDeg": corridor_vertical_deg,
        "actualEntryPositionKm": list(hyperbola_entry_position),
        "entryInsideCorridor": entry_inside_corridor,
    }
    periapsis_relative_position = tuple(value * periapsis_radius for value in axis_x)
    flyby_plane_normal = _normalize(_cross(incoming_direction, outgoing_direction_relative))

    def relative_latitude(vector: tuple) -> float:
        return asin(max(-1.0, min(1.0, vector[2] / max(_magnitude(vector), 1e-12)))) * 180 / pi

    entry_latitude = relative_latitude(entry_relative_position)
    periapsis_latitude = relative_latitude(periapsis_relative_position)
    exit_latitude = relative_latitude(exit_relative_position)
    periapsis_speed = sqrt(excess_speed**2 + 2 * planet_mu / periapsis_radius)
    observation_radius = max(periapsis_radius, radius_km + 1_000_000.0)
    observation_half_chord = sqrt(max(0.0, observation_radius**2 - periapsis_radius**2))
    observation_window_hours = 2 * observation_half_chord / max(excess_speed, 1e-9) / 3_600
    heliocentric_speed_before = _magnitude(arrival_velocity)
    heliocentric_speed_after = _magnitude(gravity_exit_velocity)
    aimpoint_warning = None
    if (
        aimpoint_enabled
        and desired_periapsis_direction is not None
        and bidirectional_solution is not None
        and (
            bidirectional_solution["turnClosureResidualRad"] > 1e-10
            or (
                aimpoint_alignment_after_rad is not None
                and aimpoint_alignment_after_rad > 1e-4
            )
        )
    ):
        aimpoint_warning = "Gewählter Aimpoint ist mit aktueller Flyby-Geometrie nicht erreichbar."
    payload_warnings = [aimpoint_warning] if aimpoint_warning else []
    if entry_corridor["enabled"] and not entry_inside_corridor:
        payload_warnings.append(
            "Der berechnete SOI-Eintritt liegt außerhalb des definierten Korridors."
        )

    payload = {
        "startDate": result.config.start_date,
        "totalFlightDays": trajectory[-1]["elapsedDays"],
        "warnings": payload_warnings,
        "entryCorridor": entry_corridor_payload,
        "solarBoundary": {
            "definition": "outbound osculating speed at 1 AU",
            "radiusAu": 1.0,
            "desiredExitSpeedKmS": desired_solar_exit_speed,
            "actualExitSpeedKmS": solar_exit_speed,
            "speedResidualKmS": solar_speed_residual,
            "toleranceKmS": None if desired_solar_exit_speed is None else solar_speed_tolerance,
            "speedBoundaryReached": solar_speed_boundary_reached,
            "availableOberthDeltaVKmS": available_oberth_delta_v,
            "requiredOberthVectorDeltaVKmS": injection_delta_v,
            "maximumExitSpeedWithAvailableBurnKmS": maximum_exit_speed_with_available_burn,
            "minimumOberthDeltaVForDesiredSpeedKmS": minimum_oberth_delta_v,
            "additionalDeltaVRequiredKmS": additional_oberth_delta_v,
            "energeticallyReachable": solar_energy_reachable,
            "constraintKind": "propulsion-delta-v",
            "electricalPowerDeficit": False,
            "energyBoundModel": "two-body upper bound at configured perihelion",
            "entryDate": result.config.start_date,
            "entryElapsedDays": trajectory[0]["elapsedDays"],
            "entryPositionKm": trajectory[0]["positionKm"],
            "perihelionDateTime": burn_datetime.isoformat(),
            "perihelionElapsedDays": burn_point.elapsed_days,
            "perihelionPositionKm": list(burn_point.position_km),
        },
        "guide": {
            "mode": "start-waypoint-target",
            "nodes": [
                {
                    "id": "start",
                    "kind": "start",
                    "elapsedDays": trajectory[0]["elapsedDays"],
                    "positionKm": trajectory[0]["positionKm"],
                },
                {
                    "id": "waypoint",
                    "kind": "planetary-waypoint",
                    "elapsedDays": periapsis_day,
                    "positionKm": list(planet_periapsis_position),
                },
                {
                    "id": "target",
                    "kind": "direction",
                    "direction": list(target_direction),
                },
            ],
            "legs": [
                {
                    "id": "start-to-waypoint",
                    "from": "start",
                    "to": "waypoint",
                    "physicalSegments": ["earth-to-oberth", "lambert-to-soi", "jupiter-hyperbola"],
                },
                {
                    "id": "waypoint-to-target",
                    "from": "waypoint",
                    "to": "target",
                    "physicalSegments": ["post-flyby"],
                },
            ],
        },
        "waypoint": {
            "id": waypoint_id,
            "name": planet_row[1],
            "encounterDay": periapsis_day,
            "entryDay": entry_day,
            "exitDay": exit_day,
            "flybyAltitudeKm": periapsis_radius - radius_km,
            "minimumFlybyAltitudeKm": altitude_km,
            "trajectoryIndex": periapsis_index,
            "positionKm": list(planet_periapsis_position),
        },
        "segments": [
            {"id": "earth-to-oberth", "label": "Erde → Solar Oberth", "guideLeg": "start-to-waypoint", "startIndex": 0, "endIndex": inbound_end},
            {"id": "lambert-to-soi", "label": "Lambert → Jupiter-SOI", "guideLeg": "start-to-waypoint", "startIndex": inbound_end, "endIndex": lambert_end},
            {"id": "jupiter-hyperbola", "label": "Jupiter-Hyperbel", "guideLeg": "start-to-waypoint", "startIndex": lambert_end, "endIndex": flyby_end},
            {"id": "post-flyby", "label": "Zielbahn" if (passive_targeting or target_injection_applied) else "Gravitativer Ausflug (Soll-Zielimpuls nicht verfügbar)", "guideLeg": "waypoint-to-target", "startIndex": flyby_end, "endIndex": post_end},
        ],
        "transitionDiagnostics": {
            "entryPositionResidualKmBeforePatch": entry_position_patch_residual,
            "entryVelocityResidualKmS": entry_velocity_patch_residual,
            "exitPositionResidualKm": 0.0,
            "exitVelocityResidualKmS": 0.0,
            "exitTargetInjectionDeltaVKmS": target_correction_delta_v,
            "exitTargetInjectionApplied": target_injection_applied,
            "exitTargetInjectionDirectionChangeDeg": acos(max(-1.0, min(1.0, _dot(
                _normalize(gravity_exit_velocity), _normalize(target_solution["velocity"]),
            )))) * 180 / pi,
            "burnToLambertDirectionChangeDeg": burn_to_lambert_direction_change,
            "lambertToHyperbolaDirectionChangeDeg": lambert_to_hyperbola_direction_change,
            "lambertPropagationEndpointResidualKm": lambert_propagation_position_residual,
            "lambertPropagationVelocityResidualKmS": lambert_propagation_velocity_residual,
            "lambertSelection": lambert_selection,
            "bidirectionalMatch": {
                "method": "forward Lambert + backward target-asymptote boundary matching",
                "maximumTurnDeg": bidirectional_solution["maximumTurnRad"] * 180 / pi,
                "demandedTurnDeg": bidirectional_solution["demandedTurnRad"] * 180 / pi,
                "turnClosureResidualDeg": bidirectional_solution["turnClosureResidualRad"] * 180 / pi,
                "aimpointRole": aimpoint_role if aimpoint_enabled else None,
                "aimpointAlignmentBeforeDeg": aimpoint_alignment_before_rad * 180 / pi if aimpoint_alignment_before_rad is not None else None,
                "aimpointAlignmentAfterDeg": aimpoint_alignment_after_rad * 180 / pi if aimpoint_alignment_after_rad is not None else None,
                "aimpointWarning": aimpoint_warning,
                "boundaryVelocityResidualKmS": bidirectional_solution["boundaryVelocityResidualKmS"],
                "backwardAlignmentDeg": bidirectional_solution["backwardAlignmentRad"] * 180 / pi,
                "initialTargetProgressKmS": bidirectional_solution["initialTargetProgressKmS"],
                "backwardIterations": bidirectional_solution["backwardIterations"],
                "passiveMatch": passive_targeting,
            },
        },
        "trajectory": trajectory,
        "outgoingDirection": list(outgoing_direction),
        "flybyGeometry": {
            "curveModel": "analytic two-body hyperbola in the moving planet frame",
            "sampleCount": len(relative_hyperbola_points),
            "stateContinuousWithinFlyby": True,
            "separateTargetImpulseAtSoiExit": target_injection_applied,
            "incomingExcessDirection": list(incoming_direction),
            "outgoingExcessDirection": list(outgoing_direction_relative),
            "incomingHeliocentricDirection": list(_normalize(arrival_velocity)),
            "outgoingHeliocentricDirection": list(_normalize(exit_velocity)),
            "gravityOnlyOutgoingDirection": list(gravity_only_direction),
            "backwardRequiredOutgoingExcessDirection": list(bidirectional_solution["backwardRequiredRelativeDirection"]),
            "backwardRequiredHeliocentricVelocityKmS": list(bidirectional_solution["backwardRequiredVelocityKmS"]),
            "targetInjectionDirection": list(target_injection_direction),
            "targetAsymptoteDirection": list(outgoing_direction),
            "actualPostFlybyDirection": list(_normalize(exit_velocity)),
            "periapsisRadiusKm": periapsis_radius,
            "planetRadiusKm": radius_km,
            "sphereOfInfluenceRadiusKm": sphere_of_influence_km,
            "hyperbolaEccentricity": eccentricity,
            "semiMajorAxisMagnitudeKm": semi_major_axis,
            "hyperbolicAnomalyLimit": hyperbolic_limit,
            "axisX": list(axis_x),
            "axisY": list(axis_y),
            "flybyPlaneNormal": list(flyby_plane_normal),
            "entryRelativePositionKm": list(entry_relative_position),
            "periapsisRelativePositionKm": list(periapsis_relative_position),
            "exitRelativePositionKm": list(exit_relative_position),
            "entryLatitudeDeg": entry_latitude,
            "periapsisLatitudeDeg": periapsis_latitude,
            "exitLatitudeDeg": exit_latitude,
            "verticalTurnDeg": (
                asin(max(-1.0, min(1.0, outgoing_direction_relative[2])))
                - asin(max(-1.0, min(1.0, incoming_direction[2])))
            ) * 180 / pi,
            "relativeTrajectory": relative_hyperbola_points,
            "aimpoint": {
                "enabled": aimpoint_enabled,
                "clockAngleDeg": aimpoint_clock_deg,
                "screenRadiusNorm": aimpoint_screen_radius,
                "role": aimpoint_role,
                "altitudeKm": aimpoint_altitude_km,
                "requestedRelativePositionKm": list(desired_periapsis_relative_position) if desired_periapsis_relative_position is not None else None,
                "relativePositionKm": list(aimpoint_position_relative) if aimpoint_position_relative is not None else None,
                "absolutePositionKm": list(aimpoint_position) if aimpoint_position is not None else None,
                "requestedDirection": list(desired_periapsis_direction) if desired_periapsis_direction is not None else None,
                "alignmentBeforeDeg": aimpoint_alignment_before_rad * 180 / pi if aimpoint_alignment_before_rad is not None else None,
                "alignmentAfterDeg": aimpoint_alignment_after_rad * 180 / pi if aimpoint_alignment_after_rad is not None else None,
                "warning": aimpoint_warning,
            },
        },
        "uncertainty": _route_uncertainty(trajectory, result.config, waypoint_id),
        "summary": {
            "flybyMode": flyby_mode,
            "requiredInjectionDeltaVKmS": injection_delta_v,
            "availableInjectionDeltaVKmS": available_oberth_delta_v,
            "solarDepartureInjectionApplied": injection_delta_v <= available_oberth_delta_v,
            "desiredSolarExitSpeedKmS": desired_solar_exit_speed,
            "actualSolarExitSpeedKmS": solar_exit_speed,
            "solarExitSpeedResidualKmS": solar_speed_residual,
            "solarSpeedBoundaryReached": solar_speed_boundary_reached,
            "incomingExcessSpeedKmS": excess_speed,
            "turnAngleDeg": turn_angle * 180 / pi,
            "heliocentricSpeedBeforeKmS": heliocentric_speed_before,
            "heliocentricSpeedAfterKmS": heliocentric_speed_after,
            "speedGainKmS": heliocentric_speed_after - heliocentric_speed_before,
            "targetDepartureSpeedKmS": target_departure_speed,
            "solarEscapeSpeedAtExitKmS": solar_escape_speed,
            "targetCorrectionDeltaVKmS": target_correction_delta_v,
            "targetInjectionApplied": target_injection_applied,
            "passiveTargeting": passive_targeting,
            "actualTrajectoryMode": "target-injection" if target_injection_applied else "gravity-only",
            "actualTargetAlignmentDeg": actual_target_alignment * 180 / pi if actual_target_alignment is not None else None,
            "targetProgressMonotonic": target_progress_monotonic,
            "minimumTargetProgressRateKmS": minimum_target_progress_rate,
            "targetingIterations": targeting_iterations,
            "targetingMode": (
                "gravity-assist plus applied SOI-exit target injection"
                if target_injection_applied
                else "passive gravity-assist matched by backward targeting"
                if passive_targeting
                else "gravity-assist; target injection evaluated but rejected"
            ),
            "courseChangeDeg": course_change * 180 / pi,
            "periapsisSpeedKmS": periapsis_speed,
            "observationWindowHours": observation_window_hours,
            "targetAlignmentDeg": target_alignment * 180 / pi,
            "entryCorridorTargeted": entry_corridor["enabled"],
            "entryInsideCorridor": entry_inside_corridor,
            "feasibleWithConfiguredBurn": (
                injection_delta_v <= available_oberth_delta_v
                and target_correction_delta_v <= available_oberth_delta_v
                and solar_speed_boundary_reached
                and actual_target_alignment is not None
                and actual_target_alignment <= 0.01 * pi / 180
                and target_progress_monotonic
                and entry_inside_corridor
            ),
            "warnings": payload_warnings,
            "stateContinuity": "position exact at both SOI boundaries; velocity residual reported at model switch",
            "model": "Bidirectional boundary matching: forward Lambert + backward target asymptote + B-plane hyperbola",
        },
    }
    payload["highFidelityNBody"] = {
        "enabled": high_fidelity_n_body,
        "converged": False,
        "trajectory": [],
    }
    if high_fidelity_n_body:
        target_impulse = (
            _subtract(exit_velocity, gravity_exit_velocity)
            if target_injection_applied
            else (0.0, 0.0, 0.0)
        )
        continuous_validation = validate_continuous_waypoint_route(
            epoch_days_j2000=epoch_days,
            burn_day=burn_point.elapsed_days,
            burn_position_km=burn_point.position_km,
            reference_departure_velocity_km_s=departure_velocity,
            entry_day=entry_day,
            reference_entry_position_km=hyperbola_entry_position,
            reference_entry_velocity_km_s=hyperbola_entry_velocity,
            exit_day=exit_day,
            reference_exit_position_km=exit_position,
            reference_gravity_exit_velocity_km_s=gravity_exit_velocity,
            target_impulse_km_s=target_impulse,
            outbound_end_day=float(post_flyby_trajectory[-1]["elapsedDays"]),
            waypoint_ephemeris=ephemeris,
            waypoint_radius_km=radius_km,
        )
        corrected_departure_velocity = tuple(
            continuous_validation["differentialCorrection"][
                "correctedDepartureVelocityKmS"
            ]
        )
        continuous_required_delta_v = _magnitude(
            _subtract(corrected_departure_velocity, pre_burn_velocity)
        )
        continuous_validation["differentialCorrection"][
            "requiredDepartureDeltaVKmS"
        ] = continuous_required_delta_v
        continuous_validation["differentialCorrection"][
            "feasibleWithConfiguredBurn"
        ] = continuous_required_delta_v <= available_oberth_delta_v
        payload["highFidelityNBody"] = continuous_validation
        payload["summary"]["highFidelityNBodyConverged"] = continuous_validation[
            "converged"
        ]
        payload["summary"]["highFidelityNBodyCollision"] = continuous_validation[
            "collision"
        ]
        payload["summary"][
            "highFidelityRequiredDepartureDeltaVKmS"
        ] = continuous_required_delta_v
        if not continuous_validation["converged"]:
            payload_warnings.append(
                "Die differentielle Korrektur der kontinuierlichen "
                "N-Körper-Bahn hat die Eintrittstoleranz nicht erreicht."
            )
        if continuous_validation["collision"]:
            payload_warnings.append(
                "Die kontinuierliche N-Körper-Bahn schneidet den Planetenkörper."
            )

    position_gaps = [
        {"fromSegment": left_id, "toSegment": right_id, "gapKm": _magnitude(_subtract(
            tuple(trajectory[right_index]["positionKm"]),
            tuple(trajectory[left_index]["positionKm"]),
        ))}
        for left_id, left_index, right_id, right_index in (
            ("earth-to-oberth", inbound_end, "lambert-to-soi", inbound_end),
            ("lambert-to-soi", lambert_end, "jupiter-hyperbola", lambert_end),
            ("jupiter-hyperbola", flyby_end, "post-flyby", flyby_end),
        )
    ]
    payload["audit"] = write_route_audit({
        "units": {"position": "km", "velocity": "km/s", "time": "days since mission start", "angle": "degrees", "gravitationalParameter": "km^3/s^2"},
        "inputs": {
            "mission": result.config.to_dict(),
            "visualConfiguration": values.get("visual"),
            "waypointId": waypoint_id,
            "requestedEncounterDay": periapsis_day,
            "minimumFlybyAltitudeKm": altitude_km,
            "flybyMode": flyby_mode,
            "desiredSolarExitSpeedKmS": desired_solar_exit_speed,
            "targetRightAscensionDeg": target_ra * 180 / pi,
            "targetDeclinationDeg": target_dec * 180 / pi,
            "flybyAimpoint": aimpoint_values,
            "entryCorridor": {
                **entry_corridor,
                "centerDirection": list(entry_corridor["centerDirection"]),
            },
            "highFidelityNBody": high_fidelity_n_body,
            "manualVisualRouteSketch": values.get("routeSketch"),
            "manualSketchAffectsDynamics": False,
        },
        "constants": {
            "muSunKm3S2": MU_SUN,
            "planetMuKm3S2": planet_mu,
            "planetRadiusKm": radius_km,
            "sphereOfInfluenceRadiusKm": sphere_of_influence_km,
        },
        "coordinateTransform": {"obliquityDeg": obliquity * 180 / pi, "targetEclipticUnitVector": list(target_direction)},
        "solarOberthBoundary": {
            "elapsedDays": burn_point.elapsed_days,
            "positionKm": list(burn_point.position_km),
            "preBurnVelocityKmS": list(pre_burn_velocity),
            "nominalVelocityKmS": list(burn_point.velocity_km_s),
            "lambertDepartureVelocityKmS": list(departure_velocity),
            "desiredExitSpeedAt1AuKmS": desired_solar_exit_speed,
            "actualExitSpeedAt1AuKmS": solar_exit_speed,
            "speedResidualKmS": solar_speed_residual,
            "speedBoundaryReached": solar_speed_boundary_reached,
        },
        "lambert": {
            "flightTimeDays": flight_seconds / DAY_SECONDS,
            "departureVelocityKmS": list(departure_velocity),
            "arrivalVelocityKmS": list(arrival_velocity),
            "requiredInjectionDeltaVKmS": injection_delta_v,
            "entryDay": entry_day,
            "entryPositionKm": list(hyperbola_entry_position),
            "entryPositionResidualBeforePatchKm": entry_position_patch_residual,
            "entryVelocityResidualKmS": entry_velocity_patch_residual,
            "propagatedEndpointResidualBeforePatchKm": lambert_propagation_position_residual,
            "propagatedVelocityResidualKmS": lambert_propagation_velocity_residual,
            "selection": lambert_selection,
            "burnToLambertDirectionChangeDeg": burn_to_lambert_direction_change,
            "lambertToHyperbolaDirectionChangeDeg": lambert_to_hyperbola_direction_change,
        },
        "flyby": {
            "incomingExcessVelocityKmS": list(incoming_asymptote),
            "incomingExcessSpeedKmS": excess_speed,
            "maximumSafeTurnDeg": maximum_turn_angle * 180 / pi,
            "selectedTurnDeg": turn_angle * 180 / pi,
            "minimumPeriapsisRadiusKm": minimum_periapsis_radius,
            "actualPeriapsisRadiusKm": periapsis_radius,
            "minimumAltitudeKm": altitude_km,
            "actualAltitudeKm": periapsis_radius - radius_km,
            "surfaceClearanceKm": periapsis_radius - radius_km,
            "eccentricity": eccentricity,
            "semiMajorAxisMagnitudeKm": semi_major_axis,
            "gravityExitVelocityKmS": list(gravity_exit_velocity),
            "gravitySpeedGainKmS": heliocentric_speed_after - heliocentric_speed_before,
            "entryRelativePositionKm": list(entry_relative_position),
            "periapsisRelativePositionKm": list(periapsis_relative_position),
            "exitRelativePositionKm": list(exit_relative_position),
            "entryLatitudeDeg": entry_latitude,
            "periapsisLatitudeDeg": periapsis_latitude,
            "exitLatitudeDeg": exit_latitude,
            "flybyPlaneNormal": list(flyby_plane_normal),
            "bidirectionalMatch": payload["transitionDiagnostics"]["bidirectionalMatch"],
        },
        "targeting": {
            "injectionDirection": list(target_injection_direction),
            "asymptoticTargetDirection": list(target_direction),
            "solarEscapeSpeedAtExitKmS": solar_escape_speed,
            "selectedDepartureSpeedKmS": target_departure_speed,
            "correctionDeltaVKmS": target_correction_delta_v,
            "injectionApplied": target_injection_applied,
            "actualTrajectoryMode": "target-injection" if target_injection_applied else "gravity-only",
            "actualExitVelocityKmS": list(exit_velocity),
            "actualSolarAsymptoteDirection": list(actual_solar_asymptote) if actual_solar_asymptote is not None else None,
            "actualTargetAlignmentDeg": actual_target_alignment * 180 / pi if actual_target_alignment is not None else None,
            "passiveTargeting": passive_targeting,
            "targetProgressMonotonic": target_progress_monotonic,
            "minimumTargetProgressRateKmS": minimum_target_progress_rate,
            "shootingIterations": targeting_iterations,
            "shootingConverged": targeting_converged,
            "asymptoteErrorDeg": target_alignment * 180 / pi,
        },
        "continuity": {
            "positionGapsKm": position_gaps,
            "maximumPositionGapKm": max(item["gapKm"] for item in position_gaps),
            "intentionalVelocityChanges": ([{"boundary": "SOI-exit", "reason": "target injection after gravity assist", "deltaVKmS": target_correction_delta_v}] if target_injection_applied else []),
            "rejectedVelocityChanges": (
                [{"boundary": "SOI-exit", "reason": "target injection exceeds configured propulsion", "deltaVKmS": target_correction_delta_v}]
                if target_correction_delta_v > 1e-9 and not target_injection_applied
                else []
            ),
        },
        "continuousNBody": (
            {
                key: value
                for key, value in payload["highFidelityNBody"].items()
                if key != "trajectory"
            }
            if high_fidelity_n_body
            else {"enabled": False}
        ),
        "validation": {
            "collisionFree": periapsis_radius > radius_km,
            "minimumAltitudeRespected": periapsis_radius >= minimum_periapsis_radius,
            "continuousPositions": all(item["gapKm"] <= 1e-6 for item in position_gaps),
            "solarEscapeMarginPositive": _magnitude(exit_velocity) > solar_escape_speed,
            "plannedTargetToleranceReached": target_alignment * 180 / pi <= 0.01,
            "actualTargetToleranceReached": actual_target_alignment is not None and actual_target_alignment * 180 / pi <= 0.01,
            "desiredSolarExitSpeedReached": solar_speed_boundary_reached,
            "postFlybyNeverMovesAwayFromTarget": target_progress_monotonic,
            "entryInsideCorridor": entry_inside_corridor,
            "routeFeasibleWithConfiguredPropulsion": payload["summary"]["feasibleWithConfiguredBurn"],
        },
    })
    if include_mission_result:
        payload["mission"] = result.to_dict()
    return payload


def _legacy_direct_solar_route(values: dict | None, include_mission_result: bool = False) -> dict:
    """Direct 3D Solar-Oberth alternative without a planetary waypoint."""
    values = values or {}
    requested_mission_values = dict(values.get("mission") or {})
    mission_values = dict(requested_mission_values)
    mission_values["missionYears"] = 1
    result = simulate_mission(mission_values)
    burn_event = next(event for event in result.events if event.name == "SOLAR_OBERTH_BURN_STARTED")

    target_ra = float(values.get("targetRightAscensionDeg", 217.43)) * pi / 180
    target_dec = float(values.get("targetDeclinationDeg", -62.68)) * pi / 180
    obliquity = 23.43928 * pi / 180
    equatorial_x = cos(target_dec) * cos(target_ra)
    equatorial_y = cos(target_dec) * sin(target_ra)
    equatorial_z = sin(target_dec)
    target_direction = _normalize((
        equatorial_x,
        equatorial_y * cos(obliquity) + equatorial_z * sin(obliquity),
        -equatorial_y * sin(obliquity) + equatorial_z * cos(obliquity),
    ))

    pre_burn_velocity = burn_event.velocity_km_s
    desired_speed = _magnitude(pre_burn_velocity) + result.summary.achieved_burn_delta_v_km_s
    target_longitude = atan2(target_direction[1], target_direction[0])
    target_latitude = asin(max(-1.0, min(1.0, target_direction[2])))

    def direct_shooting_error(angles) -> float:
        longitude, latitude = float(angles[0]), float(angles[1])
        latitude = max(-pi / 2, min(pi / 2, latitude))
        cosine_latitude = cos(latitude)
        direction = (
            cosine_latitude * cos(longitude),
            cosine_latitude * sin(longitude),
            sin(latitude),
        )
        state = (burn_event.position_km, tuple(component * desired_speed for component in direction))
        shooting_step = 2 * 365.25 * DAY_SECONDS / 120
        minimum_radius = _magnitude(state[0])
        for _ in range(120):
            state = _rk4(state, shooting_step)
            minimum_radius = min(minimum_radius, _magnitude(state[0]))
        final_velocity_direction = _normalize(state[1])
        alignment = acos(max(-1.0, min(1.0, _dot(final_velocity_direction, target_direction))))
        solar_penalty = max(0.0, (696_340 * 1.1 - minimum_radius) / 696_340)
        return alignment**2 + solar_penalty * 100

    shooting_result = minimize(
        direct_shooting_error,
        (target_longitude, target_latitude),
        method="Nelder-Mead",
        options={"maxiter": 70, "xatol": 1e-6, "fatol": 1e-10},
    )
    optimized_longitude = float(shooting_result.x[0])
    optimized_latitude = max(-pi / 2, min(pi / 2, float(shooting_result.x[1])))
    optimized_cosine_latitude = cos(optimized_latitude)
    optimized_direction = (
        optimized_cosine_latitude * cos(optimized_longitude),
        optimized_cosine_latitude * sin(optimized_longitude),
        sin(optimized_latitude),
    )
    desired_velocity = tuple(component * desired_speed for component in optimized_direction)
    required_vector_delta_v = _magnitude(_subtract(desired_velocity, pre_burn_velocity))
    available_delta_v = result.summary.achieved_burn_delta_v_km_s

    inbound = [point for point in result.trajectory if point.elapsed_days <= burn_event.elapsed_days]
    inbound_stride = max(1, len(inbound) // 80)
    trajectory = [{
        "elapsedDays": point.elapsed_days,
        "positionKm": list(point.position_km),
    } for point in inbound[::inbound_stride]]
    if inbound and (not trajectory or trajectory[-1]["elapsedDays"] != inbound[-1].elapsed_days):
        trajectory.append({"elapsedDays": inbound[-1].elapsed_days, "positionKm": list(inbound[-1].position_km)})
    burn_index = len(trajectory) - 1

    preview_years = min(5.0, max(2.0, float(requested_mission_values.get("missionYears", 10.0))))
    outbound_count = 360
    outbound_step = preview_years * 365.25 * DAY_SECONDS / outbound_count
    outbound_state = (burn_event.position_km, desired_velocity)
    for index in range(outbound_count):
        outbound_state = _rk4(outbound_state, outbound_step)
        trajectory.append({
            "elapsedDays": burn_event.elapsed_days + (index + 1) * outbound_step / DAY_SECONDS,
            "positionKm": list(outbound_state[0]),
        })

    final_direction = _normalize(outbound_state[1])
    final_alignment = acos(max(-1.0, min(1.0, _dot(final_direction, target_direction)))) * 180 / pi
    payload = {
        "startDate": result.config.start_date,
        "burnDay": burn_event.elapsed_days,
        "guide": {
            "mode": "start-target",
            "nodes": [
                {
                    "id": "start",
                    "kind": "start",
                    "elapsedDays": trajectory[0]["elapsedDays"],
                    "positionKm": trajectory[0]["positionKm"],
                },
                {
                    "id": "target",
                    "kind": "direction",
                    "direction": list(target_direction),
                },
            ],
            "legs": [
                {
                    "id": "start-to-target",
                    "from": "start",
                    "to": "target",
                    "physicalSegments": ["earth-to-oberth", "direct-solar-outbound"],
                },
            ],
        },
        "trajectory": trajectory,
        "segments": [
            {"id": "earth-to-oberth", "label": "Erde → Solar Oberth", "guideLeg": "start-to-target", "startIndex": 0, "endIndex": burn_index},
            {"id": "direct-solar-outbound", "label": "Direkter 3D-Solarausflug", "guideLeg": "start-to-target", "startIndex": burn_index, "endIndex": len(trajectory) - 1},
        ],
        "targetDirection": list(target_direction),
        "uncertainty": _route_uncertainty(trajectory, result.config, "direct-solar"),
        "summary": {
            "requiredVectorDeltaVKmS": required_vector_delta_v,
            "availableDeltaVKmS": available_delta_v,
            "feasibleWithConfiguredBurn": required_vector_delta_v <= available_delta_v,
            "finalTargetAlignmentDeg": final_alignment,
            "optimizedBurnLongitudeDeg": optimized_longitude * 180 / pi,
            "optimizedBurnLatitudeDeg": optimized_latitude * 180 / pi,
            "shootingIterations": int(getattr(shooting_result, "nit", 0)),
            "targetEclipticLatitudeDeg": asin(max(-1.0, min(1.0, target_direction[2]))) * 180 / pi,
            "model": "Direct 3D Solar-Oberth vector burn + heliocentric propagation",
        },
    }
    if include_mission_result:
        payload["mission"] = result.to_dict()
    return payload



def _common_solar_request(values, waypoint, include_mission_result=False):
    from datetime import timedelta
    from solver.orbital import utc,timestamp
    from planner.trajectory_planner import calculate_trajectory_plan,section_result_adapter
    values=values or {};mission=dict(values.get('mission') or {})
    start_date=mission.get('startDate') or timestamp(utc(__import__('datetime').datetime.now(__import__('datetime').timezone.utc)))
    waypoints=[{'id':'solar-oberth','type':'solar_oberth','perihelionAU':mission.get('targetPerihelionAu',.05),'burnDeltaVKmS':mission.get('oberthDeltaVKmS',8),'desiredExitSpeedKmS':values.get('desiredSolarExitSpeedKmS',25),'maximumBurnDeltaVKmS':mission.get('oberthDeltaVKmS',8)}]
    if waypoint:
        waypoints.append({'id':'planet-flyby','type':'body_flyby','bodyId':values.get('waypointId','jupiter'),'targetDate':timestamp(utc(start_date)+timedelta(days=float(values.get('encounterDay',730)))),'flybyAltitudeKm':values.get('flybyAltitudeKm',100000),'entryCorridor':values.get('entryCorridor',{}),'aimpoint':values.get('flybyAimpoint',{})})
    target={'type':'direction','rightAscensionDeg':values.get('targetRightAscensionDeg',217.43),'declinationDeg':values.get('targetDeclinationDeg',-62.68),'distanceAU':values.get('directionDistanceAU',50)}
    result=calculate_trajectory_plan({'start':{'type':'orbit','bodyId':'earth','orbitAltitudeKm':mission.get('parkingOrbitAltitudeKm',400),'startDate':start_date},'target':target,'waypoints':waypoints,'mission':mission,'constraints':{'desiredSolarExitSpeedKmS':values.get('desiredSolarExitSpeedKmS',25)},'simulation':{'includeAudit':True,'sampleTrajectoryPoints':180,'propagationYears':max(20,float(mission.get('missionYears',20))),'highFidelityNBody':values.get('highFidelityNBody',False)}})
    adapted=section_result_adapter(result,start_date)
    adapted['summary'].update(finalTargetAlignmentDeg=result['summary'].get('targetAlignmentDeg',180),solarExitSpeedKmS=(result.get('solarBoundary') or {}).get('actualExitSpeedKmS',result['summary']['finalHeliocentricSpeedKmS']),requiredVectorDeltaVKmS=result['summary']['totalDeltaVKmS'],availableDeltaVKmS=result['input']['constraints'].get('maxTotalDeltaVKmS',0),angularChangeDeg=result['summary'].get('targetAlignmentDeg',180))
    if include_mission_result:adapted['mission']=_route_mission_payload(result,mission)
    return adapted


def simulate_waypoint_route(values, include_mission_result=False):
    return _common_solar_request(values,True,include_mission_result)


def simulate_direct_solar_route(values, include_mission_result=False):
    return _common_solar_request(values,False,include_mission_result)


def _route_mission_payload(result,mission):
    """Legacy mission presentation of these exact states, without re-simulation."""
    from solver.trajectory import MissionConfig,MissionSummary
    from solver.orbital import utc,timestamp
    from datetime import timedelta
    import numpy as np
    points=result['trajectory'];config=MissionConfig.from_dict(mission).to_dict()
    if any(p.get('massKg') is None for p in points):raise ValueError('Missionsübernahme benötigt einen vollständigen Fahrzeugnachweis.')
    phase={'INITIAL_STATE':'EARTH_PARKING_ORBIT','EARTH_ESCAPE':'EARTH_ESCAPE','SUNDIVER_INJECTION':'SUNDIVER_TRANSFER','SOLAR_APPROACH':'SOLAR_APPROACH','SOLAR_OBERTH':'SOLAR_OBERTH_BURN'}
    shown=[{**p,'phase':phase.get(p['phase'],'DEEP_SPACE_CRUISE')} for p in points]
    events=[]
    for e in result['events']:
        day=float(e.get('elapsedDays',0));point=min(points,key=lambda p:abs(p['elapsedDays']-day));actual=e.get('stateAfter',point)
        events.append({**e,'elapsedDays':day,'date':timestamp(utc(result['start']['date'])+timedelta(days=day)),
            'phase':phase.get(e['type'],'DEEP_SPACE_CRUISE'),'name':e['type'],'description':'Ereignis aus derselben propagierten Route',
            'massKg':actual['massKg'],'speedKmS':float(np.linalg.norm(actual['velocityKmS'])),'positionKm':actual['positionKm'],'velocityKmS':actual['velocityKmS'],'warningLevel':'info' if e.get('applied',True) else 'warning'})
    solar=next((b for b in result['maneuvers'] if b['type']=='SOLAR_OBERTH'),None)
    peri=min(float(np.linalg.norm(p['positionKm']))/AU_KM for p in points)
    warnings=[*result['warnings'],'Missionsdarstellung der Impulsroute; kontinuierliche Antriebe und Navigation werden in diesem Planner-Lauf nicht simuliert.']
    config.update(electricSailEnabled=False,kalmanEnabled=False)
    summary=MissionSummary(status='WARNING' if result['summary']['feasible'] else 'ABORT',total_flight_days=result['summary']['totalFlightDays'],perihelion_au=peri,max_solar_flux_w_m2=1361/peri**2,
        pre_burn_speed_km_s=float(np.linalg.norm(solar['stateBefore']['velocityKmS'])) if solar else float(np.linalg.norm(points[0]['velocityKmS'])),
        post_burn_speed_km_s=float(np.linalg.norm(solar['stateAfter']['velocityKmS'])) if solar else float(np.linalg.norm(points[-1]['velocityKmS'])),
        achieved_burn_delta_v_km_s=solar['deltaVKmS'] if solar and solar['applied'] else 0,propellant_used_kg=sum(b['propellantUsedKg'] or 0 for b in result['maneuvers'] if b['applied']),
        payload_mass_kg=config['payloadMassKg'],distance_au_by_year={},speed_km_s_by_year={},electric_sail_gain_km_s=0,navigation_cycles=0,position_uncertainty_km=0,velocity_uncertainty_km_s=0,max_planetary_perturbation_mm_s2=0,propulsion_report=[],time_to_saturn_days=None,time_to_voyager_distance_days=None,warnings=warnings).to_dict()
    summary.update(totalDeltaVKmS=result['summary']['totalDeltaVKmS'],model=result['summary']['model'])
    return {'schemaVersion':result['schemaVersion'],'calculationBuild':result['calculationBuild'],'config':config,'events':events,'trajectory':shown,'summary':summary,'validation':result['validation'],'maneuvers':result['maneuvers'],'segments':result['segments']}
