"""Persistence and display contracts must retain the solver's exact states."""
import unittest,tempfile,json
from pathlib import Path
from copy import deepcopy
from services.calculation_store import CalculationStore
from services.project_store import ProjectStore
from services.calculation_version import CALCULATION_BUILD
from planner.trajectory_planner import calculate_trajectory_plan
from planner.launch_to_orbit import calculate_launch_to_orbit
from solver.trajectory import AU_KM

class RouteContractTests(unittest.TestCase):
    def result(self):
        return calculate_trajectory_plan({'start':{'type':'state_vector','positionKm':[AU_KM,0,0],'velocityKmS':[0,50,0],'startDate':'2031-01-01T12:00:00Z'},'target':{'type':'boundary','distanceAU':1.01},'vehicle':{'wetMassKg':1000,'propellantMassKg':500,'engineIspSeconds':450},'simulation':{'includeAudit':False,'sampleTrajectoryPoints':24}})

    def test_persisted_full_state_and_all_burns_are_lossless(self):
        r=self.result()
        with tempfile.TemporaryDirectory() as directory:
            project_store=ProjectStore(Path(directory)/'state.db')
            store=CalculationStore(project_store.database_path);run=store.start_run({'baseDate':r['start']['date']})
            variant=store.record_variant(run['id'],{'startDate':r['start']['date']},r['input'],result=r,status='solver-completed')
            saved=store.get_variant(variant)
            self.assertFalse(saved['needsRecalculation']);self.assertTrue(saved['currentValidated'])
            self.assertEqual(saved['resultMetadata']['calculationBuild'],CALCULATION_BUILD)
            for before,after in zip(r['trajectory'],saved['trajectory']):
                for key in ('positionKm','velocityKmS','epochUtc','frame','centerBodyId','massKg','propellantKg'):self.assertEqual(before[key],after[key])
            self.assertEqual(len(saved['deltaV']),len(r['maneuvers']))
            self.assertAlmostEqual(sum(x['required_delta_v_km_s'] for x in saved['deltaV']),r['summary']['totalDeltaVKmS'])
            old=deepcopy(r);old['calculationBuild']='previous-build'
            variant=store.record_variant(run['id'],{'iteration':2},r['input'],result=old,status='solver-completed')
            self.assertTrue(store.get_variant(variant)['needsRecalculation'])
            self.assertFalse(store.get_variant(variant)['currentValidated'])

    def test_mass_requires_propellant_and_isp(self):
        values={'start':{'type':'state_vector','positionKm':[AU_KM,0,0],'velocityKmS':[0,50,0],'startDate':'2031-01-01','massKg':1000},'target':{'type':'boundary','distanceAU':2},'simulation':{'includeAudit':False}}
        with self.assertRaisesRegex(ValueError,'Treibstoffmasse'):calculate_trajectory_plan(values)

    def test_fairing_event_checks_conditions_and_conserves_mass(self):
        p=json.loads((Path(__file__).parent/'fixtures/launch_reference.json').read_text())
        p['launchVehicle'].update(fairingMassKg=500,fairingSeparation={'minimumAltitudeKm':100,'maximumDynamicPressurePa':100})
        r=calculate_launch_to_orbit(p);e=next(e for e in r['events'] if e['type']=='FAIRING_SEPARATION')
        self.assertGreaterEqual(e['altitudeKm'],100-1e-6);self.assertLessEqual(e['dynamicPressurePa'],100+1e-6)
        self.assertEqual(e['jettisonedMassKg'],500);self.assertTrue(r['summary']['orbitReached'])
        self.assertLess(r['summary']['massBalanceResidualKg'],1e-6);self.assertTrue(r['continuity']['stateChain'])
        p['launchVehicle'].pop('fairingSeparation')
        with self.assertRaisesRegex(ValueError,'Abwurfbedingungen'):calculate_launch_to_orbit(p)

    def test_exact_event_ephemerides_are_shared_with_display(self):
        r=calculate_trajectory_plan({'start':{'type':'orbit','bodyId':'earth','startDate':'2031-01-01T12:00:00Z'},'target':{'type':'body','bodyId':'earth-moon','targetDate':'2031-01-04T12:00:00Z'},'simulation':{'includeAudit':False,'sampleTrajectoryPoints':24}})
        entry=r['routeSections'][0];samples=r['bodyEphemerides']['tracks']['earth-moon']
        body=next(p for p in samples if p['elapsedDays']==entry['entryDay'])
        self.assertEqual(body['positionKm'],r['target']['positionKm']);self.assertEqual(r['bodyEphemerides']['epochUtc'],r['start']['date'])

    def test_solar_exit_speed_is_measured_at_one_au(self):
        r=calculate_trajectory_plan({'start':{'type':'orbit','bodyId':'earth','startDate':'2031-01-01'},'target':{'type':'direction','direction':[1,0,0],'distanceAU':2,'vInfinityKmS':15},'waypoints':[{'type':'solar_oberth','perihelionAU':.05,'desiredExitSpeedKmS':25,'maximumBurnDeltaVKmS':8}],'simulation':{'includeAudit':False,'sampleTrajectoryPoints':24}})
        self.assertTrue(r['solarBoundary']['speedBoundaryReached'])
        self.assertAlmostEqual(r['solarBoundary']['actualExitSpeedKmS'],25,places=5)
        self.assertTrue(r['summary']['feasible'])
        self.assertEqual(r['solarBoundary']['radiusAu'],1)

    def test_optimizer_uses_physical_evidence_and_export_keeps_states(self):
        from planner.mission_optimizer import _physical_route_reasons
        from planner.route_planner import _route_mission_payload
        r=self.result();geometry,energy=_physical_route_reasons(r)
        self.assertEqual(geometry,[]);self.assertEqual(energy,[])
        broken=deepcopy(r);broken['validation']['stateContinuous']=False
        self.assertTrue(_physical_route_reasons(broken)[0])
        broken=deepcopy(r);broken['summary']['vehicleFeasible']=False
        self.assertTrue(_physical_route_reasons(broken)[1])
        exported=_route_mission_payload(r,{})
        for expected,actual in zip(r['trajectory'],exported['trajectory']):
            for key in ('positionKm','velocityKmS','massKg','epochUtc','propellantKg'):self.assertEqual(expected[key],actual[key])
        self.assertEqual(r['summary']['totalDeltaVKmS'],exported['summary']['totalDeltaVKmS'])
