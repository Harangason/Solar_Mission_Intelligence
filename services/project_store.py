"""Versioned local project persistence for the mission planner."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


from services.runtime_paths import DATA_DIRECTORY

PROJECT_DATABASE = DATA_DIRECTORY / "solar_simulator.db"
PROJECT_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_project(values: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    name = str(values.get("name", "")).strip()
    description = str(values.get("description", "")).strip()
    state = values.get("state")
    if not name:
        raise ValueError("Bitte einen Projektnamen eingeben.")
    if len(name) > 120:
        raise ValueError("Der Projektname darf höchstens 120 Zeichen enthalten.")
    if len(description) > 2_000:
        raise ValueError("Die Beschreibung darf höchstens 2.000 Zeichen enthalten.")
    if not isinstance(state, dict):
        raise ValueError("Der Projektzustand fehlt oder ist ungültig.")
    if state.get("schemaVersion") != PROJECT_SCHEMA_VERSION:
        raise ValueError(f"Nicht unterstützte Projektschema-Version: {state.get('schemaVersion')!r}.")
    return name, description, state


class ProjectStore:
    def __init__(self, database_path: Path = PROJECT_DATABASE):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    schema_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS projects_updated_idx ON projects(updated_at_utc DESC)"
            )

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        state = json.loads(row["state_json"])
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "schemaVersion": row["schema_version"],
            "revision": row["revision"],
            "createdAtUtc": row["created_at_utc"],
            "updatedAtUtc": row["updated_at_utc"],
            "routeSectionCount": len(state.get("routeSections", [])),
            "hasCalculatedRoute": state.get("plannedRoute") is not None,
        }

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at_utc DESC, name COLLATE NOCASE"
            ).fetchall()
        return [self._summary(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise LookupError("Projekt nicht gefunden.")
        return {**self._summary(row), "state": json.loads(row["state_json"])}

    def create_project(self, values: dict[str, Any]) -> dict[str, Any]:
        name, description, state = _validate_project(values)
        project_id = str(uuid4())
        timestamp = _utc_now()
        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, description, schema_version, revision,
                    state_json, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    project_id,
                    name,
                    description,
                    PROJECT_SCHEMA_VERSION,
                    state_json,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_project(project_id)

    def update_project(self, project_id: str, values: dict[str, Any]) -> dict[str, Any]:
        name, description, state = _validate_project(values)
        timestamp = _utc_now()
        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, state_json = ?,
                    schema_version = ?, revision = revision + 1, updated_at_utc = ?
                WHERE id = ?
                """,
                (
                    name,
                    description,
                    state_json,
                    PROJECT_SCHEMA_VERSION,
                    timestamp,
                    project_id,
                ),
            )
        if cursor.rowcount == 0:
            raise LookupError("Projekt nicht gefunden.")
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        if cursor.rowcount == 0:
            raise LookupError("Projekt nicht gefunden.")
