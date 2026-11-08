"""Physical trajectory calculation for the Solar-Oberth satellite mission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from math import cos, hypot, pi, sin, sqrt

from scipy import constants

from solver.ephemeris import get_ephemeris_status
from solver.ephemeris import planet_state as _spice_planet_state
from solver.ephemeris import utc_to_ephemeris_seconds
from visualization.view_3d_celestials import PLANET_DATA
from models.propulsion import (
    PropulsionSystem,
    PropulsionType,
    SimulationEnvironment,
    build_propulsion_modules,
    default_propulsion_modules,
)
from models.satellite import (
    ElectricSail,
    HeatShield,
    KickStage,
    LaunchStage,
    MissionPhase,
    PayloadProbe,
    PowerMode,
    Satellite,
    SatelliteState,
    SolarOberthCarrier,
    Vector3,
)


AU_KM = constants.astronomical_unit / constants.kilo
DAY_SECONDS = constants.day
YEAR_DAYS = 365.25
MU_SUN = 1.32712440018e11
MU_EARTH = 398_600.4418
EARTH_RADIUS_KM = 6_378.137
SOLAR_CONSTANT_W_M2 = 1_361.0
G_KM3_KG_S2 = constants.G / 1_000**3

Vector = tuple[float, float, float]
State = tuple[Vector, Vector]
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)

# id, mass, semi-major axis, period, eccentricity, inclination,
# mean longitude, perihelion longitude, ascending node
PLANET_EPHEMERIDES = tuple(
    (row[0], row[2], row[5], row[6], row[9], row[10], row[11], row[12], row[13])
    for row in PLANET_DATA
)


@dataclass(slots=True)
class MissionConfig:
    start_date: str = field(default_factory=lambda: date.today().isoformat())
    parking_orbit_altitude_km: float = 400.0
    payload_mass_kg: float = 120.0
    carrier_mass_kg: float = 1_200.0
    heatshield_mass_kg: float = 450.0
    propellant_mass_kg: float = 7_200.0
    target_perihelion_au: float = 0.05
    oberth_delta_v_km_s: float = 8.0
    burn_duration_seconds: float = 240.0
    engine_isp_seconds: float = 450.0
    separation_delta_v_km_s: float = 0.005
    launch_stage_enabled: bool = True
    carrier_enabled: bool = True
    heatshield_enabled: bool = True
    kick_stage_enabled: bool = True
    mission_years: float = 10.0
    electric_sail_enabled: bool = True
    tether_count: int = 80
    instrumented_tether_count: int = 16
    tether_length_km: float = 30.0
    tether_voltage_kv: float = 20.0
    spin_rate_rpm: float = 1.0
    end_masses_enabled: bool = True
    fiber_communication_enabled: bool = True
    sensor_nodes_enabled: bool = True
    sail_acceleration_mm_s2: float = 0.1
    heatshield_limit_w_m2: float = 600_000.0
    carrier_disposal: str = "safe_orbit"
    n_body_enabled: bool = True
    kalman_enabled: bool = True
    navigation_cycle_hours: float = 24.0
    position_measurement_noise_km: float = 25.0
    velocity_measurement_noise_km_s: float = 0.005
    propulsion_modules: list[dict] = field(default_factory=default_propulsion_modules)
    theoretical_propulsion_mode: bool = False

    @classmethod
    def from_dict(cls, values: dict | None) -> "MissionConfig":
        values = values or {}
        mapping = {
            "startDate": "start_date",
            "parkingOrbitAltitudeKm": "parking_orbit_altitude_km",
            "payloadMassKg": "payload_mass_kg",
            "carrierMassKg": "carrier_mass_kg",
            "heatshieldMassKg": "heatshield_mass_kg",
            "propellantMassKg": "propellant_mass_kg",
            "targetPerihelionAu": "target_perihelion_au",
            "oberthDeltaVKmS": "oberth_delta_v_km_s",
            "burnDurationSeconds": "burn_duration_seconds",
            "engineIspSeconds": "engine_isp_seconds",
            "separationDeltaVKmS": "separation_delta_v_km_s",
            "launchStageEnabled": "launch_stage_enabled",
            "carrierEnabled": "carrier_enabled",
            "heatshieldEnabled": "heatshield_enabled",
            "kickStageEnabled": "kick_stage_enabled",
            "missionYears": "mission_years",
            "electricSailEnabled": "electric_sail_enabled",
            "tetherCount": "tether_count",
            "instrumentedTetherCount": "instrumented_tether_count",
            "tetherLengthKm": "tether_length_km",
            "tetherVoltageKv": "tether_voltage_kv",
            "spinRateRpm": "spin_rate_rpm",
            "endMassesEnabled": "end_masses_enabled",
            "fiberCommunicationEnabled": "fiber_communication_enabled",
            "sensorNodesEnabled": "sensor_nodes_enabled",
            "sailAccelerationMmS2": "sail_acceleration_mm_s2",
            "heatshieldLimitWm2": "heatshield_limit_w_m2",
            "carrierDisposal": "carrier_disposal",
            "nBodyEnabled": "n_body_enabled",
            "kalmanEnabled": "kalman_enabled",
            "navigationCycleHours": "navigation_cycle_hours",
            "positionMeasurementNoiseKm": "position_measurement_noise_km",
            "velocityMeasurementNoiseKmS": "velocity_measurement_noise_km_s",
            "propulsionModules": "propulsion_modules",
            "theoreticalPropulsionMode": "theoretical_propulsion_mode",
        }
        converted = {mapping[key]: value for key, value in values.items() if key in mapping}
        if "tether_count" in converted:
            converted["tether_count"] = int(converted["tether_count"])
        if "instrumented_tether_count" in converted:
            converted["instrumented_tether_count"] = int(converted["instrumented_tether_count"])
        return cls(**converted)

    def to_dict(self) -> dict:
        return {
            "startDate": self.start_date,
            "parkingOrbitAltitudeKm": self.parking_orbit_altitude_km,
            "payloadMassKg": self.payload_mass_kg,
            "carrierMassKg": self.carrier_mass_kg,
            "heatshieldMassKg": self.heatshield_mass_kg,
            "propellantMassKg": self.propellant_mass_kg,
            "targetPerihelionAu": self.target_perihelion_au,
            "oberthDeltaVKmS": self.oberth_delta_v_km_s,
            "burnDurationSeconds": self.burn_duration_seconds,
            "engineIspSeconds": self.engine_isp_seconds,
            "separationDeltaVKmS": self.separation_delta_v_km_s,
            "launchStageEnabled": self.launch_stage_enabled,
            "carrierEnabled": self.carrier_enabled,
            "heatshieldEnabled": self.heatshield_enabled,
            "kickStageEnabled": self.kick_stage_enabled,
            "missionYears": self.mission_years,
            "electricSailEnabled": self.electric_sail_enabled,
            "tetherCount": self.tether_count,
            "instrumentedTetherCount": self.instrumented_tether_count,
            "tetherLengthKm": self.tether_length_km,
            "tetherVoltageKv": self.tether_voltage_kv,
            "spinRateRpm": self.spin_rate_rpm,
            "endMassesEnabled": self.end_masses_enabled,
            "fiberCommunicationEnabled": self.fiber_communication_enabled,
            "sensorNodesEnabled": self.sensor_nodes_enabled,
            "sailAccelerationMmS2": self.sail_acceleration_mm_s2,
            "heatshieldLimitWm2": self.heatshield_limit_w_m2,
            "carrierDisposal": self.carrier_disposal,
            "nBodyEnabled": self.n_body_enabled,
            "kalmanEnabled": self.kalman_enabled,
            "navigationCycleHours": self.navigation_cycle_hours,
            "positionMeasurementNoiseKm": self.position_measurement_noise_km,
            "velocityMeasurementNoiseKmS": self.velocity_measurement_noise_km_s,
            "propulsionModules": self.propulsion_modules,
            "theoreticalPropulsionMode": self.theoretical_propulsion_mode,
        }


@dataclass(slots=True)
class MissionEvent:
    elapsed_days: float
    phase: MissionPhase
    name: str
    description: str
    mass_kg: float
    speed_km_s: float
    position_km: Vector
    velocity_km_s: Vector
    warning_level: str = "info"

    def to_dict(self) -> dict:
        return {
            "elapsedDays": self.elapsed_days,
            "phase": self.phase.value,
            "name": self.name,
            "description": self.description,
            "massKg": self.mass_kg,
            "speedKmS": self.speed_km_s,
            "positionKm": list(self.position_km),
            "velocityKmS": list(self.velocity_km_s),
            "warningLevel": self.warning_level,
        }


@dataclass(slots=True)
class TrajectoryPoint:
    elapsed_days: float
    position_km: Vector
    velocity_km_s: Vector
    phase: MissionPhase
    mass_kg: float

    def to_dict(self) -> dict:
        return {
            "elapsedDays": self.elapsed_days,
            "positionKm": list(self.position_km),
            "velocityKmS": list(self.velocity_km_s),
            "phase": self.phase.value,
            "massKg": self.mass_kg,
        }


@dataclass(slots=True)
class MissionSummary:
    status: str
    total_flight_days: float
    perihelion_au: float
    max_solar_flux_w_m2: float
    pre_burn_speed_km_s: float
    post_burn_speed_km_s: float
    achieved_burn_delta_v_km_s: float
    propellant_used_kg: float
    payload_mass_kg: float
    distance_au_by_year: dict[str, float]
    speed_km_s_by_year: dict[str, float]
    electric_sail_gain_km_s: float
    navigation_cycles: int
    position_uncertainty_km: float
    velocity_uncertainty_km_s: float
    max_planetary_perturbation_mm_s2: float
    propulsion_report: list[dict]
    time_to_saturn_days: float | None
    time_to_voyager_distance_days: float | None
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "totalFlightDays": self.total_flight_days,
            "perihelionAu": self.perihelion_au,
            "maxSolarFluxWm2": self.max_solar_flux_w_m2,
            "preBurnSpeedKmS": self.pre_burn_speed_km_s,
            "postBurnSpeedKmS": self.post_burn_speed_km_s,
            "achievedBurnDeltaVKmS": self.achieved_burn_delta_v_km_s,
            "propellantUsedKg": self.propellant_used_kg,
            "payloadMassKg": self.payload_mass_kg,
            "distanceAuByYear": self.distance_au_by_year,
            "speedKmSByYear": self.speed_km_s_by_year,
            "electricSailGainKmS": self.electric_sail_gain_km_s,
            "navigationCycles": self.navigation_cycles,
            "positionUncertaintyKm": self.position_uncertainty_km,
            "velocityUncertaintyKmS": self.velocity_uncertainty_km_s,
            "maxPlanetaryPerturbationMmS2": self.max_planetary_perturbation_mm_s2,
            "propulsionReport": self.propulsion_report,
            "timeToSaturnDays": self.time_to_saturn_days,
            "timeToVoyagerDistanceDays": self.time_to_voyager_distance_days,
            "warnings": self.warnings,
        }


@dataclass(slots=True)
class MissionResult:
    config: MissionConfig
    events: list[MissionEvent]
    trajectory: list[TrajectoryPoint]
    summary: MissionSummary
    departure_validation: dict | None = None

    def to_dict(self) -> dict:
        return {
            "schemaVersion": "2.0",
            "departureValidation": self.departure_validation,
            "config": self.config.to_dict(),
            "ephemeris": get_ephemeris_status(),
            "events": [event.to_dict() for event in self.events],
            "trajectory": [point.to_dict() for point in self.trajectory],
            "summary": self.summary.to_dict(),
        }


def _add(first: Vector, second: Vector, scale: float = 1.0) -> Vector:
    return tuple(first[index] + second[index] * scale for index in range(3))  # type: ignore[return-value]


def _magnitude(vector: Vector) -> float:
    return hypot(*vector)


def _normalize(vector: Vector) -> Vector:
    length = _magnitude(vector) or 1.0
    return tuple(value / length for value in vector)  # type: ignore[return-value]


def _mission_epoch_days(start_date: str) -> float:
    from solver.orbital import utc
    timestamp = utc(start_date)
    ephemeris_seconds = utc_to_ephemeris_seconds(timestamp)
    if ephemeris_seconds is not None:
        return ephemeris_seconds / DAY_SECONDS
    return (timestamp - J2000).total_seconds() / DAY_SECONDS


def _kepler_planet_position_at(ephemeris: tuple, days_since_j2000: float) -> Vector:
    """Approximate heliocentric J2000 position from the shared orbital elements."""
    _, _, semi_major_axis_au, period_days, eccentricity, inclination_deg, mean_longitude_deg, perihelion_deg, node_deg = ephemeris
    inclination = inclination_deg * pi / 180
    ascending_node = node_deg * pi / 180
    argument_perihelion = (perihelion_deg - node_deg) * pi / 180
    mean_longitude = (mean_longitude_deg + 360 * days_since_j2000 / period_days) * pi / 180
    perihelion_longitude = perihelion_deg * pi / 180
    mean_anomaly = (mean_longitude - perihelion_longitude + pi) % (2 * pi) - pi
    eccentric_anomaly = mean_anomaly
    for _ in range(8):
        eccentric_anomaly -= (
            eccentric_anomaly - eccentricity * sin(eccentric_anomaly) - mean_anomaly
        ) / (1 - eccentricity * cos(eccentric_anomaly))
    semi_major_axis_km = semi_major_axis_au * AU_KM
    x_prime = semi_major_axis_km * (cos(eccentric_anomaly) - eccentricity)
    y_prime = semi_major_axis_km * sqrt(1 - eccentricity**2) * sin(eccentric_anomaly)
    cos_argument, sin_argument = cos(argument_perihelion), sin(argument_perihelion)
    cos_node, sin_node = cos(ascending_node), sin(ascending_node)
    cos_inclination, sin_inclination = cos(inclination), sin(inclination)
    x = (cos_argument * cos_node - sin_argument * sin_node * cos_inclination) * x_prime \
        + (-sin_argument * cos_node - cos_argument * sin_node * cos_inclination) * y_prime
    y = (cos_argument * sin_node + sin_argument * cos_node * cos_inclination) * x_prime \
        + (-sin_argument * sin_node + cos_argument * cos_node * cos_inclination) * y_prime
    z = sin_argument * sin_inclination * x_prime + cos_argument * sin_inclination * y_prime
    return x, y, z


def _planet_position_at(ephemeris: tuple, days_since_j2000: float) -> Vector:
    """Return a geometric heliocentric ECLIPJ2000 planet position."""
    spice_result = _spice_planet_state(
        str(ephemeris[0]), days_since_j2000 * DAY_SECONDS
    )
    if spice_result is not None:
        state, _ = spice_result
        return state[0], state[1], state[2]
    return _kepler_planet_position_at(ephemeris, days_since_j2000)


def _planet_state_at(ephemeris: tuple, days_since_j2000: float) -> State:
    """Return position and velocity from SPICE or the Kepler fallback."""
    spice_result = _spice_planet_state(
        str(ephemeris[0]), days_since_j2000 * DAY_SECONDS
    )
    if spice_result is not None:
        state, _ = spice_result
        return (
            (state[0], state[1], state[2]),
            (state[3], state[4], state[5]),
        )

    delta_days = 60.0 / DAY_SECONDS
    position = _kepler_planet_position_at(ephemeris, days_since_j2000)
    before = _kepler_planet_position_at(ephemeris, days_since_j2000 - delta_days)
    after = _kepler_planet_position_at(ephemeris, days_since_j2000 + delta_days)
    velocity = tuple(
        (after[index] - before[index]) / 120.0 for index in range(3)
    )
    return position, velocity  # type: ignore[return-value]


def _earth_state_at(start_date: str) -> State:
    earth = next(
        ephemeris for ephemeris in PLANET_EPHEMERIDES if ephemeris[0] == "earth"
    )
    return _planet_state_at(earth, _mission_epoch_days(start_date))


def _earth_position_at(start_date: str) -> Vector:
    return _earth_state_at(start_date)[0]


def _planetary_perturbation(position: Vector, days_since_j2000: float) -> Vector:
    """Planet accelerations including the indirect heliocentric-frame term."""
    acceleration: Vector = (0.0, 0.0, 0.0)
    for ephemeris in PLANET_EPHEMERIDES:
        _, mass_kg, semi_major_axis_au, _, _, _, _, _, _ = ephemeris
        planet_position = _planet_position_at(ephemeris, days_since_j2000)
        relative = tuple(planet_position[i] - position[i] for i in range(3))
        separation = _magnitude(relative)
        planet_radius = _magnitude(planet_position)
        sphere_of_influence = semi_major_axis_au * AU_KM * (mass_kg / 1.9885e30) ** 0.4
        # Starts and close encounters use a patched-conic local model; this
        # heliocentric perturbation must not become singular at a planet centre.
        if separation < sphere_of_influence:
            continue
        mu = G_KM3_KG_S2 * mass_kg
        direct = tuple(value * mu / separation**3 for value in relative)
        indirect = tuple(value * mu / planet_radius**3 for value in planet_position)
        acceleration = _add(acceleration, tuple(direct[i] - indirect[i] for i in range(3)))
    return acceleration


def _acceleration(
    position: Vector,
    sail_acceleration_mm_s2: float = 0.0,
    days_since_j2000: float = 0.0,
    n_body_enabled: bool = False,
    external_acceleration_km_s2: Vector = (0.0, 0.0, 0.0),
) -> Vector:
    radius = _magnitude(position)
    gravity_scale = -MU_SUN / radius**3
    acceleration = tuple(value * gravity_scale for value in position)
    if n_body_enabled:
        acceleration = _add(acceleration, _planetary_perturbation(position, days_since_j2000))
    if sail_acceleration_mm_s2 > 0:
        radius_au = radius / AU_KM
        sail_km_s2 = sail_acceleration_mm_s2 * 1e-6 / max(0.1, radius_au)
        acceleration = _add(acceleration, _normalize(position), sail_km_s2)
    acceleration = _add(acceleration, external_acceleration_km_s2)
    return acceleration  # type: ignore[return-value]


def _rk4(
    state: State,
    step_seconds: float,
    sail_acceleration_mm_s2: float = 0.0,
    epoch_days_j2000: float = 0.0,
    elapsed_seconds: float = 0.0,
    n_body_enabled: bool = False,
    external_acceleration_km_s2: Vector = (0.0, 0.0, 0.0),
) -> State:
    def derivative(sample: State, offset_seconds: float) -> State:
        position, velocity = sample
        absolute_days = epoch_days_j2000 + (elapsed_seconds + offset_seconds) / DAY_SECONDS
        return velocity, _acceleration(
            position,
            sail_acceleration_mm_s2,
            absolute_days,
            n_body_enabled,
            external_acceleration_km_s2,
        )

    position, velocity = state
    k1 = derivative(state, 0.0)
    k2 = derivative((_add(position, k1[0], step_seconds / 2), _add(velocity, k1[1], step_seconds / 2)), step_seconds / 2)
    k3 = derivative((_add(position, k2[0], step_seconds / 2), _add(velocity, k2[1], step_seconds / 2)), step_seconds / 2)
    k4 = derivative((_add(position, k3[0], step_seconds), _add(velocity, k3[1], step_seconds)), step_seconds)

    def combine(base: Vector, values: tuple[Vector, Vector, Vector, Vector]) -> Vector:
        return tuple(
            base[index]
            + step_seconds * (values[0][index] + 2 * values[1][index] + 2 * values[2][index] + values[3][index]) / 6
            for index in range(3)
        )  # type: ignore[return-value]

    return combine(position, (k1[0], k2[0], k3[0], k4[0])), combine(velocity, (k1[1], k2[1], k3[1], k4[1]))


@dataclass(slots=True)
class AxisKalmanCovariance:
    """Two-state position/velocity covariance for one Cartesian axis."""

    position_variance: float
    cross_covariance: float
    velocity_variance: float

    def predict(self, dt: float, acceleration_sigma_km_s2: float) -> None:
        q = acceleration_sigma_km_s2**2
        p00, p01, p11 = self.position_variance, self.cross_covariance, self.velocity_variance
        self.position_variance = p00 + 2 * dt * p01 + dt**2 * p11 + q * dt**4 / 4
        self.cross_covariance = p01 + dt * p11 + q * dt**3 / 2
        self.velocity_variance = p11 + q * dt**2

    def update(self, position_noise_km: float, velocity_noise_km_s: float) -> None:
        # Sequential scalar measurements (position, then velocity) preserve
        # the position/velocity cross-covariance without a matrix dependency.
        p00, p01, p11 = self.position_variance, self.cross_covariance, self.velocity_variance
        position_denominator = p00 + position_noise_km**2
        k_position = p00 / position_denominator
        k_velocity = p01 / position_denominator
        p00_new = (1 - k_position) * p00
        p01_new = (1 - k_position) * p01
        p11_new = p11 - k_velocity * p01

        velocity_denominator = p11_new + velocity_noise_km_s**2
        k_position = p01_new / velocity_denominator
        k_velocity = p11_new / velocity_denominator
        self.position_variance = max(0.0, p00_new - k_position * p01_new)
        self.cross_covariance = p01_new - k_position * p11_new
        self.velocity_variance = max(0.0, p11_new * (1 - k_velocity))


@dataclass(slots=True)
class KalmanNavigationSystem:
    axes: tuple[AxisKalmanCovariance, AxisKalmanCovariance, AxisKalmanCovariance]
    cycles: int = 0

    @classmethod
    def create(cls, position_noise_km: float, velocity_noise_km_s: float) -> "KalmanNavigationSystem":
        return cls(tuple(
            AxisKalmanCovariance(position_noise_km**2, 0.0, velocity_noise_km_s**2)
            for _ in range(3)
        ))  # type: ignore[arg-type]

    def cycle(self, dt: float, position_noise_km: float, velocity_noise_km_s: float) -> None:
        # 10^-9 km/s² represents small unmodelled forces and numerical error.
        for covariance in self.axes:
            covariance.predict(dt, acceleration_sigma_km_s2=1e-9)
            covariance.update(position_noise_km, velocity_noise_km_s)
        self.cycles += 1

    @property
    def position_uncertainty_km(self) -> float:
        return sqrt(sum(axis.position_variance for axis in self.axes))

    @property
    def velocity_uncertainty_km_s(self) -> float:
        return sqrt(sum(axis.velocity_variance for axis in self.axes))


def _adaptive_step_seconds(radius_au: float, outbound: bool) -> float:
    if radius_au < 0.08:
        return 30.0
    if radius_au < 0.15:
        return 180.0
    if radius_au < 0.35:
        return 900.0
    if radius_au < 0.7:
        return 3_600.0
    return 43_200.0 if outbound else 21_600.0


def _trajectory_record_interval_seconds(radius_au: float) -> float:
    """Sampling interval for drawing curved conic sections without long chords."""
    if radius_au < 0.08:
        return 5 * 60.0
    if radius_au < 0.15:
        return 30 * 60.0
    if radius_au < 0.35:
        return 3 * 3_600.0
    if radius_au < 0.7:
        return 12 * 3_600.0
    if radius_au < 2.0:
        return DAY_SECONDS
    if radius_au < 5.0:
        return 3 * DAY_SECONDS
    return 10 * DAY_SECONDS


def validate_mission_config(config: MissionConfig) -> list[str]:
    errors: list[str] = []
    sun_radius_au = 696_340 / AU_KM
    if config.target_perihelion_au <= sun_radius_au:
        errors.append("Perihel liegt innerhalb der Sonne.")
    if config.target_perihelion_au >= 1:
        errors.append("Perihel muss kleiner als 1 AE sein.")
    if not 0 <= config.instrumented_tether_count <= config.tether_count:
        errors.append("Instrumentierte Tethers dürfen die Gesamtzahl nicht überschreiten.")
    if config.tether_count < 1 or config.tether_length_km <= 0:
        errors.append("Tether-Anzahl und -Länge müssen positiv sein.")
    masses = (config.payload_mass_kg, config.carrier_mass_kg, config.heatshield_mass_kg, config.propellant_mass_kg)
    if any(mass <= 0 for mass in masses):
        errors.append("Alle Massen müssen positiv sein.")
    if config.parking_orbit_altitude_km <= 100:
        errors.append("Die Parkbahnhöhe muss über 100 km liegen.")
    if config.oberth_delta_v_km_s < 0 or config.mission_years < 1:
        errors.append("Delta-v und Missionsdauer sind ungültig.")
    if config.carrier_disposal not in {"safe_orbit", "solar_orbit", "sun_impact"}:
        errors.append("Die Trägerentsorgung ist ungültig.")
    if config.navigation_cycle_hours <= 0:
        errors.append("Der Navigationszyklus muss positiv sein.")
    if config.position_measurement_noise_km <= 0 or config.velocity_measurement_noise_km_s <= 0:
        errors.append("Die Kalman-Messunsicherheiten müssen positiv sein.")
    for module in config.propulsion_modules:
        try:
            module_type = PropulsionType(module.get("type"))
        except (TypeError, ValueError):
            errors.append(f"Unbekannter Antriebstyp: {module.get('type')}.")
            continue
        parameters = module.get("parameters") or {}
        if module_type == PropulsionType.ELECTRIC_SAIL:
            total = int(parameters.get("totalTetherCount", config.tether_count))
            instrumented = int(parameters.get("instrumentedTetherCount", config.instrumented_tether_count))
            progress = float(parameters.get("deploymentProgress", 0.0))
            if total <= 0 or float(parameters.get("tetherLengthKm", config.tether_length_km)) <= 0:
                errors.append("Electric Sail benötigt positive Tether-Anzahl und -Länge.")
            if not 0 <= instrumented <= total:
                errors.append("Instrumentierte Electric-Sail-Tethers überschreiten die Gesamtzahl.")
            if not 0 <= progress <= 1:
                errors.append("Electric-Sail-Entfaltungsfortschritt muss zwischen 0 und 1 liegen.")
    return errors


def _build_satellite(config: MissionConfig) -> Satellite:
    launch_stage = LaunchStage(
        name="Startstufe",
        dry_mass_kg=0.0,
        propellant_mass_kg=0.0,
        specific_impulse_seconds=300.0,
        attached=False,
        active=False,
    ) if config.launch_stage_enabled else None
    sail = ElectricSail.build(
        tether_count=config.tether_count,
        instrumented_tether_count=config.instrumented_tether_count,
        tether_length_km=config.tether_length_km,
        voltage_kv=config.tether_voltage_kv,
        spin_rate_rpm=config.spin_rate_rpm,
        end_masses_enabled=config.end_masses_enabled,
        fiber_communication_enabled=config.fiber_communication_enabled,
        sensor_nodes_enabled=config.sensor_nodes_enabled,
    )
    return Satellite(
        name="Solar-Oberth Electric-Sail Probe",
        state=SatelliteState(
            position_km=Vector3(x=AU_KM, y=0.0),
            velocity_km_s=Vector3(x=0.0, y=sqrt(MU_SUN / AU_KM)),
        ),
        payload=PayloadProbe(name="Nutzlastsonde", dry_mass_kg=config.payload_mass_kg),
        carrier=SolarOberthCarrier(
            name="Solar-Oberth-Träger",
            dry_mass_kg=config.carrier_mass_kg,
            attached=config.carrier_enabled,
            active=config.carrier_enabled,
            disposal_mode=config.carrier_disposal,
        ),
        heatshield=HeatShield(
            name="Hitzeschild",
            dry_mass_kg=config.heatshield_mass_kg,
            attached=config.heatshield_enabled,
            active=config.heatshield_enabled,
            flux_limit_w_m2=config.heatshield_limit_w_m2,
        ),
        kick_stage=KickStage(
            name="Kick-Stufe",
            dry_mass_kg=0.0,
            propellant_mass_kg=config.propellant_mass_kg,
            specific_impulse_seconds=config.engine_isp_seconds,
            attached=config.kick_stage_enabled,
            active=config.kick_stage_enabled,
        ),
        electric_sail=sail,
        launch_stage=launch_stage,
    )


def simulate_mission(config_or_values: MissionConfig | dict | None = None) -> MissionResult:
    config = config_or_values if isinstance(config_or_values, MissionConfig) else MissionConfig.from_dict(config_or_values)
    errors = validate_mission_config(config)
    if errors:
        raise ValueError(" ".join(errors))

    satellite = _build_satellite(config)
    propulsion_system = PropulsionSystem(
        build_propulsion_modules(config.propulsion_modules),
        theoretical_mode=config.theoretical_propulsion_mode,
    )
    electric_propulsion = propulsion_system.module(PropulsionType.ELECTRIC_SAIL)
    if electric_propulsion is not None:
        electric_propulsion.enabled = electric_propulsion.enabled and config.electric_sail_enabled
        electric_propulsion.parameters.update({
            "totalTetherCount": config.tether_count,
            "instrumentedTetherCount": config.instrumented_tether_count,
            "tetherLengthKm": config.tether_length_km,
            "effectiveDiameterKm": config.tether_length_km * 2,
            "tetherVoltageKV": config.tether_voltage_kv,
            "spinRateRpm": config.spin_rate_rpm,
            "electronGunPowerW": electric_propulsion.parameters.get("electronGunPowerW", 700),
        })
    oberth_propulsion = propulsion_system.module(PropulsionType.SOLAR_OBERTH)
    if oberth_propulsion is not None:
        oberth_propulsion.enabled = oberth_propulsion.enabled and config.carrier_enabled and config.kick_stage_enabled
        oberth_propulsion.parameters.update({
            "targetPerihelionAU": config.target_perihelion_au,
            "burnDeltaVKmS": config.oberth_delta_v_km_s,
            "burnDurationS": config.burn_duration_seconds,
            "performed": False,
        })
    events: list[MissionEvent] = []
    trajectory: list[TrajectoryPoint] = []
    warnings: list[str] = []
    for configured_module in config.propulsion_modules:
        if configured_module.get("type") == PropulsionType.ELECTRIC_SAIL.value:
            parameters = configured_module.get("parameters") or {}
            if parameters.get("charged") and not parameters.get("deployed"):
                warnings.append("Electric Sail kann nicht geladen werden, bevor die Tethers entfaltet wurden.")
    elapsed_seconds = 0.0
    epoch_days_j2000 = _mission_epoch_days(config.start_date)
    navigation = KalmanNavigationSystem.create(
        config.position_measurement_noise_km,
        config.velocity_measurement_noise_km_s,
    )
    navigation_elapsed_seconds = 0.0
    navigation_cycle_seconds = config.navigation_cycle_hours * 3_600.0
    propulsion_activations: set[str] = set()

    def effective_mass_kg() -> float:
        existing_types = {
            PropulsionType.CHEMICAL,
            PropulsionType.SOLID_KICK_STAGE,
            PropulsionType.SOLAR_OBERTH,
            PropulsionType.ELECTRIC_SAIL,
        }
        additional = sum(
            module.dry_mass_kg + module.propellant_mass_kg
            for module in propulsion_system.modules
            if module.enabled and module.type not in existing_types
        )
        return satellite.total_mass_kg + additional

    def run_navigation_cycle(step_seconds: float) -> None:
        nonlocal navigation_elapsed_seconds
        if not config.kalman_enabled:
            return
        navigation_elapsed_seconds += step_seconds
        while navigation_elapsed_seconds >= navigation_cycle_seconds:
            navigation.cycle(
                navigation_cycle_seconds,
                config.position_measurement_noise_km,
                config.velocity_measurement_noise_km_s,
            )
            navigation_elapsed_seconds -= navigation_cycle_seconds

    def sync_satellite(state: State, phase: MissionPhase) -> None:
        satellite.state = SatelliteState(
            position_km=Vector3(x=state[0][0], y=state[0][1], z=state[0][2]),
            velocity_km_s=Vector3(x=state[1][0], y=state[1][1], z=state[1][2]),
            timestamp_seconds=elapsed_seconds,
            phase=phase,
        )

    def log_event(name: str, phase: MissionPhase, description: str, state: State, level: str = "info") -> None:
        sync_satellite(state, phase)
        events.append(MissionEvent(
            elapsed_days=elapsed_seconds / DAY_SECONDS,
            phase=phase,
            name=name,
            description=description,
            mass_kg=satellite.total_mass_kg,
            speed_km_s=_magnitude(state[1]),
            position_km=state[0],
            velocity_km_s=state[1],
            warning_level=level,
        ))

    def record(state: State, phase: MissionPhase) -> None:
        sync_satellite(state, phase)
        trajectory.append(TrajectoryPoint(
            elapsed_days=elapsed_seconds / DAY_SECONDS,
            position_km=state[0],
            velocity_km_s=state[1],
            phase=phase,
            mass_kg=satellite.total_mass_kg,
        ))

    # The mission and route views share the same physical departure. No
    # synthetic Earth loops or state replacement at the solar transfer boundary.
    from planner.trajectory_planner import PhysicalRoute, _normalized_input
    prefix_values=_normalized_input({'start':{'type':'orbit','bodyId':'earth','startDate':config.start_date,'orbitAltitudeKm':config.parking_orbit_altitude_km},
        'target':{'type':'body','bodyId':'sun'},'vehicle':{'wetMassKg':effective_mass_kg(),'propellantMassKg':satellite.kick_stage.mass_kg,'engineIspSeconds':config.engine_isp_seconds},'mission':{**config.to_dict(),'oberthDeltaVKmS':0},'simulation':{'includeAudit':False,'sampleTrajectoryPoints':180}})
    prefix=PhysicalRoute(prefix_values,config.start_date)
    prefix.solar_passage(prefix_values['target'])
    prefix.target_reached=all(x['targetConditionSatisfied'] for x in prefix.sections)
    evidence=prefix.to_result(prefix_values['target'],'solar-departure')
    warnings.extend(evidence['warnings'])
    for point in prefix.points:
        phase=MissionPhase.EARTH_PARKING_ORBIT if point['phase']=='INITIAL_STATE' else MissionPhase.SOLAR_APPROACH if point['phase']=='SOLAR_APPROACH' else MissionPhase.SUNDIVER_TRANSFER
        trajectory.append(TrajectoryPoint(elapsed_days=point['elapsedDays'],position_km=tuple(point['positionKm']),velocity_km_s=tuple(point['velocityKmS']),phase=phase,mass_kg=point.get('massKg',satellite.total_mass_kg)))
    for burn in prefix.maneuvers:
        from solver.orbital import utc
        elapsed_seconds=(utc(burn['epochUtc'])-utc(config.start_date)).total_seconds()
        log_event(burn['type'],MissionPhase.SUNDIVER_TRANSFER,f"Geplanter Impuls {burn['deltaVKmS']:.3f} km/s; Treibstoffnachweis {burn['vehicleFeasible']}",(tuple(burn['stateAfter']['positionKm']),tuple(burn['stateAfter']['velocityKmS'])))
    elapsed_seconds=prefix.seconds
    state=(tuple(prefix.r),tuple(prefix.v))
    prefix_fuel_used=sum(m['propellantUsedKg'] or 0 for m in prefix.maneuvers if m['vehicleFeasible'])
    if not evidence['summary']['feasible']:
        log_event('MISSION_ABORT',MissionPhase.MISSION_ABORT,'Abflug nicht mit Fahrzeug, Geometrie oder Ephemeriden nachgewiesen; Trajektorie nur Entwurf.',state,'warning')
        summary=MissionSummary(status='ABORT',total_flight_days=elapsed_seconds/DAY_SECONDS,perihelion_au=_magnitude(state[0])/AU_KM,
            max_solar_flux_w_m2=SOLAR_CONSTANT_W_M2/(_magnitude(state[0])/AU_KM)**2,pre_burn_speed_km_s=_magnitude(state[1]),post_burn_speed_km_s=_magnitude(state[1]),achieved_burn_delta_v_km_s=0,
            propellant_used_kg=prefix_fuel_used,payload_mass_kg=config.payload_mass_kg,distance_au_by_year={},speed_km_s_by_year={},electric_sail_gain_km_s=0,navigation_cycles=0,position_uncertainty_km=0,velocity_uncertainty_km_s=0,
            max_planetary_perturbation_mm_s2=0,propulsion_report=propulsion_system.reports(),time_to_saturn_days=None,time_to_voyager_distance_days=None,warnings=list(dict.fromkeys(warnings)))
        return MissionResult(config=config,events=events,trajectory=trajectory,summary=summary,departure_validation=evidence['validation'])
    satellite.kick_stage.propellant_mass_kg=prefix.propellant
    log_event('EARTH_SOI_EXIT',MissionPhase.SUNDIVER_TRANSFER,'Physikalischer Abflug und Solartransfer propagiert; alle Abflugimpulse budgetiert.',state)
    actual_perihelion_au = _magnitude(state[0]) / AU_KM
    max_solar_flux = SOLAR_CONSTANT_W_M2 / actual_perihelion_au**2
    thermally_safe = satellite.heatshield.register_flux(max_solar_flux)
    if actual_perihelion_au < 0.05:
        warnings.append("Extreme thermische Belastung: Perihel kleiner als 0,05 AE.")
    if not thermally_safe:
        warnings.append("Hitzeschild fehlt oder sein Grenzwert wurde überschritten.")
    if not config.carrier_enabled or not config.kick_stage_enabled or oberth_propulsion is None or not oberth_propulsion.enabled:
        warnings.append("Oberth-Burn deaktiviert: Träger- oder Kick-Stufe nicht aktiv.")
    log_event("PERIHELION_REACHED", MissionPhase.SOLAR_OBERTH_BURN, f"Perihel bei {actual_perihelion_au:.4f} AE; Solarfluss {max_solar_flux:.0f} W/m².", state, "warning" if warnings else "info")

    pre_burn_speed = _magnitude(state[1])
    log_event("SOLAR_OBERTH_BURN_STARTED", MissionPhase.SOLAR_OBERTH_BURN, f"Prograder Burn über {config.burn_duration_seconds:g} s gestartet.", state)
    if config.carrier_enabled and config.kick_stage_enabled and oberth_propulsion is not None and oberth_propulsion.enabled:
        achieved_delta_v, propellant_used = satellite.perform_oberth_burn(config.oberth_delta_v_km_s)
    else:
        achieved_delta_v, propellant_used = 0.0, 0.0
    if achieved_delta_v < config.oberth_delta_v_km_s - 0.001:
        warnings.append("Gewünschtes Oberth-Delta-v durch Konfiguration oder Treibstoff begrenzt.")
    direction = _normalize(state[1])
    state = (state[0], _add(state[1], direction, achieved_delta_v))
    if oberth_propulsion is not None and oberth_propulsion.enabled:
        oberth_propulsion.active_seconds += config.burn_duration_seconds
        oberth_propulsion.delivered_delta_v_km_s += achieved_delta_v
        oberth_propulsion.total_propellant_used_kg += propellant_used
        oberth_propulsion.parameters["performed"] = True
    log_event("SOLAR_OBERTH_BURN_COMPLETED", MissionPhase.SOLAR_OBERTH_BURN, f"Delta-v {achieved_delta_v:.2f} km/s entlang des Geschwindigkeitsvektors.", state)
    post_burn_speed = _magnitude(state[1])
    record(state, MissionPhase.SOLAR_OBERTH_BURN)

    satellite.separate_payload()
    state = (state[0], _add(state[1], direction, config.separation_delta_v_km_s))
    log_event("PAYLOAD_SEPARATED", MissionPhase.PAYLOAD_SEPARATION, "Leichte Nutzlastsonde getrennt; der Trennimpuls beträgt nur wenige m/s.", state)
    log_event("HEATSHIELD_DISCARDED", MissionPhase.PAYLOAD_SEPARATION, "Hitzeschild abgeworfen.", state)
    log_event("CARRIER_DISCARDED", MissionPhase.PAYLOAD_SEPARATION, f"Trägerentsorgung: {config.carrier_disposal}.", state)
    log_event("PAYLOAD_COMMISSIONING_STARTED", MissionPhase.PAYLOAD_COMMISSIONING, "Nutzlast stabilisiert; Spinachse wird ausgerichtet.", state)
    satellite.payload.power_mode = PowerMode.COMMISSIONING
    record(state, MissionPhase.PAYLOAD_SEPARATION)
    deployment_start = elapsed_seconds
    log_event("ELECTRIC_SAIL_DEPLOYMENT_STARTED", MissionPhase.ELECTRIC_SAIL_DEPLOYMENT, f"{config.tether_count} Tethers werden paarweise rotationsgestützt entfaltet.", state)
    record(state, MissionPhase.ELECTRIC_SAIL_DEPLOYMENT)

    mission_end_seconds = config.mission_years * YEAR_DAYS * DAY_SECONDS
    year_targets = [year for year in (1, 5, 10) if year <= config.mission_years]
    distance_by_year: dict[str, float] = {}
    speed_by_year: dict[str, float] = {}
    sail_active = False
    deployment_batch = 0
    deep_space_logged = False
    last_recorded_seconds = elapsed_seconds
    last_recorded_phase = MissionPhase.ELECTRIC_SAIL_DEPLOYMENT
    previous_elapsed = elapsed_seconds
    previous_state = state
    coast_state = state

    while elapsed_seconds < mission_end_seconds:
        radius_au = _magnitude(state[0]) / AU_KM
        deployment_days = (elapsed_seconds - deployment_start) / DAY_SECONDS
        if electric_propulsion is not None:
            deployment_progress = min(1.0, max(0.0, deployment_days / 4.0))
            electric_propulsion.parameters["deploymentProgress"] = deployment_progress
            electric_propulsion.parameters["deployed"] = deployment_progress >= 1.0
        reached_batch = min(4, int(deployment_days))
        while deployment_batch < reached_batch:
            deployment_batch += 1
            target_count = round(config.tether_count * deployment_batch / 4)
            deployed_count = satellite.electric_sail.deploy_batch(target_count)
            log_event("TETHER_DEPLOYED", MissionPhase.ELECTRIC_SAIL_DEPLOYMENT, f"Entfaltungscharge {deployment_batch}/4: {deployed_count} von {config.tether_count} Tethers ausgefahren.", state)
        if not sail_active and deployment_days >= 4:
            log_event("ALL_TETHERS_DEPLOYED", MissionPhase.ELECTRIC_SAIL_DEPLOYMENT, f"{config.tether_count} Tethers à {config.tether_length_km:g} km entfaltet; {config.instrumented_tether_count} instrumentiert.", state)
            log_event("ELECTRIC_SAIL_STRUCTURE_STABLE", MissionPhase.ELECTRIC_SAIL_DEPLOYMENT, f"Spinrate {config.spin_rate_rpm:g} rpm; Tether-Struktur stabil.", state)
            log_event("TETHER_CHARGING_STARTED", MissionPhase.ELECTRIC_SAIL_CHARGING, f"{config.tether_voltage_kv:g} kV Hochspannung aktiviert; eigene Bordenergie erforderlich.", state)
            if config.electric_sail_enabled:
                satellite.electric_sail.charge()
                if electric_propulsion is not None:
                    electric_propulsion.parameters["deployed"] = True
                    electric_propulsion.parameters["charged"] = True
                satellite.payload.power_mode = PowerMode.HIGH_VOLTAGE
                sail_active = True
                log_event("ELECTRIC_SAIL_ACTIVE", MissionPhase.ELECTRIC_SAIL_PROPULSION, "Radiales Electric-Sail-Modell aktiv.", state)
                log_event("ELECTRIC_SAIL_PROPULSION_ACTIVE", MissionPhase.ELECTRIC_SAIL_PROPULSION, "Schub nimmt umgekehrt proportional zum Sonnenabstand ab.", state)
            else:
                sail_active = True  # deployment is complete; propulsion remains zero
        if not deep_space_logged and radius_au >= 5:
            satellite.payload.power_mode = PowerMode.CRUISE
            log_event("DEEP_SPACE_CRUISE_STARTED", MissionPhase.DEEP_SPACE_CRUISE, "Jupiterdistanz erreicht; Deep-Space-Modus aktiv.", state)
            deep_space_logged = True

        if deep_space_logged:
            phase = MissionPhase.DEEP_SPACE_CRUISE
        elif config.electric_sail_enabled and sail_active:
            phase = MissionPhase.ELECTRIC_SAIL_PROPULSION
        elif sail_active:
            # Generic post-commissioning coast/propulsion phase when no
            # Electric Sail is selected (ion, solar sail, nuclear, ...).
            phase = MissionPhase.DEEP_SPACE_CRUISE
        else:
            phase = MissionPhase.ELECTRIC_SAIL_DEPLOYMENT
        step = min(_adaptive_step_seconds(radius_au, True), mission_end_seconds - elapsed_seconds)
        previous_elapsed = elapsed_seconds
        previous_state = state
        solar_power_w = 20_000.0 / max(radius_au**2, 0.04)
        environment = SimulationEnvironment(
            phase=phase.value,
            distance_au=radius_au,
            solar_flux_w_m2=SOLAR_CONSTANT_W_M2 / max(radius_au**2, 1e-6),
            solar_wind_factor=1.0,
            power_available_w=solar_power_w,
            theoretical_mode=config.theoretical_propulsion_mode,
        )
        propulsion_result = propulsion_system.update(step, state, environment, effective_mass_kg())
        for module in propulsion_system.modules:
            if module.active_seconds > 0 and module.id not in propulsion_activations:
                propulsion_activations.add(module.id)
                log_event(
                    f"PROPULSION_{module.type.value.upper()}_ACTIVE",
                    phase,
                    f"{module.name} aktiv; Readiness {module.readiness.value}.",
                    state,
                    "warning" if module.readiness.value in {"conceptual", "speculative", "fictional"} else "info",
                )
        state = _rk4(
            state,
            step,
            0.0,
            epoch_days_j2000,
            elapsed_seconds,
            config.n_body_enabled,
            propulsion_result.acceleration_vector_km_s2,
        )
        coast_state = _rk4(
            coast_state,
            step,
            0.0,
            epoch_days_j2000,
            elapsed_seconds,
            config.n_body_enabled,
        )
        elapsed_seconds += step
        run_navigation_cycle(step)
        record_interval = _trajectory_record_interval_seconds(_magnitude(state[0]) / AU_KM)
        if phase != last_recorded_phase or elapsed_seconds - last_recorded_seconds >= record_interval:
            record(state, phase)
            last_recorded_seconds = elapsed_seconds
            last_recorded_phase = phase

        for year in year_targets:
            target_seconds = year * YEAR_DAYS * DAY_SECONDS
            key = str(year)
            if previous_elapsed < target_seconds <= elapsed_seconds and key not in distance_by_year:
                fraction = (target_seconds - previous_elapsed) / (elapsed_seconds - previous_elapsed)
                interpolated_position = _add(previous_state[0], tuple(state[0][i] - previous_state[0][i] for i in range(3)), fraction)  # type: ignore[arg-type]
                interpolated_velocity = _add(previous_state[1], tuple(state[1][i] - previous_state[1][i] for i in range(3)), fraction)  # type: ignore[arg-type]
                distance_by_year[key] = _magnitude(interpolated_position) / AU_KM
                speed_by_year[key] = _magnitude(interpolated_velocity)
                log_event(f"YEAR_{year}_REACHED", MissionPhase.DEEP_SPACE_CRUISE, f"{year}-Jahres-Zustand berechnet: {distance_by_year[key]:.2f} AE.", state)

    record(state, MissionPhase.MISSION_COMPLETE)
    log_event("SIMULATION_COMPLETE", MissionPhase.MISSION_COMPLETE, f"{config.mission_years:g}-Jahres-Simulation vollständig berechnet.", state, "warning" if warnings else "info")
    final_speed = _magnitude(state[1])
    sail_gain = max(0.0, final_speed - _magnitude(coast_state[1])) if config.electric_sail_enabled else 0.0
    warnings.extend(propulsion_system.warnings)
    warnings = list(dict.fromkeys(warnings))
    time_to_saturn_days = next(
        (point.elapsed_days for point in trajectory if _magnitude(point.position_km) / AU_KM >= 9.5367),
        None,
    )
    time_to_voyager_distance_days = next(
        (point.elapsed_days for point in trajectory if _magnitude(point.position_km) / AU_KM >= 35.8),
        None,
    )
    max_planetary_perturbation = 0.0
    if config.n_body_enabled:
        max_planetary_perturbation = max(
            _magnitude(_planetary_perturbation(
                point.position_km,
                epoch_days_j2000 + point.elapsed_days,
            ))
            for point in trajectory
        ) * 1e6
    summary = MissionSummary(
        status="WARNING" if warnings else "SUCCESS",
        total_flight_days=elapsed_seconds / DAY_SECONDS,
        perihelion_au=actual_perihelion_au,
        max_solar_flux_w_m2=max_solar_flux,
        pre_burn_speed_km_s=pre_burn_speed,
        post_burn_speed_km_s=post_burn_speed,
        achieved_burn_delta_v_km_s=achieved_delta_v,
        propellant_used_kg=prefix_fuel_used + propellant_used,
        payload_mass_kg=config.payload_mass_kg,
        distance_au_by_year=distance_by_year,
        speed_km_s_by_year=speed_by_year,
        electric_sail_gain_km_s=sail_gain,
        navigation_cycles=navigation.cycles,
        position_uncertainty_km=navigation.position_uncertainty_km if config.kalman_enabled else 0.0,
        velocity_uncertainty_km_s=navigation.velocity_uncertainty_km_s if config.kalman_enabled else 0.0,
        max_planetary_perturbation_mm_s2=max_planetary_perturbation,
        propulsion_report=propulsion_system.reports(),
        time_to_saturn_days=time_to_saturn_days,
        time_to_voyager_distance_days=time_to_voyager_distance_days,
        warnings=warnings,
    )
    return MissionResult(config=config, events=events, trajectory=trajectory, summary=summary, departure_validation=evidence['validation'])


def get_default_mission_config() -> dict:
    return MissionConfig().to_dict()
