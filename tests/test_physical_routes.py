"""Acceptance criteria from physical references, independent of display geometry."""
from copy import deepcopy
from datetime import timedelta
from math import pi,sqrt,exp
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
import numpy as np
from scipy.integrate import quad
from planner.trajectory_planner import calculate_trajectory_plan,_start_state
from planner.launch_to_orbit import calculate_launch_to_orbit,launch_initial_state,launch_sites
from solver.nbody_propagation import propagate_conic
from solver.orbital import elements,rotate_inertial,utc,timestamp,maneuver,state
from solver.trajectory import AU_KM,MU_SUN,_mission_epoch_days,simulate_mission
from services.calculation_store import CalculationStore

SIM={'includeAudit':False,'sampleTrajectoryPoints':40,'propagationYears':5}
def request(target,**extra):
    return {'start':{'type':'orbit','bodyId':'earth','startDate':'2031-01-01'},'target':target,'simulation':SIM,**extra}


class ReferenceDynamicsTests(unittest.TestCase):
    def test_circle_after_one_period_and_invariants(self):
        mu=398600.435507;radius=7000.;speed=sqrt(mu/radius);period=2*pi*sqrt(radius**3/mu)
        result=propagate_conic(([radius,0,0],[0,speed,0]),period,mu)
        self.assertLess(np.linalg.norm(np.array(result['finalPositionKm'])-[radius,0,0]),1e-5)
        for p in result['trajectory']:
            r,v=np.array(p['positionKm']),np.array(p['velocityKmS'])
            self.assertAlmostEqual(np.dot(v,v)/2-mu/np.linalg.norm(r),-mu/(2*radius),places=8)
            self.assertAlmostEqual(np.linalg.norm(np.cross(r,v)),radius*speed,places=4)

    def test_radius_event_time_matches_independent_radial_quadrature(self):
        r0=AU_KM;r1=2*AU_KM;v0=50.;energy=v0*v0/2-MU_SUN/r0
        expected=quad(lambda r:1/sqrt(2*(energy+MU_SUN/r)),r0,r1,epsabs=1e-3)[0]
        result=propagate_conic(([r0,0,0],[v0,0,0]),2*expected,MU_SUN,stop_radius_km=r1,crossing_direction=1)
        self.assertAlmostEqual(result['durationSeconds'],expected,places=3)
        self.assertLess(abs(np.linalg.norm(result['finalPositionKm'])-r1),1e-5)

    def test_frame_rotates_position_and_velocity_and_roundtrips(self):
        r,v=rotate_inertial([1,2,3],[4,5,6],'J2000','ECLIPJ2000')
        rr,vv=rotate_inertial(r,v,'ECLIPJ2000','J2000')
        np.testing.assert_allclose(rr,[1,2,3],atol=1e-14);np.testing.assert_allclose(vv,[4,5,6],atol=1e-14)
        self.assertNotEqual(list(v),[4,5,6])

    def test_utc_time_and_timezone_are_not_truncated(self):
        self.assertEqual(_mission_epoch_days('2031-01-01T12:00:00+02:00'),_mission_epoch_days('2031-01-01T10:00:00Z'))
        values=request({'type':'body','bodyId':'mars'})
        r0,_,_=_start_state(values,utc('2031-01-01'));r1,_,_=_start_state(values,utc('2031-01-01T12:00:00Z'))
        self.assertGreater(np.linalg.norm(np.array(r1)-r0),1e6)

    def test_rocket_equation_independent_mass_check(self):
        before=state([AU_KM,0,0],[0,30,0],'2031-01-01',mass=1000,propellant=800)
        burn=maneuver(before,[0,31,0],'TEST',isp_seconds=450,available_propellant=800)
        expected=1000*(1-exp(-1/(450*.00980665)))
        self.assertAlmostEqual(burn['propellantUsedKg'],expected,places=10)
        denied=maneuver(before,[0,130,0],'TEST',isp_seconds=450,available_propellant=800)
        self.assertFalse(denied['applied']);self.assertEqual(denied['stateAfter']['massKg'],1000)


