"""Train the route ranker on planets, principal moons, round trips and a Voyager grand tour."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ai.evaluation import train_and_evaluate
from scripts.train_saved_project import (
    BATCH_DIRECTORY,
    _calculate,
    _parse_date,
    _sample_dates,
    _validate_search_window,
)
from services.activity_log import write_activity
from solver.trajectory import get_default_mission_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLANET_TARGETS = (
    ("mercury", "Merkur"),
    ("venus", "Venus"),
    ("mars", "Mars"),
    ("jupiter", "Jupiter"),
    ("saturn", "Saturn"),
    ("uranus", "Uranus"),
    ("neptune", "Neptun"),
)
MOON_TARGETS = (
    ("earth-moon", "Mond"),
    ("mars-phobos", "Phobos"),
    ("mars-deimos", "Deimos"),
    ("jupiter-io", "Io"),
    ("jupiter-europa", "Europa"),
    ("jupiter-ganymede", "Ganymed"),
    ("jupiter-callisto", "Kallisto"),
    ("saturn-titan", "Titan"),
    ("saturn-enceladus", "Enceladus"),
    ("uranus-titania", "Titania"),
    ("uranus-oberon", "Oberon"),
    ("neptune-triton", "Triton"),
)

ROUNDTRIP_TARGETS = (
    ("earth-moon-earth", "Erde → Mond → Erde", [("earth", "earth-moon"), ("earth-moon", "earth")]),
    ("earth-mars-earth", "Erde → Mars → Erde", [("earth", "mars"), ("mars", "earth")]),
    ("earth-venus-earth", "Erde → Venus → Erde", [("earth", "venus"), ("venus", "earth")]),
)


def _section(origin_id: str, target_id: str, index: int, *, passage: dict | None = None) -> dict:
    return {
        "id": f"training-section-{index}",
        "originId": origin_id,
        "targetId": target_id,
        "corridor": {
            "enabled": False,
            "centerDirection": [1.0, 0.0, 0.0],
            "horizontalHalfAngleDeg": 8.0,
            "verticalHalfAngleDeg": 5.0,
            "rotationDeg": 0.0,
        },
        "deltaVMinusKmS": 0.5,
        "deltaVPlusKmS": 0.5,
        "passage": passage or {
            "mode": "direct",
            "orbitAngleDeg": 0.0,
            "orbitDirection": "prograde",
            "entryBehavior": "ballistic",
            "exitBehavior": "ballistic",
        },
    }


def _voyager_mission() -> dict:
    mission = get_default_mission_config()
    for module in mission.get("propulsionModules") or []:
        module["enabled"] = module.get("type") in {"chemical", "solid_kick_stage"}
    mission["electricSailEnabled"] = False
    mission["theoreticalPropulsionMode"] = False
    mission["missionYears"] = 25
    return mission


def _voyager_route() -> list[dict]:
    assist = {
        "mode": "partial-orbit",
        "orbitAngleDeg": 180.0,
        "orbitDirection": "prograde",
        "entryBehavior": "ballistic",
        "exitBehavior": "tangential-accelerate",
    }
    targets = ("jupiter", "saturn", "uranus", "neptune")
    origins = ("earth", *targets[:-1])
    return [
        _section(origin, target, index, passage=assist if index < len(targets) else None)
        for index, (origin, target) in enumerate(zip(origins, targets), start=1)
    ]


def build_scenarios() -> list[dict]:
    default_mission = get_default_mission_config()
    scenarios = [
        {
            "group": "planet",
            "targetId": target_id,
            "name": name,
            "routeSections": [_section("earth", target_id, 1)],
            "mission": default_mission,
        }
        for target_id, name in PLANET_TARGETS
    ]
    scenarios.extend({
        "group": "moon",
        "targetId": target_id,
        "name": name,
        "routeSections": [_section("earth", target_id, 1)],
        "mission": default_mission,
    } for target_id, name in MOON_TARGETS)
    scenarios.extend({
        "group": "roundtrip",
        "targetId": route_id,
        "name": name,
        "routeSections": [
            _section(origin, target, index)
            for index, (origin, target) in enumerate(legs, start=1)
        ],
        "mission": default_mission,
    } for route_id, name, legs in ROUNDTRIP_TARGETS)
    scenarios.append({
        "group": "voyager",
        "targetId": "neptune",
        "name": "Voyager Grand Tour",
        "routeSections": _voyager_route(),
        "mission": _voyager_mission(),
    })
    return scenarios


def run_catalog_batch(
    *, runs_per_scenario: int, search_start: str, search_end: str,
    workers: int, allow_historical: bool = False,
) -> dict:
    _validate_search_window(
        search_start, search_end, allow_historical=allow_historical,
    )
    scenarios = build_scenarios()
    dates = _sample_dates(_parse_date(search_start), _parse_date(search_end), runs_per_scenario)
    batch_id = f"ml-catalog-batch-{uuid4().hex}"
    jobs = [
        (scenario, start_date)
        for scenario in scenarios
        for start_date in dates
    ]

    def calculate(job: tuple[dict, str]) -> dict:
        scenario, start_date = job
        calculated = _calculate((start_date, scenario["routeSections"], scenario["mission"]))
        return {"scenario": scenario, "calculated": calculated}

    results: list[dict] = []
    counters: Counter[tuple[str, str, str]] = Counter()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        for index, item in enumerate(executor.map(calculate, jobs), start=1):
            scenario = item["scenario"]
            calculated = item["calculated"]
            route_label = " · ".join(
                f"{section['originId']} → {section['targetId']}"
                for section in scenario["routeSections"]
            )
            counters[(scenario["group"], scenario["targetId"], calculated["status"])] += 1
            compact = {
                "group": scenario["group"],
                "targetId": scenario["targetId"],
                "targetName": scenario["name"],
                **calculated,
            }
            results.append(compact)
            write_activity(
                source="ml-training",
                category="calculation",
                action="constellation-candidate",
                status=calculated["status"],
                message=calculated["message"],
                values=calculated["features"],
                details={
                    "searchRunId": batch_id,
                    "trainingBatchId": batch_id,
                    "scenarioGroup": scenario["group"],
                    "targetId": scenario["targetId"],
                    "routeLabel": route_label,
                },
            )
            if index % 25 == 0 or index == len(jobs):
                print(f"{index}/{len(jobs)} Berechnungen abgeschlossen", flush=True)

    report = train_and_evaluate(persist_model=True)
    scenario_summaries = [{
        "group": scenario["group"],
        "targetId": scenario["targetId"],
        "targetName": scenario["name"],
        "calculations": runs_per_scenario,
        "successfulCalculations": counters[(scenario["group"], scenario["targetId"], "success")],
        "rejectedCalculations": counters[(scenario["group"], scenario["targetId"], "rejected")],
    } for scenario in scenarios]
    summary = {
        "schemaVersion": "1.0",
        "batchId": batch_id,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "searchStartDate": search_start,
        "searchEndDate": search_end,
        "searchMode": "retrospective-what-if" if allow_historical else "future-planning",
        "runsPerScenario": runs_per_scenario,
        "calculations": len(results),
        "successfulCalculations": sum(item["status"] == "success" for item in results),
        "rejectedCalculations": sum(item["status"] != "success" for item in results),
        "scenarios": scenario_summaries,
        "results": results,
        "mlReport": report,
    }
    BATCH_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_path = BATCH_DIRECTORY / f"{batch_id}.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    summary["batchPath"] = str(output_path.relative_to(PROJECT_ROOT))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-per-scenario", type=int, default=50)
    parser.add_argument("--search-start", required=True)
    parser.add_argument("--search-end", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--allow-historical", action="store_true")
    args = parser.parse_args()
    summary = run_catalog_batch(
        runs_per_scenario=max(1, min(args.runs_per_scenario, 1_000)),
        search_start=args.search_start,
        search_end=args.search_end,
        workers=args.workers,
        allow_historical=args.allow_historical,
    )
    print(json.dumps({
        key: summary[key]
        for key in ("batchId", "batchPath", "calculations", "successfulCalculations", "rejectedCalculations")
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
