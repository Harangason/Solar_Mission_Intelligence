"""Optional NAIF SPICE ephemeris backend.

The SPICE kernel pool is process-global.  Access is therefore serialized and
the configured meta-kernel is furnished exactly once per process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
import os
from pathlib import Path
from threading import RLock
from typing import Any

try:
    import spiceypy as _spice
    from spiceypy.utils.exceptions import SpiceyError
except ImportError:  # The Kepler fallback must work without SpiceyPy.
    _spice = None

    class SpiceyError(Exception):
        """Fallback exception type used when SpiceyPy is unavailable."""


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_META_KERNEL = PROJECT_ROOT / "kernels" / "solar_system.tm"
VALID_MODES = {"auto", "kepler", "spice"}

# Prefer planet centers when an appropriate satellite SPK is present.  The
# compact DE440s kernel contains barycenters for several planetary systems, so
# each center has a barycenter fallback.
SPICE_TARGETS: dict[str, tuple[str, ...]] = {
    "sun": ("SUN",),
    "earth-moon": ("MOON",),
    "mars-phobos": ("PHOBOS",), "mars-deimos": ("DEIMOS",),
    "jupiter-io": ("IO",), "jupiter-europa": ("EUROPA",),
    "jupiter-ganymede": ("GANYMEDE",), "jupiter-callisto": ("CALLISTO",),
    "saturn-titan": ("TITAN",), "saturn-enceladus": ("ENCELADUS",),
    "uranus-titania": ("TITANIA",), "uranus-oberon": ("OBERON",),
    "neptune-triton": ("TRITON",),
    "mercury": ("MERCURY", "MERCURY BARYCENTER"),
    "venus": ("VENUS", "VENUS BARYCENTER"),
    "earth": ("EARTH", "EARTH BARYCENTER"),
    "mars": ("MARS", "MARS BARYCENTER"),
    "jupiter": ("JUPITER", "JUPITER BARYCENTER"),
    "saturn": ("SATURN", "SATURN BARYCENTER"),
    "uranus": ("URANUS", "URANUS BARYCENTER"),
    "neptune": ("NEPTUNE", "NEPTUNE BARYCENTER"),
}


def _configured_mode() -> str:
    mode = os.environ.get("SOLAR_SYSTEM_EPHEMERIS", "auto").strip().lower()
    if mode not in VALID_MODES:
        choices = ", ".join(sorted(VALID_MODES))
        raise RuntimeError(
            f"Ungültiger Wert SOLAR_SYSTEM_EPHEMERIS={mode!r}; erlaubt: {choices}."
        )
    return mode


def _configured_meta_kernel() -> Path:
    configured = os.environ.get("SOLAR_SYSTEM_SPICE_METAKERNEL")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_META_KERNEL


@dataclass(eq=False)
class SpiceEphemeris:
    """Load and query a configured SPICE planetary ephemeris."""

    mode: str = field(default_factory=_configured_mode)
    meta_kernel: Path = field(default_factory=_configured_meta_kernel)
    frame: str = "ECLIPJ2000"
    observer: str = "SUN"
    aberration_correction: str = "NONE"
    _loaded: bool = field(default=False, init=False)
    _attempted: bool = field(default=False, init=False)
    _error: str | None = field(default=None, init=False)
    _resolved_targets: dict[str, str] = field(default_factory=dict, init=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    @property
    def strict(self) -> bool:
        return self.mode == "spice"

    def _unavailable(self, message: str) -> bool:
        self._error = message
        if self.strict:
            raise RuntimeError(message)
        return False

    def ensure_loaded(self) -> bool:
        """Return whether SPICE is active, loading the meta-kernel if needed."""
        if self.mode == "kepler":
            return False
        with self._lock:
            if self._loaded:
                return True
            if self._attempted:
                if self.strict and self._error:
                    raise RuntimeError(self._error)
                return False

            self._attempted = True
            if _spice is None:
                return self._unavailable(
                    "SPICE wurde angefordert, aber das Paket 'spiceypy' ist nicht installiert."
                )
            if not self.meta_kernel.is_file():
                return self._unavailable(
                    "Kein SPICE-Meta-Kernel gefunden: "
                    f"{self.meta_kernel}. Bitte 'python scripts/download_spice_kernels.py' ausführen."
                )
            try:
                _spice.furnsh(str(self.meta_kernel))
            except SpiceyError as error:
                return self._unavailable(
                    f"SPICE-Meta-Kernel konnte nicht geladen werden: {error}"
                )

            self._loaded = True
            self._error = None
            return True

    def utc_to_et(self, timestamp: datetime) -> float | None:
        """Convert an aware UTC datetime to SPICE ephemeris seconds past J2000."""
        if not self.ensure_loaded():
            return None
        timestamp = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
        utc = timestamp.strftime("%Y-%m-%d %H:%M:%S.%f UTC")
        with self._lock:
            try:
                return float(_spice.str2et(utc))
            except SpiceyError as error:
                raise RuntimeError(f"SPICE-Zeitumrechnung fehlgeschlagen: {error}") from error

    @lru_cache(maxsize=32_768)
    def state(self, body_id: str, et_seconds: float) -> tuple[tuple[float, ...], str] | None:
        """Return geometric heliocentric state in km and km/s."""
        if not self.ensure_loaded():
            return None
        if body_id not in SPICE_TARGETS:
            raise ValueError(f"Kein SPICE-Ziel für Himmelskörper {body_id!r} definiert.")

        with self._lock:
            candidates = SPICE_TARGETS[body_id]
            preferred = self._resolved_targets.get(body_id)
            if preferred:
                candidates = (preferred,) + tuple(
                    candidate for candidate in candidates if candidate != preferred
                )

            errors: list[str] = []
            for target in candidates:
                try:
                    state, _ = _spice.spkezr(
                        target,
                        et_seconds,
                        self.frame,
                        self.aberration_correction,
                        self.observer,
                    )
                except SpiceyError as error:
                    errors.append(f"{target}: {error}")
                    continue

                self._resolved_targets[body_id] = target
                return tuple(float(value) for value in state), target

        details = "; ".join(errors)
        raise RuntimeError(
            f"Keine SPICE-Ephemeride für {body_id!r} bei ET={et_seconds:.3f}: {details}"
        )

    def status(self) -> dict[str, Any]:
        """Describe the selected backend without exposing kernel internals."""
        active = self.ensure_loaded()
        version = None
        if _spice is not None:
            version = getattr(_spice, "__version__", None)
        return {
            "mode": self.mode,
            "backend": "spice" if active else "kepler",
            "active": active,
            "metaKernel": str(self.meta_kernel),
            "spiceypyVersion": version,
            "frame": self.frame if active else None,
            "observer": self.observer if active else None,
            "aberrationCorrection": self.aberration_correction if active else None,
            "resolvedTargets": dict(self._resolved_targets),
            "error": self._error,
        }


EPHEMERIS = SpiceEphemeris()


def utc_to_ephemeris_seconds(timestamp: datetime) -> float | None:
    return EPHEMERIS.utc_to_et(timestamp)


def planet_state(
    body_id: str, et_seconds: float
) -> tuple[tuple[float, ...], str] | None:
    return EPHEMERIS.state(body_id, et_seconds)


def get_ephemeris_status() -> dict[str, Any]:
    return EPHEMERIS.status()


def body_gm(body_id: str) -> float | None:
    """Kernel constants are the canonical GM source, in km^3/s^2."""
    if not EPHEMERIS.ensure_loaded() or body_id not in SPICE_TARGETS:
        return None
    with EPHEMERIS._lock:
        try:
            _, values = _spice.bodvrd(SPICE_TARGETS[body_id][0], "GM", 1)
            return float(values[0])
        except SpiceyError:
            return None


def body_quality(body_id: str, epoch_seconds: float) -> dict:
    if body_id == "sun":
        return {"available": True, "centerExact": True, "backend": "origin"}
    if not EPHEMERIS.ensure_loaded() or body_id not in SPICE_TARGETS:
        return {"available": False, "centerExact": False, "backend": "mean-elements"}
    try:
        _, resolved = EPHEMERIS.state(body_id, epoch_seconds)
    except (ValueError, RuntimeError):
        return {"available": False, "centerExact": False, "backend": "unavailable"}
    return {"available": True, "centerExact": "BARYCENTER" not in resolved,
            "backend": "spice", "resolvedTarget": resolved}
