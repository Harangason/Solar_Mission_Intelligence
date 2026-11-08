"""State-continuous multi-section routing from the interactive 2D planner.

Every 2D section contributes a planet-centred three-dimensional entry cone.
Heliocentric Lambert legs connect those cones; inside a sphere of influence the
spacecraft is propagated under simultaneous solar and planetary gravity.  The
SOI exit state is used directly as the reference state of the following leg.
"""

from __future__ import annotations

from math import acos, asin, cos, log, pi, sin, sqrt

from scipy.integrate import solve_ivp

from planner.route_planner import (
    G_KM3_KG_S2,
    _corridor_coordinates_deg,
    _corridor_direction,
    _cross,
    _dot,
    _lambert_candidates,
    _parse_entry_corridor,
    _propagate_lambert_segment,
    _rotate_about_axis,
    _subtract,
)
from solver.trajectory import (
    AU_KM,
    DAY_SECONDS,
    MU_SUN,
    PLANET_EPHEMERIDES,
    _add,
    _magnitude,
    _mission_epoch_days,
    _normalize,
    _planet_position_at,
    _planet_state_at,
    simulate_mission,
)
from visualization.view_3d_celestials import PLANET_DATA
from planner.generic_route_planner import parse_route_passage, simulate_generic_route_sections
from planner.interstellar_targets import (
    INTERSTELLAR_ROUTE_TARGETS,
    interstellar_direction as _interstellar_direction,
)


SUN_MASS_KG = MU_SUN / G_KM3_KG_S2
MAX_LOCAL_PROPAGATION_DAYS = 800.0

def _planet_records(planet_id: str) -> tuple[tuple, tuple]:
    ephemeris = next((row for row in PLANET_EPHEMERIDES if row[0] == planet_id), None)
    data = next((row for row in PLANET_DATA if row[0] == planet_id), None)
    if ephemeris is None or data is None:
        raise ValueError(
            f"Routenziel '{planet_id}' ist kein propagierbarer Planet des Sonnensystems."
        )
    return ephemeris, data


def _sphere_of_influence_km(ephemeris: tuple, planet_data: tuple) -> float:
    return ephemeris[2] * AU_KM * (planet_data[2] / SUN_MASS_KG) ** (2 / 5)


def _transfer_duration_seconds(origin_radius_km: float, target_radius_km: float) -> float:
    transfer_semi_major_axis = (origin_radius_km + target_radius_km) / 2
    return pi * sqrt(transfer_semi_major_axis**3 / MU_SUN)


def _desired_departure_direction(
    target_id: str | None,
    *,
    planet_position: tuple,
    arrival_day: float,
    epoch_days: float,
) -> tuple | None:
    if not target_id:
        return None
    stellar_direction = _interstellar_direction(target_id)
    if stellar_direction is not None:
        return stellar_direction
    try:
        target_ephemeris, _ = _planet_records(target_id)
    except ValueError:
        return None
    flight_seconds = _transfer_duration_seconds(
        _magnitude(planet_position), target_ephemeris[2] * AU_KM
    )
    future_target_position = _planet_position_at(
        target_ephemeris,
        epoch_days + arrival_day + flight_seconds / DAY_SECONDS,
    )
    return _normalize(_subtract(future_target_position, planet_position))


def _predicted_passive_exit(
    *,
    relative_position: tuple,
    relative_velocity: tuple,
    planet_velocity: tuple,
    planet_mu: float,
) -> tuple[tuple, float]:
    angular_momentum = _cross(relative_position, relative_velocity)
    angular_momentum_direction = _normalize(angular_momentum)
    eccentricity_vector = _subtract(
        tuple(
            component / planet_mu
            for component in _cross(relative_velocity, angular_momentum)
        ),
        _normalize(relative_position),
    )
    eccentricity = _magnitude(eccentricity_vector)
    if eccentricity <= 1.0:
        return _normalize(relative_velocity), 180.0
    turn_angle = 2 * asin(min(1.0, 1 / eccentricity))
    outgoing_relative_direction = _normalize(
        _rotate_about_axis(
            _normalize(relative_velocity),
            angular_momentum_direction,
            turn_angle,
        )
    )
    excess_speed = sqrt(max(
        0.0,
        _dot(relative_velocity, relative_velocity)
        - 2 * planet_mu / max(_magnitude(relative_position), 1.0),
    ))
    outgoing_heliocentric = _add(
        planet_velocity,
        tuple(excess_speed * component for component in outgoing_relative_direction),
    )
    return _normalize(outgoing_heliocentric), turn_angle * 180 / pi


def _corridor_candidates(corridor: dict) -> list[tuple[tuple, float, float]]:
    if not corridor["enabled"]:
        return [((0.0, 0.0, 0.0), 0.0, 0.0)]
    return [
        (
            _corridor_direction(
                corridor["centerDirection"],
                horizontal_factor * corridor["horizontalHalfAngleDeg"],
                vertical_factor * corridor["verticalHalfAngleDeg"],
                corridor["rotationDeg"],
            ),
            horizontal_factor * corridor["horizontalHalfAngleDeg"],
            vertical_factor * corridor["verticalHalfAngleDeg"],
        )
        for horizontal_factor in (-1.0, -0.5, 0.0, 0.5, 1.0)
        for vertical_factor in (-1.0, -0.5, 0.0, 0.5, 1.0)
    ]