class PhysicalRouteAcceptanceTests(unittest.TestCase):
    def test_zero_budget_rejects_boundary_direction_and_rendezvous(self):
        cases=[{'type':'boundary','distanceAU':2},{'type':'direction','direction':[1,2,3],'distanceAU':2},
               {'type':'state_vector','positionKm':[2*AU_KM,AU_KM,0],'velocityKmS':[0,0,0],'targetDate':'2032-01-01'}]
        for target in cases:
            with self.subTest(target=target['type']):
                r=calculate_trajectory_plan(request(target,constraints={'maxTotalDeltaVKmS':0}))
                self.assertFalse(r['summary']['feasible']);self.assertGreater(r['summary']['totalDeltaVKmS'],0)
                self.assertAlmostEqual(r['summary']['totalDeltaVKmS'],sum(m['deltaVKmS'] for m in r['maneuvers']),places=8)

    def test_planet_and_moon_orbits_are_local_bound_states(self):
        for origin,target,date in [('earth','mars','2031-09-18'),('earth','earth-moon','2031-01-04'),('jupiter','jupiter-io','2031-01-04')]:
            with self.subTest(target=target):
                p=request({'type':'body_orbit','bodyId':target,'orbitAltitudeKm':100,'targetDate':date});p['start']['bodyId']=origin
                r=calculate_trajectory_plan(p);s=r['routeSections'][-1]
                self.assertTrue(s['targetConditionSatisfied']);self.assertTrue(s['orbitElements']['bound'])
                self.assertLess(s['periapsisResidualKm'],1);self.assertGreaterEqual(s['completedRevolutions'],1-1e-6)
                self.assertTrue(r['continuity']['stateChain'])
                self.assertTrue(any(m['type']=='ORBIT_INSERTION' for m in r['maneuvers']))

    def test_tiny_moon_orbit_outside_hill_region_is_rejected(self):
        p=request({'type':'body_orbit','bodyId':'mars-phobos','orbitAltitudeKm':100,'targetDate':'2031-01-04'});p['start']['bodyId']='mars'
        with self.assertRaisesRegex(ValueError,'Stabilitätsgrenze'):calculate_trajectory_plan(p)

    def test_lunar_return_measures_orbit_and_entry_flightpath(self):
        r=calculate_trajectory_plan(request({'type':'body','bodyId':'earth'},missionTemplate='earth_moon_orbit_return'))
        self.assertTrue(r['summary']['lunarOrbitCompleted']);self.assertTrue(r['summary']['returnReached'])
        self.assertAlmostEqual(r['routeSections'][-1]['minimumAltitudeKm'],120,places=3)
        self.assertAlmostEqual(r['routeSections'][-1]['entryFlightPathAngleDeg'],-6.5,places=3)
        for name in ['TRANS_LUNAR_INJECTION','LUNAR_ORBIT_INSERTION','TRANS_EARTH_INJECTION']:
            self.assertTrue(any(m['type']==name for m in r['maneuvers']))
        self.assertTrue(r['continuity']['stateChain'])

    def test_missing_ephemeris_never_claims_feasible(self):
        with patch('planner.trajectory_planner.body_quality',return_value={'available':False,'centerExact':False}):
            r=calculate_trajectory_plan(request({'type':'body','bodyId':'mars','targetDate':'2031-09-18'}))
        self.assertFalse(r['summary']['feasible']);self.assertEqual(r['summary']['status'],'data_unavailable')

    def test_default_solar_vehicle_does_not_get_free_earth_escape(self):
        r=simulate_mission({'startDate':'2031-01-01','missionYears':1})
        self.assertEqual(r.summary.status,'ABORT')
        self.assertFalse(any(e.name in {'EARTH_SWING_LOOP_1','EARTH_SWING_LOOP_2'} for e in r.events))


class LaunchHandoverTests(unittest.TestCase):
    def fixture(self):return json.loads((Path(__file__).parent/'fixtures/launch_reference.json').read_text(encoding='utf-8'))

    def test_ascent_stages_mass_rotation_and_measured_orbit(self):
        p=self.fixture();r=calculate_launch_to_orbit(p)
        self.assertTrue(r['summary']['orbitReached']);self.assertTrue(r['summary']['orbitElements']['bound'])
        self.assertLess(r['summary']['massBalanceResidualKg'],1e-6)
        self.assertGreater(r['summary']['maxDynamicPressurePa'],1000)
        self.assertTrue(any(e['type']=='STAGE_SEPARATION' for e in r['events']))
        self.assertTrue(r['continuity']['stateChain'])
        _,v,_,_=launch_initial_state(launch_sites()[0],p['launchDateTime'])
        self.assertGreater(np.linalg.norm(v),.4);self.assertLess(np.linalg.norm(v),.47)

    def test_insufficient_thrust_and_unreachable_inclination(self):
        p=self.fixture();p['launchVehicle']['stages'][0]['thrustSeaLevelN']=1000
        with self.assertRaisesRegex(ValueError,'Abheben'):calculate_launch_to_orbit(p)
        p=self.fixture();p['targetOrbitInclinationDeg']=0
        self.assertFalse(calculate_launch_to_orbit(p)['summary']['orbitReached'])

    def test_route_launch_endpoint_is_exact_departure_state(self):
        launch=self.fixture();r=calculate_trajectory_plan(request({'type':'body','bodyId':'earth-moon','targetDate':'2031-01-05'},start={'type':'launch_site','startDate':launch['launchDateTime']},launch=launch))
        burn=next(s for s in r['segments'] if s['phase']=='TRANSFER_INJECTION')
        ascent=next(s for s in reversed(r['segments'][:r['segments'].index(burn)]) if s['phase']=='POWERED_ASCENT')
        self.assertEqual(ascent['endState'],burn['startState'])
        self.assertTrue(r['continuity']['stateChain']);self.assertGreater(r['summary']['ascentDeltaVKmS'],8)
