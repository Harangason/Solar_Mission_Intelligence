import unittest
import numpy as np
from planner.generic_route_planner import _candidate,parse_route_passage,SUN_RADIUS_KM
from planner.multi_route_planner import simulate_route_sections

def section(origin,target,ident='leg',**extra):
    return {'id':ident,'originId':origin,'targetId':target,'corridor':{'enabled':False},'deltaVPlusKmS':100.,'deltaVMinusKmS':100.,**extra}

class GenericRoutePlannerTests(unittest.TestCase):
    def calculate(self,sections,**extra):
        return simulate_route_sections({'mission':{'startDate':'2031-01-01'},'routeSections':sections,**extra})

    def test_full_orbit_always_uses_360_degrees(self):
        passage = parse_route_passage({
            "mode": "full-orbit",
            "orbitAngleDeg": 12,
        })

        self.assertEqual(passage["orbitAngleDeg"], 360.0)

    def test_partial_orbit_defaults_to_45_degrees(self):
        passage = parse_route_passage({
            "mode": "partial-orbit",
        })

        self.assertEqual(passage["orbitAngleDeg"], 45.0)

    def test_partial_orbit_accepts_one_and_a_half_orbits(self):
        passage = parse_route_passage({
            "mode": "partial-orbit",
            "orbitAngleDeg": 540,
        })

        self.assertEqual(passage["orbitAngleDeg"], 540.0)

    def test_partial_orbit_is_limited_to_three_orbits(self):
        passage = parse_route_passage({
            "mode": "partial-orbit",
            "orbitAngleDeg": 2000,
        })

        self.assertEqual(passage["orbitAngleDeg"], 1080.0)

    def test_acceleration_boundary_behavior_is_accepted(self):
        passage = parse_route_passage({
            "entryBehavior": "tangential-accelerate",
            "exitBehavior": "tangential-accelerate",
        })

        self.assertEqual(passage["entryBehavior"], "tangential-accelerate")
        self.assertEqual(passage["exitBehavior"], "tangential-accelerate")

    def test_unsafe_lambert_fallback_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Kein kollisionsfreier"):
            _candidate(
                (100.0, 0.0, 0.0),
                (0.0, 200.0, 0.0),
                1_000.0,
                (0.0, 0.0, 0.0),
                1_000.0,
                minimum_central_radius_km=1_000.0,
            )

    def test_selected_origin_is_preserved_and_start_is_outside_body(self):
        r=self.calculate([section('venus','sun')])
        self.assertEqual(r['routeSections'][0]['originId'],'venus')
        self.assertGreater(r['validation']['minimumSolarAltitudeKm'],0)
        self.assertTrue(r['continuity']['stateChain'])

    def test_planet_moon_transfer_has_full_velocity_states(self):
        r=self.calculate([section('earth','earth-moon')])
        self.assertEqual(r['routeSections'][0]['sectionType'],'Erde-zentrierter Transfer')
        self.assertLess(r['routeSections'][0]['lambertEndpointResidualKm'],.1)
        self.assertTrue(all(len(p['velocityKmS'])==3 and p['epochUtc'] for p in r['trajectory']))

    def test_two_planet_legs_use_actual_exit_as_next_start(self):
        r=self.calculate([section('earth','mars','a'),section('mars','earth','b')])
        self.assertEqual([(s['originId'],s['targetId']) for s in r['routeSections']],[('earth','mars'),('mars','earth')])
        for check in r['continuity']['checks']:
            self.assertLess(check['positionResidualKm'],.01)
            self.assertLess(check['velocityResidualKmS'],1e-7)
            self.assertEqual(check['epochResidualSeconds'],0)
        self.assertAlmostEqual(r['summary']['totalDeltaVKmS'],sum(m['deltaVKmS'] for m in r['maneuvers']),places=8)

    def test_real_partial_orbit_is_bound_and_measured(self):
        r=self.calculate([section('earth','earth-moon',passage={'mode':'partial-orbit','orbitAngleDeg':540,'orbitDirection':'prograde'})])
        s=r['routeSections'][0]
        self.assertTrue(s['orbitElements']['bound'])
        self.assertAlmostEqual(s['completedRevolutions'],1.5,places=5)
        self.assertLess(s['periapsisResidualKm'],1)
        self.assertGreater(s['requiredPassageDeltaVKmS'],.1)

    def test_zero_section_budget_never_claims_an_applied_mission(self):
        r=self.calculate([section('earth','mars',deltaVPlusKmS=0,deltaVMinusKmS=0)])
        self.assertFalse(r['summary']['feasibleWithConfiguredBurn'])
        self.assertFalse(r['summary']['targetInjectionApplied'])
        self.assertFalse(r['summary']['passiveTargeting'])

    def test_solar_corridor_uses_measured_entry_not_requested_vector(self):
        r=self.calculate([section('earth','sun',corridor={'enabled':True,'centerDirection':[0,0,1],'horizontalHalfAngleDeg':1,'verticalHalfAngleDeg':1})])
        s=r['routeSections'][0]
        self.assertFalse(s['corridor']['entryInsideCorridor'])
        self.assertFalse(r['summary']['targetReached'])
        self.assertGreater(np.linalg.norm(np.array(s['entryDirection'])-[0,0,1]),.1)

    def test_direction_section_is_propagated_and_not_a_straight_ray(self):
        r=self.calculate([section('earth','proxima-centauri','out')])
        s=r['routeSections'][-1]
        self.assertEqual(s['sectionType'],'interstellar-asymptote')
        self.assertTrue(s['hypothetical'])
        self.assertLess(s['lambertEndpointResidualKm'],.01)
        self.assertLess(r['summary']['targetAlignmentDeg'],5)
        self.assertAlmostEqual(np.linalg.norm(r['trajectory'][-1]['positionKm'])/149597870.7,50,places=7)
        self.assertGreater(len({tuple(round(x,6) for x in p['velocityKmS']) for p in r['trajectory']}),20)

    def test_vehicle_budget_covers_every_maneuver(self):
        r=self.calculate([section('earth','mars')],mission={'startDate':'2031-01-01','payloadMassKg':900,'carrierMassKg':100,'heatshieldMassKg':100,'propellantMassKg':1,'engineIspSeconds':450})
        self.assertFalse(r['summary']['feasibleWithConfiguredBurn'])
        self.assertTrue(r['summary']['vehicleValidated'])
        self.assertTrue(any(m['vehicleFeasible'] is False for m in r['maneuvers']))