def _find_transfer(
    *,
    start_position: tuple,
    start_velocity: tuple,
    start_day: float,
    epoch_days: float,
    target_ephemeris: tuple,
    target_data: tuple,
    corridor: dict,
    fixed_arrival_day: float | None,
    minimum_periapsis_radius_km: float,
    lookahead_target_id: str | None = None,
    maximum_transition_delta_v_km_s: float | None = None,
) -> dict:
    sphere_of_influence_km = _sphere_of_influence_km(target_ephemeris, target_data)
    planet_mu = G_KM3_KG_S2 * target_data[2]
    if fixed_arrival_day is not None:
        estimate = (fixed_arrival_day - start_day) * DAY_SECONDS
        hohmann = _transfer_duration_seconds(
            _magnitude(start_position), target_ephemeris[2] * AU_KM
        )
        durations = sorted({
            estimate * factor for factor in (0.88, 0.94, 1.0, 1.06, 1.12)
        } | {
            hohmann * factor for factor in (0.7, 0.85, 1.0, 1.15, 1.3)
        })
    else:
        target_radius_km = target_ephemeris[2] * AU_KM
        nominal = _transfer_duration_seconds(_magnitude(start_position), target_radius_km)
        durations = [
            nominal * factor
            for factor in (0.55, 0.65, 0.75, 0.85, 1.0, 1.15, 1.3, 1.5, 1.7)
        ]

    candidates: list[dict] = []
    for duration_seconds in durations:
        if duration_seconds <= 5 * DAY_SECONDS:
            continue
        arrival_day = start_day + duration_seconds / DAY_SECONDS
        planet_position, planet_velocity = _planet_state_at(
            target_ephemeris, epoch_days + arrival_day
        )
        desired_departure_direction = _desired_departure_direction(
            lookahead_target_id,
            planet_position=planet_position,
            arrival_day=arrival_day,
            epoch_days=epoch_days,
        )
        for direction, horizontal_offset, vertical_offset in _corridor_candidates(corridor):
            entry_direction = (
                direction
                if corridor["enabled"]
                else _normalize(_subtract(start_position, planet_position))
            )
            entry_position = _add(
                planet_position,
                tuple(component * sphere_of_influence_km for component in entry_direction),
            )
            try:
                branches = _lambert_candidates(
                    start_position, entry_position, duration_seconds
                )
            except ValueError:
                continue
            for branch in branches:
                departure_velocity = branch["departure"]
                arrival_velocity = branch["arrival"]
                arrival_relative_velocity = _subtract(arrival_velocity, planet_velocity)
                inward_speed = -_dot(arrival_relative_velocity, entry_direction)
                if inward_speed <= 0.001:
                    continue
                angular_momentum = _cross(
                    tuple(component * sphere_of_influence_km for component in entry_direction),
                    arrival_relative_velocity,
                )
                eccentricity_vector = _subtract(
                    tuple(
                        component / planet_mu
                        for component in _cross(arrival_relative_velocity, angular_momentum)
                    ),
                    entry_direction,
                )
                eccentricity = _magnitude(eccentricity_vector)
                predicted_periapsis_radius_km = (
                    _dot(angular_momentum, angular_momentum)
                    / planet_mu
                    / max(1.0 + eccentricity, 1e-12)
                )
                if predicted_periapsis_radius_km < minimum_periapsis_radius_km:
                    continue
                predicted_outgoing_direction, predicted_turn_deg = (
                    _predicted_passive_exit(
                        relative_position=tuple(
                            component * sphere_of_influence_km
                            for component in entry_direction
                        ),
                        relative_velocity=arrival_relative_velocity,
                        planet_velocity=planet_velocity,
                        planet_mu=planet_mu,
                    )
                )
                lookahead_alignment_deg = (
                    _angle_deg(
                        predicted_outgoing_direction,
                        desired_departure_direction,
                    )
                    if desired_departure_direction is not None
                    else 0.0
                )
                diagnostics = {
                    key: value
                    for key, value in branch.items()
                    if key not in {"departure", "arrival"}
                }
                injection_delta_v_km_s = _magnitude(
                    _subtract(departure_velocity, start_velocity)
                )
                departure_radial_speed_km_s = _dot(
                    departure_velocity,
                    _normalize(start_position),
                )
                departure_direction_change_deg = _angle_deg(
                    departure_velocity,
                    start_velocity,
                )
                target_is_outside_start = (
                    target_ephemeris[2] * AU_KM
                    > _magnitude(start_position) * 1.05
                )
                candidates.append({
                    "arrivalDay": arrival_day,
                    "durationSeconds": duration_seconds,
                    "entryDirection": entry_direction,
                    "entryPosition": entry_position,
                    "planetPosition": planet_position,
                    "planetVelocity": planet_velocity,
                    "departureVelocity": departure_velocity,
                    "arrivalVelocity": arrival_velocity,
                    "inwardSpeedKmS": inward_speed,
                    "horizontalOffsetDeg": horizontal_offset,
                    "verticalOffsetDeg": vertical_offset,
                    "injectionDeltaVKmS": injection_delta_v_km_s,
                    "transitionDeltaVDeficitKmS": max(
                        0.0,
                        injection_delta_v_km_s
                        - (maximum_transition_delta_v_km_s or 0.0),
                    ),
                    "departureRadialSpeedKmS": departure_radial_speed_km_s,
                    "departureDirectionChangeDeg": departure_direction_change_deg,
                    "backtracksFromOuterTarget": (
                        target_is_outside_start
                        and departure_radial_speed_km_s < -0.02
                    ),
                    "lambertDiagnostics": diagnostics,
                    "sphereOfInfluenceKm": sphere_of_influence_km,
                    "predictedPeriapsisRadiusKm": predicted_periapsis_radius_km,
                    "predictedOutgoingDirection": predicted_outgoing_direction,
                    "predictedTurnDeg": predicted_turn_deg,
                    "desiredDepartureDirection": desired_departure_direction,
                    "lookaheadTargetId": lookahead_target_id,
                    "lookaheadAlignmentDeg": lookahead_alignment_deg,
                })
    if not candidates:
        raise ValueError(
            f"Kein nach innen gerichteter Eintritt in den Zielkorridor von "
            f"{target_data[1]} ist für diesen Abschnitt erreichbar."
        )
    # Start at solar periapsis with a continuous, outward-moving state.  A
    # visually well-aligned Lambert branch is not useful if it first reverses
    # the trajectory or needs hundreds of km/s of unconfigured impulse.
    outbound_candidates = [
        item for item in candidates
        if not item["backtracksFromOuterTarget"]
    ]
    if outbound_candidates:
        candidates = outbound_candidates

    affordable_candidates = (
        [
            item for item in candidates
            if item["injectionDeltaVKmS"]
            <= maximum_transition_delta_v_km_s + 1e-9
        ]
        if maximum_transition_delta_v_km_s is not None
        else []
    )
    if affordable_candidates:
        candidates = affordable_candidates
        return min(
            candidates,
            key=lambda item: (
                item["lookaheadAlignmentDeg"],
                item["departureDirectionChangeDeg"],
                item["injectionDeltaVKmS"],
                abs(log(
                    item["predictedPeriapsisRadiusKm"]
                    / minimum_periapsis_radius_km
                )),
            ),
        )

    return min(
        candidates,
        key=lambda item: (
            item["transitionDeltaVDeficitKmS"],
            item["injectionDeltaVKmS"],
            item["departureDirectionChangeDeg"],
            item["lookaheadAlignmentDeg"],
            abs(log(
                item["predictedPeriapsisRadiusKm"]
                / minimum_periapsis_radius_km
            )),
        ),
    )


