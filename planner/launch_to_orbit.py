"""Finite-thrust staged ascent; a measured orbit state is the only handover."""
from datetime import timedelta
from math import cos, sin, sqrt, pi
from pathlib import Path
import json
import numpy as np
from scipy.integrate import solve_ivp
from solver.ephemeris import EPHEMERIS, body_gm
from solver.orbital import G0_KM_S2, SCHEMA_VERSION, elements, state, timestamp, unit, utc
from solver.trajectory import _mission_epoch_days, DAY_SECONDS
from planner.generic_route_planner import _catalog, _body_state

SITES=Path(__file__).resolve().parents[1]/'web'/'public'/'launch-sites.json'
OMEGA=np.array([0.,0.,7.292115e-5]);A=6378.137;E2=6.69437999014e-3


def launch_sites():
    return json.loads(SITES.read_text(encoding='utf-8'))['sites']


def launch_initial_state(site,epoch):
    """WGS84 geodetic -> Earth-fixed -> J2000 r AND rotating-site velocity."""
    import spiceypy as spice
    if not EPHEMERIS.ensure_loaded():raise ValueError('Startaufstieg benötigt SPICE-Zeit- und Orientierungsdaten.')
    et=EPHEMERIS.utc_to_et(utc(epoch));lat,lon=np.radians([site['latitudeDeg'],site['longitudeDeg']])
    height=float(site.get('altitudeM',0))/1000;N=A/sqrt(1-E2*sin(lat)**2)
    fixed=np.array([(N+height)*cos(lat)*cos(lon),(N+height)*cos(lat)*sin(lon),(N*(1-E2)+height)*sin(lat)])
    with EPHEMERIS._lock:state_transform=spice.sxform('IAU_EARTH','J2000',et)
    inertial=state_transform@np.r_[fixed,[0.,0.,0.]];transform=state_transform[:3,:3]
    east=np.array([-sin(lon),cos(lon),0.]);north=np.array([-sin(lat)*cos(lon),-sin(lat)*sin(lon),cos(lat)])
    return inertial[:3],inertial[3:],transform@east,transform@north


