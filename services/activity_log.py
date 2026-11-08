"""Persistent activity log with query and CSV export support."""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


from services.runtime_paths import LOG_DIRECTORY

ACTIVITY_LOG = LOG_DIRECTORY / "activities.jsonl"
_WRITE_LOCK = Lock()
_MAX_TEXT_LENGTH = 2_000


def _safe_text(value: object, max_length: int = _MAX_TEXT_LENGTH) -> str:
    return str(value or "")[:max_length]


def _safe_scalar(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return _safe_text(value)


def _safe_mapping(value: object, limit: int = 120) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        _safe_text(key, 120): _safe_scalar(item)
        for key, item in list(value.items())[:limit]
    }


def flatten_scalar_values(
    value: object,
    prefix: str = "",
    *,
    limit: int = 120,
) -> dict:
    """Flatten calculation output into bounded, CSV-friendly scalar values."""
    flattened: dict[str, str | int | float | bool | None] = {}

    def visit(current: object, path: str) -> None:
        if len(flattened) >= limit:
            return
        if current is None or isinstance(current, (str, bool, int, float)):
            if path:
                flattened[path] = _safe_scalar(current)
            return
        if isinstance(current, dict):
            for key, item in current.items():
                next_path = f"{path}.{key}" if path else str(key)
                visit(item, next_path)
                if len(flattened) >= limit:
                    break
            return
        if isinstance(current, (list, tuple)) and len(current) <= 12:
            for index, item in enumerate(current):
                visit(item, f"{path}.{index}" if path else str(index))
                if len(flattened) >= limit:
                    break

    visit(value, prefix)
    return flattened


def write_activity(
    *,
    source: str,
    category: str,
    action: str,
    status: str = "success",
    project_id: str | None = None,
    duration_ms: float | None = None,
    message: str = "",
    values: dict | None = None,
    details: dict | None = None,
) -> dict:
    record = {
        "id": f"activity-{uuid4().hex}",
        "timestampUtc": datetime.now(timezone.utc).isoformat(),
        "source": _safe_text(source, 80),
        "category": _safe_text(category, 80),
        "action": _safe_text(action, 160),
        "status": _safe_text(status, 40),
        "projectId": _safe_text(project_id, 160),
        "durationMs": round(float(duration_ms), 3) if duration_ms is not None else None,
        "message": _safe_text(message),
        "values": _safe_mapping(values),
        "details": _safe_mapping(details),
    }
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with _WRITE_LOCK:
        ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ACTIVITY_LOG.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
    return record


def read_activities(
    *,
    limit: int = 250,
    category: str = "",
    status: str = "",
    project_id: str = "",
) -> list[dict]:
    if not ACTIVITY_LOG.exists():
        return []
    bounded_limit = max(1, min(int(limit), 5_000))
    with _WRITE_LOCK:
        lines = ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    records = (json.loads(line) for line in reversed(lines) if line.strip())
    filtered = [
        record for record in records
        if (not category or record.get("category") == category)
        and (not status or record.get("status") == status)
        and (not project_id or record.get("projectId") == project_id)
    ]
    return filtered[:bounded_limit]


def _csv_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


def activities_csv(records: list[dict]) -> bytes:
    value_columns = sorted({
        key
        for record in records
        for key in (record.get("values") or {}).keys()
    })
    metadata_columns = [
        "id",
        "timestampUtc",
        "source",
        "category",
        "action",
        "status",
        "projectId",
        "durationMs",
        "message",
    ]
    fieldnames = metadata_columns + [f"value.{key}" for key in value_columns] + ["detailsJson"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for record in reversed(records):
        row = {key: _csv_safe(record.get(key)) for key in metadata_columns}
        row.update({
            f"value.{key}": _csv_safe((record.get("values") or {}).get(key))
            for key in value_columns
        })
        row["detailsJson"] = _csv_safe(json.dumps(
            record.get("details") or {},
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        writer.writerow(row)
    return ("\ufeff" + output.getvalue()).encode("utf-8")