def _propagate_inside_sphere(
    *,
    entry: dict,
    epoch_days: float,
    target_ephemeris: tuple,
    target_data: tuple,
    minimum_periapsis_radius_km: float,
    maximum_corridor_insertion_delta_v_km_s: float,
) -> dict:
    planet_mu = G_KM3_KG_S2 * target_data[2]
    planet_radius_km = target_data[3] / 1_000
    sphere_of_influence_km = entry["sphereOfInfluenceKm"]
    entry_day = entry["arrivalDay"]
    entry_relative_velocity = _subtract(
        entry["arrivalVelocity"], entry["planetVelocity"]
    )
    entry_direction = entry["entryDirection"]
    entry_speed = _magnitude(entry_relative_velocity)
    tangential = _subtract(
        entry_relative_velocity,
        tuple(
            _dot(entry_relative_velocity, entry_direction) * component
            for component in entry_direction
        ),
    )
    if _magnitude(tangential) < 1e-9:
        reference = (
            (0.0, 0.0, 1.0)
            if abs(entry_direction[2]) < 0.9
            else (0.0, 1.0, 0.0)
        )
        tangential = _cross(reference, entry_direction)
    baseline_tangential_direction = _normalize(tangential)
    excess_speed_squared = max(
        0.0, entry_speed**2 - 2 * planet_mu / sphere_of_influence_km
    )
    desired_departure_direction = entry.get("desiredDepartureDirection")
    clock_candidates = range(0, 360, 5) if desired_departure_direction else (0,)
    steering_candidates: list[dict] = [{
        "clockAngleDeg": -1.0,
        "targetPeriapsisRadiusKm": entry["predictedPeriapsisRadiusKm"],
        "relativeVelocity": entry_relative_velocity,
        "heliocentricVelocity": entry["arrivalVelocity"],
        "predictedDirection": entry["predictedOutgoingDirection"],
        "predictedTurnDeg": entry["predictedTurnDeg"],
        "alignmentDeg": entry["lookaheadAlignmentDeg"],
        "deltaVKmS": 0.0,
    }]
    periapsis_factors = (
        (1.0, 1.25, 1.6, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0)
        if desired_departure_direction
        else (1.0,)
    )
    for periapsis_factor in periapsis_factors:
        target_periapsis_radius_km = min(
            minimum_periapsis_radius_km * periapsis_factor,
            sphere_of_influence_km * 0.35,
        )
        target_tangential_speed = (
            target_periapsis_radius_km
            * sqrt(
                excess_speed_squared
                + 2 * planet_mu / target_periapsis_radius_km
            )
            / sphere_of_influence_km
        )
        target_tangential_speed = min(
            target_tangential_speed, entry_speed * 0.999999
        )
        target_radial_speed = -sqrt(max(
            0.0, entry_speed**2 - target_tangential_speed**2
        ))
        for clock_angle_deg in clock_candidates:
            tangential_direction = _normalize(_rotate_about_axis(
                baseline_tangential_direction,
                entry_direction,
                clock_angle_deg * pi / 180,
            ))
            relative_velocity = tuple(
                target_radial_speed * entry_direction[axis]
                + target_tangential_speed * tangential_direction[axis]
                for axis in range(3)
            )
            predicted_direction, predicted_turn_deg = _predicted_passive_exit(
                relative_position=tuple(
                    sphere_of_influence_km * component
                    for component in entry_direction
                ),
                relative_velocity=relative_velocity,
                planet_velocity=entry["planetVelocity"],
                planet_mu=planet_mu,
            )
            heliocentric_velocity = _add(
                entry["planetVelocity"], relative_velocity
            )
            steering_candidates.append({
                "clockAngleDeg": float(clock_angle_deg),
                "targetPeriapsisRadiusKm": target_periapsis_radius_km,
                "relativeVelocity": relative_velocity,
                "heliocentricVelocity": heliocentric_velocity,
                "predictedDirection": predicted_direction,
                "predictedTurnDeg": predicted_turn_deg,
                "alignmentDeg": (
                    _angle_deg(
                        predicted_direction, desired_departure_direction
                    )
                    if desired_departure_direction is not None
                    else 0.0
                ),
                "deltaVKmS": _magnitude(_subtract(
                    heliocentric_velocity, entry["arrivalVelocity"]
                )),
            })
    affordable_steering = [
        item for item in steering_candidates
        if item["deltaVKmS"]
        <= maximum_corridor_insertion_delta_v_km_s + 1e-9
    ]
    selected_steering = min(
        affordable_steering or steering_candidates,
        key=(
            (lambda item: (item["alignmentDeg"], item["deltaVKmS"]))
            if affordable_steering
            else (lambda item: (item["deltaVKmS"], item["alignmentDeg"]))
        ),
    )
    steered_arrival_velocity = selected_steering["heliocentricVelocity"]
    initial_state = [*entry["entryPosition"], *steered_arrival_velocity]

    def derivative(seconds: float, state) -> list[float]:
        position = tuple(float(state[index]) for index in range(3))
        planet_position = _planet_position_at(
            target_ephemeris, epoch_days + entry_day + seconds / DAY_SECONDS
        )
        relative = _subtract(position, planet_position)
        solar_radius = max(_magnitude(position), 1.0)
        planet_radius = max(_magnitude(relative), 1.0)
        return [
            float(state[3]),
            float(state[4]),
            float(state[5]),
            *[
                -MU_SUN * position[index] / solar_radius**3
                - planet_mu * relative[index] / planet_radius**3
                for index in range(3)
            ],
        ]

    def soi_exit(seconds: float, state) -> float:
        if seconds < 30.0:
            return -1.0
        planet_position = _planet_position_at(
            target_ephemeris, epoch_days + entry_day + seconds / DAY_SECONDS
        )
        return _magnitude(_subtract(tuple(state[:3]), planet_position)) - sphere_of_influence_km

    def collision(seconds: float, state) -> float:
        planet_position = _planet_position_at(
            target_ephemeris, epoch_days + entry_day + seconds / DAY_SECONDS
        )
        return _magnitude(_subtract(tuple(state[:3]), planet_position)) - planet_radius_km

    soi_exit.direction = 1
    soi_exit.terminal = True
    collision.direction = -1
    collision.terminal = True
    solution = solve_ivp(
        derivative,
        (0.0, MAX_LOCAL_PROPAGATION_DAYS * DAY_SECONDS),
        initial_state,
        method="DOP853",
        rtol=2e-10,
        atol=[1e-2, 1e-2, 1e-2, 1e-9, 1e-9, 1e-9],
        events=(soi_exit, collision),
        dense_output=True,
        max_step=0.2 * DAY_SECONDS,
    )
    if solution.t_events[1].size:
        raise ValueError(
            f"Die Bahn im Einflussbereich von {target_data[1]} kollidiert mit dem Planeten."
        )
    if not solution.success or not solution.t_events[0].size:
        raise ValueError(
            f"Die Bahn verlässt den Einflussbereich von {target_data[1]} nicht innerhalb "
            f"von {MAX_LOCAL_PROPAGATION_DAYS:g} Tagen."
        )

    exit_seconds = float(solution.t_events[0][0])
    sample_count = 240
    sample_times = [
        exit_seconds * index / sample_count for index in range(sample_count + 1)
    ]
    sampled = solution.sol(sample_times)
    trajectory: list[dict] = []
    relative_trajectory: list[dict] = []
    minimum_radius = float("inf")
    periapsis_index = 0
    for index, seconds in enumerate(sample_times):
        position = tuple(float(sampled[axis][index]) for axis in range(3))
        velocity = tuple(float(sampled[axis][index]) for axis in range(3, 6))
        day = entry_day + seconds / DAY_SECONDS
        planet_position, planet_velocity = _planet_state_at(
            target_ephemeris, epoch_days + day
        )
        relative_position = _subtract(position, planet_position)
        relative_velocity = _subtract(velocity, planet_velocity)
        radius = _magnitude(relative_position)
        if radius < minimum_radius:
            minimum_radius = radius
            periapsis_index = index
        trajectory.append({
            "elapsedDays": day,
            "positionKm": list(position),
            "velocityKmS": list(velocity),
            "waypointPositionKm": list(planet_position),
            "waypointRelativePositionKm": list(relative_position),
        })
        relative_trajectory.append({
            "elapsedDays": day,
            "anomaly": index,
            "positionKm": list(relative_position),
            "velocityKmS": list(relative_velocity),
        })

    exit_position = tuple(float(sampled[axis][-1]) for axis in range(3))
    exit_velocity = tuple(float(sampled[axis][-1]) for axis in range(3, 6))
    exit_planet_position, exit_planet_velocity = _planet_state_at(
        target_ephemeris, epoch_days + entry_day + exit_seconds / DAY_SECONDS
    )
    return {
        "trajectory": trajectory,
        "relativeTrajectory": relative_trajectory,
        "exitDay": entry_day + exit_seconds / DAY_SECONDS,
        "exitPosition": exit_position,
        "exitVelocity": exit_velocity,
        "exitRelativePosition": _subtract(exit_position, exit_planet_position),
        "exitRelativeVelocity": _subtract(exit_velocity, exit_planet_velocity),
        "minimumRadiusKm": minimum_radius,
        "periapsisIndex": periapsis_index,
        "periapsisDay": trajectory[periapsis_index]["elapsedDays"],
        "periapsisPosition": tuple(trajectory[periapsis_index]["positionKm"]),
        "planetRadiusKm": planet_radius_km,
        "corridorInsertionDeltaVKmS": selected_steering["deltaVKmS"],
        "steeredArrivalVelocity": steered_arrival_velocity,
        "entryVelocityPreserved": selected_steering["deltaVKmS"] < 1e-9,
        "selectedBPlaneClockDeg": selected_steering["clockAngleDeg"],
        "targetPeriapsisRadiusKm": selected_steering["targetPeriapsisRadiusKm"],
        "predictedLookaheadAlignmentDeg": selected_steering["alignmentDeg"],
        "predictedPassiveTurnDeg": selected_steering["predictedTurnDeg"],
        "predictedOutgoingDirection": selected_steering["predictedDirection"],
    }