def calculate_launch_to_orbit(values):
    import spiceypy as spice
    v=dict(values or {});epoch=utc(v.get('launchDateTime',v.get('startDate')))
    site=v.get('launchSite') or next((s for s in launch_sites() if s['id']==v.get('launchSiteId')),None)
    if not site:raise ValueError('Ein Startplatz mit geprüften Koordinaten ist erforderlich.')
    vehicle=v.get('launchVehicle')
    if not isinstance(vehicle,dict) or not vehicle.get('stages'):raise ValueError('Ein konkretes Fahrzeug mit Stufen-, Schub- und Isp-Daten ist erforderlich.')
    stages=vehicle['stages'];payload=float(v.get('payloadMassKg',0));fairing=float(vehicle.get('fairingMassKg',0));adapter=float(vehicle.get('payloadAdapterMassKg',0))
    if not np.isfinite(payload) or payload<=0:raise ValueError('Nutzlastmasse muss positiv sein.')
    if not np.isfinite(fairing) or fairing<0 or not np.isfinite(adapter) or adapter<0:raise ValueError('Verkleidungs- und Adaptermasse müssen endlich und nicht negativ sein.')
    separation=vehicle.get('fairingSeparation')
    if fairing and (not isinstance(separation,dict) or separation.get('minimumAltitudeKm') is None or separation.get('maximumDynamicPressurePa') is None):raise ValueError('Eine Verkleidung benötigt explizite Abwurfbedingungen: minimumAltitudeKm und maximumDynamicPressurePa.')
    if fairing and (float(separation['minimumAltitudeKm'])<0 or float(separation['maximumDynamicPressurePa'])<0 or not np.isfinite([float(separation['minimumAltitudeKm']),float(separation['maximumDynamicPressurePa'])]).all()):raise ValueError('Ungültige Abwurfbedingungen.')
    for s in stages:
        for key in ('dryMassKg','propellantMassKg','thrustVacuumN','specificImpulseVacuumS'):
            if not np.isfinite(float(s.get(key,0))) or float(s.get(key,0))<=0:raise ValueError(f'Stufendaten {key} müssen positiv und endlich sein.')
    az=float(v.get('launchAzimuthDeg',90));alt=float(v.get('targetOrbitAltitudeKm',200));incl=float(v.get('targetOrbitInclinationDeg',abs(site['latitudeDeg'])))
    if not np.isfinite(az) or not 0<=az<360:raise ValueError('Ungültiger Startazimut.')
    if not np.isfinite([float(site['latitudeDeg']),float(site['longitudeDeg']),float(site.get('altitudeM',0))]).all() or not -90<=float(site['latitudeDeg'])<=90 or not -180<=float(site['longitudeDeg'])<=180:raise ValueError('Ungültige Startplatzkoordinaten.')
    if not 100<alt<36000 or not 0<=incl<=180:raise ValueError('Ungültiger Zielorbit.')
    if not site.get('active',True) or not site.get('selectable',True):raise ValueError('Startplatz nicht auswählbar.')
    if site.get('allowedAzimuthMinDeg') is not None and not site['allowedAzimuthMinDeg']<=az<=site['allowedAzimuthMaxDeg']:raise ValueError('Startazimut liegt außerhalb des gepflegten Korridors.')
    r,vel,east,north=launch_initial_state(site,epoch);heading=sin(az*pi/180)*east+cos(az*pi/180)*north
    normal=unit(np.cross(r,heading));surface_radius=np.linalg.norm(r)-float(site.get('altitudeM',0))/1000;target_radius=surface_radius+alt
    mu=body_gm('earth');catalog=_catalog();time=0.;events=[];segments=[];points=[];max_q=0.;collided=False;orbit_reached=False;ideal_dv=0.;gravity_loss=0.;drag_loss=0.
    mass=payload+fairing+adapter+sum(float(s['dryMassKg'])+float(s['propellantMassKg']) for s in stages)
    initial_mass=mass;dropped=0.;consumed=0.;last_isp=None
    earth_days=_mission_epoch_days(timestamp(epoch))
    rotation_epoch=EPHEMERIS.utc_to_et(epoch)
    with EPHEMERIS._lock:rotation_axis=spice.pxform('IAU_EARTH','J2000',rotation_epoch)@OMEGA
    def global_state(r,vel,mass,fuel,t):
        ep,ev=_body_state(catalog['earth'],earth_days+t/DAY_SECONDS,catalog)
        # Ascent is integrated in J2000; route uses ECLIPJ2000.
        from solver.orbital import rotate_inertial
        rr,vv=rotate_inertial(r,vel,'J2000','ECLIPJ2000')
        return state(np.array(ep)+rr,np.array(ev)+vv,epoch+timedelta(seconds=t),mass=mass,propellant=max(0.,fuel))
    fuel=sum(float(s['propellantMassKg']) for s in stages)
    points.append({**global_state(r,vel,mass,fuel,0),'elapsedDays':0.,'phase':'LAUNCH_PAD','altitudeKm':float(site.get('altitudeM',0))/1000,'dynamicPressurePa':0.})
    for index,s in enumerate(stages):
        stage_fuel=float(s['propellantMassKg']);later_fuel=sum(float(k['propellantMassKg']) for k in stages[index+1:]);minimum_mass=mass-stage_fuel
        vacuum_thrust=float(s['thrustVacuumN']);vacuum_isp=float(s['specificImpulseVacuumS']);last_isp=vacuum_isp
        sea_thrust=float(s.get('thrustSeaLevelN',vacuum_thrust));sea_isp=float(s.get('specificImpulseSeaLevelS',vacuum_isp));area=float(s.get('referenceAreaM2',1));cd=float(s.get('dragCoefficient',.3))
        if index==0 and sea_thrust/mass/1000<=mu/np.linalg.norm(r)**2:raise ValueError('Startschub reicht nicht zum Abheben.')
        part=0
        while True:
            part+=1
            start_t=time;start_state=points[-1];start_index=len(points)-1
            def forces(t,y):
                rr,vv=y[:3],y[3:6];rad=unit(rr);radius=np.linalg.norm(rr);height=max(0.,radius-surface_radius);rho=1.225*np.exp(-height/7.2) if height<150 else 0.
                # Atmosphere rotates with Earth; no wind in this first model.
                rotation=np.cross(rotation_axis,rr);air=vv-rotation;airspeed=np.linalg.norm(air);q=.5*rho*(airspeed*1000)**2
                fraction=min(1.,np.exp(-height/7.2));thrust=vacuum_thrust+(sea_thrust-vacuum_thrust)*fraction;isp=vacuum_isp+(sea_isp-vacuum_isp)*fraction
                throttle=1.
                limit=v.get('maxDynamicPressurePa')
                if limit and q>float(limit):throttle=max(.2,float(limit)/q)
                accel=thrust*throttle/y[6]/1000;vr=np.dot(vv,rad);tangent=unit(np.cross(normal,rad));vt=np.dot(vv,tangent);vc=sqrt(mu/radius)
                if t<float(v.get('verticalAscentSeconds',12)):pitch=1.
                else:
                    desired_vr=np.clip((target_radius-radius)/float(v.get('altitudeGuidanceTimeSeconds',80)),-.08,.9)
                    pitch=float(np.clip(((desired_vr-vr)/20+mu/radius**2-vt**2/radius)/max(accel,1e-12),-.8,.999))
                direction=pitch*rad+sqrt(max(0.,1-pitch*pitch))*tangent
                if height>alt*.7 and vt>.9*vc:
                    # Terminal guidance commands acceleration; thrust remains
                    # finite, throttled and charged to the active stage.
                    ar=(np.clip((target_radius-radius)/30,-.1,.15)-vr)/12+mu/radius**2-vt**2/radius
                    at=(vc-vt)/12+vr*vt/radius
                    requested=ar*rad+at*tangent;required=np.linalg.norm(requested)
                    if required>1e-12:
                        direction=unit(requested);throttle=min(throttle,max(float(s.get('minimumThrottle',0.)),required/(thrust/y[6]/1000)))
                        accel=thrust*throttle/y[6]/1000;pitch=float(np.dot(direction,rad))
                drag=-q*cd*area/y[6]/1000*unit(air) if airspeed>1e-12 else np.zeros(3)
                derivative=np.r_[vv,-mu*rr/radius**3+accel*direction+drag,-thrust*throttle/(isp*9.80665),accel,np.linalg.norm(drag),mu/radius**2*max(0.,pitch)]
                return derivative,q,isp
            def derivative(t,y):return forces(t,y)[0]
            def burnout(t,y):return y[6]-minimum_mass
            burnout.terminal=True;burnout.direction=-1
            def ground(t,y):return np.linalg.norm(y[:3])-surface_radius+1e-4
            ground.terminal=True;ground.direction=-1
            def insertion(t,y):
                el=elements(y[:3],y[3:6],mu)
                if not el['bound'] or el['apoapsisRadiusKm'] is None:return 10.
                return max(abs(el['periapsisRadiusKm']-target_radius),abs(el['apoapsisRadiusKm']-target_radius))/float(v.get('orbitAltitudeToleranceKm',5))-1
            insertion.terminal=True;insertion.direction=-1
            def fairing_event(t,y):
                height=np.linalg.norm(y[:3])-surface_radius;q=forces(t,y)[1]
                return min(height-float(separation['minimumAltitudeKm']), (float(separation['maximumDynamicPressurePa'])-q)/1000.)
            fairing_event.terminal=True;fairing_event.direction=1
            duration=float(s.get('burnTimeS',stage_fuel*vacuum_isp*9.80665/vacuum_thrust*1.8))
            solution=solve_ivp(derivative,(time,time+duration),np.r_[r,vel,mass,0.,0.,0.],method='DOP853',rtol=2e-9,atol=1e-8,max_step=2,dense_output=True,events=[burnout,ground,insertion]+([fairing_event] if fairing else []))
            if not solution.success:raise RuntimeError(solution.message)
            for t in np.linspace(start_t,solution.t[-1],max(30,int(solution.t[-1]-start_t)//2))[1:]:
                y=solution.sol(t);q=forces(t,y)[1];max_q=max(max_q,q)
                points.append({**global_state(y[:3],y[3:6],y[6],later_fuel+max(0.,y[6]-minimum_mass),t),'elapsedDays':float(t/DAY_SECONDS),'phase':'POWERED_ASCENT','altitudeKm':float(np.linalg.norm(y[:3])-surface_radius),'dynamicPressurePa':float(q)})
            end=solution.y[:,-1];time=float(solution.t[-1]);r,vel=end[:3],end[3:6];consumed+=mass-end[6];mass=float(end[6]);ideal_dv+=end[7];drag_loss+=end[8];gravity_loss+=end[9]
            segments.append({'id':f'ascent-stage-{index+1}-{part}','label':s.get('name',f'Stufe {index+1}'),'phase':'POWERED_ASCENT','startIndex':start_index,'endIndex':len(points)-1,'startState':{k:x for k,x in start_state.items() if k not in ('phase','elapsedDays','altitudeKm','dynamicPressurePa')},'endState':global_state(r,vel,mass,later_fuel+max(0.,mass-minimum_mass),time)})
            if not fairing or not len(solution.t_events[3]):events.append({'type':'ENGINE_CUTOFF','stageId':s.get('id',str(index+1)),'elapsedDays':time/DAY_SECONDS,'massKg':mass})
            collided=bool(len(solution.t_events[1]));orbit_reached=bool(len(solution.t_events[2]))
            if fairing and len(solution.t_events[3]):
                before=global_state(r,vel,mass,later_fuel+max(0.,mass-minimum_mass),time)
                dropped+=fairing;mass-=fairing;minimum_mass-=fairing
                after=global_state(r,vel,mass,later_fuel+max(0.,mass-minimum_mass),time);begin=len(points)-1
                events.append({'type':'FAIRING_SEPARATION','elapsedDays':time/DAY_SECONDS,'jettisonedMassKg':fairing,'altitudeKm':float(np.linalg.norm(r)-surface_radius),'dynamicPressurePa':float(forces(time,np.r_[r,vel,mass,0,0,0])[1]),'conditions':separation})
                fairing=0.;points.append({**after,'elapsedDays':time/DAY_SECONDS,'phase':'FAIRING_SEPARATION'})
                segments.append({'id':'fairing-separation','label':'Verkleidungsabwurf','phase':'FAIRING_SEPARATION','startIndex':begin,'endIndex':len(points)-1,'startState':before,'endState':after})
                continue
            break
        if collided or orbit_reached:break
        if not len(solution.t_events[0]):break
        if index<len(stages)-1:
            before=global_state(r,vel,mass,later_fuel,time);mass-=float(s['dryMassKg']);dropped+=float(s['dryMassKg'])
            after=global_state(r,vel,mass,later_fuel,time);begin=len(points)-1;points.append({**after,'elapsedDays':time/DAY_SECONDS,'phase':'STAGE_SEPARATION'})
            segments.append({'id':f'separation-{index+1}','label':'Stufentrennung','phase':'STAGE_SEPARATION','startIndex':begin,'endIndex':len(points)-1,'startState':before,'endState':after})
            events.append({'type':'STAGE_SEPARATION','stageId':s.get('id',str(index+1)),'elapsedDays':time/DAY_SECONDS,'jettisonedMassKg':float(s['dryMassKg'])})
    orbit=elements(r,vel,mu);inclination_ok=abs(orbit['inclinationDeg']-incl)<=float(v.get('inclinationToleranceDeg',.5))
    orbit_reached=orbit_reached and not collided and inclination_ok and orbit['bound'] and orbit['periapsisRadiusKm']>surface_radius+100
    if v.get('targetOrbitRAANDeg') is not None:orbit_reached &= orbit['raanDeg'] is not None and abs((orbit['raanDeg']-float(v['targetOrbitRAANDeg'])+180)%360-180)<=float(v.get('raanToleranceDeg',.5))
    if v.get('targetOrbitEccentricity') is not None:orbit_reached &= abs(orbit['eccentricity']-float(v['targetOrbitEccentricity']))<=float(v.get('eccentricityTolerance',.001))
    pressure_ok=v.get('maxDynamicPressurePa') is None or max_q<=float(v['maxDynamicPressurePa'])*(1+1e-3)
    orbit_reached &= pressure_ok
    remaining=sum(float(k['propellantMassKg']) for k in stages[index+1:])+max(0.,mass-minimum_mass)
    end_state=global_state(r,vel,mass,remaining,time)
    from solver.orbital import continuity
    chain=continuity(segments);orbit_reached=bool(orbit_reached and chain['stateChain'])
    events.append({'type':'PARKING_ORBIT_REACHED' if orbit_reached else 'MISSION_ABORT','elapsedDays':time/DAY_SECONDS,'state':end_state})
    from services.calculation_version import CALCULATION_BUILD
    result={'schemaVersion':SCHEMA_VERSION,'calculationBuild':CALCULATION_BUILD,'mode':'launch-to-orbit','input':v,'trajectory':points,'segments':segments,'maneuvers':[],'events':events,'endState':end_state,'handoverIspSeconds':last_isp,'continuity':chain,
            'summary':{'orbitReached':orbit_reached,'feasible':orbit_reached,'durationSeconds':time,'totalFlightDays':time/DAY_SECONDS,'idealPropulsiveDeltaVKmS':float(ideal_dv),'dragLossKmS':float(drag_loss),'gravityLossKmS':float(gravity_loss),'maxDynamicPressurePa':float(max_q),'collision':collided,'propellantUsedKg':float(consumed),'jettisonedMassKg':dropped,'massBalanceResidualKg':float(abs(initial_mass-mass-consumed-dropped)),'orbitElements':orbit,'inclinationSatisfied':inclination_ok,'dynamicPressureSatisfied':pressure_ok,'model':'Finite thrust, staged mass flow, rotating exponential atmosphere, spherical gravity; no wind or operational flight certification.'},
            'warnings':['Aufstiegsmodell ohne Wind, variable Aerodynamik und Flugfreigabe; Sicherheitskorridore sind nur prüfbar, wenn gepflegt.'],'validation':{'stateContinuous':chain['stateChain'],'massBalanceSatisfied':abs(initial_mass-mass-consumed-dropped)<1e-6,'orbitReached':orbit_reached}}
    return result
