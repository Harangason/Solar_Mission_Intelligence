import unittest
from unittest.mock import patch

from main import app
from planner.trajectory_planner import (
    calculate_candidate_score,
    calculate_trajectory_plan,
)


BASE_SIMULATION = {
    "sampleTrajectoryPoints": 40,
    "includeUncertainty": False,
    "includeAudit": False,
    "propagationYears": 5,
}


class GenericTrajectoryPlannerTests(unittest.TestCase):
    def body_request(self, target_id: str) -> dict:
        return {
            "start": {
                "type": "body", "bodyId": "earth",
                "orbitAltitudeKm": 400, "startDate": "2031-01-01",
            },
            "target": {
                "type": "body", "bodyId": target_id,
                "targetDate": "2032-01-01", "arrivalMode": "flyby",
            },
            "waypoints": [],
            "searchWindow": {
                "departureStartDate": "2031-01-01",
                "departureEndDate": "2031-01-01",
                "departureStepDays": 10,
            },
            "constraints": {"minFlightDays": 20, "maxFlightDays": 2_000},
            "optimizationMode": "balanced",
            "simulation": BASE_SIMULATION,
        }

    def test_body_targets_use_same_generic_planner(self):
        for target_id in ("mars", "jupiter", "saturn"):
            with self.subTest(target_id=target_id):
                result = calculate_trajectory_plan(self.body_request(target_id))
                self.assertEqual(result["mode"], "body-to-body")
                self.assertEqual(result["target"]["bodyId"], target_id)
                self.assertGreater(len(result["trajectory"]), 10)
                self.assertEqual(result["guide"]["legs"][0]["physicalSegments"], ["lambert-transfer"])
                self.assertIn("targetReached", result["summary"])

    def test_launch_window_grid_pairs_each_departure_with_flight_times(self):
        request = self.body_request("mars")
        request["target"].pop("targetDate")
        request["searchWindow"].update({
            "departureEndDate": "2031-03-02",
            "departureStepDays": 30,
            "arrivalStepDays": 40,
        })
        request["constraints"].update({"minFlightDays": 180, "maxFlightDays": 300})
        result = calculate_trajectory_plan(request)
        departures = {candidate["departureDate"] for candidate in result["candidates"]}
        self.assertGreaterEqual(len(departures), 3)
        self.assertTrue(all(candidate["arrivalDate"] > candidate["departureDate"] for candidate in result["candidates"]))

    def test_body_transfer_targets_entry_corridor_surface_not_planet_center(self):
        request = self.body_request("mars")
        request["target"].pop("targetDate")
        request["target"]["entryCorridor"] = {
            "enabled": True,
            "centerDirection": [0, 1, 0],
            "horizontalHalfAngleDeg": 6,
            "verticalHalfAngleDeg": 4,
            "rotationDeg": 0,
        }
        request["searchWindow"].update({"arrivalStepDays": 30})
        request["constraints"].update({"minFlightDays": 180, "maxFlightDays": 360})
        result = calculate_trajectory_plan(request)
        target_center = result["target"]["positionKm"]
        entry_position = result["target"]["entryPositionKm"]
        offset_km = sum(
            (entry_position[index] - target_center[index]) ** 2
            for index in range(3)
        ) ** 0.5
        self.assertGreater(offset_km, 100_000)
        self.assertTrue(result["summary"]["entryCorridorTargeted"])
        self.assertTrue(result["entryCorridor"]["entryInsideCorridor"])

    def test_zone_and_boundary_emit_crossing_contract(self):
        zone = self.body_request("mars")
        zone["target"] = {"type": "zone", "zoneId": "asteroid_belt", "arrivalMode": "crossing"}
        zone["constraints"]["desiredSolarExitSpeedKmS"] = 25
        zone["simulation"]["propagationYears"] = 5
        zone_result = calculate_trajectory_plan(zone)
        self.assertEqual(zone_result["target"]["zoneId"], "asteroid_belt")
        self.assertTrue(any(event["type"] == "ZONE_ENTRY" for event in zone_result["events"]))

        boundary = self.body_request("mars")
        boundary["target"] = {"type": "boundary", "boundaryId": "custom", "distanceAU": 2}
        boundary["constraints"]["desiredSolarExitSpeedKmS"] = 25
        boundary_result = calculate_trajectory_plan(boundary)
        self.assertTrue(any(event["type"] == "BOUNDARY_REACHED" for event in boundary_result["events"]))

    def test_direction_accepts_free_three_dimensional_vector(self):
        request = self.body_request("mars")
        request["target"] = {"type": "direction", "positionKm": [1, 2, 3], "distanceAU": 2}
        request["constraints"]["targetToleranceDeg"] = 5
        result = calculate_trajectory_plan(request)
        self.assertEqual(result["target"]["type"], "direction")
        self.assertAlmostEqual(sum(value * value for value in result["target"]["direction"]), 1.0, places=8)
        self.assertIn("targetAlignmentDeg", result["summary"])

    def test_solar_oberth_planet_waypoint_continues_to_direction(self):
        request = self.body_request("mars")
        request["target"] = {
            "type": "direction", "rightAscensionDeg": 217.43,
            "declinationDeg": -62.68, "distanceAU": 50,
        }
        request["waypoints"] = [
            {"id": "oberth", "type": "solar_oberth", "burnDeltaVKmS": 8},
            {
                "id": "jupiter-flyby", "type": "body_flyby", "bodyId": "jupiter",
                "encounterDay": 730, "flybyAltitudeKm": 100_000,
                "flybyMode": "acceleration",
            },
        ]
        fake_legacy = {
            "trajectory": [
                {"elapsedDays": 0.0, "positionKm": [149_597_870.7, 0, 0], "velocityKmS": [0, 29.78, 0]},
                {"elapsedDays": 730.0, "positionKm": [778_000_000, 0, 0], "velocityKmS": [0, 20, 0]},
            ],
            "segments": [{"id": "flyby", "label": "Jupiter-Flyby", "startIndex": 0, "endIndex": 1}],
            "outgoingDirection": [0, 1, 0],
            "summary": {
                "requiredInjectionDeltaVKmS": 8, "heliocentricSpeedAfterKmS": 20,
                "feasibleWithConfiguredBurn": True, "model": "existing flyby solver",
            },
        }
        with patch("planner.trajectory_planner.simulate_waypoint_route", return_value=fake_legacy):
            result = calculate_trajectory_plan(request)
        self.assertEqual(result["mode"], "multi-leg-direction")
        self.assertEqual(result["segments"][-1]["id"], "post-flyby-direction")
        self.assertEqual(result["target"]["type"], "direction")
        self.assertEqual(result["guide"]["nodes"][-1]["kind"], "direction")
        self.assertEqual(result["guide"]["legs"][-1]["physicalSegments"], ["post-flyby-direction"])

    def test_audit_contract_contains_required_sections(self):
        request = self.body_request("mars")
        request["simulation"] = {**BASE_SIMULATION, "includeAudit": True}
        with patch("planner.trajectory_planner.write_route_audit") as write_audit:
            write_audit.return_value = {"runId": "audit-1"}
            result = calculate_trajectory_plan(request)
        audit_input = write_audit.call_args.args[0]
        self.assertEqual(result["audit"]["runId"], "audit-1")
        for key in ("inputs", "constants", "coordinateTransform", "segments", "continuity", "validation", "summary"):
            self.assertIn(key, audit_input)

    def test_maximum_exit_speed_prefers_higher_speed(self):
        slow = calculate_candidate_score({"finalHeliocentricSpeedKmS": 10}, "maximum_exit_speed")
        fast = calculate_candidate_score({"finalHeliocentricSpeedKmS": 30}, "maximum_exit_speed")
        self.assertLess(fast, slow)

    def test_http_api_uses_generic_entry_point(self):
        request = self.body_request("mars")
        with app.test_client() as client:
            response = client.post("/api/trajectory/plan", json=request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["target"]["bodyId"], "mars")

    def test_route_api_can_return_direct_lambert_body_route(self):
        with app.test_client() as client:
            response = client.post("/api/route/simulate", json={
                "preferLambertBodyToBody": True,
                "mission": {"startDate": "2031-01-01", "parkingOrbitAltitudeKm": 400, "oberthDeltaVKmS": 20},
                "lambertConstraints": {"minFlightDays": 180, "maxFlightDays": 360, "arrivalStepDays": 30},
                "routeSections": [{
                    "id": "route-section-1",
                    "originId": "earth",
                    "targetId": "mars",
                    "deltaVMinusKmS": 0,
                    "deltaVPlusKmS": 0,
                    "corridor": {
                        "enabled": True,
                        "centerDirection": [0, 1, 0],
                        "horizontalHalfAngleDeg": 6,
                        "verticalHalfAngleDeg": 4,
                        "rotationDeg": 0,
                    },
                    "passage": {"mode": "direct", "orbitAngleDeg": 0, "orbitDirection": "prograde"},
                }],
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["routeSections"][0]["sectionType"], "lambert-body-to-body")
        self.assertLessEqual(payload["routeSections"][0]["lambertEndpointResidualKm"], 100)
        self.assertTrue(payload["routeSections"][0]["corridor"]["entryInsideCorridor"])

    def test_route_api_can_return_multi_leg_direct_lambert_route(self):
        base_section = {
            "deltaVMinusKmS": 0,
            "deltaVPlusKmS": 0,
            "corridor": {
                "enabled": True,
                "centerDirection": [0, 1, 0],
                "horizontalHalfAngleDeg": 6,
                "verticalHalfAngleDeg": 4,
                "rotationDeg": 0,
            },
            "passage": {"mode": "direct", "orbitAngleDeg": 0, "orbitDirection": "prograde"},
        }
        with app.test_client() as client:
            response = client.post("/api/route/simulate", json={
                "preferLambertBodyToBody": True,
                "mission": {"startDate": "2031-01-01", "parkingOrbitAltitudeKm": 400, "oberthDeltaVKmS": 20},
                "lambertConstraints": {"minFlightDays": 180, "maxFlightDays": 360, "arrivalStepDays": 30},
                "routeSections": [
                    {"id": "route-section-1", "originId": "earth", "targetId": "mars", **base_section},
                    {"id": "route-section-2", "originId": "mars", "targetId": "earth", **base_section},
                ],
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["routeSections"]), 2)
        self.assertTrue(all(section["sectionType"] == "lambert-body-to-body" for section in payload["routeSections"]))
        self.assertTrue(payload["summary"]["feasibleWithConfiguredBurn"])
        self.assertGreater(payload["totalFlightDays"], 360)


if __name__ == "__main__":
    unittest.main()
