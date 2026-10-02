"""Append-only audit preparation for future, role-separated AI calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


from services.runtime_paths import LOG_DIRECTORY, STORAGE_ROOT as PROJECT_ROOT

AI_AUDIT_LOGS = {
    "interaction": LOG_DIRECTORY / "ai_interaction.jsonl",
    "calculation": LOG_DIRECTORY / "ai_calculation.jsonl",
    "plausibility": LOG_DIRECTORY / "ai_plausibility.jsonl",
}
_WRITE_LOCK = Lock()
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def write_ai_audit(
    *,
    role: str,
    model_name: str,
    input_payload: dict,
    output_payload: dict | None,
    status: str,
    prompt_version: str,
    solver_run_ids: list[str] | None = None,
    error: str | None = None,
) -> dict:
    """Record a future AI call without granting it solver authority."""
    if role not in AI_AUDIT_LOGS:
        raise ValueError(f"Unknown AI role: {role}")
    if not model_name.strip():
        raise ValueError("AI audit records require a model name.")
    if status not in {"success", "error", "rejected"}:
        raise ValueError(f"Unknown AI audit status: {status}")
    run_id = (
        f"ai-{role}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}-"
        f"{uuid4().hex[:8]}"
    )
    record = {
        "schemaVersion": "1.0",
        "runId": run_id,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "modelName": model_name,
        "promptVersion": prompt_version,
        "status": status,
        "solverAuthority": False,
        "solverRunIds": solver_run_ids or [],
        "input": _redact(input_payload),
        "output": _redact(output_payload or {}),
        "error": error,
    }
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    path = AI_AUDIT_LOGS[role]
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
    return {
        "runId": run_id,
        "createdAtUtc": record["createdAtUtc"],
        "role": role,
        "logFile": str(path.relative_to(PROJECT_ROOT)),
    }


def read_latest_ai_audit(role: str) -> dict | None:
    if role not in AI_AUDIT_LOGS:
        raise ValueError(f"Unknown AI role: {role}")
    path = AI_AUDIT_LOGS[role]
    if not path.exists():
        return None
    with _WRITE_LOCK:
        lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1]) if lines else None
