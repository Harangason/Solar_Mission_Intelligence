"""Lunar return template assembled from the common physical route phases."""
from copy import deepcopy
from planner.trajectory_planner import _normalized_input, calculate_trajectory_plan, _audit_result
from planner.generic_route_planner import _catalog


def calculate_lunar_return(values):
    original=deepcopy(values);include_audit=(values.get('simulation') or {}).get('includeAudit',True)
    values=deepcopy(values);values.setdefault('simulation',{})['includeAudit']=False;p=values.pop('lunarReturn',{})
    values.pop('missionTemplate',None)
    start=values.setdefault('start',{})
    start.update(type=start.get('type','orbit'),bodyId='earth',orbitAltitudeKm=p.get('earthParkingOrbitAltitudeKm',400))
    start.setdefault('startDate',p.get('startDate'))
    revolutions=float(p.get('requiredLunarRevolutions',1))
    if revolutions<1:raise ValueError('Die Mondorbit-Mission benötigt mindestens einen vollständigen Umlauf.')
    waypoint={'id':'lunar-orbit','type':'body_orbit','bodyId':'earth-moon',
              'flightDays':p.get('outboundFlightDays',3),'orbitAltitudeKm':p.get('lunarOrbitPeriluneKm',100),
              'apoapsisAltitudeKm':p.get('lunarOrbitApoluneKm',100),'requiredRevolutions':revolutions,
              'captureBurnName':'LUNAR_ORBIT_INSERTION','departureBurnName':'TRANS_LUNAR_INJECTION'}
    # An explicit plane must be proved by the solved state, not assigned metadata.
    if p.get('lunarOrbitInclinationDeg') is not None:waypoint['inclinationDeg']=p['lunarOrbitInclinationDeg']
    mode=p.get('returnMode','earth_reentry')
    if mode=='earth_reentry':
        target={'type':'earth_reentry','bodyId':'earth','entryRadiusKm':_catalog()['earth'].radius_km+p.get('targetEarthEntryInterfaceAltitudeKm',120),
                'entryFlightPathAngleDeg':p.get('targetEarthEntryFlightPathAngleDeg',-6.5)}
    elif mode=='earth_orbit_capture':
        target={'type':'body_orbit','bodyId':'earth','orbitAltitudeKm':p.get('returnOrbitAltitudeKm',400),'requiredRevolutions':1,'captureBurnName':'EARTH_ORBIT_INSERTION'}
    elif mode=='flyby_return':
        target={'type':'flyby','bodyId':'earth','flybyAltitudeKm':p.get('returnFlybyAltitudeKm',1000)}
    else:raise ValueError('Unbekannter Rückkehrmodus.')
    target['flightDays']=p.get('returnFlightDays',3);target['departureBurnName']='TRANS_EARTH_INJECTION'
    values['target']=target;values['waypoints']=[waypoint]
    result=calculate_trajectory_plan(_normalized_input(values));result['mode']='earth-moon-orbit-return'
    result['missionTemplate']='earth_moon_orbit_return';result['returnMode']=mode
    departure=[m for m in result['maneuvers'] if m['type']=='TRANSFER_INJECTION']
    if departure:
        result['maneuvers'][result['maneuvers'].index(departure[0])]['type']='TRANS_EARTH_INJECTION'
    result['summary']['lunarOrbitCompleted']=result['routeSections'][0].get('completedRevolutions',0)>=revolutions-1e-6
    result['summary']['returnReached']=bool(result['routeSections'][-1]['targetConditionSatisfied'])
    for key,burn in [('tliDeltaVKmS','TRANS_LUNAR_INJECTION'),('loiDeltaVKmS','LUNAR_ORBIT_INSERTION'),('teiDeltaVKmS','TRANS_EARTH_INJECTION')]:
        actual=sum(m['deltaVKmS'] for m in result['maneuvers'] if m['type']==burn)
        result['summary'][key]=actual
        if p.get(key) is not None and actual>float(p[key])+1e-8:
            result['summary'].update(feasible=False,flightReady=False,status='infeasible');result['warnings'].append(f'{burn}: vorgegebenes Einzelbudget überschritten.')
    if include_audit:result['audit']=_audit_result(result,original)
    return result