def _angle_deg(first: tuple, second: tuple) -> float:
    return acos(max(-1.0, min(1.0, _dot(_normalize(first), _normalize(second))))) * 180 / pi


def classify_route_sections(raw_sections: object) -> dict:
    """Select the propagation model and expose the decision for diagnostics."""
    if not isinstance(raw_sections, list) or not raw_sections:
        return {"solver": "invalid", "reason": "missing-route-sections"}
    if any(not isinstance(raw, dict) for raw in raw_sections):
        return {"solver": "invalid", "reason": "malformed-route-section"}

    propagable_solar_bodies = {"sun", *(row[0] for row in PLANET_EPHEMERIDES)}
    first_origin = str(raw_sections[0].get("originId") or "")
    target_ids = [
        str(raw.get("targetId") or "")
        for raw in raw_sections
    ]
    unknown_targets = [
        target_id
        for target_id in target_ids
        if target_id not in propagable_solar_bodies
        and target_id not in INTERSTELLAR_ROUTE_TARGETS
    ]
    interstellar_indices = [
        index
        for index, target_id in enumerate(target_ids)
        if target_id in INTERSTELLAR_ROUTE_TARGETS
    ]
    has_non_direct_passage = any(
        parse_route_passage(raw.get("passage")).get("mode") != "direct"
        for raw in raw_sections
    )
    has_terminal_interstellar_asymptote = (
        len(interstellar_indices) == 1
        and interstellar_indices[0] == len(target_ids) - 1
    )

    if unknown_targets:
        return {
            "solver": "generic",
            "reason": "unknown-or-nonplanet-target",
            "unknownTargets": unknown_targets,
        }
    if has_terminal_interstellar_asymptote:
        return {
            "solver": "generic",
            "reason": "terminal-hypothetical-50-au-asymptote",
        }
    if first_origin != "sun":
        return {"solver": "generic", "reason": "freely-selected-origin"}
    if has_non_direct_passage:
        return {
            "solver": "generic",
            "reason": "explicit-local-passage-without-interstellar-asymptote",
        }
    return {"solver": "coupled-solar-oberth", "reason": "supported-solar-planet-chain"}


