import unittest
from datetime import datetime, timedelta

from scripts.train_catalog_routes import MOON_TARGETS, PLANET_TARGETS, build_scenarios
from scripts.train_saved_project import _validate_search_window


class CatalogTrainingRouteTests(unittest.TestCase):
    def test_builds_seven_planets_twelve_moons_three_roundtrips_and_one_voyager_scenario(self):
        scenarios = build_scenarios()
        self.assertEqual(len(PLANET_TARGETS), 7)
        self.assertEqual(len(MOON_TARGETS), 12)
        self.assertEqual(len(scenarios), 23)
        self.assertEqual([item["group"] for item in scenarios].count("voyager"), 1)
        self.assertEqual([item["group"] for item in scenarios].count("roundtrip"), 3)

    def test_roundtrip_scenarios_include_earth_moon_mars_and_venus(self):
        scenarios = build_scenarios()
        route_ids = {item["targetId"] for item in scenarios if item["group"] == "roundtrip"}
        self.assertEqual(route_ids, {"earth-moon-earth", "earth-mars-earth", "earth-venus-earth"})

    def test_voyager_scenario_is_chemical_four_leg_grand_tour(self):
        voyager = next(item for item in build_scenarios() if item["group"] == "voyager")
        self.assertEqual(
            [(item["originId"], item["targetId"]) for item in voyager["routeSections"]],
            [("earth", "jupiter"), ("jupiter", "saturn"), ("saturn", "uranus"), ("uranus", "neptune")],
        )
        enabled = {
            item["type"] for item in voyager["mission"]["propulsionModules"]
            if item["enabled"]
        }
        self.assertEqual(enabled, {"chemical", "solid_kick_stage"})

    def test_training_window_rejects_dates_before_today(self):
        today = datetime.now().astimezone().date()
        with self.assertRaisesRegex(ValueError, "liegt vor dem heutigen Datum"):
            _validate_search_window(
                (today - timedelta(days=1)).isoformat(),
                (today + timedelta(days=1)).isoformat(),
            )
        _validate_search_window(
            today.isoformat(), (today + timedelta(days=1)).isoformat(),
        )
        _validate_search_window(
            (today - timedelta(days=1)).isoformat(),
            today.isoformat(),
            allow_historical=True,
        )


if __name__ == "__main__":
    unittest.main()
