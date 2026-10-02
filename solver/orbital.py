"""Shared state contracts and orbital algebra; propagation stays in the solver."""
from __future__ import annotations

from datetime import datetime, timezone
from math import acos, atan2, cos, exp, isfinite, log, pi, sin, sqrt

import numpy as np

DAY_SECONDS = 86400.0
G0_KM_S2 = 0.00980665
SCHEMA_VERSION = "2.0"


def utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Existing date-only project files mean UTC midnight.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def timestamp(value: str | datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def vector(value, field="vector") -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{field} benötigt drei endliche Komponenten.")
    return result


def unit(value) -> np.ndarray:
    value = vector(value)
    length = float(np.linalg.norm(value))
    if length <= 1e-14:
        raise ValueError("Ein Nullvektor besitzt keine Richtung.")
    return value / length


def angle(first, second) -> float:
    return acos(float(np.clip(np.dot(unit(first), unit(second)), -1, 1))) * 180 / pi


def state(position, velocity, epoch, *, frame="ECLIPJ2000", center="sun", mass=None, propellant=None) -> dict:
    result = {"positionKm": vector(position).tolist(), "velocityKmS": vector(velocity).tolist(),
              "epochUtc": timestamp(epoch), "frame": frame, "centerBodyId": center}
    if mass is not None:
        if not isfinite(float(mass)) or mass <= 0:
            raise ValueError("Zustandsmasse muss positiv und endlich sein.")
        result["massKg"] = float(mass)
    if propellant is not None:
        if not isfinite(float(propellant)) or propellant < 0 or (mass is not None and propellant >= mass):
            raise ValueError("Ungültige Treibstoffmasse im Zustand.")
        result["propellantKg"] = float(propellant)
    return result


def elements(position, velocity, mu: float) -> dict:
    r, v = vector(position), vector(velocity)
    radius, speed = float(np.linalg.norm(r)), float(np.linalg.norm(v))
    if mu <= 0 or radius <= 0:
        raise ValueError("Bahnelemente benötigen positiven Radius und GM.")
    h = np.cross(r, v); hn = float(np.linalg.norm(h))
    energy = speed * speed / 2 - mu / radius
    evec = np.cross(v, h) / mu - r / radius
    eccentricity = float(np.linalg.norm(evec))
    semimajor = -mu / (2 * energy) if abs(energy) > 1e-16 else None
    p = hn * hn / mu
    periapsis = p / (1 + eccentricity) if hn else 0.0
    apoapsis = p / (1 - eccentricity) if eccentricity < 1 else None
    node = np.cross([0., 0., 1.], h); nn = float(np.linalg.norm(node))
    inclination = acos(float(np.clip(h[2] / hn, -1, 1))) * 180 / pi if hn else None
    raan = atan2(node[1], node[0]) * 180 / pi % 360 if nn > 1e-12 else None
    anomaly = atan2(float(np.dot(np.cross(evec, r), h)) / max(hn, 1e-30), float(np.dot(evec, r))) if eccentricity > 1e-12 else 0.0
    return {"specificEnergyKm2S2": energy, "eccentricity": eccentricity,
            "semiMajorAxisKm": semimajor, "periapsisRadiusKm": periapsis,
            "apoapsisRadiusKm": apoapsis, "inclinationDeg": inclination,
            "raanDeg": raan, "trueAnomalyDeg": anomaly * 180 / pi,
            "angularMomentumKm2S": hn, "bound": energy < 0 and eccentricity < 1,
            "periodSeconds": 2 * pi * sqrt(semimajor**3 / mu) if energy < 0 and semimajor else None}


def circular_state(radius: float, mu: float, *, inclination_deg=0., raan_deg=0., phase_deg=0.) -> tuple:
    if radius <= 0 or mu <= 0:
        raise ValueError("Eine Parkbahn benötigt Radius > 0 und bekanntes GM.")
    i, node, phase = (float(x) * pi / 180 for x in (inclination_deg, raan_deg, phase_deg))
    p = np.array([cos(node), sin(node), 0.])
    q = np.array([-sin(node) * cos(i), cos(node) * cos(i), sin(i)])
    return radius * (cos(phase) * p + sin(phase) * q), sqrt(mu / radius) * (-sin(phase) * p + cos(phase) * q)


def rotate_inertial(position, velocity, source: str, destination: str) -> tuple:
    if source == destination:
        return vector(position), vector(velocity)
    aliases = {"ECI": "J2000", "ICRF": "J2000"}
    source, destination = aliases.get(source, source), aliases.get(destination, destination)
    if {source, destination} != {"J2000", "ECLIPJ2000"}:
        raise ValueError(f"Nicht unterstützte inertiale Transformation {source} → {destination}.")
    eps = 23.439291111 * pi / 180 * (1 if source == "ECLIPJ2000" else -1)
    transform = np.array([[1, 0, 0], [0, cos(eps), -sin(eps)], [0, sin(eps), cos(eps)]])
    return transform @ vector(position), transform @ vector(velocity)


def maneuver(before: dict, new_velocity, kind: str, *, isp_seconds=None, available_propellant=None) -> dict:
    delta = vector(new_velocity) - vector(before["velocityKmS"])
    required = float(np.linalg.norm(delta)); after = dict(before)
    after["velocityKmS"] = vector(new_velocity).tolist()
    used = None; mass_valid = None
    if "massKg" in before and available_propellant is not None and isp_seconds is not None:
        if isp_seconds <= 0 or available_propellant < 0:
            raise ValueError("Ungültige Antriebsdaten.")
        used = before["massKg"] * (1 - exp(-required / (isp_seconds * G0_KM_S2)))
        mass_valid = used <= available_propellant + 1e-9
        # An infeasible design has no executed after-state.
        if mass_valid:
            after["massKg"] -= used; after["propellantKg"] = available_propellant - used
    return {"type": kind, "epochUtc": before["epochUtc"], "deltaVVectorKmS": delta.tolist(),
            "deltaVKmS": required, "stateBefore": before, "stateAfter": after,
            "propellantUsedKg": used, "vehicleFeasible": mass_valid, "applied": mass_valid is not False}


def continuity(segments: list[dict], *, position_tolerance_km=.01, velocity_tolerance_km_s=1e-7) -> dict:
    checks = []
    for left, right in zip(segments, segments[1:]):
        a, b = left.get("endState"), right.get("startState")
        if not a or not b:
            checks.append({"from": left["id"], "to": right["id"], "valid": False, "reason": "Zustand fehlt"}); continue
        same_frame = a.get("frame") == b.get("frame") and a.get("centerBodyId") == b.get("centerBodyId")
        dr = float(np.linalg.norm(vector(a["positionKm"]) - vector(b["positionKm"])))
        dv = float(np.linalg.norm(vector(a["velocityKmS"]) - vector(b["velocityKmS"])))
        dt = abs((utc(a["epochUtc"]) - utc(b["epochUtc"])).total_seconds())
        declared_burn = right.get("departureManeuver")
        accounted = False
        if declared_burn:
            accounted = np.linalg.norm(vector(b["velocityKmS"]) - vector(a["velocityKmS"]) - vector(declared_burn["deltaVVectorKmS"])) <= velocity_tolerance_km_s
        mass_ok = a.get("massKg") is None or b.get("massKg") is None or b["massKg"] <= a["massKg"] + 1e-8
        checks.append({"from": left["id"], "to": right["id"], "positionResidualKm": dr,
                       "velocityResidualKmS": dv, "epochResidualSeconds": dt,
                       "maneuverAccountsForVelocityChange": bool(accounted),
                       "valid": bool(same_frame and dr <= position_tolerance_km and dt <= 1e-6 and (dv <= velocity_tolerance_km_s or accounted) and mass_ok)})
    complete = bool(segments) and all(s.get("startState") and s.get("endState") for s in segments)
    return {"stateChain": complete and all(c["valid"] for c in checks), "checks": checks,
            "positionToleranceKm": position_tolerance_km, "velocityToleranceKmS": velocity_tolerance_km_s}