def _legacy_coupled_route_sections(values: dict | None) -> dict:
    """Propagate the complete ordered 2D route-section list in one state chain."""
    values = values or {}
    raw_sections = values.get("routeSections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("Mindestens ein 2D-Routenabschnitt ist erforderlich.")

    route_classification = classify_route_sections(raw_sections)
    if route_classification["solver"] == "invalid":
        raise ValueError("Mindestens ein gültiger 2D-Routenabschnitt ist erforderlich.")
    # Keep the specialised high-accuracy solar-Oberth chain only for the
    # mission it actually models.  Every freely selected origin, and every
    # Sun/moon endpoint, must use the selected bodies instead of an implicit
    # Earth departure.
    if route_classification["solver"] == "generic":
        return simulate_generic_route_sections(values)

    sections: list[dict] = []
    previous_target: str | None = None
    for index, raw in enumerate(raw_sections):
        if not isinstance(raw, dict):
            raise ValueError(f"Routenabschnitt {index + 1} ist ungültig.")
        origin_id = str(raw.get("originId") or "")
        target_id = str(raw.get("targetId") or "")
        if previous_target is not None and origin_id != previous_target:
            raise ValueError(
                f"Abschnitt {index + 1} beginnt bei '{origin_id}', erwartet wird "
                f"der Endpunkt '{previous_target}' des vorherigen Abschnitts."
            )
        # The 2D editor's red/green state is based on a straight schematic
        # approach line.  The multi-section solver performs the authoritative
        # curved-path collision and periapsis check, so stale UI blocking
        # metadata must not suppress a dynamically safe polar solution.
        raw_corridor = dict(raw.get("corridor") or {})
        raw_corridor["blocked"] = False
        raw_corridor.pop("blockReasons", None)
        corridor = _parse_entry_corridor(raw_corridor)
        sections.append({
            "id": str(raw.get("id") or f"route-section-{index + 1}"),
            "originId": origin_id,
            "targetId": target_id,
            "corridor": corridor,
            "passage": parse_route_passage(raw.get("passage")),
            "deltaVMinusKmS": max(0.0, float(raw.get("deltaVMinusKmS", 0.0))),
            "deltaVPlusKmS": max(0.0, float(raw.get("deltaVPlusKmS", 0.0))),
        })
        previous_target = target_id

    mission = simulate_mission(dict(values.get("mission") or {}))
    burn_point = next(
        point for point in mission.trajectory if point.phase.value == "SOLAR_OBERTH_BURN"
    )
    epoch_days = _mission_epoch_days(mission.config.start_date)
    trajectory = [
        {
            "elapsedDays": point.elapsed_days,
            "positionKm": list(point.position_km),
            "velocityKmS": list(point.velocity_km_s),
        }
        for point in mission.trajectory
        if point.elapsed_days <= burn_point.elapsed_days
    ]
    segments: list[dict] = [{
        "id": "earth-to-oberth",
        "label": "Erde → Solar Oberth",
        "startIndex": 0,
        "endIndex": len(trajectory) - 1,
    }]
    start_position = burn_point.position_km
    start_velocity = burn_point.velocity_km_s
    start_day = burn_point.elapsed_days
    raw_first_arrival_day = values.get("encounterDay")
    first_arrival_day = (
        None
        if raw_first_arrival_day is None
        else float(raw_first_arrival_day)
    )
    flyby_altitude_km = max(100.0, float(values.get("flybyAltitudeKm", 100_000.0)))
    calculated_sections: list[dict] = []
    total_transition_delta_v = 0.0

    for index, section in enumerate(sections):
        stellar_record = INTERSTELLAR_ROUTE_TARGETS.get(section["targetId"])
        if stellar_record is not None:
            if index == 0:
                raise ValueError(
                    "Eine interstellare Asymptote benötigt zuerst einen "
                    "planetaren Fly-by-Abschnitt."
                )
            if index != len(sections) - 1:
                raise ValueError(
                    "Ein interstellares Ziel kann nur der letzte "
                    "Routenabschnitt sein."
                )
            desired_direction = _interstellar_direction(section["targetId"])
            transfer_start_index = len(trajectory) - 1
            escape_duration_seconds = 15 * 365.25 * DAY_SECONDS
            escape_trajectory, exit_position, exit_velocity = (
                _propagate_lambert_segment(
                    start_position,
                    start_velocity,
                    start_day,
                    escape_duration_seconds,
                    360,
                )
            )
            trajectory.extend(escape_trajectory[1:])
            escape_end_index = len(trajectory) - 1
            alignment_deg = _angle_deg(start_velocity, desired_direction)
            calculated_sections.append({
                "id": section["id"],
                "originId": section["originId"],
                "targetId": section["targetId"],
                "targetName": stellar_record[0],
                "sectionType": "interstellar-asymptote",
                "transferStartIndex": transfer_start_index,
                "entryIndex": transfer_start_index,
                "periapsisIndex": transfer_start_index,
                "exitIndex": escape_end_index,
                "entryDay": start_day,
                "periapsisDay": start_day,
                "exitDay": start_day + escape_duration_seconds / DAY_SECONDS,
                "entryPositionKm": list(start_position),
                "entryDirection": list(_normalize(start_velocity)),
                "entryLatitudeDeg": asin(max(
                    -1.0, min(1.0, _normalize(start_velocity)[2])
                )) * 180 / pi,
                "exitPositionKm": list(exit_position),
                "exitVelocityKmS": list(exit_velocity),
                "minimumAltitudeKm": 0.0,
                "sphereOfInfluenceRadiusKm": 0.0,
                "requiredTransitionDeltaVKmS": 0.0,
                "corridorInsertionDeltaVKmS": 0.0,
                "entryVelocityPreserved": True,
                "lookaheadTargetId": None,
                "lookaheadAlignmentDeg": alignment_deg,
                "desiredDepartureDirection": list(desired_direction),
                "predictedOutgoingDirection": list(_normalize(start_velocity)),
                "predictedPassiveTurnDeg": 0.0,
                "passage": section["passage"],
                "corridor": {
                    "enabled": section["corridor"]["enabled"],
                    "centerDirection": list(desired_direction),
                    "horizontalHalfAngleDeg": section["corridor"]["horizontalHalfAngleDeg"],
                    "verticalHalfAngleDeg": section["corridor"]["verticalHalfAngleDeg"],
                    "rotationDeg": section["corridor"]["rotationDeg"],
                    "actualHorizontalOffsetDeg": alignment_deg,
                    "actualVerticalOffsetDeg": 0.0,
                    "entryInsideCorridor": (
                        not section["corridor"]["enabled"]
                        or alignment_deg
                        <= max(
                            section["corridor"]["horizontalHalfAngleDeg"],
                            section["corridor"]["verticalHalfAngleDeg"],
                        )
                    ),
                },
                "relativeTrajectory": [],
                "lambertEndpointResidualKm": 0.0,
                "lambertVelocityResidualKmS": 0.0,
            })
            segments.append({
                "id": f"{section['id']}-asymptote",
                "label": f"Ausflug → {stellar_record[0]}",
                "startIndex": transfer_start_index,
                "endIndex": escape_end_index,
            })
            start_position = exit_position
            start_velocity = exit_velocity
            start_day += escape_duration_seconds / DAY_SECONDS
            continue

        target_ephemeris, target_data = _planet_records(section["targetId"])
        lookahead_target_id = (
            sections[index + 1]["targetId"]
            if index + 1 < len(sections)
            else None
        )
        transfer = _find_transfer(
            start_position=start_position,
            start_velocity=start_velocity,
            start_day=start_day,
            epoch_days=epoch_days,
            target_ephemeris=target_ephemeris,
            target_data=target_data,
            corridor=section["corridor"],
            fixed_arrival_day=first_arrival_day if index == 0 else None,
            minimum_periapsis_radius_km=target_data[3] / 1_000 + flyby_altitude_km,
            lookahead_target_id=lookahead_target_id,
            maximum_transition_delta_v_km_s=(
                mission.config.oberth_delta_v_km_s
                if index == 0
                else sections[index - 1]["deltaVPlusKmS"]
            ),
        )
        transfer_start_index = len(trajectory) - 1
        transfer_trajectory, propagated_position, propagated_velocity = (
            _propagate_lambert_segment(
                start_position,
                transfer["departureVelocity"],
                start_day,
                transfer["durationSeconds"],
                300,
            )
        )
        transfer_trajectory[-1] = {
            "elapsedDays": transfer["arrivalDay"],
            "positionKm": list(transfer["entryPosition"]),
            "velocityKmS": list(transfer["arrivalVelocity"]),
        }
        trajectory.extend(transfer_trajectory[1:])
        transfer_end_index = len(trajectory) - 1
        local = _propagate_inside_sphere(
            entry=transfer,
            epoch_days=epoch_days,
            target_ephemeris=target_ephemeris,
            target_data=target_data,
            minimum_periapsis_radius_km=target_data[3] / 1_000 + flyby_altitude_km,
            maximum_corridor_insertion_delta_v_km_s=section["deltaVPlusKmS"],
        )
        trajectory[transfer_end_index]["velocityKmS"] = list(
            local["steeredArrivalVelocity"]
        )
        trajectory.extend(local["trajectory"][1:])
        local_end_index = len(trajectory) - 1
        periapsis_index = transfer_end_index + local["periapsisIndex"]
        actual_entry_direction = _normalize(
            _subtract(transfer["entryPosition"], transfer["planetPosition"])
        )
        horizontal_deg, vertical_deg = _corridor_coordinates_deg(
            actual_entry_direction,
            section["corridor"]["centerDirection"],
            section["corridor"]["rotationDeg"],
        )
        entry_latitude_deg = asin(
            max(-1.0, min(1.0, actual_entry_direction[2]))
        ) * 180 / pi
        total_transition_delta_v += (
            transfer["injectionDeltaVKmS"] + local["corridorInsertionDeltaVKmS"]
        )
        calculated_sections.append({
            "id": section["id"],
            "originId": section["originId"],
            "targetId": section["targetId"],
            "targetName": target_data[1],
            "transferStartIndex": transfer_start_index,
            "entryIndex": transfer_end_index,
            "periapsisIndex": periapsis_index,
            "exitIndex": local_end_index,
            "entryDay": transfer["arrivalDay"],
            "periapsisDay": local["periapsisDay"],
            "exitDay": local["exitDay"],
            "entryPositionKm": list(transfer["entryPosition"]),
            "entryDirection": list(actual_entry_direction),
            "entryLatitudeDeg": entry_latitude_deg,
            "exitPositionKm": list(local["exitPosition"]),
            "exitVelocityKmS": list(local["exitVelocity"]),
            "minimumAltitudeKm": local["minimumRadiusKm"] - local["planetRadiusKm"],
            "sphereOfInfluenceRadiusKm": transfer["sphereOfInfluenceKm"],
            "requiredTransitionDeltaVKmS": transfer["injectionDeltaVKmS"],
            "availableTransitionDeltaVKmS": (
                mission.config.oberth_delta_v_km_s
                if index == 0
                else sections[index - 1]["deltaVPlusKmS"]
            ),
            "transitionDeltaVDeficitKmS": transfer["transitionDeltaVDeficitKmS"],
            "departureRadialSpeedKmS": transfer["departureRadialSpeedKmS"],
            "departureDirectionChangeDeg": transfer["departureDirectionChangeDeg"],
            "backtracksFromOuterTarget": transfer["backtracksFromOuterTarget"],
            "transferDurationDays": transfer["durationSeconds"] / DAY_SECONDS,
            "lambertDiagnostics": transfer["lambertDiagnostics"],
            "corridorInsertionDeltaVKmS": local["corridorInsertionDeltaVKmS"],
            "entryVelocityPreserved": local["entryVelocityPreserved"],
            "lookaheadTargetId": transfer["lookaheadTargetId"],
            "lookaheadAlignmentDeg": local["predictedLookaheadAlignmentDeg"],
            "selectedBPlaneClockDeg": local["selectedBPlaneClockDeg"],
            "targetPeriapsisRadiusKm": local["targetPeriapsisRadiusKm"],
            "desiredDepartureDirection": (
                list(transfer["desiredDepartureDirection"])
                if transfer["desiredDepartureDirection"] is not None
                else None
            ),
            "predictedOutgoingDirection": list(
                local["predictedOutgoingDirection"]
            ),
            "predictedPassiveTurnDeg": local["predictedPassiveTurnDeg"],
            "passage": section["passage"],
            "corridor": {
                "enabled": section["corridor"]["enabled"],
                "centerDirection": list(section["corridor"]["centerDirection"]),
                "horizontalHalfAngleDeg": section["corridor"]["horizontalHalfAngleDeg"],
                "verticalHalfAngleDeg": section["corridor"]["verticalHalfAngleDeg"],
                "rotationDeg": section["corridor"]["rotationDeg"],
                "actualHorizontalOffsetDeg": horizontal_deg,
                "actualVerticalOffsetDeg": vertical_deg,
                "entryInsideCorridor": (
                    not section["corridor"]["enabled"]
                    or (
                        abs(horizontal_deg)
                        <= section["corridor"]["horizontalHalfAngleDeg"] + 1e-6
                        and abs(vertical_deg)
                        <= section["corridor"]["verticalHalfAngleDeg"] + 1e-6
                    )
                ),
            },
            "relativeTrajectory": local["relativeTrajectory"],
            "lambertEndpointResidualKm": _magnitude(
                _subtract(propagated_position, transfer["entryPosition"])
            ),
            "lambertVelocityResidualKmS": _magnitude(
                _subtract(propagated_velocity, transfer["arrivalVelocity"])
            ),
        })
        segments.extend([
            {
                "id": f"{section['id']}-transfer",
                "label": f"{section['originId']} → {target_data[1]}-SOI",
                "startIndex": transfer_start_index,
                "endIndex": transfer_end_index,
            },
            {
                "id": f"{section['id']}-soi",
                "label": f"{target_data[1]} · Einflussbereich",
                "startIndex": transfer_end_index,
                "endIndex": local_end_index,
            },
        ])
        start_position = local["exitPosition"]
        start_velocity = local["exitVelocity"]
        start_day = local["exitDay"]

    first = calculated_sections[0]
    first_section = sections[0]
    first_relative = first["relativeTrajectory"]
    incoming_relative_velocity = tuple(first_relative[0]["velocityKmS"])
    outgoing_relative_velocity = tuple(first_relative[-1]["velocityKmS"])
    turn_angle_deg = _angle_deg(incoming_relative_velocity, outgoing_relative_velocity)
    first_target_ephemeris, first_target_data = _planet_records(first["targetId"])
    first_planet_position, _ = _planet_state_at(
        first_target_ephemeris, epoch_days + first["periapsisDay"]
    )
    first_periapsis_relative = _subtract(
        tuple(trajectory[first["periapsisIndex"]]["positionKm"]), first_planet_position
    )
    warnings = [
        (
            f"Abschnitt {index + 1} benötigt am Übergang Δv "
            f"{section['requiredTransitionDeltaVKmS']:.2f} km/s."
        )
        for index, section in enumerate(calculated_sections)
        if index > 0 and section["requiredTransitionDeltaVKmS"] > 0.05
    ]
    if (
        calculated_sections[0]["requiredTransitionDeltaVKmS"]
        > calculated_sections[0]["availableTransitionDeltaVKmS"] + 1e-9
    ):
        warnings.insert(
            0,
            (
                "Solarer Austritt benÃ¶tigt Î”v "
                f"{calculated_sections[0]['requiredTransitionDeltaVKmS']:.2f} km/s; "
                "verfÃ¼gbar sind "
                f"{calculated_sections[0]['availableTransitionDeltaVKmS']:.2f} km/s."
            ),
        )
    warnings.extend(
        (
            f"Korridoreinschuss bei {section['targetName']} benötigt Δv "
            f"{section['corridorInsertionDeltaVKmS']:.2f} km/s; der eingestellte "
            f"Δv-Fächer erlaubt +{sections[index]['deltaVPlusKmS']:.2f} km/s."
        )
        for index, section in enumerate(calculated_sections)
        if section["corridorInsertionDeltaVKmS"]
        > sections[index]["deltaVPlusKmS"] + 1e-9
    )
    solar_periapsis_index = segments[0]["endIndex"]
    solar_periapsis_radius_km = _magnitude(
        tuple(trajectory[solar_periapsis_index]["positionKm"])
    )
    solar_corridor_radius_km = max(
        0.12 * AU_KM,
        min(0.35 * AU_KM, solar_periapsis_radius_km * 3.0),
    )
    solar_entry_index = 0
    for trajectory_index in range(solar_periapsis_index - 1, -1, -1):
        if (
            _magnitude(tuple(trajectory[trajectory_index]["positionKm"]))
            >= solar_corridor_radius_km
        ):
            solar_entry_index = trajectory_index
            break
    solar_exit_index = solar_periapsis_index
    for trajectory_index in range(solar_periapsis_index + 1, len(trajectory)):
        if (
            _magnitude(tuple(trajectory[trajectory_index]["positionKm"]))
            >= solar_corridor_radius_km
        ):
            solar_exit_index = trajectory_index
            break
    solar_entry_position = tuple(trajectory[solar_entry_index]["positionKm"])
    solar_exit_position = tuple(trajectory[solar_exit_index]["positionKm"])
    solar_entry_direction = _normalize(solar_entry_position)
    solar_exit_direction = _normalize(solar_exit_position)
    solar_angular_momentum = _cross(
        tuple(trajectory[solar_periapsis_index]["positionKm"]),
        tuple(trajectory[solar_periapsis_index]["velocityKmS"]),
    )
    return {
        "startDate": mission.config.start_date,
        "totalFlightDays": trajectory[-1]["elapsedDays"],
        "warnings": warnings,
        "trajectory": trajectory,
        "segments": segments,
        "routeSections": calculated_sections,
        "solarPassage": {
            "entryIndex": solar_entry_index,
            "periapsisIndex": solar_periapsis_index,
            "exitIndex": solar_exit_index,
            "corridorRadiusKm": solar_corridor_radius_km,
            "periapsisRadiusKm": solar_periapsis_radius_km,
            "entryPositionKm": list(solar_entry_position),
            "exitPositionKm": list(solar_exit_position),
            "entryDirection": list(solar_entry_direction),
            "exitDirection": list(solar_exit_direction),
            "passageAngleDeg": _angle_deg(
                solar_entry_direction,
                solar_exit_direction,
            ),
            "orbitDirection": (
                "prograde" if solar_angular_momentum[2] >= 0 else "retrograde"
            ),
            "outboundAfterPeriapsis": (
                calculated_sections[0]["departureRadialSpeedKmS"] >= -0.02
            ),
        },
        "stateChain": {
            "continuousPosition": True,
            "exitStateFeedsNextSection": True,
            "transitionImpulsesExplicit": True,
            "entryVelocityPreservedAtSoi": all(
                section["entryVelocityPreserved"]
                for section in calculated_sections
            ),
            "flybyPlaneUsesNextTargetLookahead": len(sections) > 1,
            "coordinateConvention": "x=Breite, y=Tiefe, z=Höhe (ECLIPJ2000)",
        },
        "waypoint": {
            "id": first["targetId"],
            "name": first["targetName"],
            "encounterDay": first["periapsisDay"],
            "entryDay": first["entryDay"],
            "exitDay": first["exitDay"],
            "flybyAltitudeKm": first["minimumAltitudeKm"],
            "minimumFlybyAltitudeKm": 0.0,
            "trajectoryIndex": first["periapsisIndex"],
            "positionKm": trajectory[first["periapsisIndex"]]["positionKm"],
        },
        "entryCorridor": {
            "enabled": first_section["corridor"]["enabled"],
            "surface": "planetary sphere of influence",
            "selectionStrategy": "state-continuous 3D multi-section corridor match",
            "centerDirection": list(first_section["corridor"]["centerDirection"]),
            "horizontalHalfAngleDeg": first_section["corridor"]["horizontalHalfAngleDeg"],
            "verticalHalfAngleDeg": first_section["corridor"]["verticalHalfAngleDeg"],
            "rotationDeg": first_section["corridor"]["rotationDeg"],
            "selectedDirection": first["entryDirection"],
            "evaluatedTargetCount": 125 if first_section["corridor"]["enabled"] else 5,
            "actualEntryDirection": first["entryDirection"],
            "actualHorizontalOffsetDeg": first["corridor"]["actualHorizontalOffsetDeg"],
            "actualVerticalOffsetDeg": first["corridor"]["actualVerticalOffsetDeg"],
            "actualEntryPositionKm": first["entryPositionKm"],
            "entryInsideCorridor": first["corridor"]["entryInsideCorridor"],
        },
        "outgoingDirection": list(_normalize(start_velocity)),
        "flybyGeometry": {
            "curveModel": "simultaneous Sun + planet DOP853 propagation",
            "targetingMode": "next-target-directed B-plane with explicit SOI targeting delta-v",
            "sampleCount": len(first_relative),
            "stateContinuousWithinFlyby": True,
            "separateTargetImpulseAtSoiExit": len(calculated_sections) > 1,
            "incomingExcessDirection": list(_normalize(incoming_relative_velocity)),
            "outgoingExcessDirection": list(_normalize(outgoing_relative_velocity)),
            "incomingHeliocentricDirection": list(
                _normalize(tuple(trajectory[first["entryIndex"]]["velocityKmS"]))
            ),
            "outgoingHeliocentricDirection": list(
                _normalize(tuple(trajectory[first["exitIndex"]]["velocityKmS"]))
            ),
            "periapsisRadiusKm": _magnitude(first_periapsis_relative),
            "planetRadiusKm": first_target_data[3] / 1_000,
            "sphereOfInfluenceRadiusKm": first["sphereOfInfluenceRadiusKm"],
            "hyperbolaEccentricity": 0.0,
            "semiMajorAxisMagnitudeKm": 0.0,
            "entryRelativePositionKm": first_relative[0]["positionKm"],
            "periapsisRelativePositionKm": list(first_periapsis_relative),
            "exitRelativePositionKm": first_relative[-1]["positionKm"],
            "entryLatitudeDeg": first["entryLatitudeDeg"],
            "periapsisLatitudeDeg": asin(
                max(-1.0, min(1.0, first_periapsis_relative[2] / _magnitude(first_periapsis_relative)))
            ) * 180 / pi,
            "exitLatitudeDeg": asin(
                max(-1.0, min(1.0, first_relative[-1]["positionKm"][2] /
                              _magnitude(tuple(first_relative[-1]["positionKm"]))))
            ) * 180 / pi,
            "verticalTurnDeg": 0.0,
            "relativeTrajectory": first_relative,
        },
        "summary": {
            "flybyMode": "multi-section",
            "requiredInjectionDeltaVKmS": calculated_sections[0]["requiredTransitionDeltaVKmS"],
            "availableInjectionDeltaVKmS": mission.config.oberth_delta_v_km_s,
            "solarDepartureInjectionApplied": (
                calculated_sections[0]["requiredTransitionDeltaVKmS"]
                <= mission.config.oberth_delta_v_km_s + 1e-9
            ),
            "incomingExcessSpeedKmS": _magnitude(incoming_relative_velocity),
            "turnAngleDeg": turn_angle_deg,
            "heliocentricSpeedBeforeKmS": _magnitude(
                tuple(trajectory[first["entryIndex"]]["velocityKmS"])
            ),
            "heliocentricSpeedAfterKmS": _magnitude(
                tuple(trajectory[first["exitIndex"]]["velocityKmS"])
            ),
            "speedGainKmS": (
                _magnitude(tuple(trajectory[first["exitIndex"]]["velocityKmS"]))
                - _magnitude(tuple(trajectory[first["entryIndex"]]["velocityKmS"]))
            ),
            "targetCorrectionDeltaVKmS": (
                calculated_sections[1]["requiredTransitionDeltaVKmS"]
                if len(calculated_sections) > 1
                else 0.0
            ),
            "targetInjectionApplied": len(calculated_sections) > 1,
            "passiveTargeting": len(calculated_sections) == 1,
            "courseChangeDeg": turn_angle_deg,
            "periapsisSpeedKmS": _magnitude(
                tuple(first_relative[first["periapsisIndex"] - first["entryIndex"]]["velocityKmS"])
            ) if first_relative else 0.0,
            "observationWindowHours": (
                (first["exitDay"] - first["entryDay"]) * 24
            ),
            "targetAlignmentDeg": calculated_sections[-1]["lookaheadAlignmentDeg"],
            "actualTargetAlignmentDeg": calculated_sections[-1]["lookaheadAlignmentDeg"],
            "feasibleWithConfiguredBurn": (
                all(
                    section["requiredTransitionDeltaVKmS"]
                    <= section["availableTransitionDeltaVKmS"] + 1e-9
                    for section in calculated_sections
                    if section.get("sectionType") != "interstellar-asymptote"
                )
                and all(
                    section["corridorInsertionDeltaVKmS"]
                    <= sections[index]["deltaVPlusKmS"] + 1e-9
                    for index, section in enumerate(calculated_sections)
                )
            ),
            "entryCorridorTargeted": first_section["corridor"]["enabled"],
            "entryInsideCorridor": first["corridor"]["entryInsideCorridor"],
            "warnings": warnings,
            "model": "2D corridor chain → Lambert → simultaneous Sun/planet SOI propagation",
            "totalTransitionDeltaVKmS": total_transition_delta_v,
        },
    }


def simulate_route_sections(values: dict | None) -> dict:
    from planner.trajectory_planner import calculate_section_route
    return calculate_section_route(values or {})
