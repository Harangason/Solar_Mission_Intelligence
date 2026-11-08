"""Normalized SQLite persistence for route calculation runs and variants."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID, uuid4

from services.project_store import PROJECT_DATABASE


CALCULATION_SCHEMA_VERSION = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid(value: object, field_name: str = "ID") -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field_name} ist keine gültige UUID.") from error


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    return None if number is None else int(number)


def _boolean(value: object) -> int | None:
    return None if value is None else int(bool(value))


def _clean_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json(value: object) -> str:
    return json.dumps(
        _clean_json(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _vector(value: object) -> tuple[float | None, float | None, float | None]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None, None, None
    return (
        _optional_float(value[0]),
        _optional_float(value[1]),
        _optional_float(value[2]),
    )


def _without_heavy_trajectories(result: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(result)
    metadata.pop("trajectory", None)
    for key in ('genericTrajectoryPlan','legacyRoute'):
        if isinstance(metadata.get(key),dict):metadata[key]=_without_heavy_trajectories(metadata[key])
    high_fidelity = metadata.get("highFidelityNBody")
    if isinstance(high_fidelity, dict):
        metadata["highFidelityNBody"] = {
            key: value for key, value in high_fidelity.items() if key != "trajectory"
        }
    flyby = metadata.get("flybyGeometry")
    if isinstance(flyby, dict):
        metadata["flybyGeometry"] = {
            key: value for key, value in flyby.items() if key != "relativeTrajectory"
        }
    return metadata


class CalculationStore:
    """Persist calculation runs and their normalized result hierarchy."""

    def __init__(self, database_path: Path = PROJECT_DATABASE):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS calculation_schema_migrations (
                    id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL UNIQUE,
                    applied_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS calculation_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NULL REFERENCES projects(id) ON DELETE SET NULL,
                    run_type TEXT NOT NULL,
                    solver_name TEXT NOT NULL,
                    route_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    base_date TEXT NULL,
                    search_start_date TEXT NULL,
                    search_end_date TEXT NULL,
                    broad_step_days INTEGER NULL,
                    geometric_node_count INTEGER NOT NULL DEFAULT 0,
                    graph_edge_count INTEGER NOT NULL DEFAULT 0,
                    shortlist_count INTEGER NOT NULL DEFAULT 0,
                    preflight_budget INTEGER NOT NULL DEFAULT 0,
                    full_validation_budget INTEGER NOT NULL DEFAULT 0,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    flight_ready_count INTEGER NOT NULL DEFAULT 0,
                    best_variant_id TEXT NULL
                        REFERENCES calculation_variants(id) ON DELETE SET NULL,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    geometry_snapshot_json TEXT NOT NULL DEFAULT '[]',
                    error_message TEXT NOT NULL DEFAULT '',
                    started_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT NULL
                );

                CREATE TABLE IF NOT EXISTS calculation_variants (
                    id TEXT PRIMARY KEY,
                    calculation_run_id TEXT NOT NULL
                        REFERENCES calculation_runs(id) ON DELETE CASCADE,
                    iteration_number INTEGER NOT NULL,
                    start_date TEXT NOT NULL,
                    search_stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    full_corridor_check INTEGER NOT NULL DEFAULT 0,
                    geometric_score REAL NULL,
                    quality_score REAL NULL,
                    result_rank INTEGER NULL,
                    is_selected INTEGER NOT NULL DEFAULT 0,
                    geometry_valid INTEGER NULL,
                    section_order_valid INTEGER NULL,
                    state_continuous INTEGER NULL,
                    endpoints_reached INTEGER NULL,
                    maximum_endpoint_residual_km REAL NULL,
                    performance_evaluated INTEGER NOT NULL DEFAULT 0,
                    is_hypothetical_interstellar INTEGER NOT NULL DEFAULT 0,
                    is_feasible INTEGER NULL,
                    corridor_satisfied INTEGER NULL,
                    collision_free INTEGER NULL,
                    total_flight_days REAL NULL,
                    required_delta_v_km_s REAL NULL,
                    available_delta_v_km_s REAL NULL,
                    target_correction_delta_v_km_s REAL NULL,
                    corridor_insertion_deficit_km_s REAL NULL,
                    delta_v_deficit_km_s REAL NULL,
                    target_alignment_deg REAL NULL,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    result_metadata_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    UNIQUE(calculation_run_id, iteration_number)
                );

                CREATE TABLE IF NOT EXISTS calculation_route_sections (
                    id TEXT PRIMARY KEY,
                    calculation_variant_id TEXT NOT NULL
                        REFERENCES calculation_variants(id) ON DELETE CASCADE,
                    source_section_id TEXT NOT NULL,
                    section_index INTEGER NOT NULL,
                    origin_body_id TEXT NOT NULL,
                    target_body_id TEXT NOT NULL,
                    target_name TEXT NOT NULL DEFAULT '',
                    section_type TEXT NOT NULL DEFAULT '',
                    reference_frame TEXT NOT NULL DEFAULT 'heliocentric-ecliptic',
                    entry_day REAL NULL,
                    periapsis_day REAL NULL,
                    exit_day REAL NULL,
                    transfer_duration_days REAL NULL,
                    minimum_altitude_km REAL NULL,
                    passage_angle_deg REAL NULL,
                    entry_position_x_km REAL NULL,
                    entry_position_y_km REAL NULL,
                    entry_position_z_km REAL NULL,
                    entry_direction_x REAL NULL,
                    entry_direction_y REAL NULL,
                    entry_direction_z REAL NULL,
                    corridor_enabled INTEGER NULL,
                    corridor_satisfied INTEGER NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(calculation_variant_id, section_index)
                );

                CREATE TABLE IF NOT EXISTS calculation_delta_v (
                    id TEXT PRIMARY KEY,
                    calculation_variant_id TEXT NOT NULL
                        REFERENCES calculation_variants(id) ON DELETE CASCADE,
                    calculation_route_section_id TEXT NULL
                        REFERENCES calculation_route_sections(id) ON DELETE CASCADE,
                    delta_v_type TEXT NOT NULL,
                    required_delta_v_km_s REAL NULL,
                    available_delta_v_km_s REAL NULL,
                    applied_delta_v_km_s REAL NULL,
                    delta_v_deficit_km_s REAL NULL,
                    is_applied INTEGER NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS calculation_velocities (
                    id TEXT PRIMARY KEY,
                    calculation_variant_id TEXT NOT NULL
                        REFERENCES calculation_variants(id) ON DELETE CASCADE,
                    calculation_route_section_id TEXT NULL
                        REFERENCES calculation_route_sections(id) ON DELETE CASCADE,
                    velocity_event TEXT NOT NULL,
                    reference_frame TEXT NOT NULL,
                    velocity_x_km_s REAL NULL,
                    velocity_y_km_s REAL NULL,
                    velocity_z_km_s REAL NULL,
                    speed_km_s REAL NULL,
                    elapsed_days REAL NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS calculation_trajectory_points (
                    id TEXT PRIMARY KEY,
                    calculation_variant_id TEXT NOT NULL
                        REFERENCES calculation_variants(id) ON DELETE CASCADE,
                    calculation_route_section_id TEXT NULL
                        REFERENCES calculation_route_sections(id) ON DELETE SET NULL,
                    trajectory_kind TEXT NOT NULL,
                    point_index INTEGER NOT NULL,
                    elapsed_days REAL NOT NULL,
                    position_x_km REAL NOT NULL,
                    position_y_km REAL NOT NULL,
                    position_z_km REAL NOT NULL,
                    velocity_x_km_s REAL NULL,
                    velocity_y_km_s REAL NULL,
                    velocity_z_km_s REAL NULL,
                    phase_name TEXT NOT NULL DEFAULT '',
                    UNIQUE(calculation_variant_id, trajectory_kind, point_index)
                );

                CREATE TABLE IF NOT EXISTS calculation_warnings (
                    id TEXT PRIMARY KEY,
                    calculation_variant_id TEXT NOT NULL
                        REFERENCES calculation_variants(id) ON DELETE CASCADE,
                    warning_index INTEGER NOT NULL,
                    warning_code TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL DEFAULT 'warning',
                    message TEXT NOT NULL,
                    source_reference TEXT NOT NULL DEFAULT '',
                    UNIQUE(calculation_variant_id, warning_index)
                );

                CREATE INDEX IF NOT EXISTS calculation_runs_project_idx
                    ON calculation_runs(project_id, started_at_utc DESC);
                CREATE INDEX IF NOT EXISTS calculation_runs_status_idx
                    ON calculation_runs(status, started_at_utc DESC);
                CREATE INDEX IF NOT EXISTS calculation_variants_run_idx
                    ON calculation_variants(calculation_run_id, iteration_number);
                CREATE INDEX IF NOT EXISTS calculation_variants_quality_idx
                    ON calculation_variants(calculation_run_id, quality_score DESC);
                CREATE INDEX IF NOT EXISTS calculation_sections_variant_idx
                    ON calculation_route_sections(calculation_variant_id, section_index);
                CREATE INDEX IF NOT EXISTS calculation_delta_v_variant_idx
                    ON calculation_delta_v(calculation_variant_id, delta_v_type);
                CREATE INDEX IF NOT EXISTS calculation_velocities_variant_idx
                    ON calculation_velocities(calculation_variant_id, velocity_event);
                CREATE INDEX IF NOT EXISTS calculation_trajectory_variant_idx
                    ON calculation_trajectory_points(
                        calculation_variant_id, trajectory_kind, point_index
                    );
                CREATE INDEX IF NOT EXISTS calculation_warnings_variant_idx
                    ON calculation_warnings(calculation_variant_id, warning_index);
                """
            )
            variant_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(calculation_variants)"
                ).fetchall()
            }
            if (
                "corridor_insertion_delta_v_km_s" in variant_columns
                and "corridor_insertion_deficit_km_s" not in variant_columns
            ):
                connection.execute(
                    """
                    ALTER TABLE calculation_variants
                    RENAME COLUMN corridor_insertion_delta_v_km_s
                    TO corridor_insertion_deficit_km_s
                    """
                )
            variant_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(calculation_variants)"
                ).fetchall()
            }
            variant_column_additions = {
                "geometry_valid": "INTEGER NULL",
                "section_order_valid": "INTEGER NULL",
                "state_continuous": "INTEGER NULL",
                "endpoints_reached": "INTEGER NULL",
                "maximum_endpoint_residual_km": "REAL NULL",
                "performance_evaluated": "INTEGER NOT NULL DEFAULT 0",
                "is_hypothetical_interstellar": "INTEGER NOT NULL DEFAULT 0",
            }
            for column_name, column_definition in variant_column_additions.items():
                if column_name not in variant_columns:
                    connection.execute(
                        f"ALTER TABLE calculation_variants "
                        f"ADD COLUMN {column_name} {column_definition}"
                    )
            run_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(calculation_runs)"
                ).fetchall()
            }
            if "geometry_snapshot_json" not in run_columns:
                connection.execute(
                    "ALTER TABLE calculation_runs "
                    "ADD COLUMN geometry_snapshot_json TEXT NOT NULL DEFAULT '[]'"
                )
            point_columns={r['name'] for r in connection.execute('PRAGMA table_info(calculation_trajectory_points)').fetchall()}
            if 'state_json' not in point_columns:
                connection.execute("ALTER TABLE calculation_trajectory_points ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}'")
            connection.execute(
                """
                INSERT OR IGNORE INTO calculation_schema_migrations (
                    id, schema_version, applied_at_utc
                ) VALUES (?, ?, ?)
                """,
                (str(uuid4()), CALCULATION_SCHEMA_VERSION, _utc_now()),
            )

    def start_run(self, values: dict[str, Any], project_id: str = "") -> dict[str, Any]:
        run_id = str(uuid4())
        timestamp = _utc_now()
        linked_project_id: str | None = None
        if project_id:
            project_id = _uuid(project_id, "Projekt-ID")
            with self._connection() as connection:
                project_exists = connection.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
            if project_exists is None:
                raise LookupError("Projekt für den Berechnungslauf nicht gefunden.")
            linked_project_id = project_id
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO calculation_runs (
                    id, project_id, run_type, solver_name, route_label, status,
                    base_date, search_start_date, search_end_date, broad_step_days,
                    geometric_node_count, graph_edge_count, shortlist_count,
                    preflight_budget, full_validation_budget, input_json,
                    started_at_utc
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    linked_project_id,
                    str(values.get("runType") or "constellation-search"),
                    str(values.get("solverName") or "temporal-weighted-best-first"),
                    str(values.get("routeLabel") or ""),
                    str(values.get("baseDate") or "") or None,
                    str(values.get("searchStartDate") or "") or None,
                    str(values.get("searchEndDate") or "") or None,
                    _optional_int(values.get("broadStepDays")),
                    _optional_int(values.get("graphNodes")) or 0,
                    _optional_int(values.get("graphEdges")) or 0,
                    _optional_int(values.get("shortlistCount")) or 0,
                    _optional_int(values.get("preflightBudget")) or 0,
                    _optional_int(values.get("fullValidationBudget")) or 0,
                    _json(values.get("input") or {}),
                    timestamp,
                ),
            )
        return self.get_run(run_id, include_trajectories=False)

    def update_run(self, run_id: str, values: dict[str, Any]) -> dict[str, Any]:
        run_id = _uuid(run_id, "Berechnungslauf-ID")
        columns = {
            "status": ("status", str),
            "graphNodes": ("geometric_node_count", _optional_int),
            "graphEdges": ("graph_edge_count", _optional_int),
            "shortlistCount": ("shortlist_count", _optional_int),
            "preflightBudget": ("preflight_budget", _optional_int),
            "fullValidationBudget": ("full_validation_budget", _optional_int),
            "resultCount": ("result_count", _optional_int),
            "flightReadyCount": ("flight_ready_count", _optional_int),
            "geometryPoints": ("geometry_snapshot_json", _json),
            "error": ("error_message", str),
        }
        assignments: list[str] = []
        parameters: list[object] = []
        for source_name, (column_name, converter) in columns.items():
            if source_name not in values:
                continue
            assignments.append(f"{column_name} = ?")
            parameters.append(converter(values[source_name]))
        if "bestVariantId" in values:
            best_variant_id = values.get("bestVariantId")
            assignments.append("best_variant_id = ?")
            parameters.append(
                None if not best_variant_id else _uuid(best_variant_id, "Varianten-ID")
            )
        status = str(values.get("status") or "")
        if status in {"completed", "rejected", "failed", "cancelled"}:
            assignments.append("completed_at_utc = ?")
            parameters.append(_utc_now())
        if not assignments:
            return self.get_run(run_id, include_trajectories=False)
        parameters.append(run_id)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE calculation_runs SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
        if cursor.rowcount == 0:
            raise LookupError("Berechnungslauf nicht gefunden.")
        return self.get_run(run_id, include_trajectories=False)

    def record_variant(
        self,
        run_id: str,
        metadata: dict[str, Any],
        request_values: dict[str, Any],
        *,
        result: dict[str, Any] | None,
        status: str,
        error_message: str = "",
    ) -> str:
        run_id = _uuid(run_id, "Berechnungslauf-ID")
        variant_id = str(uuid4())
        timestamp = _utc_now()
        summary = result.get("summary") if isinstance(result, dict) else {}
        summary = summary if isinstance(summary, dict) else {}
        route_sections = result.get("routeSections") if isinstance(result, dict) else []
        route_sections = route_sections if isinstance(route_sections, list) else []
        required_delta_v = _optional_float(summary.get("requiredInjectionDeltaVKmS"))
        available_delta_v = _optional_float(summary.get("availableInjectionDeltaVKmS"))
        target_delta_v = _optional_float(summary.get("targetCorrectionDeltaVKmS"))
        target_alignment = _optional_float(
            summary.get("actualTargetAlignmentDeg", summary.get("targetAlignmentDeg"))
        )
        is_feasible = summary.get("feasibleWithConfiguredBurn")
        is_hypothetical_interstellar = bool(
            summary.get("hypotheticalInterstellarAsymptote")
        )
        if str(request_values.get("calculationStage") or "") == "geometry":
            is_feasible = None
        collision_free = None
        if isinstance(result, dict):
            validation = result.get("validation")
            n_body = result.get("highFidelityNBody")
            collision_free = not (
                isinstance(validation, dict) and validation.get("collisionFree") is False
            ) and not (
                isinstance(n_body, dict) and n_body.get("collision") is True
            )
        with self._connection() as connection:
            run_exists = connection.execute(
                "SELECT 1 FROM calculation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run_exists is None:
                raise LookupError("Berechnungslauf nicht gefunden.")
            connection.execute(
                """
                INSERT INTO calculation_variants (
                    id, calculation_run_id, iteration_number, start_date,
                    search_stage, status, full_corridor_check, geometric_score,
                    is_hypothetical_interstellar, is_feasible, collision_free,
                    total_flight_days,
                    required_delta_v_km_s, available_delta_v_km_s,
                    target_correction_delta_v_km_s, target_alignment_deg,
                    input_json, result_metadata_json, error_message,
                    created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    variant_id,
                    run_id,
                    _optional_int(metadata.get("iteration")) or 0,
                    str(
                        metadata.get("startDate")
                        or (request_values.get("mission") or {}).get("startDate")
                        or ""
                    ),
                    str(metadata.get("stage") or "route-simulation"),
                    status,
                    int(bool(metadata.get("fullCorridorCheck"))),
                    _optional_float(metadata.get("geometricScore")),
                    _boolean(is_hypothetical_interstellar),
                    _boolean(is_feasible),
                    _boolean(collision_free),
                    _optional_float(
                        result.get("totalFlightDays") if isinstance(result, dict) else None
                    ),
                    required_delta_v,
                    available_delta_v,
                    target_delta_v,
                    target_alignment,
                    _json(request_values),
                    _json(_without_heavy_trajectories(result) if result else {}),
                    str(error_message or ""),
                    timestamp,
                    timestamp,
                ),
            )
            section_ids = self._insert_sections(
                connection, variant_id, route_sections
            )
            self._insert_delta_v(
                connection,
                variant_id,
                section_ids,
                route_sections,
                summary,
                request_values,
                result or {},
            )
            self._insert_velocities(
                connection, variant_id, section_ids, route_sections, summary
            )
            self._insert_trajectories(
                connection, variant_id, section_ids, result or {}
            )
            self._insert_warnings(connection, variant_id, result or {}, summary)
        return variant_id

    def _insert_sections(
        self,
        connection: sqlite3.Connection,
        variant_id: str,
        sections: list[object],
    ) -> list[str]:
        section_ids: list[str] = []
        for index, raw_section in enumerate(sections):
            section = raw_section if isinstance(raw_section, dict) else {}
            section_id = str(uuid4())
            section_ids.append(section_id)
            entry_position = _vector(section.get("entryPositionKm"))
            entry_direction = _vector(section.get("entryDirection"))
            corridor = section.get("corridor")
            corridor = corridor if isinstance(corridor, dict) else {}
            passage_angle = section.get(
                "selectedPassageAngleDeg", section.get("requestedPassageAngleDeg")
            )
            connection.execute(
                """
                INSERT INTO calculation_route_sections (
                    id, calculation_variant_id, source_section_id, section_index,
                    origin_body_id, target_body_id, target_name, section_type,
                    entry_day, periapsis_day, exit_day, transfer_duration_days,
                    minimum_altitude_km, passage_angle_deg,
                    entry_position_x_km, entry_position_y_km, entry_position_z_km,
                    entry_direction_x, entry_direction_y, entry_direction_z,
                    corridor_enabled, corridor_satisfied, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section_id,
                    variant_id,
                    str(section.get("id") or f"section-{index + 1}"),
                    index,
                    str(section.get("originId") or ""),
                    str(section.get("targetId") or ""),
                    str(section.get("targetName") or ""),
                    str(section.get("sectionType") or ""),
                    _optional_float(section.get("entryDay")),
                    _optional_float(section.get("periapsisDay")),
                    _optional_float(section.get("exitDay")),
                    _optional_float(section.get("transferDurationDays")),
                    _optional_float(section.get("minimumAltitudeKm")),
                    _optional_float(passage_angle),
                    *entry_position,
                    *entry_direction,
                    _boolean(corridor.get("enabled")),
                    _boolean(corridor.get("entryInsideCorridor")),
                    _json(section),
                ),
            )
        return section_ids

    @staticmethod
    def _delta_v_row(
        variant_id: str,
        section_id: str | None,
        delta_v_type: str,
        required: object,
        available: object,
        applied: object = None,
        deficit: object = None,
        is_applied: object = None,
        details: object = None,
    ) -> tuple[object, ...]:
        required_value = _optional_float(required)
        available_value = _optional_float(available)
        deficit_value = _optional_float(deficit)
        if (
            deficit_value is None
            and required_value is not None
            and available_value is not None
        ):
            deficit_value = max(0.0, required_value - available_value)
        return (
            str(uuid4()),
            variant_id,
            section_id,
            delta_v_type,
            required_value,
            available_value,
            _optional_float(applied),
            deficit_value,
            _boolean(is_applied),
            _json(details or {}),
        )

    def _insert_delta_v(
        self,
        connection: sqlite3.Connection,
        variant_id: str,
        section_ids: list[str],
        sections: list[object],
        summary: dict[str, Any],
        request_values: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if result.get('schemaVersion')=='2.0' and isinstance(result.get('maneuvers'),list):
            rows=[self._delta_v_row(variant_id,None,burn['type'],burn['deltaVKmS'],None,
                burn['deltaVKmS'] if burn.get('applied') else 0.,is_applied=burn.get('applied'),details=burn)
                for burn in result['maneuvers']]
            if summary.get('ascentDeltaVKmS'):
                rows.insert(0,self._delta_v_row(variant_id,None,'FINITE_THRUST_ASCENT',summary['ascentDeltaVKmS'],None,
                    summary['ascentDeltaVKmS'],is_applied=True,details={'includedInTotal':True}))
            connection.executemany('INSERT INTO calculation_delta_v (id,calculation_variant_id,calculation_route_section_id,delta_v_type,required_delta_v_km_s,available_delta_v_km_s,applied_delta_v_km_s,delta_v_deficit_km_s,is_applied,details_json) VALUES (?,?,?,?,?,?,?,?,?,?)',rows)
            return
        rows = [
            self._delta_v_row(
                variant_id,
                None,
                "injection",
                summary.get("requiredInjectionDeltaVKmS"),
                summary.get("availableInjectionDeltaVKmS"),
                summary.get("requiredInjectionDeltaVKmS")
                if summary.get("solarDepartureInjectionApplied")
                else 0.0,
                is_applied=summary.get("solarDepartureInjectionApplied"),
            )
        ]
        target_correction = _optional_float(summary.get("targetCorrectionDeltaVKmS"))
        if target_correction is not None:
            rows.append(
                self._delta_v_row(
                    variant_id,
                    None,
                    "target_correction",
                    target_correction,
                    None,
                    target_correction if summary.get("targetInjectionApplied") else 0.0,
                    is_applied=summary.get("targetInjectionApplied"),
                )
            )
        request_sections = request_values.get("routeSections")
        request_sections = request_sections if isinstance(request_sections, list) else []
        for index, raw_section in enumerate(sections):
            section = raw_section if isinstance(raw_section, dict) else {}
            section_id = section_ids[index] if index < len(section_ids) else None
            rows.append(
                self._delta_v_row(
                    variant_id,
                    section_id,
                    "transition",
                    section.get("requiredTransitionDeltaVKmS"),
                    section.get("availableTransitionDeltaVKmS"),
                    deficit=section.get("transitionDeltaVDeficitKmS"),
                    details={"sourceSectionId": section.get("id")},
                )
            )
            requested = request_sections[index] if index < len(request_sections) else {}
            requested = requested if isinstance(requested, dict) else {}
            rows.append(
                self._delta_v_row(
                    variant_id,
                    section_id,
                    "corridor_insertion",
                    section.get("corridorInsertionDeltaVKmS"),
                    requested.get("deltaVPlusKmS"),
                    details={"sourceSectionId": section.get("id")},
                )
            )
        connection.executemany(
            """
            INSERT INTO calculation_delta_v (
                id, calculation_variant_id, calculation_route_section_id,
                delta_v_type, required_delta_v_km_s, available_delta_v_km_s,
                applied_delta_v_km_s, delta_v_deficit_km_s, is_applied,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _insert_velocities(
        self,
        connection: sqlite3.Connection,
        variant_id: str,
        section_ids: list[str],
        sections: list[object],
        summary: dict[str, Any],
    ) -> None:
        events = {
            "incoming_excess": summary.get("incomingExcessSpeedKmS"),
            "heliocentric_before_flyby": summary.get("heliocentricSpeedBeforeKmS"),
            "heliocentric_after_flyby": summary.get("heliocentricSpeedAfterKmS"),
            "periapsis": summary.get("periapsisSpeedKmS"),
            "target_departure": summary.get("targetDepartureSpeedKmS"),
            "solar_escape_at_exit": summary.get("solarEscapeSpeedAtExitKmS"),
            "actual_solar_exit": summary.get("actualSolarExitSpeedKmS"),
        }
        rows: list[tuple[object, ...]] = []
        for event_name, speed in events.items():
            if _optional_float(speed) is None:
                continue
            rows.append(
                (
                    str(uuid4()),
                    variant_id,
                    None,
                    event_name,
                    "heliocentric-ecliptic",
                    None,
                    None,
                    None,
                    _optional_float(speed),
                    None,
                    "{}",
                )
            )
        for index, raw_section in enumerate(sections):
            section = raw_section if isinstance(raw_section, dict) else {}
            section_id = section_ids[index] if index < len(section_ids) else None
            radial_speed = _optional_float(section.get("departureRadialSpeedKmS"))
            if radial_speed is not None:
                rows.append(
                    (
                        str(uuid4()),
                        variant_id,
                        section_id,
                        "departure_radial",
                        "local-radial",
                        None,
                        None,
                        None,
                        radial_speed,
                        _optional_float(section.get("exitDay")),
                        "{}",
                    )
                )
        if rows:
            connection.executemany(
                """
                INSERT INTO calculation_velocities (
                    id, calculation_variant_id, calculation_route_section_id,
                    velocity_event, reference_frame, velocity_x_km_s,
                    velocity_y_km_s, velocity_z_km_s, speed_km_s,
                    elapsed_days, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _insert_trajectories(
        self,
        connection: sqlite3.Connection,
        variant_id: str,
        section_ids: list[str],
        result: dict[str, Any],
    ) -> None:
        sources: list[tuple[str, Iterable[object]]] = []
        trajectory = result.get("trajectory")
        if isinstance(trajectory, list):
            sources.append(("nominal", trajectory))
        n_body = result.get("highFidelityNBody")
        if isinstance(n_body, dict) and isinstance(n_body.get("trajectory"), list):
            sources.append(("high_fidelity_n_body", n_body["trajectory"]))
        flyby = result.get("flybyGeometry")
        if isinstance(flyby, dict) and isinstance(flyby.get("relativeTrajectory"), list):
            sources.append(("flyby_relative", flyby["relativeTrajectory"]))
        rows: list[tuple[object, ...]] = []
        for trajectory_kind, points in sources:
            for point_index, raw_point in enumerate(points):
                point = raw_point if isinstance(raw_point, dict) else {}
                position = _vector(point.get("positionKm"))
                if any(component is None for component in position):
                    continue
                velocity = _vector(point.get("velocityKmS"))
                rows.append(
                    (
                        str(uuid4()),
                        variant_id,
                        self._section_for_point(
                            point_index, result.get("routeSections"), section_ids
                        ),
                        trajectory_kind,
                        point_index,
                        _optional_float(point.get("elapsedDays")) or 0.0,
                        *position,
                        *velocity,
                        str(point.get("phase") or ""),
                        _json({k:point[k] for k in ('epochUtc','frame','centerBodyId','massKg','propellantKg') if k in point}),
                    )
                )
        if rows:
            connection.executemany(
                """
                INSERT INTO calculation_trajectory_points (
                    id, calculation_variant_id, calculation_route_section_id,
                    trajectory_kind, point_index, elapsed_days,
                    position_x_km, position_y_km, position_z_km,
                    velocity_x_km_s, velocity_y_km_s, velocity_z_km_s,
                    phase_name, state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    @staticmethod
    def _section_for_point(
        point_index: int, raw_sections: object, section_ids: list[str]
    ) -> str | None:
        if not isinstance(raw_sections, list):
            return None
        for index, raw_section in enumerate(raw_sections):
            section = raw_section if isinstance(raw_section, dict) else {}
            start_index = _optional_int(section.get("entryIndex"))
            end_index = _optional_int(section.get("exitIndex"))
            if (
                start_index is not None
                and end_index is not None
                and start_index <= point_index <= end_index
                and index < len(section_ids)
            ):
                return section_ids[index]
        return None

    def _insert_warnings(
        self,
        connection: sqlite3.Connection,
        variant_id: str,
        result: dict[str, Any],
        summary: dict[str, Any],
    ) -> None:
        messages: list[str] = []
        for source in (result.get("warnings"), summary.get("warnings")):
            if not isinstance(source, list):
                continue
            for warning in source:
                message = str(warning or "").strip()
                if message and message not in messages:
                    messages.append(message)
        connection.executemany(
            """
            INSERT INTO calculation_warnings (
                id, calculation_variant_id, warning_index, message
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (str(uuid4()), variant_id, index, message)
                for index, message in enumerate(messages)
            ],
        )

    def update_variant(
        self, run_id: str, variant_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        run_id = _uuid(run_id, "Berechnungslauf-ID")
        variant_id = _uuid(variant_id, "Varianten-ID")
        columns = {
            "status": ("status", str),
            "quality": ("quality_score", _optional_float),
            "rank": ("result_rank", _optional_int),
            "selected": ("is_selected", _boolean),
            "geometryValid": ("geometry_valid", _boolean),
            "sectionOrderValid": ("section_order_valid", _boolean),
            "stateContinuous": ("state_continuous", _boolean),
            "endpointsReached": ("endpoints_reached", _boolean),
            "maximumEndpointResidualKm": (
                "maximum_endpoint_residual_km",
                _optional_float,
            ),
            "performanceEvaluated": ("performance_evaluated", _boolean),
            "hypotheticalInterstellarAsymptote": (
                "is_hypothetical_interstellar",
                _boolean,
            ),
            "feasible": ("is_feasible", _boolean),
            "corridorSatisfied": ("corridor_satisfied", _boolean),
            "collisionFree": ("collision_free", _boolean),
            "corridorInsertionDeficitKmS": (
                "corridor_insertion_deficit_km_s",
                _optional_float,
            ),
            "deltaVDeficitKmS": ("delta_v_deficit_km_s", _optional_float),
            "targetAlignmentDeg": ("target_alignment_deg", _optional_float),
            "error": ("error_message", str),
        }
        assignments = ["updated_at_utc = ?"]
        parameters: list[object] = [_utc_now()]
        for source_name, (column_name, converter) in columns.items():
            if source_name in values:
                assignments.append(f"{column_name} = ?")
                parameters.append(converter(values[source_name]))
        parameters.extend((variant_id, run_id))
        with self._connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE calculation_variants
                SET {", ".join(assignments)}
                WHERE id = ? AND calculation_run_id = ?
                """,
                parameters,
            )
            if cursor.rowcount == 0:
                raise LookupError("Berechnungsvariante nicht gefunden.")
            if values.get("selected"):
                connection.execute(
                    """
                    UPDATE calculation_variants
                    SET is_selected = CASE WHEN id = ? THEN 1 ELSE 0 END
                    WHERE calculation_run_id = ?
                    """,
                    (variant_id, run_id),
                )
        return self.get_variant(variant_id, include_trajectory=False)

    @staticmethod
    def _run_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "runId": row["id"],
            "projectId": row["project_id"] or "",
            "runType": row["run_type"],
            "solverName": row["solver_name"],
            "routeLabel": row["route_label"],
            "status": row["status"],
            "running": row["status"] == "running",
            "baseDate": row["base_date"] or "",
            "searchStartDate": row["search_start_date"] or "",
            "searchEndDate": row["search_end_date"] or "",
            "broadStepDays": row["broad_step_days"] or 0,
            "graphNodes": row["geometric_node_count"],
            "graphEdges": row["graph_edge_count"],
            "geometricShortlist": row["shortlist_count"],
            "preflightBudget": row["preflight_budget"],
            "fullValidationBudget": row["full_validation_budget"],
            "resultCount": row["result_count"],
            "flightReadyCount": row["flight_ready_count"],
            "geometryPoints": json.loads(row["geometry_snapshot_json"] or "[]"),
            "bestVariantId": row["best_variant_id"] or "",
            "error": row["error_message"],
            "startedAtUtc": row["started_at_utc"],
            "completedAtUtc": row["completed_at_utc"],
        }

    @staticmethod
    def _variant_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "calculationRunId": row["calculation_run_id"],
            "iteration": row["iteration_number"],
            "date": row["start_date"],
            "stage": row["search_stage"],
            "status": row["status"],
            "fullCorridorCheck": bool(row["full_corridor_check"]),
            "geometricScore": row["geometric_score"],
            "quality": row["quality_score"],
            "rank": row["result_rank"],
            "selected": bool(row["is_selected"]),
            "geometryValid": (
                None if row["geometry_valid"] is None else bool(row["geometry_valid"])
            ),
            "sectionOrderValid": (
                None
                if row["section_order_valid"] is None
                else bool(row["section_order_valid"])
            ),
            "stateContinuous": (
                None
                if row["state_continuous"] is None
                else bool(row["state_continuous"])
            ),
            "endpointsReached": (
                None
                if row["endpoints_reached"] is None
                else bool(row["endpoints_reached"])
            ),
            "maximumEndpointResidualKm": row["maximum_endpoint_residual_km"],
            "performanceEvaluated": bool(row["performance_evaluated"]),
            "hypotheticalInterstellarAsymptote": bool(
                row["is_hypothetical_interstellar"]
            ),
            "feasible": None if row["is_feasible"] is None else bool(row["is_feasible"]),
            "corridorSatisfied": (
                None
                if row["corridor_satisfied"] is None
                else bool(row["corridor_satisfied"])
            ),
            "collisionFree": (
                None if row["collision_free"] is None else bool(row["collision_free"])
            ),
            "totalFlightDays": row["total_flight_days"],
            "requiredInjectionDeltaVKmS": row["required_delta_v_km_s"],
            "availableInjectionDeltaVKmS": row["available_delta_v_km_s"],
            "targetCorrectionDeltaVKmS": row[
                "target_correction_delta_v_km_s"
            ],
            "corridorInsertionDeficitKmS": row[
                "corridor_insertion_deficit_km_s"
            ],
            "deltaVDeficitKmS": row["delta_v_deficit_km_s"],
            "targetAlignmentDeg": row["target_alignment_deg"],
            "message": row["error_message"],
            "createdAtUtc": row["created_at_utc"],
            "updatedAtUtc": row["updated_at_utc"],
        }

    def list_runs(
        self, *, project_id: str = "", limit: int = 25
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        parameters: list[object] = []
        where_clause = ""
        if project_id:
            project_id = _uuid(project_id, "Projekt-ID")
            where_clause = "WHERE project_id = ?"
            parameters.append(project_id)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM calculation_runs
                {where_clause}
                ORDER BY started_at_utc DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._run_summary(row) for row in rows]

    def get_run(
        self, run_id: str, *, include_trajectories: bool = True
    ) -> dict[str, Any]:
        run_id = _uuid(run_id, "Berechnungslauf-ID")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM calculation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Berechnungslauf nicht gefunden.")
            variant_rows = connection.execute(
                """
                SELECT * FROM calculation_variants
                WHERE calculation_run_id = ?
                ORDER BY iteration_number
                """,
                (run_id,),
            ).fetchall()
            variants = [self._variant_summary(item) for item in variant_rows]
            if include_trajectories:
                for variant in variants:
                    variant["routePoints"] = self._route_points(
                        connection, variant["id"], limit=320
                    )
        return {**self._run_summary(row), "candidates": variants}

    def get_variant(
        self, variant_id: str, *, include_trajectory: bool = True
    ) -> dict[str, Any]:
        variant_id = _uuid(variant_id, "Varianten-ID")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM calculation_variants WHERE id = ?", (variant_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Berechnungsvariante nicht gefunden.")
            result = self._variant_summary(row)
            metadata=json.loads(row['result_metadata_json'] or '{}')
            result['resultMetadata']=metadata
            result['calculationVersion']=metadata.get('schemaVersion')
            from services.calculation_version import CALCULATION_BUILD
            result['needsRecalculation']=metadata.get('schemaVersion')!='2.0' or metadata.get('calculationBuild')!=CALCULATION_BUILD
            result['currentValidated']=not result['needsRecalculation'] and bool((metadata.get('summary') or {}).get('feasible')) and all((metadata.get('validation') or {}).get(k) is True for k in ('collisionFree','stateContinuous','ephemeridesValidated','targetReached'))
            result["sections"] = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT * FROM calculation_route_sections
                    WHERE calculation_variant_id = ?
                    ORDER BY section_index
                    """,
                    (variant_id,),
                ).fetchall()
            ]
            result["deltaV"] = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT * FROM calculation_delta_v
                    WHERE calculation_variant_id = ?
                    ORDER BY delta_v_type, id
                    """,
                    (variant_id,),
                ).fetchall()
            ]
            result["velocities"] = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT * FROM calculation_velocities
                    WHERE calculation_variant_id = ?
                    ORDER BY velocity_event, id
                    """,
                    (variant_id,),
                ).fetchall()
            ]
            result["warnings"] = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT * FROM calculation_warnings
                    WHERE calculation_variant_id = ?
                    ORDER BY warning_index
                    """,
                    (variant_id,),
                ).fetchall()
            ]
            if include_trajectory:
                result["routePoints"] = self._route_points(
                    connection, variant_id, limit=None
                )
                rows=connection.execute("SELECT * FROM calculation_trajectory_points WHERE calculation_variant_id=? AND trajectory_kind='nominal' ORDER BY point_index",(variant_id,)).fetchall()
                result['trajectory']=[{**json.loads(p['state_json'] or '{}'),'elapsedDays':p['elapsed_days'],
                    'positionKm':[p['position_x_km'],p['position_y_km'],p['position_z_km']],
                    'velocityKmS':[p['velocity_x_km_s'],p['velocity_y_km_s'],p['velocity_z_km_s']],'phase':p['phase_name']} for p in rows]
        return result

    @staticmethod
    def _route_points(
        connection: sqlite3.Connection, variant_id: str, limit: int | None
    ) -> list[list[float]]:
        rows = connection.execute(
            """
            SELECT point_index, position_x_km, position_y_km, position_z_km
            FROM calculation_trajectory_points
            WHERE calculation_variant_id = ? AND trajectory_kind = 'nominal'
            ORDER BY point_index
            """,
            (variant_id,),
        ).fetchall()
        if limit and len(rows) > limit:
            stride = math.ceil(len(rows) / limit)
            sampled = list(rows[::stride])
            if sampled[-1]["point_index"] != rows[-1]["point_index"]:
                sampled.append(rows[-1])
            rows = sampled
        return [
            [
                row["position_x_km"],
                row["position_y_km"],
                row["position_z_km"],
            ]
            for row in rows
        ]

    def delete_run(self, run_id: str) -> None:
        run_id = _uuid(run_id, "Berechnungslauf-ID")
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM calculation_runs WHERE id = ?", (run_id,)
            )
        if cursor.rowcount == 0:
            raise LookupError("Berechnungslauf nicht gefunden.")
