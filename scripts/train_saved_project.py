"""Calculate a saved route repeatedly and persist ML training evidence.

The batch spans the complete requested date window. It stores compact feature
rows in the calculation activity log, writes an auditable batch summary and
re-trains the candidate-prioritisation model. Physical validity always comes
from the deterministic route solver, never from the ranker.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ai.evaluation import train_and_evaluate
from planner.multi_route_planner import simulate_route_sections
from services.activity_log import write_activity
from services.project_store import ProjectStore
from solver.trajectory import get_default_mission_config


BATCH_DIRECTORY = PROJECT_ROOT / "data" / "ml_training_batches"


def _available_memory_gb() -> float | None:
    if os.name != "nt":
        return None

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.ullAvailPhys / (1024 ** 3)


def resolve_worker_count(
    requested_workers: int,
    job_count: int,
    *,
    memory_gb_per_worker: float = 0.75,
) -> int:
    cpu_workers = os.cpu_count() or 1
    requested = cpu_workers if requested_workers <= 0 else requested_workers
    memory_gb = _available_memory_gb()
    if memory_gb is None:
        memory_workers = requested
    else:
        memory_workers = max(1, int(memory_gb / max(0.1, memory_gb_per_worker)))
    return max(1, min(requested, cpu_workers, memory_workers, max(1, job_count)))


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _temporal_features(value: str) -> dict[str, float]:
    epoch_days = (_parse_date(value) - date(2000, 1, 1)).days
    annual_phase = 2.0 * math.pi * epoch_days / 365.2425
    jupiter_phase = 2.0 * math.pi * epoch_days / 4_332.59
    return {
        "annualPhaseSin": math.sin(annual_phase),
        "annualPhaseCos": math.cos(annual_phase),
        "jupiterPhaseSin": math.sin(jupiter_phase),
        "jupiterPhaseCos": math.cos(jupiter_phase),
    }


def _sample_dates(start: date, end: date, count: int) -> list[str]:
    if count <= 1:
        return [start.isoformat()]
    span_days = (end - start).days
    return [
        (start + timedelta(days=round(span_days * index / (count - 1)))).isoformat()
        for index in range(count)
    ]


def _validate_search_window(
    search_start: str,
    search_end: str,
    *,
    allow_historical: bool = False,
) -> None:
    start = _parse_date(search_start)
    end = _parse_date(search_end)
    today = datetime.now().astimezone().date()
    if start < today and not allow_historical:
        raise ValueError(
            f"Der Suchstart {search_start} liegt vor dem heutigen Datum {today.isoformat()}."
        )
    if end < start:
        raise ValueError("Das Suchende muss am oder nach dem Suchstart liegen.")


def _calculate(values: tuple[str, list[dict], dict]) -> dict:
    start_date, route_sections, mission = values
    try:
        result = simulate_route_sections({
            "mission": {**mission, "startDate": start_date},
            "waypointId": route_sections[0]["targetId"],
            "calculationStage": "performance",
            "routeSections": route_sections,
        })
    except (TypeError, ValueError) as error:
        return {
            "startDate": start_date,
            "status": "rejected",
            "message": str(error),
            "features": {
                "geometricScore": 0.0,
                "targetAlignmentDeg": 180.0,
                "deltaVDeficitKmS": 1_000.0,
                "requiredInjectionDeltaVKmS": 1_000.0,
                "availableInjectionDeltaVKmS": 0.0,
                "targetCorrectionDeltaVKmS": 1_000.0,
                "corridorInsertionDeficitKmS": 0.0,
                "corridorSatisfied": False,
                "collisionFree": False,
                "fullCorridorCheck": True,
                "quality": -50_000.0,
                "feasible": False,
                "startDate": start_date,
                **_temporal_features(start_date),
            },
        }

    summary = result["summary"]
    required = float(summary.get("requiredInjectionDeltaVKmS") or 0.0)
    available = float(summary.get("availableInjectionDeltaVKmS") or required)
    correction = float(summary.get("targetCorrectionDeltaVKmS") or 0.0)
    corridor_deficit = sum(
        max(0.0, float(section.get("corridorInsertionDeltaVKmS") or 0.0)
            - float(route_sections[index].get("deltaVPlusKmS") or 0.0))
        for index, section in enumerate(result.get("routeSections") or [])
    )
    deficit = max(0.0, required + correction - available) + corridor_deficit
    alignment = float(
        summary.get("actualTargetAlignmentDeg")
        or summary.get("targetAlignmentDeg")
        or 0.0
    )
    collision_free = result.get("validation", {}).get("collisionFree") is True
    corridor_required = any(
        (section.get("corridor") or {}).get("enabled") is True
        for section in route_sections
    )
    corridor_satisfied = (
        not corridor_required or summary.get("entryInsideCorridor") is True
    )
    feasible = (
        summary.get("feasibleWithConfiguredBurn") is True
        and collision_free
        and corridor_satisfied
    )
    quality = (
        (1_000.0 if feasible else 0.0)
        + (250.0 if corridor_satisfied else -500.0)
        + (200.0 if collision_free else -1_000.0)
        - deficit * 50.0
        - alignment * 8.0
    )
    return {
        "startDate": start_date,
        "status": "success" if feasible else "rejected",
        "message": "",
        "features": {
            "geometricScore": 0.0,
            "targetAlignmentDeg": alignment,
            "deltaVDeficitKmS": deficit,
            "requiredInjectionDeltaVKmS": required,
            "availableInjectionDeltaVKmS": available,
            "targetCorrectionDeltaVKmS": correction,
            "corridorInsertionDeficitKmS": corridor_deficit,
            "corridorSatisfied": corridor_satisfied,
            "collisionFree": collision_free,
            "fullCorridorCheck": True,
            "quality": quality,
            "feasible": feasible,
            "startDate": start_date,
            **_temporal_features(start_date),
        },
    }


def run_training_batch(
    *,
    project_id: str,
    runs: int,
    search_start: str,
    search_end: str,
    workers: int,
    allow_historical: bool = False,
) -> dict:
    _validate_search_window(
        search_start, search_end, allow_historical=allow_historical,
    )
    project = ProjectStore().get_project(project_id)
    state = project["state"]
    route_sections = state.get("routeSections") or []
    if not route_sections:
        raise ValueError("Das gespeicherte Projekt besitzt keine Route.")
    mission = state.get("missionConfig") or get_default_mission_config()
    dates = _sample_dates(_parse_date(search_start), _parse_date(search_end), runs)
    batch_id = f"ml-batch-{uuid4().hex}"
    route_label = " · ".join(
        f"{section['originId']} → {section['targetId']}" for section in route_sections
    )
    arguments = [(item, route_sections, mission) for item in dates]
    results: list[dict] = []
    worker_count = resolve_worker_count(workers, len(arguments))
    print(f"Training nutzt {worker_count} Worker", flush=True)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for index, calculated in enumerate(executor.map(_calculate, arguments), start=1):
            results.append(calculated)
            write_activity(
                source="ml-training",
                category="calculation",
                action="constellation-candidate",
                status=calculated["status"],
                project_id=project_id,
                message=calculated["message"],
                values=calculated["features"],
                details={
                    "searchRunId": batch_id,
                    "trainingBatchId": batch_id,
                    "routeLabel": route_label,
                },
            )
            if index % 10 == 0 or index == runs:
                print(f"{index}/{runs} Berechnungen abgeschlossen", flush=True)

    report = train_and_evaluate(persist_model=True)
    summary = {
        "schemaVersion": "1.0",
        "batchId": batch_id,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "projectId": project_id,
        "projectName": project["name"],
        "routeLabel": route_label,
        "searchStartDate": search_start,
        "searchEndDate": search_end,
        "searchMode": "retrospective-what-if" if allow_historical else "future-planning",
        "calculations": len(results),
        "successfulCalculations": sum(item["status"] == "success" for item in results),
        "rejectedCalculations": sum(item["status"] != "success" for item in results),
        "results": results,
        "mlReport": report,
    }
    BATCH_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_path = BATCH_DIRECTORY / f"{batch_id}.json"
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary["batchPath"] = str(output_path.relative_to(PROJECT_ROOT))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--search-start", required=True)
    parser.add_argument("--search-end", required=True)
    parser.add_argument("--workers", type=int, default=0, help="0 = automatisch alle CPU-Kerne, RAM-schonend begrenzt")
    parser.add_argument("--allow-historical", action="store_true")
    args = parser.parse_args()
    summary = run_training_batch(
        project_id=args.project_id,
        runs=max(1, min(args.runs, 10_000)),
        search_start=args.search_start,
        search_end=args.search_end,
        workers=args.workers,
        allow_historical=args.allow_historical,
    )
    print(json.dumps({
        key: summary[key]
        for key in [
            "batchId", "batchPath", "calculations", "successfulCalculations",
            "rejectedCalculations", "searchStartDate", "searchEndDate",
        ]
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
