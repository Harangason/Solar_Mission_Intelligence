"""Adaptive launch-window search for target and planetary waypoint missions.

The optimizer uses a fast two-body Solar-Oberth approximation for the broad
search and validates the winning candidate with the full RK4/N-body/Kalman
mission model.  The reported confidence is numerical search convergence, not
a probability of mission success.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import acos, asin, cos, pi, sin, sqrt

from services.calculation_audit import OPTIMIZER_AUDIT_LOG, write_optimizer_audit
from planner.route_planner import (
    G_KM3_KG_S2,
    _dot,
    _lambert,
    _planet_velocity,
    _solve_bidirectional_flyby,
    _solve_solar_target_injection,
    _solar_speed_at_radius,
    simulate_direct_solar_route,
    _subtract,
    simulate_waypoint_route,
)
from solver.trajectory import (
    AU_KM,
    DAY_SECONDS,
    MU_SUN,
    PLANET_EPHEMERIDES,
    MissionConfig,
    _add,
    _earth_state_at,
    _magnitude,
    _mission_epoch_days,
    _normalize,
    _planet_position_at,
    validate_mission_config,
)
from visualization.view_3d_celestials import PLANET_DATA


@dataclass(slots=True)
class Candidate:
    offset_days: int
    encounter_day: float
    start_date: str
    score: float
    required_delta_v: float
    target_alignment_deg: float
    turn_angle_deg: float
    speed_gain: float
    target_correction_delta_v: float
    solar_exit_speed: float
    solar_speed_residual: float
    backward_alignment_deg: float
    boundary_velocity_residual: float
    geometry_feasible: bool
    solar_energy_reachable: bool
    feasible: bool

    def to_dict(self) -> dict:
        return {
            "startDate": self.start_date,
            "encounterDay": self.encounter_day,
            "score": self.score,
            "requiredInjectionDeltaVKmS": self.required_delta_v,
            "targetAlignmentDeg": self.target_alignment_deg,
            "turnAngleDeg": self.turn_angle_deg,
            "speedGainKmS": self.speed_gain,
            "targetCorrectionDeltaVKmS": self.target_correction_delta_v,
            "solarExitSpeedKmS": self.solar_exit_speed,
            "solarSpeedResidualKmS": self.solar_speed_residual,
            "backwardAlignmentDeg": self.backward_alignment_deg,
            "boundaryVelocityResidualKmS": self.boundary_velocity_residual,
            "geometryFeasible": self.geometry_feasible,
            "solarEnergyReachable": self.solar_energy_reachable,
            "feasible": self.feasible,
        }


@dataclass(slots=True)
class DirectCandidate:
    offset_days: int
    start_date: str
    required_vector_delta_v: float
    available_delta_v: float
    angular_change_deg: float

    @property
    def feasible(self) -> bool:
        return self.required_vector_delta_v <= self.available_delta_v

    @property
    def score(self) -> float:
        return self.required_vector_delta_v + (0 if self.feasible else 1_000)

    def to_dict(self) -> dict:
        return {
            "startDate": self.start_date,
            "requiredVectorDeltaVKmS": self.required_vector_delta_v,
            "availableDeltaVKmS": self.available_delta_v,
            "angularChangeDeg": self.angular_change_deg,
            "feasible": self.feasible,
        }


def _target_direction(values: dict) -> tuple[float, float, float]:
    right_ascension = float(values.get("targetRightAscensionDeg", 217.43)) * pi / 180
    declination = float(values.get("targetDeclinationDeg", -62.68)) * pi / 180
    obliquity = 23.43928 * pi / 180
    equatorial_x = cos(declination) * cos(right_ascension)
    equatorial_y = cos(declination) * sin(right_ascension)
    equatorial_z = sin(declination)
    return _normalize((
        equatorial_x,
        equatorial_y * cos(obliquity) + equatorial_z * sin(obliquity),
        -equatorial_y * sin(obliquity) + equatorial_z * cos(obliquity),
    ))


def _calendar_date(start_date: str, elapsed_days: float) -> str:
    return (datetime.fromisoformat(start_date) + timedelta(days=elapsed_days)).date().isoformat()


def _analytical_oberth_state(config: MissionConfig) -> tuple[tuple, tuple, float]:
    earth_position, earth_velocity = _earth_state_at(config.start_date)
    earth_distance = _magnitude(earth_position)
    perihelion_radius = config.target_perihelion_au * AU_KM
    semi_major_axis = (earth_distance + perihelion_radius) / 2
    transfer_seconds = pi * sqrt(semi_major_axis**3 / MU_SUN)
    perihelion_position = tuple(-component * perihelion_radius / earth_distance for component in earth_position)
    initial_prograde = _normalize(earth_velocity)
    perihelion_direction = tuple(-component for component in initial_prograde)
    perihelion_speed = sqrt(MU_SUN * (2 / perihelion_radius - 1 / semi_major_axis))
    post_burn_speed = perihelion_speed + max(0.0, config.oberth_delta_v_km_s)
    velocity = tuple(component * post_burn_speed for component in perihelion_direction)
    return perihelion_position, velocity, transfer_seconds / DAY_SECONDS


def _solar_energy_budget(config: MissionConfig, desired_exit_speed_km_s: float) -> dict:
    """Prove whether the requested 1-AU speed is reachable with the burn budget."""
    burn_position, post_burn_velocity, _ = _analytical_oberth_state(config)
    post_burn_speed = _magnitude(post_burn_velocity)
    pre_burn_speed = max(0.0, post_burn_speed - config.oberth_delta_v_km_s)
    maximum_perihelion_speed = pre_burn_speed + config.oberth_delta_v_km_s
    maximum_velocity = tuple(
        component * maximum_perihelion_speed for component in _normalize(post_burn_velocity)
    )
    maximum_exit_speed = _solar_speed_at_radius(burn_position, maximum_velocity)
    burn_radius = max(_magnitude(burn_position), 1.0)
    required_perihelion_speed = sqrt(max(
        0.0,
        desired_exit_speed_km_s**2 + 2 * MU_SUN * (1 / burn_radius - 1 / AU_KM),
    ))
    minimum_delta_v = max(0.0, required_perihelion_speed - pre_burn_speed)
    tolerance = max(0.25, desired_exit_speed_km_s * 0.005)
    return {
        "desiredExitSpeedKmS": desired_exit_speed_km_s,
        "maximumExitSpeedWithAvailableBurnKmS": maximum_exit_speed,
        "availableOberthDeltaVKmS": config.oberth_delta_v_km_s,
        "minimumOberthDeltaVForDesiredSpeedKmS": minimum_delta_v,
        "additionalDeltaVRequiredKmS": max(0.0, minimum_delta_v - config.oberth_delta_v_km_s),
        "energeticallyReachable": desired_exit_speed_km_s <= maximum_exit_speed + tolerance,
        "constraintKind": "propulsion-delta-v",
        "electricalPowerDeficit": False,
        "model": "two-body energy upper bound at the configured perihelion",
    }


def assess_solar_energy(values: dict | None) -> dict:
    """Return the hard propulsion bound without starting a calendar search."""
    values = values or {}
    config = MissionConfig.from_dict(dict(values.get("mission") or {}))
    errors = validate_mission_config(config)
    if errors:
        raise ValueError(" ".join(errors))
    desired_exit_speed_km_s = float(values.get("desiredSolarExitSpeedKmS", 100.0))
    if not 1.0 <= desired_exit_speed_km_s <= 1_000.0:
        raise ValueError(
            "Die Zielgeschwindigkeit am 1-AE-Sonnenaustritt muss zwischen "
            "1 und 1.000 km/s liegen."
        )
    return _solar_energy_budget(config, desired_exit_speed_km_s)


def _empirical_search_seeds(
    values: dict,
    base_date: date,
    window_days: int,
    encounter_min: float,
    encounter_max: float,
) -> tuple[list[tuple[int, float]], list[str]]:
    """Reuse successful basins from compatible JSONL navigator runs."""
    if not OPTIMIZER_AUDIT_LOG.exists():
        return [], []
    waypoint_id = str(values.get("waypointId") or "jupiter")
    target_ra = float(values.get("targetRightAscensionDeg", 217.43))
    target_dec = float(values.get("targetDeclinationDeg", -62.68))
    desired_speed = float(values.get("desiredSolarExitSpeedKmS", 100.0))
    seeds: list[tuple[int, float]] = []
    source_run_ids: list[str] = []
    try:
        lines = OPTIMIZER_AUDIT_LOG.read_text(encoding="utf-8").splitlines()[-60:]
    except OSError:
        return [], []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        inputs = record.get("inputs") or {}
        if str(inputs.get("waypointId") or "jupiter") != waypoint_id:
            continue
        if abs(float(inputs.get("targetRightAscensionDeg", target_ra)) - target_ra) > 0.05:
            continue
        if abs(float(inputs.get("targetDeclinationDeg", target_dec)) - target_dec) > 0.05:
            continue
        if abs(float(inputs.get("desiredSolarExitSpeedKmS", desired_speed)) - desired_speed) > 0.5:
            continue
        candidates: list[dict] = []
        selected = record.get("selected")
        if isinstance(selected, dict):
            candidates.append(selected)
        candidates.extend(
            candidate for candidate in (record.get("fastSearch") or {}).get("topCandidates", [])[:8]
            if isinstance(candidate, dict)
        )
        candidates.extend(
            candidate for candidate in record.get("fullValidation", [])
            if isinstance(candidate, dict)
        )
        accepted_from_run = False
        for candidate in candidates:
            try:
                start = date.fromisoformat(str(candidate["startDate"]))
                encounter = float(candidate["encounterDay"])
            except (KeyError, TypeError, ValueError):
                continue
            offset = (start - base_date).days
            if -window_days <= offset <= window_days and encounter_min <= encounter <= encounter_max:
                seeds.append((offset, encounter))
                accepted_from_run = True
        if accepted_from_run:
            source_run_ids.append(str(record.get("runId") or "unknown"))
        if len(source_run_ids) >= 8:
            break
    unique: list[tuple[int, float]] = []
    seen: set[tuple[int, int]] = set()
    for offset, encounter in seeds:
        key = (round(offset), round(encounter))
        if key not in seen:
            seen.add(key)
            unique.append((round(offset), encounter))
    return unique[:32], source_run_ids


def _evaluate_direct_candidate(values: dict, mission_values: dict, base_date: date, offset_days: int) -> DirectCandidate:
    start_date = (base_date + timedelta(days=offset_days)).isoformat()
    config = MissionConfig.from_dict({**mission_values, "startDate": start_date})
    _, post_burn_velocity, _ = _analytical_oberth_state(config)
    post_burn_speed = _magnitude(post_burn_velocity)
    pre_burn_speed = max(0.0, post_burn_speed - config.oberth_delta_v_km_s)
    pre_burn_direction = _normalize(post_burn_velocity)
    pre_burn_velocity = tuple(component * pre_burn_speed for component in pre_burn_direction)
    target_direction = _target_direction(values)
    desired_velocity = tuple(component * post_burn_speed for component in target_direction)
    required_delta_v = _magnitude(_subtract(desired_velocity, pre_burn_velocity))
    angular_change = acos(max(-1.0, min(1.0, _dot(pre_burn_direction, target_direction)))) * 180 / pi
    return DirectCandidate(
        offset_days=offset_days,
        start_date=start_date,
        required_vector_delta_v=required_delta_v,
        available_delta_v=config.oberth_delta_v_km_s,
        angular_change_deg=angular_change,
    )


def _evaluate_candidate(
    values: dict,
    mission_values: dict,
    base_date: date,
    offset_days: int,
    encounter_day: float,
) -> Candidate:
    start_date = (base_date + timedelta(days=offset_days)).isoformat()
    candidate_mission = {**mission_values, "startDate": start_date}
    config = MissionConfig.from_dict(candidate_mission)
    burn_position, burn_velocity, burn_day = _analytical_oberth_state(config)
    pre_burn_speed = max(0.0, _magnitude(burn_velocity) - config.oberth_delta_v_km_s)
    pre_burn_velocity = tuple(component * pre_burn_speed for component in _normalize(burn_velocity))
    if encounter_day <= burn_day + 5:
        raise ValueError("Begegnung liegt vor dem abgeschlossenen Sonnenmanöver.")

    waypoint_id = str(values.get("waypointId") or "jupiter")
    ephemeris = next((item for item in PLANET_EPHEMERIDES if item[0] == waypoint_id), None)
    planet_row = next((row for row in PLANET_DATA if row[0] == waypoint_id), None)
    if ephemeris is None or planet_row is None or waypoint_id == "earth":
        raise ValueError("Dieser planetare Wegpunkt wird nicht unterstützt.")

    epoch_days = _mission_epoch_days(start_date)
    encounter_epoch = epoch_days + encounter_day
    planet_position = _planet_position_at(ephemeris, encounter_epoch)
    flight_seconds = (encounter_day - burn_day) * DAY_SECONDS
    departure_velocity, arrival_velocity = _lambert(
        burn_position, planet_position, flight_seconds, burn_velocity,
    )
    required_delta_v = _magnitude(_subtract(departure_velocity, pre_burn_velocity))
    desired_solar_exit_speed = float(values.get("desiredSolarExitSpeedKmS", 100.0))
    solar_energy_budget = _solar_energy_budget(config, desired_solar_exit_speed)
    solar_exit_speed = _solar_speed_at_radius(burn_position, departure_velocity)
    solar_speed_residual = abs(solar_exit_speed - desired_solar_exit_speed)
    solar_speed_tolerance = max(0.25, desired_solar_exit_speed * 0.005)
    planet_velocity = _planet_velocity(ephemeris, encounter_epoch)
    incoming_excess = _subtract(arrival_velocity, planet_velocity)
    excess_speed = _magnitude(incoming_excess)
    altitude_km = float(values.get("flybyAltitudeKm", 100_000.0))
    radius_km = planet_row[3] / 1_000
    planet_mu = G_KM3_KG_S2 * planet_row[2]
    turn_angle = 2 * asin(1 / (1 + (radius_km + altitude_km) * excess_speed**2 / planet_mu))
    target_direction = _target_direction(values)
    flyby_mode = str(values.get("flybyMode") or "acceleration")
    bidirectional = _solve_bidirectional_flyby(
        planet_position,
        planet_velocity,
        incoming_excess,
        target_direction,
        turn_angle,
        flyby_mode,
    )
    outgoing_excess = bidirectional["outgoingExcess"]
    outgoing_velocity = bidirectional["outgoingVelocity"]
    turn_angle = bidirectional["usedTurnRad"]
    target_alignment = bidirectional["actualAlignmentRad"] * 180 / pi
    speed_gain = _magnitude(outgoing_velocity) - _magnitude(arrival_velocity)
    passive_alignment = bidirectional["actualAlignmentRad"]
    if bidirectional["passiveMatch"]:
        target_correction_delta_v = 0.0
    else:
        target_solution = _solve_solar_target_injection(planet_position, outgoing_velocity, target_direction)
        target_correction_delta_v = target_solution["correctionDeltaVKmS"]
    geometry_feasible = (
        required_delta_v <= config.oberth_delta_v_km_s
        and target_correction_delta_v <= config.oberth_delta_v_km_s
        and (
            bidirectional["passiveMatch"]
            or target_correction_delta_v <= config.oberth_delta_v_km_s
        )
    )
    feasible = geometry_feasible and solar_speed_residual <= solar_speed_tolerance

    feasibility_penalty = (
        max(0.0, required_delta_v - config.oberth_delta_v_km_s)
        + max(0.0, target_correction_delta_v - config.oberth_delta_v_km_s)
    ) * 250
    alignment_weight = 3.0 if flyby_mode == "observation" else 0.8
    # Once the energy upper bound proves the speed target impossible, the
    # calendar optimizer should improve the still-changeable route geometry
    # rather than follow a nearly constant speed residual across the dates.
    solar_residual_weight = 22.0 if solar_energy_budget["energeticallyReachable"] else 0.2
    score = (
        required_delta_v * 10
        + target_correction_delta_v * 10
        + target_alignment * alignment_weight
        + solar_speed_residual * solar_residual_weight
        + bidirectional["boundaryVelocityResidualKmS"] * 60
        + bidirectional["actualAlignmentRad"] * 180 / pi * 4
        + max(0.0, -bidirectional["initialTargetProgressKmS"]) * 120
        + encounter_day / 365.25 * 0.5
        - speed_gain * 6
        - turn_angle * 180 / pi * 0.08
        + feasibility_penalty
    )
    return Candidate(
        offset_days=offset_days,
        encounter_day=encounter_day,
        start_date=start_date,
        score=score,
        required_delta_v=required_delta_v,
        target_alignment_deg=target_alignment,
        turn_angle_deg=turn_angle * 180 / pi,
        speed_gain=speed_gain,
        target_correction_delta_v=target_correction_delta_v,
        solar_exit_speed=solar_exit_speed,
        solar_speed_residual=solar_speed_residual,
        backward_alignment_deg=bidirectional["backwardAlignmentRad"] * 180 / pi,
        boundary_velocity_residual=bidirectional["boundaryVelocityResidualKmS"],
        geometry_feasible=geometry_feasible,
        solar_energy_reachable=bool(solar_energy_budget["energeticallyReachable"]),
        feasible=feasible,
    )


def optimize_launch_window(values: dict | None) -> dict:
    values = values or {}
    mission_values = dict(values.get("mission") or {})
    config = MissionConfig.from_dict(mission_values)
    errors = validate_mission_config(config)
    if errors:
        raise ValueError(" ".join(errors))

    base_date = date.fromisoformat(str(values.get("searchStartDate") or config.start_date))
    window_days = int(values.get("searchWindowDays", 1_460))
    confidence_threshold = float(values.get("confidenceThresholdPct", 95.0))
    max_iterations = int(values.get("maxIterations", 10))
    initial_encounter = float(values.get("encounterDay", 730.0))
    desired_solar_exit_speed = float(values.get("desiredSolarExitSpeedKmS", 100.0))
    if not 500 <= window_days <= 7_305:
        raise ValueError("Der bidirektionale Suchhorizont muss zwischen 500 Tagen und 20 Jahren liegen.")
    if not 90 <= confidence_threshold <= 99.9:
        raise ValueError("Die geforderte Genauigkeit muss zwischen 90 und 99,9 Prozent liegen.")
    if not 4 <= max_iterations <= 60:
        raise ValueError("Es sind 4 bis 60 Optimierungsrunden zulässig.")
    if not 1.0 <= desired_solar_exit_speed <= 1_000.0:
        raise ValueError("Die Zielgeschwindigkeit am 1-AE-Sonnenaustritt muss zwischen 1 und 1.000 km/s liegen.")

    # Requested by the mission workflow: both launch epoch and encounter day
    # are searched bidirectionally with a deterministic 100 -> 10 -> 5 -> 1
    # day schedule.  The horizon itself starts at 500 days and may expand to
    # twenty years around the requested start date.
    encounter_min = 500.0
    encounter_max = 7_305.0
    initial_encounter = max(encounter_min, min(encounter_max, initial_encounter))
    search_base_config = MissionConfig.from_dict({
        **mission_values,
        "startDate": base_date.isoformat(),
    })
    solar_energy_budget = _solar_energy_budget(search_base_config, desired_solar_exit_speed)
    cache: dict[tuple[int, float], Candidate | None] = {}

    def evaluate(offset: int, encounter: float) -> Candidate | None:
        bounded_offset = max(-window_days, min(window_days, int(round(offset))))
        bounded_encounter = max(encounter_min, min(encounter_max, round(encounter, 2)))
        key = (bounded_offset, bounded_encounter)
        if key not in cache:
            try:
                cache[key] = _evaluate_candidate(values, mission_values, base_date, *key)
            except (ValueError, RuntimeError, OverflowError, ZeroDivisionError):
                cache[key] = None
        return cache[key]

    empirical_seeds, empirical_run_ids = _empirical_search_seeds(
        values, base_date, window_days, encounter_min, encounter_max,
    )
    seed_pairs = [(0, initial_encounter), *empirical_seeds]
    for offset, encounter in seed_pairs:
        evaluate(offset, encounter)

    def valid_candidates() -> list[Candidate]:
        return [candidate for candidate in cache.values() if candidate is not None]

    def diverse_best(count: int, date_separation: int, encounter_separation: float) -> list[Candidate]:
        selected: list[Candidate] = []
        for candidate in sorted(valid_candidates(), key=lambda item: item.score):
            if any(
                abs(candidate.offset_days - existing.offset_days) < date_separation
                and abs(candidate.encounter_day - existing.encounter_day) < encounter_separation
                for existing in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= count:
                break
        return selected

    # Coarse empirical scan.  Compatible JSONL optima define additional
    # cross-sections, while the requested start epoch always remains present.
    empirical_start_offsets = [offset for offset, _ in empirical_seeds]
    start_scan_offsets = list(dict.fromkeys([0, *empirical_start_offsets[:5]]))
    for offset in start_scan_offsets:
        for encounter in range(int(encounter_min), int(encounter_max) + 1, 100):
            evaluate(offset, encounter)

    # Expand the start-date horizon symmetrically from 500 days.  Boundary
    # probes are paired with the currently strongest encounter basins instead
    # of evaluating the prohibitively large full Cartesian grid.
    explored_horizon = 500
    horizon_stages = list(range(500, window_days + 1, 100))
    if horizon_stages[-1] != window_days:
        horizon_stages.append(window_days)
    for horizon in horizon_stages:
        explored_horizon = horizon
        anchors = diverse_best(3, 100, 100.0)
        encounter_anchors = [initial_encounter, *[anchor.encounter_day for anchor in anchors]]
        for offset in (-horizon, horizon):
            for encounter in encounter_anchors[:4]:
                evaluate(offset, encounter)
        if horizon % 500 == 0 and anchors:
            anchor = anchors[0]
            for date_delta in (-100, 0, 100):
                for encounter_delta in (-100, 0, 100):
                    evaluate(anchor.offset_days + date_delta, anchor.encounter_day + encounter_delta)
        current = diverse_best(1, 1, 1.0)
        if (
            current
            and current[0].geometry_feasible
            and horizon >= max(500, abs(current[0].offset_days) + 500)
        ):
            # Once all geometry constraints are green, a provably unreachable
            # speed target cannot be improved by expanding the calendar.
            if not any(candidate.solar_energy_reachable for candidate in valid_candidates()):
                break

    if not valid_candidates():
        raise RuntimeError("Im gewählten Suchraum wurde keine gültige Lambert-Geometrie gefunden.")

    best = min(valid_candidates(), key=lambda candidate: candidate.score)
    history: list[dict] = []
    confidence = 0.0
    confidence_by_parameter: dict[str, float] = {}
    refinement_schedule = (
        (100, 100, 2),
        (10, 50, 3),
        (5, 25, 3),
        (1, 5, 4),
    )
    pass_budget = max(4, min(max_iterations, sum(stage[2] for stage in refinement_schedule)))
    stage_pass_counts = [1, 1, 1, 1]
    remaining_passes = pass_budget - len(stage_pass_counts)
    while remaining_passes > 0:
        changed = False
        for stage_index in (3, 2, 1, 0):
            if stage_pass_counts[stage_index] >= refinement_schedule[stage_index][2]:
                continue
            stage_pass_counts[stage_index] += 1
            remaining_passes -= 1
            changed = True
            if remaining_passes <= 0:
                break
        if not changed:
            break
    final_grid_candidates: list[Candidate] = []
    iteration = 0

    for stage_index, (step_days, radius_days, _) in enumerate(refinement_schedule, start=1):
        stage_grid: dict[tuple[int, float], Candidate] = {}
        for pass_index in range(stage_pass_counts[stage_index - 1]):
            iteration += 1
            anchor_pool = diverse_best(
                min(10, 3 + pass_index * 2),
                max(step_days * 2, 2),
                max(step_days * 2, 2),
            )
            anchors = anchor_pool[:3] if pass_index == 0 else [anchor_pool[0], *anchor_pool[1 + pass_index * 2:3 + pass_index * 2]]
            anchors = list({
                (anchor.offset_days, anchor.encounter_day): anchor
                for anchor in (anchors or anchor_pool[:3])
            }.values())
            deltas = range(-radius_days, radius_days + 1, step_days)
            for anchor in anchors:
                center_offset = round(anchor.offset_days / step_days) * step_days
                center_encounter = round(anchor.encounter_day / step_days) * step_days
                for delta in deltas:
                    for candidate in (
                        evaluate(center_offset + delta, center_encounter),
                        evaluate(center_offset, center_encounter + delta),
                        evaluate(center_offset + delta, center_encounter + delta),
                        evaluate(center_offset + delta, center_encounter - delta),
                    ):
                        if candidate is not None:
                            stage_grid[(candidate.offset_days, candidate.encounter_day)] = candidate
            if not stage_grid:
                raise RuntimeError(f"Im {step_days}-Tage-Raster wurde kein gültiger Kandidat gefunden.")
            final_grid_candidates = list(stage_grid.values())
            best = min(final_grid_candidates, key=lambda candidate: candidate.score)
            resolution_date = 100 * (1 - step_days / max(window_days, 1))
            resolution_encounter = 100 * (1 - step_days / max(encounter_max - encounter_min, 1))
            shared_resolution = min(resolution_date, resolution_encounter, 99.9)
            confidence_by_parameter = {
                "launchEpoch": max(0.0, min(100.0, resolution_date)),
                "encounterEpoch": max(0.0, min(100.0, resolution_encounter)),
                "requiredDeltaV": shared_resolution,
                "targetAlignment": shared_resolution,
                "flybyTurnAngle": shared_resolution,
                "speedGain": shared_resolution,
                "solarExitSpeed": shared_resolution,
                "forwardBackwardClosure": shared_resolution,
            }
            confidence = min(confidence_by_parameter.values())
            resolved_horizon = max(
                500,
                min(
                    window_days,
                    ((abs(best.offset_days) + step_days - 1) // step_days) * step_days,
                ),
            )
            history.append({
                "iteration": iteration,
                "stage": f"{step_days}-day-grid",
                "stageIndex": stage_index,
                "passWithinStage": pass_index + 1,
                "passesInStage": stage_pass_counts[stage_index - 1],
                "stepDays": step_days,
                "localRadiusDays": radius_days,
                "exploredHorizonDays": explored_horizon,
                "optimizedSearchWindowDays": resolved_horizon,
                "searchWindowResolutionDays": step_days,
                "bestStartDate": best.start_date,
                "bestEncounterDay": best.encounter_day,
                "bestScore": best.score,
                "dateResolutionDays": step_days,
                "encounterResolutionDays": step_days,
                "minimumConfidencePct": confidence,
                "fastModelFeasible": best.feasible,
                "requiredInjectionDeltaVKmS": best.required_delta_v,
                "targetCorrectionDeltaVKmS": best.target_correction_delta_v,
                "solarExitSpeedKmS": best.solar_exit_speed,
                "solarSpeedResidualKmS": best.solar_speed_residual,
                "boundaryVelocityResidualKmS": best.boundary_velocity_residual,
                "geometryFeasible": best.geometry_feasible,
                "solarEnergyReachable": best.solar_energy_reachable,
            })
    optimized_search_window_days = max(500, abs(best.offset_days))

    direct_cache: dict[int, DirectCandidate] = {}

    def evaluate_direct(offset: int) -> DirectCandidate:
        bounded_offset = max(-window_days, min(window_days, int(round(offset))))
        if bounded_offset not in direct_cache:
            direct_cache[bounded_offset] = _evaluate_direct_candidate(
                values,
                mission_values,
                base_date,
                bounded_offset,
            )
        return direct_cache[bounded_offset]

    direct_population = [
        evaluate_direct(round(-window_days + index * (2 * window_days) / 64))
        for index in range(65)
    ]
    direct_best = min(direct_population, key=lambda candidate: candidate.score)
    direct_step = max(1, round(window_days / 32))
    for _ in range(12):
        direct_step = max(1, round(direct_step / 2))
        local_direct = [evaluate_direct(direct_best.offset_days + delta * direct_step) for delta in (-3, -2, -1, 0, 1, 2, 3)]
        direct_best = min([direct_best, *local_direct], key=lambda candidate: candidate.score)
        if direct_step == 1:
            break

    max_full_validations = int(values.get("maxFullValidations", 8))
    max_full_validations = max(3, min(12, max_full_validations))
    ranked_fast_candidates = sorted(final_grid_candidates, key=lambda candidate: candidate.score)
    validation_candidates: list[Candidate] = []
    global_ranked_candidates = sorted(valid_candidates(), key=lambda candidate: candidate.score)
    global_basin_leaders = diverse_best(max_full_validations * 2, 30, 60.0)
    validation_pool = [*ranked_fast_candidates, *global_basin_leaders, *global_ranked_candidates]
    seen_validation_keys: set[tuple[int, float]] = set()
    for separation in (30, 14, 7, 2, 0):
        for candidate in validation_pool:
            key = (candidate.offset_days, candidate.encounter_day)
            if key in seen_validation_keys:
                continue
            if separation > 0 and any(
                abs(candidate.offset_days - selected.offset_days) < separation
                and abs(candidate.encounter_day - selected.encounter_day) < separation
                for selected in validation_candidates
            ):
                continue
            validation_candidates.append(candidate)
            seen_validation_keys.add(key)
            if len(validation_candidates) >= max_full_validations:
                break
        if len(validation_candidates) >= max_full_validations:
            break
    if not validation_candidates:
        validation_candidates = [best]

    full_validations: list[dict] = []
    validated_routes: list[tuple[float, Candidate, dict, dict, bool, bool]] = []
    for rank, candidate in enumerate(validation_candidates, start=1):
        candidate_values = {
            **values,
            "mission": {**mission_values, "startDate": candidate.start_date},
            "encounterDay": candidate.encounter_day,
        }
        try:
            candidate_route = simulate_waypoint_route(candidate_values, include_mission_result=True)
            candidate_mission = candidate_route.pop("mission")
            summary = candidate_route["summary"]
            transitions = candidate_route.get("transitionDiagnostics") or {}
            geometry = candidate_route.get("flybyGeometry") or {}
            solar_boundary = candidate_route.get("solarBoundary") or {}
            bidirectional_match = transitions.get("bidirectionalMatch") or {}
            energy_bound_reachable = bool(
                solar_boundary.get("energeticallyReachable", candidate.solar_energy_reachable)
            )
            geometry_reasons: list[str] = []
            energy_reasons: list[str] = []
            if float(summary["requiredInjectionDeltaVKmS"]) > config.oberth_delta_v_km_s:
                geometry_reasons.append("Lambert-Einspritz-Δv über Budget")
            if float(summary.get("targetCorrectionDeltaVKmS", 0.0)) > config.oberth_delta_v_km_s:
                geometry_reasons.append("Zielinjektions-Δv über Budget")
            if float(summary["targetAlignmentDeg"]) > 0.01:
                geometry_reasons.append("asymptotischer Zielwinkel über 0,01°")
            actual_alignment_value = summary.get("actualTargetAlignmentDeg")
            if actual_alignment_value is None or float(actual_alignment_value) > 0.01:
                geometry_reasons.append("tatsächlicher Zielwinkel nach Jupiter über 0,01°")
            if not bool(solar_boundary.get("speedBoundaryReached", False)):
                if not energy_bound_reachable:
                    energy_reasons.append(
                        "Zielgeschwindigkeit mit dem Antrieb nicht erreichbar: "
                        f"mindestens {float(solar_boundary.get('minimumOberthDeltaVForDesiredSpeedKmS', solar_energy_budget['minimumOberthDeltaVForDesiredSpeedKmS'])):.2f} km/s "
                        f"Oberth-Δv erforderlich, {config.oberth_delta_v_km_s:.2f} km/s verfügbar"
                    )
                else:
                    energy_reasons.append("Zielgeschwindigkeit am 1-AE-Sonnenaustritt nicht erreicht")
            if not bool(summary.get("targetProgressMonotonic", False)):
                geometry_reasons.append("Flugbahn bewegt sich nach Jupiter zeitweise vom Zielkorridor weg")
            if float(transitions.get("lambertPropagationEndpointResidualKm", float("inf"))) > 10.0:
                geometry_reasons.append("Lambert-Propagation verfehlt Randpunkt um mehr als 10 km")
            if float(transitions.get("entryVelocityResidualKmS", float("inf"))) > 0.01:
                geometry_reasons.append("Lambert-/SOI-Geschwindigkeitsrest über 10 m/s")
            if float(geometry.get("periapsisRadiusKm", 0.0)) <= float(geometry.get("planetRadiusKm", 0.0)):
                geometry_reasons.append("Vorbeiflug schneidet den Körper")
            if candidate_route.get('schemaVersion')=='2.0':
                geometry_reasons,energy_reasons=_physical_route_reasons(candidate_route)
            reasons = [*geometry_reasons, *energy_reasons]
            geometry_plausible = not geometry_reasons
            plausible = geometry_plausible and not energy_reasons and bool(summary["feasibleWithConfiguredBurn"])
            solar_score_weight = 100.0 if energy_bound_reachable else 0.1
            # Once an explicit, budgeted target impulse is applied, the raw
            # passive boundary mismatch must not dominate the score a second
            # time. Mission duration is a real tie-breaker among otherwise
            # green geometries, so a 19-year detour cannot beat a comparable
            # eight-year solution merely because its passive residual is zero.
            boundary_score_weight = 10.0
            duration_penalty = candidate.encounter_day / 365.25
            full_score = (
                max(0.0, float(summary["requiredInjectionDeltaVKmS"]) - config.oberth_delta_v_km_s) * 1_000
                + max(0.0, float(summary.get("targetCorrectionDeltaVKmS", 0.0)) - config.oberth_delta_v_km_s) * 1_000
                + float(summary["requiredInjectionDeltaVKmS"])
                + float(summary.get("targetCorrectionDeltaVKmS", 0.0)) * 10
                + float(summary["targetAlignmentDeg"]) * 100
                + float(solar_boundary.get("speedResidualKmS", 0.0)) * solar_score_weight
                + float(bidirectional_match.get("boundaryVelocityResidualKmS", 0.0)) * boundary_score_weight
                + float(transitions.get("lambertPropagationEndpointResidualKm", 0.0)) * 0.01
                + duration_penalty
            )
            if candidate_route.get('schemaVersion')=='2.0':
                full_score=float(summary['totalDeltaVKmS'])+duration_penalty+float(summary['targetAlignmentDeg'])*100+(0 if plausible else 100000)
            validation = {
                "rank": rank,
                "role": "optimized-candidate",
                "startDate": candidate.start_date,
                "encounterDay": candidate.encounter_day,
                "fastModel": candidate.to_dict(),
                "routeAudit": candidate_route.get("audit"),
                "requiredInjectionDeltaVKmS": summary["requiredInjectionDeltaVKmS"],
                "targetCorrectionDeltaVKmS": summary.get("targetCorrectionDeltaVKmS", 0.0),
                "targetAlignmentDeg": summary["targetAlignmentDeg"],
                "burnToLambertDirectionDeg": transitions.get("burnToLambertDirectionChangeDeg"),
                "solarExitSpeedKmS": solar_boundary.get("actualExitSpeedKmS"),
                "solarSpeedResidualKmS": solar_boundary.get("speedResidualKmS"),
                "bidirectionalBoundaryResidualKmS": bidirectional_match.get("boundaryVelocityResidualKmS"),
                "postFlybyTargetProgressMonotonic": summary.get("targetProgressMonotonic"),
                "lambertEndpointResidualKm": transitions.get("lambertPropagationEndpointResidualKm"),
                "plausible": plausible,
                "geometryPlausible": geometry_plausible,
                "solarEnergyReachable": energy_bound_reachable,
                "rejectionReasons": reasons,
                "fullScore": full_score,
                "durationPenalty": duration_penalty,
            }
            full_validations.append(validation)
            validated_routes.append((
                full_score,
                candidate,
                candidate_route,
                candidate_mission,
                plausible,
                geometry_plausible,
            ))
        except (ValueError, RuntimeError, OverflowError, ZeroDivisionError) as error:
            full_validations.append({
                "rank": rank,
                "role": "optimized-candidate",
                "startDate": candidate.start_date,
                "encounterDay": candidate.encounter_day,
                "fastModel": candidate.to_dict(),
                "plausible": False,
                "rejectionReasons": [f"Vollmodell abgebrochen: {error}"],
            })
    if not validated_routes:
        raise RuntimeError("Keiner der KI-Kandidaten konnte mit dem Vollmodell propagiert werden.")
    plausible_routes = [item for item in validated_routes if item[4]]
    geometry_routes = [item for item in validated_routes if item[5]]
    selected_validation = min(plausible_routes or geometry_routes or validated_routes, key=lambda item: item[0])
    _, best, route, mission, full_plausible, optimized_geometry_plausible = selected_validation
    optimized_search_window_days = max(500, abs(best.offset_days))
    optimized_geometry_plausible = bool(optimized_geometry_plausible)
    selected_solar_boundary = route.get("solarBoundary") or {}
    selected_energy_budget = {
        "desiredExitSpeedKmS": desired_solar_exit_speed,
        "maximumExitSpeedWithAvailableBurnKmS": float(selected_solar_boundary.get(
            "maximumExitSpeedWithAvailableBurnKmS",
            solar_energy_budget["maximumExitSpeedWithAvailableBurnKmS"],
        )),
        "availableOberthDeltaVKmS": config.oberth_delta_v_km_s,
        "minimumOberthDeltaVForDesiredSpeedKmS": float(selected_solar_boundary.get(
            "minimumOberthDeltaVForDesiredSpeedKmS",
            solar_energy_budget["minimumOberthDeltaVForDesiredSpeedKmS"],
        )),
        "additionalDeltaVRequiredKmS": float(selected_solar_boundary.get(
            "additionalDeltaVRequiredKmS",
            solar_energy_budget["additionalDeltaVRequiredKmS"],
        )),
        "energeticallyReachable": bool(selected_solar_boundary.get(
            "energeticallyReachable",
            solar_energy_budget["energeticallyReachable"],
        )),
        "constraintKind": "propulsion-delta-v",
        "electricalPowerDeficit": False,
        "model": str(selected_solar_boundary.get(
            "energyBoundModel",
            solar_energy_budget["model"],
        )),
    }
    direct_values = {
        **values,
        "mission": {**mission_values, "startDate": direct_best.start_date},
    }
    direct_route = simulate_direct_solar_route(direct_values, include_mission_result=True)
    direct_mission = direct_route.pop("mission")
    gravity_feasible = bool(route["summary"]["feasibleWithConfiguredBurn"])
    direct_feasible = bool(direct_route["summary"]["feasibleWithConfiguredBurn"])
    gravity_quality = (
        float(route["summary"]["targetAlignmentDeg"])
        + float(route["summary"]["requiredInjectionDeltaVKmS"]) * 4
        + float(route["summary"].get("targetCorrectionDeltaVKmS", 0.0)) * 4
        - float(route["summary"]["speedGainKmS"]) * 2
        + (0 if gravity_feasible else 1_000)
    )
    direct_quality = (
        float(direct_route["summary"]["finalTargetAlignmentDeg"])
        + float(direct_route["summary"]["requiredVectorDeltaVKmS"]) * 4
        + (0 if direct_feasible else 1_000)
    )
    recommended_alternative = "gravityAssist" if gravity_quality <= direct_quality else "directSolar"
    model_note = ('Die schnelle Rastersuche liefert Kandidaten. Die Vollprüfung verwendet den gemeinsamen physikalischen Planner mit vollständiger Zustandskette, Kollisionsprüfung und Gesamtbudget. Die Suche beweist kein globales Optimum.')
    stop_reason = (
        "plausible-route-found"
        if full_plausible
        else "solar-energy-boundary-unreachable-with-configured-burn"
        if optimized_geometry_plausible and not selected_energy_budget["energeticallyReachable"]
        else "search-bounds-exhausted-without-plausible-route"
    )
    requested_validation = next(
        (validation for validation in full_validations if validation.get("role") == "requested-plan"),
        None,
    )
    requested_plan = {
        "startDate": base_date.isoformat(),
        "encounterDay": initial_encounter,
        "encounterDate": _calendar_date(base_date.isoformat(), initial_encounter),
        "validation": requested_validation,
        "usedOnlyAsSearchSeed": True,
        "isConstraint": False,
    }
    plan_comparison = {
        "startDateChanged": best.start_date != base_date.isoformat(),
        "startDateDeltaDays": best.offset_days,
        "encounterDayChanged": abs(best.encounter_day - initial_encounter) >= 0.01,
        "encounterDayDelta": best.encounter_day - initial_encounter,
        "searchWindowChanged": optimized_search_window_days != window_days,
        "optimizedSearchWindowDays": optimized_search_window_days,
        "requestedPlanPlausible": bool(requested_validation and requested_validation.get("plausible")),
        "optimizedPlanPlausible": full_plausible,
        "optimizedGeometryPlausible": optimized_geometry_plausible,
    }
    optimizer_audit = write_optimizer_audit({
        "inputs": {
            "mission": config.to_dict(),
            "waypointId": values.get("waypointId", "jupiter"),
            "targetRightAscensionDeg": values.get("targetRightAscensionDeg", 217.43),
            "targetDeclinationDeg": values.get("targetDeclinationDeg", -62.68),
            "searchStartDate": base_date.isoformat(),
            "searchWindowDays": window_days,
            "encounterWindowDays": [encounter_min, encounter_max],
            "searchDirection": "bidirectional around searchStartDate",
            "refinementStepsDays": [100, 10, 5, 1],
            "refinementPasses": iteration,
            "stagePassCounts": stage_pass_counts,
            "confidenceThresholdPct": confidence_threshold,
            "desiredSolarExitSpeedKmS": desired_solar_exit_speed,
            "maxIterations": max_iterations,
            "maxFullValidations": max_full_validations,
        },
        "fastSearch": {
            "evaluatedCandidates": len(cache),
            "iterations": history,
            "empiricalSeedRunIds": empirical_run_ids,
            "empiricalSeedCount": len(empirical_seeds),
            "exploredHorizonDays": explored_horizon,
            "topCandidates": [candidate.to_dict() for candidate in ranked_fast_candidates[:25]],
        },
        "fullValidation": full_validations,
        "selected": {
            "startDate": best.start_date,
            "encounterDay": best.encounter_day,
            "encounterDate": _calendar_date(best.start_date, best.encounter_day),
            "routeAudit": route.get("audit"),
            "plausible": full_plausible,
            "requiredInjectionDeltaVKmS": route["summary"]["requiredInjectionDeltaVKmS"],
            "targetCorrectionDeltaVKmS": route["summary"].get("targetCorrectionDeltaVKmS", 0.0),
            "targetAlignmentDeg": route["summary"]["targetAlignmentDeg"],
            "solarBoundary": route.get("solarBoundary"),
            "bidirectionalMatch": (route.get("transitionDiagnostics") or {}).get("bidirectionalMatch"),
            "postFlybyTargetProgressMonotonic": route["summary"].get("targetProgressMonotonic"),
            "geometryPlausible": optimized_geometry_plausible,
            "solarEnergyFeasibility": selected_energy_budget,
        },
        "requestedPlan": requested_plan,
        "planComparison": plan_comparison,
        "directAlternative": {
            "startDate": direct_best.start_date,
            "feasible": direct_feasible,
            "requiredVectorDeltaVKmS": direct_route["summary"]["requiredVectorDeltaVKmS"],
        },
        "stopReason": stop_reason,
    })
    return {
        "optimizedStartDate": best.start_date,
        "optimizedEncounterDay": best.encounter_day,
        "optimizedEncounterDate": _calendar_date(best.start_date, best.encounter_day),
        "optimizedSearchWindowDays": optimized_search_window_days,
        "requestedPlan": requested_plan,
        "planComparison": plan_comparison,
        "confidenceThresholdPct": confidence_threshold,
        "minimumConfidencePct": confidence,
        "converged": confidence >= confidence_threshold and full_plausible,
        "plausible": full_plausible,
        "geometryPlausible": optimized_geometry_plausible,
        "stopReason": stop_reason,
        "iterations": iteration,
        "evaluations": len(cache) + len(direct_cache),
        "confidenceByParameter": confidence_by_parameter,
        "bestCandidate": best.to_dict(),
        "history": history,
        "fullValidationCandidates": full_validations,
        "audit": optimizer_audit,
        "model": model_note,
        "searchStrategy": {
            "direction": "bidirectional",
            "centerStartDate": base_date.isoformat(),
            "minimumHorizonDays": 500,
            "maximumHorizonDays": window_days,
            "exploredHorizonDays": explored_horizon,
            "optimizedHorizonDays": optimized_search_window_days,
            "encounterBoundsDays": [encounter_min, encounter_max],
            "refinementStepsDays": [100, 10, 5, 1],
            "refinementPasses": iteration,
            "stagePassCounts": stage_pass_counts,
            "empiricalSeedRunIds": empirical_run_ids,
            "empiricalSeedCount": len(empirical_seeds),
        },
        "solarEnergyFeasibility": selected_energy_budget,
        "bidirectionalSearch": {
            "method": "forward-solar/Lambert + backward-target/Jupiter boundary matching",
            "desiredSolarExitSpeedKmS": desired_solar_exit_speed,
            "solarEntry": route.get("solarBoundary"),
            "jupiterMatch": (route.get("transitionDiagnostics") or {}).get("bidirectionalMatch"),
            "postFlybyTargetProgressMonotonic": route["summary"].get("targetProgressMonotonic", False),
            "minimumTargetProgressRateKmS": route["summary"].get("minimumTargetProgressRateKmS"),
        },
        "mission": mission,
        "route": route,
        "alternatives": {
            "recommended": recommended_alternative,
            "recommendationFeasible": gravity_feasible if recommended_alternative == "gravityAssist" else direct_feasible,
            "gravityAssist": {
                "startDate": best.start_date,
                "encounterDay": best.encounter_day,
                "feasible": gravity_feasible,
                "qualityScore": gravity_quality,
                "route": route,
            },
            "directSolar": {
                "startDate": direct_best.start_date,
                "feasible": direct_feasible,
                "qualityScore": direct_quality,
                "candidate": direct_best.to_dict(),
                "mission": direct_mission,
                "route": direct_route,
            },
        },
    }


def _physical_route_reasons(route):
    """Use measured common-planner evidence, never obsolete adapter fields."""
    validation=route.get('validation') or {};summary=route.get('summary') or {}
    geometry=[];energy=[]
    for key in ('collisionFree','stateContinuous','targetReached','ephemeridesValidated'):
        if validation.get(key) is not True:geometry.append(f'{key}: physikalischer Nachweis fehlt oder ist nicht erfüllt')
    if summary.get('targetAlignmentDeg') is not None and summary['targetAlignmentDeg']>.01:geometry.append('Zielasymptote verfehlt')
    if not summary.get('vehicleValidated') or summary.get('vehicleFeasible') is not True:energy.append('Fahrzeug-/Treibstoffnachweis nicht erfüllt')
    if not validation.get('constraintsSatisfied'):energy.append('Missionsgrenzen nicht erfüllt')
    if route.get('solarBoundary') and not route['solarBoundary'].get('speedBoundaryReached'):energy.append('Solar-Austrittsgeschwindigkeit nicht erfüllt')
    if not summary.get('feasibleWithConfiguredBurn',summary.get('feasible')) and not geometry and not energy:energy.append('Mission nicht durchgängig durchführbar')
    return geometry,energy
