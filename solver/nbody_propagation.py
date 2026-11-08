"""Continuous high-accuracy heliocentric N-body spacecraft propagation."""

from __future__ import annotations

from dataclasses import dataclass
from math import acos
from typing import Callable

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares, minimize_scalar

from solver.trajectory import (
    DAY_SECONDS,
    G_KM3_KG_S2,
    MU_SUN,
    PLANET_EPHEMERIDES,
    State,
    Vector,
    _planet_state_at,
)


POSITION_ATOL_KM = 1.0e-3
VELOCITY_ATOL_KM_S = 1.0e-12
RELATIVE_TOLERANCE = 1.0e-11
MAXIMUM_STEP_SECONDS = 3.0 * DAY_SECONDS
TARGET_POSITION_TOLERANCE_KM = 10.0


def _norm(vector: np.ndarray) -> float:
    return float(np.linalg.norm(vector))


def _unit(vector: np.ndarray) -> np.ndarray:
    magnitude = _norm(vector)
    if magnitude == 0.0:
        raise RuntimeError("N-Körper-Kraftmodell erhielt einen Nullvektor.")
    return vector / magnitude


def continuous_n_body_acceleration(
    position_km: Vector,
    days_since_j2000: float,
) -> Vector:
    """Evaluate Sun and all moving planets in one heliocentric force model."""
    position = np.asarray(position_km, dtype=float)
    radius = _norm(position)
    if radius == 0.0:
        raise RuntimeError("Sonnenzentrische N-Körper-Beschleunigung ist bei r=0 singulär.")

    acceleration = -MU_SUN * position / radius**3
    for ephemeris in PLANET_EPHEMERIDES:
        planet_position_tuple, _ = _planet_state_at(ephemeris, days_since_j2000)
        planet_position = np.asarray(planet_position_tuple, dtype=float)
        relative = planet_position - position
        separation = _norm(relative)
        planet_radius = _norm(planet_position)
        if separation == 0.0 or planet_radius == 0.0:
            raise RuntimeError(
                f"N-Körper-Beschleunigung ist am Ort von {ephemeris[0]!r} singulär."
            )

        from solver.ephemeris import body_gm
        planet_mu = body_gm(ephemeris[0]) or G_KM3_KG_S2 * float(ephemeris[1])
        acceleration += planet_mu * (
            relative / separation**3
            - planet_position / planet_radius**3
        )

    # Natural satellites are separate gravitating centers; a planet's GM
    # denotes its center, rather than the whole planet/satellite barycenter.
    from solver.ephemeris import EPHEMERIS
    for body_id in ('earth-moon','mars-phobos','mars-deimos','jupiter-io','jupiter-europa','jupiter-ganymede','jupiter-callisto','saturn-titan','saturn-enceladus','uranus-titania','uranus-oberon','neptune-triton'):
        mu=body_gm(body_id)
        if not mu:continue
        try:resolved=EPHEMERIS.state(body_id,days_since_j2000*DAY_SECONDS)
        except (ValueError,RuntimeError):continue
        if resolved is None:continue
        body_position=np.asarray(resolved[0][:3]);relative=body_position-position
        separation=_norm(relative);center_radius=_norm(body_position)
        if separation==0:raise RuntimeError(f'Gravitationszentrum {body_id} erreicht.')
        acceleration+=mu*(relative/separation**3-body_position/center_radius**3)
    return tuple(float(value) for value in acceleration)  # type: ignore[return-value]


def _derivative(epoch_days_j2000: float) -> Callable[[float, np.ndarray], np.ndarray]:
    def derivative(elapsed_seconds: float, state: np.ndarray) -> np.ndarray:
        acceleration = continuous_n_body_acceleration(
            tuple(float(value) for value in state[:3]),  # type: ignore[arg-type]
            epoch_days_j2000 + elapsed_seconds / DAY_SECONDS,
        )
        return np.concatenate((state[3:], np.asarray(acceleration)))

    return derivative


@dataclass
class Propagation:
    solution: object
    start_seconds: float
    end_seconds: float

    @property
    def successful(self) -> bool:
        return bool(self.solution.success)

    @property
    def final_state(self) -> State:
        values = self.solution.y[:, -1]
        return (
            tuple(float(value) for value in values[:3]),
            tuple(float(value) for value in values[3:]),
        )  # type: ignore[return-value]

    def state_at(self, elapsed_seconds: float) -> State:
        values = self.solution.sol(elapsed_seconds)
        return (
            tuple(float(value) for value in values[:3]),
            tuple(float(value) for value in values[3:]),
        )  # type: ignore[return-value]


