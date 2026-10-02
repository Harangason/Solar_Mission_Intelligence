"""Persistent, machine-readable audit trail for trajectory calculations."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from solver.ephemeris import get_ephemeris_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]
from services.runtime_paths import LOG_DIRECTORY

AUDIT_DIRECTORY = LOG_DIRECTORY
ROUTE_AUDIT_LOG = AUDIT_DIRECTORY / "route_calculations.jsonl"
OPTIMIZER_AUDIT_LOG = AUDIT_DIRECTORY / "mission_optimizer.jsonl"
PLAYBACK_AUDIT_LOG = AUDIT_DIRECTORY / "mission_playback.jsonl"
METHOD_DOCUMENTATION = PROJECT_ROOT / "docs" / "CALCULATION_METHODS.md"
_WRITE_LOCK = Lock()
_PLAYBACK_ID_PATTERN = re.compile(r"playback-[0-9A-Za-z.-]+")
_PLAYBACK_EVENT_TYPES = {
    "checkpoint",
    "paused",
    "resumed",
    "seek",
    "section-entered",
    "target-reached",
    "reset",
    "aborted",
}


def _append_jsonl(path: Path, record: dict) -> None:
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")


def start_playback_audit(values: dict | None) -> dict:
    """Create a persistent event stream when mission playback starts."""
    values = values or {}
    playback_id = (
        f"playback-{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}-"
        f"{uuid4().hex[:8]}"
    )
    created_at = datetime.now(timezone.utc).isoformat()
    record = {
        "schemaVersion": "1.0",
        "playbackId": playback_id,
        "sequence": 0,
        "recordedAtUtc": created_at,
        "eventType": "playback-start",
        "calculationType": "mission-playback",
        "routeAuditRunId": values.get("routeAuditRunId"),
        "startDate": values.get("startDate"),
        "playbackEndDay": values.get("playbackEndDay"),
        "originId": values.get("originId"),
        "targetId": values.get("targetId"),
        "routeSectionIds": values.get("routeSectionIds") or [],
        "missionConfig": values.get("missionConfig") or {},
        "routeSections": values.get("routeSections") or [],
        "state": values.get("state") or {},
    }
    _append_jsonl(PLAYBACK_AUDIT_LOG, record)
    return {
        "playbackId": playback_id,
        "createdAtUtc": created_at,
        "logFile": str(Path("logs") / PLAYBACK_AUDIT_LOG.name),
    }


def write_playback_event(values: dict | None) -> dict:
    """Append one validated state or control event to a playback stream."""
    values = values or {}
    playback_id = str(values.get("playbackId") or "")
    event_type = str(values.get("eventType") or "")
    sequence = int(values.get("sequence", -1))
    mission_day = float(values.get("missionDay", -1))
    if not _PLAYBACK_ID_PATTERN.fullmatch(playback_id):
        raise ValueError("Ungültige Kennung des Missionslauf-Logs.")
    if event_type not in _PLAYBACK_EVENT_TYPES:
        raise ValueError("Unbekannter Ereignistyp des Missionslauf-Logs.")
    if sequence < 1:
        raise ValueError("Die Ereignisnummer muss positiv sein.")
    if not math.isfinite(mission_day) or mission_day < 0:
        raise ValueError("Der Missionstag des Log-Eintrags ist ungültig.")
    record = {
        "schemaVersion": "1.0",
        "playbackId": playback_id,
        "sequence": sequence,
        "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
        "eventType": event_type,
        "missionDay": mission_day,
        "simulatedDateTimeUtc": values.get("simulatedDateTimeUtc"),
        "sectionId": values.get("sectionId"),
        "sectionLabel": values.get("sectionLabel"),
        "state": values.get("state") or {},
        "details": values.get("details") or {},
    }
    _append_jsonl(PLAYBACK_AUDIT_LOG, record)
    return {
        "playbackId": playback_id,
        "sequence": sequence,
        "recordedAtUtc": record["recordedAtUtc"],
    }


def read_latest_playback_audit() -> dict | None:
    """Reconstruct the latest playback stream from its append-only events."""
    if not PLAYBACK_AUDIT_LOG.exists():
        return None
    with _WRITE_LOCK:
        lines = PLAYBACK_AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    start = next(
        (record for record in reversed(records) if record.get("eventType") == "playback-start"),
        None,
    )
    if start is None:
        return None
    playback_id = start["playbackId"]
    events = [
        record for record in records
        if record.get("playbackId") == playback_id and record is not start
    ]
    events.sort(key=lambda record: int(record.get("sequence", 0)))
    final_type = events[-1]["eventType"] if events else "playback-start"
    return {
        "playbackId": playback_id,
        "status": (
            "target-reached" if final_type == "target-reached"
            else "aborted" if final_type in {"reset", "aborted"}
            else "in-progress"
        ),
        "start": start,
        "events": events,
        "eventCount": len(events),
    }


def write_route_audit(calculation: dict) -> dict:
    """Append one complete calculation record and return its public metadata."""
    run_id = f"route-{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}-{uuid4().hex[:8]}"
    record = {
        "schemaVersion": "1.0",
        "runId": run_id,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "calculationType": "segmented-waypoint-route",
        "ephemeris": get_ephemeris_status(),
        **calculation,
    }
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with _WRITE_LOCK:
        AUDIT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        with ROUTE_AUDIT_LOG.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
    return {
        "runId": run_id,
        "createdAtUtc": record["createdAtUtc"],
        "logFile": str(Path("logs") / ROUTE_AUDIT_LOG.name),
        "documentation": str(METHOD_DOCUMENTATION.relative_to(PROJECT_ROOT)),
    }


def write_optimizer_audit(calculation: dict) -> dict:
    """Append one navigator search including candidates and rejection reasons."""
    run_id = f"optimizer-{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}-{uuid4().hex[:8]}"
    record = {
        "schemaVersion": "1.0",
        "runId": run_id,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "calculationType": "iterative-mission-navigation",
        "ephemeris": get_ephemeris_status(),
        **calculation,
    }
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with _WRITE_LOCK:
        AUDIT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        with OPTIMIZER_AUDIT_LOG.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
    return {
        "runId": run_id,
        "createdAtUtc": record["createdAtUtc"],
        "logFile": str(Path("logs") / OPTIMIZER_AUDIT_LOG.name),
        "documentation": str(METHOD_DOCUMENTATION.relative_to(PROJECT_ROOT)),
    }


def read_latest_route_audit() -> dict | None:
    if not ROUTE_AUDIT_LOG.exists():
        return None
    with _WRITE_LOCK:
        lines = ROUTE_AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1]) if lines else None


def read_latest_optimizer_audit() -> dict | None:
    if not OPTIMIZER_AUDIT_LOG.exists():
        return None
    with _WRITE_LOCK:
        lines = OPTIMIZER_AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1]) if lines else None