def propagate_continuous_n_body(
    initial_state: State,
    start_day: float,
    end_day: float,
    epoch_days_j2000: float,
    *,
    dense_output: bool = True,
    maximum_step_seconds: float = MAXIMUM_STEP_SECONDS,
) -> Propagation:
    """Propagate a massless spacecraft under simultaneous moving-body gravity."""
    start_seconds = start_day * DAY_SECONDS
    end_seconds = end_day * DAY_SECONDS
    if end_seconds <= start_seconds:
        raise ValueError("Das Ende der N-Körper-Propagation muss nach dem Start liegen.")

    initial = np.asarray((*initial_state[0], *initial_state[1]), dtype=float)
    solution = solve_ivp(
        _derivative(epoch_days_j2000),
        (start_seconds, end_seconds),
        initial,
        method="DOP853",
        rtol=RELATIVE_TOLERANCE,
        atol=np.asarray(
            [POSITION_ATOL_KM] * 3 + [VELOCITY_ATOL_KM_S] * 3
        ),
        max_step=maximum_step_seconds,
        dense_output=dense_output,
    )
    if not solution.success:
        raise RuntimeError(f"N-Körper-Propagation fehlgeschlagen: {solution.message}")
    return Propagation(solution, start_seconds, end_seconds)


def _sample(
    propagation: Propagation,
    start_day: float,
    end_day: float,
    sample_count: int,
) -> list[dict]:
    times = np.linspace(
        start_day * DAY_SECONDS,
        end_day * DAY_SECONDS,
        sample_count + 1,
    )
    samples = []
    for elapsed_seconds in times:
        position, velocity = propagation.state_at(float(elapsed_seconds))
        samples.append(
            {
                "elapsedDays": float(elapsed_seconds / DAY_SECONDS),
                "positionKm": list(position),
                "velocityKmS": list(velocity),
            }
        )
    return samples


def _position_residual(
    propagation: Propagation,
    target_position: Vector,
) -> np.ndarray:
    final_position = np.asarray(propagation.final_state[0])
    return final_position - np.asarray(target_position)


def validate_continuous_waypoint_route(
    *,
    epoch_days_j2000: float,
    burn_day: float,
    burn_position_km: Vector,
    reference_departure_velocity_km_s: Vector,
    entry_day: float,
    reference_entry_position_km: Vector,
    reference_entry_velocity_km_s: Vector,
    exit_day: float,
    reference_exit_position_km: Vector,
    reference_gravity_exit_velocity_km_s: Vector,
    target_impulse_km_s: Vector,
    outbound_end_day: float,
    waypoint_ephemeris: tuple,
    waypoint_radius_km: float,
) -> dict:
    """Correct the departure state, then validate the flyby without force switches."""
    reference_velocity = np.asarray(reference_departure_velocity_km_s, dtype=float)
    target_position = np.asarray(reference_entry_position_km, dtype=float)

    evaluations = 0

    def residual(candidate_velocity: np.ndarray) -> np.ndarray:
        nonlocal evaluations
        evaluations += 1
        propagation = propagate_continuous_n_body(
            (
                burn_position_km,
                tuple(float(value) for value in candidate_velocity),
            ),  # type: ignore[arg-type]
            burn_day,
            entry_day,
            epoch_days_j2000,
            dense_output=False,
        )
        # Scale to keep the nonlinear least-squares objective near unity.
        return _position_residual(
            propagation, reference_entry_position_km
        ) / 1.0e6

    correction = least_squares(
        residual,
        reference_velocity,
        bounds=(reference_velocity - 25.0, reference_velocity + 25.0),
        xtol=1.0e-10,
        ftol=1.0e-10,
        gtol=1.0e-10,
        max_nfev=20,
        x_scale="jac",
    )
    corrected_velocity = tuple(float(value) for value in correction.x)
    transfer = propagate_continuous_n_body(
        (burn_position_km, corrected_velocity),  # type: ignore[arg-type]
        burn_day,
        entry_day,
        epoch_days_j2000,
    )
    entry_state = transfer.final_state
    entry_residual_km = _norm(np.asarray(entry_state[0]) - target_position)

    flyby = propagate_continuous_n_body(
        entry_state,
        entry_day,
        exit_day,
        epoch_days_j2000,
    )

    def waypoint_distance_squared(elapsed_seconds: float) -> float:
        spacecraft_position = np.asarray(flyby.state_at(elapsed_seconds)[0])
        waypoint_position = np.asarray(
            _planet_state_at(
                waypoint_ephemeris,
                epoch_days_j2000 + elapsed_seconds / DAY_SECONDS,
            )[0]
        )
        relative = spacecraft_position - waypoint_position
        return float(np.dot(relative, relative))

    coarse_times = np.linspace(flyby.start_seconds, flyby.end_seconds, 401)
    coarse_distances = [
        waypoint_distance_squared(float(elapsed_seconds))
        for elapsed_seconds in coarse_times
    ]
    nearest_index = int(np.argmin(coarse_distances))
    lower_index = max(0, nearest_index - 1)
    upper_index = min(len(coarse_times) - 1, nearest_index + 1)
    nearest = minimize_scalar(
        waypoint_distance_squared,
        bounds=(
            float(coarse_times[lower_index]),
            float(coarse_times[upper_index]),
        ),
        method="bounded",
        options={"xatol": 1.0e-3},
    )
    periapsis_seconds = float(nearest.x)
    periapsis_radius_km = float(np.sqrt(nearest.fun))
    periapsis_altitude_km = periapsis_radius_km - waypoint_radius_km

    gravity_exit_state = flyby.final_state
    impulse = np.asarray(target_impulse_km_s, dtype=float)
    outbound_initial_velocity = tuple(
        float(value)
        for value in np.asarray(gravity_exit_state[1]) + impulse
    )
    outbound = propagate_continuous_n_body(
        (gravity_exit_state[0], outbound_initial_velocity),  # type: ignore[arg-type]
        exit_day,
        outbound_end_day,
        epoch_days_j2000,
    )

    corrected_delta = np.asarray(corrected_velocity) - reference_velocity
    entry_velocity_residual = (
        np.asarray(entry_state[1]) - np.asarray(reference_entry_velocity_km_s)
    )
    exit_position_residual = (
        np.asarray(gravity_exit_state[0]) - np.asarray(reference_exit_position_km)
    )
    exit_velocity_residual = (
        np.asarray(gravity_exit_state[1])
        - np.asarray(reference_gravity_exit_velocity_km_s)
    )
    planet_entry_velocity = np.asarray(
        _planet_state_at(waypoint_ephemeris, epoch_days_j2000 + entry_day)[1]
    )
    planet_exit_velocity = np.asarray(
        _planet_state_at(waypoint_ephemeris, epoch_days_j2000 + exit_day)[1]
    )
    incoming_relative = np.asarray(entry_state[1]) - planet_entry_velocity
    outgoing_relative = np.asarray(gravity_exit_state[1]) - planet_exit_velocity
    turn_cosine = float(
        np.clip(np.dot(_unit(incoming_relative), _unit(outgoing_relative)), -1.0, 1.0)
    )

    transfer_samples = _sample(transfer, burn_day, entry_day, 240)
    flyby_samples = _sample(flyby, entry_day, exit_day, 600)
    outbound_samples = _sample(outbound, exit_day, outbound_end_day, 300)
    trajectory = [
        *transfer_samples,
        *flyby_samples[1:],
        *outbound_samples[1:],
    ]

    return {
        "enabled": True,
        "converged": bool(correction.success)
        and entry_residual_km <= TARGET_POSITION_TOLERANCE_KM,
        "collision": periapsis_altitude_km <= 0.0,
        "forceModel": (
            "heliocentric Sun gravity plus direct and indirect gravity from "
            "all eight simultaneously moving planets"
        ),
        "integrator": {
            "method": "DOP853",
            "relativeTolerance": RELATIVE_TOLERANCE,
            "positionAbsoluteToleranceKm": POSITION_ATOL_KM,
            "velocityAbsoluteToleranceKmS": VELOCITY_ATOL_KM_S,
            "maximumStepSeconds": MAXIMUM_STEP_SECONDS,
        },
        "differentialCorrection": {
            "success": bool(correction.success),
            "message": correction.message,
            "evaluations": evaluations,
            "referenceDepartureVelocityKmS": list(reference_velocity),
            "correctedDepartureVelocityKmS": list(corrected_velocity),
            "correctionVectorKmS": list(corrected_delta),
            "correctionMagnitudeKmS": _norm(corrected_delta),
            "entryPositionResidualKm": entry_residual_km,
            "entryVelocityResidualKmS": _norm(entry_velocity_residual),
        },
        "flyby": {
            "continuousAcrossSoiBoundaries": True,
            "periapsisDay": periapsis_seconds / DAY_SECONDS,
            "periapsisRadiusKm": periapsis_radius_km,
            "periapsisAltitudeKm": periapsis_altitude_km,
            "turnAngleDeg": acos(turn_cosine) * 180.0 / np.pi,
            "exitPositionResidualToPatchedConicKm": _norm(exit_position_residual),
            "exitVelocityResidualToPatchedConicKmS": _norm(exit_velocity_residual),
        },
        "maneuvers": (
            [
                {
                    "elapsedDay": exit_day,
                    "kind": "target-injection",
                    "deltaVVectorKmS": list(impulse),
                    "deltaVMagnitudeKmS": _norm(impulse),
                }
            ]
            if _norm(impulse) > 1.0e-12
            else []
        ),
        "trajectory": trajectory,
    }


def propagate_conic(initial_state, duration_seconds, mu, *, sample_count=180,
                    stop_radius_km=None, crossing_direction=0, stop_periapsis=False,
                    collision_radius_km=0.0, gravity_acceleration=None, other_collisions=None, stop_aphelion=False):
    """Event-resolved two-body propagation shared by local and Lambert arcs."""
    initial = np.asarray((*initial_state[0], *initial_state[1]), dtype=float)
    if initial.shape != (6,) or not np.isfinite(initial).all() or mu <= 0 or duration_seconds <= 0:
        raise ValueError("Ungültiger Propagationszustand, GM oder Zeitraum.")
    def derivative(t, y):
        radius = float(np.linalg.norm(y[:3]))
        if radius <= 0:
            raise RuntimeError("Gravitationszentrum erreicht.")
        acceleration=gravity_acceleration(t,y) if gravity_acceleration else -mu*y[:3]/radius**3
        return np.r_[y[3:],acceleration]
    events=[]; names=[]
    if stop_radius_km is not None:
        def crossing(t,y):
            return float(np.linalg.norm(y[:3])) - stop_radius_km
        crossing.direction=crossing_direction; crossing.terminal=True
        events.append(crossing); names.append("radius-crossing")
    if stop_periapsis:
        def periapsis(t,y):
            return float(np.dot(y[:3],y[3:]))
        periapsis.direction=1; periapsis.terminal=True
        events.append(periapsis); names.append("periapsis")
    if stop_aphelion:
        def aphelion(t,y):return float(np.dot(y[:3],y[3:]))
        aphelion.direction=-1;aphelion.terminal=True
        events.append(aphelion);names.append('aphelion')
    if collision_radius_km:
        def collision(t,y):
            return float(np.linalg.norm(y[:3]))-collision_radius_km
        collision.direction=-1; collision.terminal=True
        events.append(collision); names.append("collision")
    for collision_name,callback in other_collisions or []:
        callback.direction=-1;callback.terminal=True
        events.append(callback);names.append('collision:'+collision_name)
    local_scale=np.sqrt(np.linalg.norm(initial[:3])**3/mu)
    solution=solve_ivp(derivative,(0.,duration_seconds),initial,method="DOP853",
                       rtol=2e-11,atol=[1e-6]*3+[1e-11]*3,dense_output=True,
                       max_step=max(1.,duration_seconds/120),
                       events=events or None)
    if not solution.success:
        raise RuntimeError(solution.message)
    end=float(solution.t[-1]); times=np.linspace(0.,end,max(2,sample_count+1))
    samples=solution.sol(times)
    emitted=[{"elapsedSeconds":float(t),"positionKm":samples[:3,i].tolist(),
              "velocityKmS":samples[3:,i].tolist()} for i,t in enumerate(times)]
    found=[{"type":names[i],"elapsedSeconds":float(t),"state":solution.y_events[i][j].tolist()}
           for i,values in enumerate(solution.t_events or []) for j,t in enumerate(values)]
    return {"trajectory":emitted,"durationSeconds":end,"events":found,
            "finalPositionKm":samples[:3,-1].tolist(),"finalVelocityKmS":samples[3:,-1].tolist(),
            "collision":any(e["type"].startswith("collision") for e in found),"successful":True}
