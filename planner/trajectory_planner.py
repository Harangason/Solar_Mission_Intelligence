"""One target-independent, state-continuous patched-conic mission planner.

Lambert is a seed/transfer solver, not an orbit-insertion or feasibility proof.
Every impulse, local coast, target condition and resource check is explicit.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from math import acos, cos, exp, isfinite, log, pi, sin, sqrt
from uuid import uuid4

import numpy as np
from scipy.optimize import least_squares

from planner.generic_route_planner import (SUN_RADIUS_KM, _body_state, _catalog,
    _entry_radius, _local_central_body, parse_route_passage)
from planner.route_planner import (G_KM3_KG_S2, _lambert_candidates,
    _parse_entry_corridor, _solar_asymptote_direction, _corridor_coordinates_deg)
from services.calculation_audit import write_route_audit
from services.calculation_version import CALCULATION_BUILD
from solver.ephemeris import body_quality, get_ephemeris_status
from solver.nbody_propagation import propagate_conic
from solver.orbital import (SCHEMA_VERSION, angle, circular_state, continuity,
    elements, maneuver, rotate_inertial, state, timestamp, unit, utc, vector)
from solver.trajectory import AU_KM, DAY_SECONDS, MU_SUN, _mission_epoch_days

ZONE_DEFINITIONS = {
    "asteroid_belt": {"name": "Asteroidengürtel", "innerRadiusAU": 2.1, "outerRadiusAU": 3.3},
    "kuiper_belt": {"name": "Kuipergürtel", "innerRadiusAU": 30., "outerRadiusAU": 50.},
    "scattered_disk": {"name": "Scattered Disk", "innerRadiusAU": 50., "outerRadiusAU": 1000.},
    "oort_cloud": {"name": "Oortsche Wolke", "innerRadiusAU": 2000., "outerRadiusAU": 100000.},
}
BOUNDARY_DEFINITIONS = {
    "100_au": {"name": "100-AE-Grenze", "radiusAU": 100.},
    "termination_shock": {"name": "Termination Shock", "radiusAU": 94.},
    "heliopause": {"name": "Heliopause", "radiusAU": 120.},
    "voyager_1_distance": {"name": "Voyager-Distanz", "radiusAU": 170.},
}
TARGET_TYPES = {"body", "body_orbit", "flyby", "zone", "boundary", "direction", "state_vector", "earth_reentry"}
OPTIMIZATION_MODES = {"minimum_energy", "minimum_time", "minimum_arrival_speed", "maximum_exit_speed", "minimum_delta_v", "balanced", "custom"}


def _number(value, default):
    if value is None: return float(default)
    if isinstance(value, bool): raise ValueError("Numerischer Parameter darf kein Boolean sein.")
    try: result = float(value)
    except (ValueError, TypeError) as error: raise ValueError("Ungültiger numerischer Parameter.") from error
    if not isfinite(result): raise ValueError("Numerische Parameter müssen endlich sein.")
    return result


def _date(value, field):
    try: return utc(value)
    except (TypeError, ValueError) as error: raise ValueError(f"{field} muss ein ISO-Zeitpunkt sein.") from error


def _date_range(start, end, step_days, field):
    first, last = _date(start, field), _date(end, field)
    step = _number(step_days, 1.)
    if step <= 0 or last < first: raise ValueError(f"Ungültiges Zeitfenster {field}.")
    count = int((last-first).total_seconds() / (step*DAY_SECONDS)) + 1
    if count > 400: raise ValueError(f"{field} enthält mehr als 400 Rasterpunkte.")
    return [first + timedelta(days=i*step) for i in range(count)]


def _vector(value, field): return tuple(vector(value, field))


def _direction(target):
    if target.get("direction") is not None: return tuple(unit(target["direction"]))
    if target.get("positionKm") is not None: return tuple(unit(target["positionKm"]))
    if target.get("eclipticLongitudeDeg") is not None:
        lon, lat = np.radians([_number(target["eclipticLongitudeDeg"],0), _number(target.get("eclipticLatitudeDeg"),0)])
        return cos(lat)*cos(lon), cos(lat)*sin(lon), sin(lat)
    ra, dec = np.radians([_number(target.get("rightAscensionDeg"),0), _number(target.get("declinationDeg"),0)])
    eq = [cos(dec)*cos(ra),cos(dec)*sin(ra),sin(dec)]
    return tuple(rotate_inertial(eq,eq,"J2000","ECLIPJ2000")[0])


def calculate_candidate_score(candidate, optimization_mode, weights=None):
    if optimization_mode not in OPTIMIZATION_MODES: raise ValueError("Unbekannter Optimierungsmodus.")
    terms={"energy":max(0.,candidate.get("c3Km2S2",0))/400.,
           "flightTime":candidate.get("flightDays",0)/3650.,
           "arrivalSpeed":candidate.get("arrivalVInfinityKmS",0)/30.,
           "deltaV":candidate.get("totalDeltaVKmS",0)/30.,
           "risk":len(candidate.get("warnings",[]))/10.}
    direct={"minimum_energy":"energy","minimum_time":"flightTime","minimum_arrival_speed":"arrivalSpeed","minimum_delta_v":"deltaV"}
    if optimization_mode in direct: return terms[direct[optimization_mode]]
    if optimization_mode=="maximum_exit_speed": return -candidate.get("finalHeliocentricSpeedKmS",0)/100.
    weights=weights or {"energy":.35,"flightTime":.25,"arrivalSpeed":.2,"risk":.1,"deltaV":.1}
    return sum(value*_number(weights.get(key),0) for key,value in terms.items())


def _normalized_input(values):
    if values is not None and not isinstance(values,dict):raise ValueError('Planner-Eingabe muss ein JSON-Objekt sein.')
    values=deepcopy(values or {}); start=values.setdefault("start",{}); target=values.setdefault("target",{})
    start.setdefault("type","body"); target.setdefault("type","body")
    if start["type"] not in {"body","orbit","state_vector","launch_site"}: raise ValueError("Unbekannter Starttyp.")
    if target["type"] not in TARGET_TYPES: raise ValueError("Unbekannter Zieltyp.")
    for waypoint in [target,*values.get('waypoints',[])]:
        aim=waypoint.get('aimpoint') or {}
        if not aim.get('enabled'):waypoint.pop('aimpoint',None)
        if aim.get('enabled') and aim.get('screenRadiusNorm') not in (None,1,1.):
            raise ValueError('Scheibenradius ist kein physikalischer Zielparameter. Bitte Perizentrumhöhe und B-Ebenenwinkel angeben.')
    search=values.setdefault("searchWindow",{}); simulation=values.setdefault("simulation",{})
    start.setdefault("startDate",search.get("departureStartDate"))
    _date(start["startDate"],"start.startDate")
    search.setdefault("departureStartDate",start["startDate"]);search.setdefault("departureEndDate",search["departureStartDate"])
    search.setdefault("departureStepDays",10)
    values.setdefault("constraints",{});values.setdefault("waypoints",[]);values.setdefault("optimizationMode","balanced")
    if values["optimizationMode"] not in OPTIMIZATION_MODES: raise ValueError("Unbekannter Optimierungsmodus.")
    if not isinstance(values["waypoints"],list): raise ValueError("waypoints muss eine geordnete Liste sein.")
    for key,value in values["constraints"].items():
        if value is not None and not isinstance(value,(dict,list,str)): _number(value,0)
    simulation.setdefault("includeAudit",True); simulation.setdefault("includeUncertainty",False)
    for key in ('highFidelityNBody','includeAudit','includeUncertainty'):
        if key in simulation and not isinstance(simulation[key],bool):raise ValueError(f'{key} muss ein Boolean sein.')
    simulation["sampleTrajectoryPoints"]=max(24,min(2000,int(_number(simulation.get("sampleTrajectoryPoints"),180))))
    simulation["propagationYears"]=_number(simulation.get("propagationYears"),20)
    if not .01<=simulation["propagationYears"]<=50000: raise ValueError("Ungültige Propagationsdauer.")
    return values


def _constraints(candidate, values):
    checks={"minFlightDays":("flightDays",False),"maxFlightDays":("flightDays",True),
            "maxC3Km2S2":("c3Km2S2",True),"maxDepartureVInfinityKmS":("departureVInfinityKmS",True),
            "maxArrivalVInfinityKmS":("arrivalVInfinityKmS",True),"maxTotalDeltaVKmS":("totalDeltaVKmS",True)}
    reasons=[]
    for key,(actual_key,maximum) in checks.items():
        if values.get(key) is None: continue
        actual=candidate.get(actual_key)
        if actual is None or not isfinite(float(actual)): reasons.append(f"{key}: erforderlicher Nachweis fehlt");continue
        limit=_number(values[key],0)
        if (maximum and actual>limit+1e-9) or (not maximum and actual<limit-1e-9):
            reasons.append(f"{key}: {actual:.6g}, Grenze {limit:.6g}")
    return not reasons,reasons


class PhysicalRoute:
    def __init__(self, values, departure):
        self.values=values;self.catalog=_catalog();self.epoch=utc(departure);self.seconds=0.
        self.points=[];self.segments=[];self.events=[];self.maneuvers=[];self.sections=[];self.warnings=[]
        self.bodies=set();self.collision=False;self.vehicle_valid=True;self.target_reached=False
        self.local_body=None;self.mass=None;self.propellant=None;self.isp=None;self.ascent_dv=0.
        self.full_gravity=bool(values['simulation'].get('highFidelityNBody',False))
        craft=values.get("vehicle") or values.get("mission") or {}
        if craft.get("wetMassKg") is not None:
            self.mass=_number(craft["wetMassKg"],0);self.propellant=_number(craft.get("propellantMassKg"),0)
        elif all(craft.get(k) is not None for k in ("payloadMassKg","carrierMassKg","heatshieldMassKg","propellantMassKg")):
            self.propellant=_number(craft["propellantMassKg"],0) if craft.get('kickStageEnabled',True) else 0.
            self.mass=_number(craft['payloadMassKg'],0)+self.propellant
            for component in ('carrier','heatshield'):
                if craft.get(component+'Enabled',True):self.mass+=_number(craft[component+'MassKg'],0)
            if not values.get('vehicle'):
                from solver.trajectory import MissionConfig,_build_satellite,build_propulsion_modules,PropulsionType
                config=MissionConfig.from_dict(craft);satellite=_build_satellite(config)
                included={PropulsionType.CHEMICAL,PropulsionType.SOLID_KICK_STAGE,PropulsionType.SOLAR_OBERTH,PropulsionType.ELECTRIC_SAIL}
                self.mass=satellite.total_mass_kg+sum(m.dry_mass_kg+m.propellant_mass_kg for m in build_propulsion_modules(config.propulsion_modules) if m.enabled and m.type not in included)
                self.propellant=satellite.kick_stage.mass_kg
        if self.mass is not None:
            if craft.get('engineIspSeconds') is None:raise ValueError('Für den Fahrzeugnachweis fehlt engineIspSeconds.')
            self.isp=_number(craft['engineIspSeconds'],0)
        start=values["start"]
        if start["type"]=="state_vector":
            self.r,self.v=rotate_inertial(start["positionKm"],start["velocityKmS"],start.get("frame","ECLIPJ2000"),"ECLIPJ2000")
            center=start.get("centerBodyId","sun")
            if center!="sun":
                p,w=self.body(center);self.r+=p;self.v+=w;self.local_body=center
            if start.get("massKg") is not None:
                self.mass=_number(start["massKg"],0);self.propellant=start.get("propellantKg");self.isp=craft.get("engineIspSeconds")
        elif start["type"]=="launch_site":
            from planner.launch_to_orbit import calculate_launch_to_orbit
            launch=calculate_launch_to_orbit({**values.get("launch",{}),**start,"startDate":timestamp(self.epoch)})
            if not launch["summary"]["orbitReached"]: raise ValueError("Der Startaufstieg hat keinen gültigen Parkorbit erreicht.")
            self.points=launch["trajectory"];self.segments=launch["segments"];self.events=launch["events"]
            self.seconds=launch["summary"]["durationSeconds"]
            self.ascent_dv=launch['summary']['idealPropulsiveDeltaVKmS'];self.warnings.extend(launch['warnings'])
            end=launch["endState"];self.r=vector(end["positionKm"]);self.v=vector(end["velocityKmS"])
            self.mass=end["massKg"];self.propellant=end["propellantKg"];self.isp=launch["handoverIspSeconds"]
            self.local_body="earth";self.bodies.add("earth")
        else:
            body=self.get_body(start.get("bodyId"));p,w=self.body(body.id)
            altitude=_number(start.get("orbitAltitudeKm"),400 if body.id=="earth" else 100)
            if altitude<=0:raise ValueError('Der Anfangsorbit muss außerhalb des Körpers liegen.')
            rp,vp=circular_state(body.radius_km+altitude,body.mass_kg*G_KM3_KG_S2,
                inclination_deg=start.get("inclinationDeg",0),raan_deg=start.get("raanDeg",0),phase_deg=start.get("phaseDeg",0))
            if not any(start.get(key) is not None for key in ("phaseDeg","inclinationDeg","raanDeg")):
                first=(values["waypoints"] or [values["target"]])[0]
                destination_id="sun" if first.get("type")=="solar_oberth" else first.get("bodyId")
                if destination_id in self.catalog and destination_id!=body.id:
                    target_body=self.get_body(destination_id)
                    flight=_number(first.get("flightDays"),3 if target_body.parent_id==body.id else 260)
                    end=_date(first.get("targetDate",values.get("_arrivalDate")),"arrival") if first.get("targetDate",values.get("_arrivalDate")) else self.epoch+timedelta(days=flight)
                    tp,tv=self.body(destination_id,(end-self.epoch).total_seconds())
                    if target_body.parent_id==body.id:
                        ep,ev=self.body(body.id,(end-self.epoch).total_seconds())
                        # Free parking phase may be chosen, but a precisely
                        # antipodal endpoint has no unique Lambert plane.
                        normal=unit(np.cross(tp-ep,tv-ev));base=-unit(tp-ep)
                        offset=float(values.get('_parkingPhaseOffsetRad',.002))
                        radial=cos(offset)*base+sin(offset)*np.cross(normal,base)
                        rp=(body.radius_km+altitude)*radial;vp=sqrt(body.mass_kg*G_KM3_KG_S2/np.linalg.norm(rp))*unit(np.cross(normal,radial))
                    elif body.id!="sun":
                        if destination_id=="sun":excess=-unit(w)*20.
                        else:
                            seeds=_lambert_candidates(tuple(p),tuple(tp),(end-self.epoch).total_seconds())
                            seed=min(seeds,key=lambda b:np.linalg.norm(vector(b["departure"])-w))
                            excess=vector(seed["departure"])-w
                        speed=max(float(np.linalg.norm(excess)),.1);d=unit(excess)
                        normal=unit(np.cross(d,[0,0,1] if abs(d[2])<.9 else [0,1,0]))
                        eccentricity=1+(body.radius_km+altitude)*speed**2/(body.mass_kg*G_KM3_KG_S2)
                        nu=acos(-1/eccentricity)
                        radial=cos(nu)*d-sin(nu)*np.cross(normal,d)
                        rp=(body.radius_km+altitude)*radial;vp=sqrt(body.mass_kg*G_KM3_KG_S2/np.linalg.norm(rp))*unit(np.cross(normal,radial))
                    start["parkingOrbitAssumption"]="Freie Bahnphase und Bahnebene aus dem Abflugziel gelöst; explizite Orbitparameter fixieren den Zustand."
            if start.get("orbitFrame"):
                rp,vp=rotate_inertial(rp,vp,start["orbitFrame"],"ECLIPJ2000")
            self.r=p+rp;self.v=w+vp;self.local_body=body.id
        if self.mass is not None:
            if self.propellant is None or self.isp is None or _number(self.isp,0)<=0:
                raise ValueError('Ein massenbehafteter Startzustand benötigt Treibstoffmasse und positiven Isp.')
            self.propellant=_number(self.propellant,0);self.isp=_number(self.isp,0)
        self.initial={k:v for k,v in self.points[0].items() if k in {'positionKm','velocityKmS','massKg','propellantKg','epochUtc','frame','centerBodyId'}} if self.points else self.snapshot()
        if not self.points:self.points=[self.point("INITIAL_STATE")]

    def get_body(self,body_id):
        if body_id not in self.catalog: raise ValueError(f"Körper '{body_id}' besitzt keine lokale Ephemeride.")
        return self.catalog[body_id]

    def body(self,body_id,seconds=None):
        self.bodies.add(body_id);body=self.get_body(body_id)
        days=_mission_epoch_days(timestamp(self.epoch))+(self.seconds if seconds is None else seconds)/DAY_SECONDS
        p,v=_body_state(body,days,self.catalog);return vector(p),vector(v)

    def snapshot(self):
        return state(self.r,self.v,self.epoch+timedelta(seconds=self.seconds),mass=self.mass,propellant=self.propellant)

    def point(self,phase):
        return {**self.snapshot(),"elapsedDays":self.seconds/DAY_SECONDS,"phase":phase}

    def burn(self,new_velocity,kind):
        before=self.snapshot();begin=len(self.points)-1
        burn=maneuver(before,new_velocity,kind,isp_seconds=self.isp,available_propellant=self.propellant)
        if burn["vehicleFeasible"] is False:
            self.vehicle_valid=False;self.warnings.append(f"{kind}: Treibstoff reicht für {burn['deltaVKmS']:.3f} km/s nicht aus; nur geplanter Zustand.")
        if burn["vehicleFeasible"]:
            self.mass=burn["stateAfter"]["massKg"];self.propellant=burn["stateAfter"]["propellantKg"]
        self.v=vector(new_velocity);self.maneuvers.append(burn);self.events.append({**burn,"elapsedDays":self.seconds/DAY_SECONDS})
        self.points.append(self.point(kind))
        self.segments.append({"id":f"maneuver-{len(self.maneuvers)}","label":kind,"phase":kind,"frame":"ECLIPJ2000",
            "startIndex":begin,"endIndex":len(self.points)-1,"startState":before,"endState":self.snapshot(),"status":"planned" if burn["vehicleFeasible"] is False else "validated"})

    def coast(self,duration,center,phase,*,radius=None,direction=0,periapsis=False,aphelion=False,count=None):
        body=self.get_body(center);p,w=self.body(center);relative=(self.r-p,self.v-w)
        before=self.snapshot();begin=len(self.points)-1;begin_seconds=self.seconds
        collisions=[]
        from planner.generic_route_planner import KNOWN_MOON_RADII_KM
        for other in self.catalog.values():
            if other.id==center or (other.kind=='moon' and other.id not in KNOWN_MOON_RADII_KM):continue
            def check(t,y,other=other):
                cp,_=self.body(center,begin_seconds+t);bp,_=self.body(other.id,begin_seconds+t)
                return float(np.linalg.norm(cp+y[:3]-bp))-other.radius_km
            collisions.append((other.id,check))
        result=propagate_conic(relative,duration,body.mass_kg*G_KM3_KG_S2,
            sample_count=count or self.values["simulation"]["sampleTrajectoryPoints"],
            stop_radius_km=radius,crossing_direction=direction,stop_periapsis=periapsis,
            collision_radius_km=body.radius_km,gravity_acceleration=self.gravity(center,begin_seconds) if self.full_gravity else None,other_collisions=collisions,stop_aphelion=aphelion)
        for point in result["trajectory"][1:]:
            self.seconds=begin_seconds+point["elapsedSeconds"];p,w=self.body(center)
            self.r=p+vector(point["positionKm"]);self.v=w+vector(point["velocityKmS"])
            self.points.append(self.point(phase))
        self.collision|=result["collision"]
        self.segments.append({"id":f"segment-{len(self.segments)+1}","label":phase,"phase":phase,
            "frame":"ECLIPJ2000","dynamicsCenterBodyId":center,"startIndex":begin,"endIndex":len(self.points)-1,
            "startState":before,"endState":self.snapshot(),"status":"collision" if result["collision"] else "validated"})
        self.events.extend({"type":e["type"].upper(),"bodyId":center,"elapsedDays":(begin_seconds+e["elapsedSeconds"])/DAY_SECONDS} for e in result["events"])
        if self.collision:
            hit=next((e['type'].split(':',1)[1] for e in result['events'] if e['type'].startswith('collision:')),center)
            raise ValueError(f"Die propagierte Route kollidiert mit {self.get_body(hit).name}.")
        return result

    def gravity(self,center,begin_seconds):
        from solver.nbody_propagation import continuous_n_body_acceleration
        epoch_days=_mission_epoch_days(timestamp(self.epoch))
        def acceleration(t,y):
            seconds=begin_seconds+t;p,w=self.body(center,seconds)
            absolute=p+y[:3]
            value=vector(continuous_n_body_acceleration(tuple(absolute),epoch_days+seconds/DAY_SECONDS))
            if center!='sun':
                # Differentiate the same ephemeris velocities used to translate
                # the origin, so the inertial spacecraft state remains continuous.
                _,vm=self.body(center,seconds-30);_,vp=self.body(center,seconds+30)
                value-=(vp-vm)/60
            return value
        return acceleration

    def escape(self,center,outbound_direction,excess_speed=1.,kind="DEPARTURE_BURN"):
        if center=="sun":return
        body=self.get_body(center);p,w=self.body(center);r=self.r-p;v=self.v-w
        radius=float(np.linalg.norm(r));mu=body.mass_kg*G_KM3_KG_S2
        boundary=_entry_radius(body,self.catalog)
        if radius>=boundary*.999:return
        radial=unit(r);tangent=v-np.dot(v,radial)*radial
        speed=sqrt(excess_speed**2+2*mu/radius)
        self.burn(w+speed*unit(tangent),kind)
        result=self.coast(max(10*DAY_SECONDS,4*boundary/max(excess_speed,.1)),center,"SOI_EXIT",radius=boundary,direction=1)
        if not any(e["type"]=="radius-crossing" for e in result["events"]):raise ValueError("SOI-Austritt nicht erreicht.")
        self.local_body=None

    def transfer(self,target,arrival=None,branch_index=0,departure_kind="TRANSFER_INJECTION"):
        target=dict(target)
        aimpoint=target.get('aimpoint') or {}
        if aimpoint.get('enabled'):
            if aimpoint.get('role','periapsis') not in {'periapsis','periapsis_point'}:raise ValueError('Physikalischer Aimpoint benötigt Periapsis-Rolle; Entry/Exit benötigt einen expliziten Zustandsvektor.')
            if aimpoint.get('altitudeKm') is not None:target['periapsisAltitudeKm']=_number(aimpoint['altitudeKm'],100)
        if target.get("bodyId")=="sun":return self.solar_passage(target)
        body=self.get_body(target.get("bodyId"));origin=self.get_body(self.local_body or (self.sections[-1]['targetId'] if self.sections else self.values["start"].get("bodyId","sun")))
        leg_begin=len(self.points)-1;leg_burn_begin=len(self.maneuvers)
        central=_local_central_body(origin,body,self.catalog)
        center=central.id if central else "sun";mu=self.get_body(center).mass_kg*G_KM3_KG_S2
        flight_days=_number(target.get("flightDays"),3 if central else 260)
        end_seconds=(utc(arrival)-self.epoch).total_seconds() if arrival else self.seconds+flight_days*DAY_SECONDS
        if self.local_body and self.local_body!=center:
            next_p,_=self.body(body.id,end_seconds)
            departure_excess=target.get("departureVInfinityKmS")
            if departure_excess is None and center=='sun':
                op,ov=self.body(self.local_body)
                seeds=_lambert_candidates(tuple(op),tuple(next_p),end_seconds-self.seconds)
                departure_excess=min(np.linalg.norm(vector(b["departure"])-ov) for b in seeds)
            self.escape(self.local_body,next_p-self.r,_number(departure_excess,1),target.get('departureBurnName',departure_kind))
        duration=end_seconds-self.seconds
        if duration<=0:raise ValueError("Ankunft liegt vor dem tatsächlichen Abflugzustand.")
        cp,cw=self.body(center);tp,tv=self.body(body.id,end_seconds)
        acp,_=self.body(center,end_seconds)
        corridor=_parse_entry_corridor(target.get("entryCorridor") or {})
        boundary=_number(target.get("entryRadiusKm"),_entry_radius(body,self.catalog))
        near=unit((self.r-cp)-(tp-acp))
        direction=unit(corridor["centerDirection"]) if corridor["enabled"] else near
        desired_peri=body.radius_km+_number(target.get("periapsisAltitudeKm",target.get("orbitAltitudeKm",target.get("flybyAltitudeKm"))),100)
        needs_local=target["type"] in {"body_orbit","flyby"}
        if body.kind=='moon' and target['type']=='body_orbit':
            parent=self.get_body(body.parent_id);data=body.moon_elements or {}
            hill=float(data['semiMajorAxisKm'])*(1-float(data.get('eccentricity',0)))*(body.mass_kg/(3*parent.mass_kg))**(1/3)
            stable=hill*(.9 if target.get('orbitDirection')=='retrograde' else .49)
            apo=body.radius_km+_number(target.get('apoapsisAltitudeKm',target.get('orbitAltitudeKm')),100)
            if max(desired_peri,apo)>stable:raise ValueError(f'Der Zielorbit um {body.name} liegt außerhalb der konservativen Hill-Stabilitätsgrenze; N-Körper-Optimierung erforderlich.')
        start_r=self.r-cp;reference=self.v-cw
        if body.id==center and needs_local:
            boundary=min(_entry_radius(body,self.catalog),max(desired_peri*2,float(np.linalg.norm(start_r))*.25))
        preferred_branch=None;preferred_normal=None
        def select(entry_direction):
            endpoint=tp+boundary*unit(entry_direction)-acp
            branches=_lambert_candidates(tuple(start_r),tuple(endpoint),duration,mu)
            if needs_local or target['type']=='earth_reentry':
                inbound=[b for b in branches if np.dot(unit(entry_direction),vector(b['arrival'])+self.body(center,end_seconds)[1]-tv)<0]
                if not inbound:raise ValueError('Keine einlaufende Lambert-Lösung am Ankunftsrand.')
                branches=inbound
            branches.sort(key=lambda b:np.linalg.norm(vector(b["departure"])-reference))
            matching=[b for b in branches if preferred_branch is not None and b["revolutionFamily"]==preferred_branch[1]
                      and np.dot(np.cross(start_r,vector(b["departure"])),preferred_normal)>0]
            selected=matching[0] if matching else branches[min(branch_index,len(branches)-1)]
            relative_arrival=vector(selected["arrival"])+(self.body(center,end_seconds)[1])-tv
            return selected,endpoint,relative_arrival
        if needs_local and not corridor["enabled"] and body.mass_kg>0:
            if body.id==center:
                plane=unit(np.cross(start_r,reference));hint=unit(near+.05*np.cross(plane,near))
                center_branches=_lambert_candidates(tuple(start_r),tuple(boundary*hint),duration,mu)
            else:center_branches=_lambert_candidates(tuple(start_r),tuple(tp-acp),duration,mu)
            center_branches.sort(key=lambda b:np.linalg.norm(vector(b["departure"])-reference))
            seed=center_branches[min(branch_index,len(center_branches)-1)]
            preferred_branch=(seed["transferSide"],seed["revolutionFamily"])
            preferred_normal=unit(np.cross(start_r,vector(seed["departure"])))
            seed_velocity=vector(seed["arrival"])+self.body(center,end_seconds)[1]-tv
            near=unit(start_r) if body.id==center else -unit(seed_velocity)
            axis=unit(np.cross(near,[0,0,1] if abs(near[2])<.9 else [0,1,0]));other=np.cross(near,axis)
            clock=_number((target.get("aimpoint") or {}).get("clockAngleDeg"),0)*pi/180
            tangent=cos(clock)*axis+sin(clock)*other
            normal=unit(np.cross(near,tangent));second=unit(np.cross(normal,tangent))
            def residual(x):
                trial=unit(near+x[0]*tangent+x[1]*other)
                try:
                    _,_,rv=select(trial);el=elements(boundary*trial,rv,body.mass_kg*G_KM3_KG_S2)
                    h=unit(np.cross(trial,rv));plane_residual=float(np.dot(h,tangent))
                    if target['type']=='body_orbit' and target.get('inclinationDeg') is not None:
                        inc,node=np.radians([float(target['inclinationDeg']),float(target.get('raanDeg',0))]);wanted=np.array([sin(inc)*sin(node),-sin(inc)*cos(node),cos(inc)])
                        evec=np.cross(rv,np.cross(boundary*trial,rv))/(body.mass_kg*G_KM3_KG_S2)-trial
                        plane_residual=float(np.dot(unit(evec),wanted))
                    return [(el["periapsisRadiusKm"]-desired_peri)/max(desired_peri,1),plane_residual]
                except (ValueError,RuntimeError):return [1e6,1e6]
            seed_speed=max(float(np.linalg.norm(seed_velocity)),.01)
            impact=desired_peri*sqrt(1+2*body.mass_kg*G_KM3_KG_S2/(desired_peri*seed_speed**2))
            fit=least_squares(residual,[min(1.8,impact/boundary),0.],bounds=([-2,-2],[2,2]),max_nfev=100,xtol=1e-11,ftol=1e-11,gtol=1e-11)
            direction=unit(near+fit.x[0]*tangent+fit.x[1]*other)
        if target['type']=='earth_reentry' and target.get('entryFlightPathAngleDeg') is not None:
            normal=unit(np.cross(start_r,reference));axis=unit(np.cross(normal,near))
            def entry_residual(x):
                d=cos(x[0])*near+sin(x[0])*axis
                try:
                    _,_,rv=select(d)
                    fpa=np.degrees(np.arcsin(np.clip(np.dot(d,rv)/np.linalg.norm(rv),-1,1)))
                    return [(fpa-_number(target['entryFlightPathAngleDeg'],-6.5))/10]
                except (ValueError,RuntimeError):return [1e6]
            fits=[least_squares(entry_residual,[guess],bounds=([-3.14],[3.14]),max_nfev=45) for guess in (.8,-.8,2.,-2.)]
            fit=min(fits,key=lambda f:np.linalg.norm(f.fun));direction=cos(fit.x[0])*near+sin(fit.x[0])*axis
        selected,endpoint,relative_arrival=select(direction)
        correction=None
        if self.full_gravity:
            acceleration=self.gravity(center,self.seconds)
            def shooting(v):
                trial=propagate_conic((start_r,v),duration,mu,sample_count=2,gravity_acceleration=acceleration)
                return (vector(trial['finalPositionKm'])-endpoint)/1000.
            correction=least_squares(shooting,vector(selected['departure']),diff_step=1e-5,max_nfev=35,xtol=1e-10,ftol=1e-10,gtol=1e-10)
            selected={**selected,'departure':correction.x.tolist()}
            shot=propagate_conic((start_r,correction.x),duration,mu,sample_count=2,gravity_acceleration=acceleration)
            relative_arrival=vector(shot['finalVelocityKmS'])+self.body(center,end_seconds)[1]-tv
        before=self.snapshot();burn_start=len(self.maneuvers);begin=len(self.points)-1
        self.burn(cw+vector(selected["departure"]),departure_kind)
        self.coast(duration,center,"TRANSFER")
        actual_center,actual_body_v=self.body(body.id)
        relative_position=self.r-actual_center;residual=float(np.linalg.norm(relative_position-boundary*direction))
        actual_dir=unit(relative_position)
        horizontal,vertical=_corridor_coordinates_deg(tuple(actual_dir),tuple(corridor['centerDirection']),corridor['rotationDeg'])
        inside=not corridor['enabled'] or (abs(horizontal)<=corridor['horizontalHalfAngleDeg']+1e-6 and abs(vertical)<=corridor['verticalHalfAngleDeg']+1e-6)
        entry_index=len(self.points)-1;self.local_body=body.id
        vinf_sq=max(0.,float(np.linalg.norm(self.v-actual_body_v))**2-2*body.mass_kg*G_KM3_KG_S2/max(boundary,1))
        info={"id":target.get("id",f"leg-{len(self.sections)+1}"),"originId":origin.id,"targetId":body.id,"targetName":body.name,
            "sectionType":"lambert-body-to-body" if center=="sun" else f"{self.get_body(center).name}-zentrierter Transfer",
            "transferStartIndex":leg_begin,"entryIndex":entry_index,"periapsisIndex":entry_index,"exitIndex":entry_index,
            "entryDay":self.seconds/DAY_SECONDS,"periapsisDay":self.seconds/DAY_SECONDS,"exitDay":self.seconds/DAY_SECONDS,
            "entryPositionKm":self.r.tolist(),"entryDirection":actual_dir.tolist(),"exitPositionKm":self.r.tolist(),"exitVelocityKmS":self.v.tolist(),
            "minimumAltitudeKm":float(np.linalg.norm(relative_position))-body.radius_km,
            "requiredTransitionDeltaVKmS":float(np.linalg.norm(vector(selected["departure"])-reference)),"requiredPassageDeltaVKmS":0.,
            "requiredSectionDeltaVKmS":0.,"lambertEndpointResidualKm":residual,"lambertVelocityResidualKmS":float(np.linalg.norm(self.v-actual_body_v-relative_arrival)),
            "arrivalVInfinityKmS":sqrt(vinf_sq),"corridor":{"enabled":corridor["enabled"],"centerDirection":list(direction),"horizontalHalfAngleDeg":corridor["horizontalHalfAngleDeg"],"verticalHalfAngleDeg":corridor["verticalHalfAngleDeg"],"rotationDeg":corridor["rotationDeg"],"entryInsideCorridor":inside,"actualEntryPositionKm":self.r.tolist()},
            "passage":target.get("passage",{"mode":"direct"}),"physicalSegments":[],
            'lambertBranch':{k:selected[k] for k in ('transferSide','revolutionFamily','motion')},'entryEpochUtc':timestamp(self.epoch+timedelta(seconds=self.seconds))}
        if correction is not None:info['differentialCorrection']={'success':bool(correction.success),'entryResidualKm':float(np.linalg.norm(correction.fun)*1000),'evaluations':int(correction.nfev)}
        info["targetConditionSatisfied"]=residual<=_number(self.values["constraints"].get("positionToleranceKm"),.1) and inside
        if needs_local:self.encounter(body,target,info)
        elif target["type"]=="earth_reentry":
            radial_speed=float(np.dot(self.r-actual_center,self.v-actual_body_v))/np.linalg.norm(self.r-actual_center)
            actual_angle=np.degrees(np.arcsin(np.clip(radial_speed/max(np.linalg.norm(self.v-actual_body_v),1e-30),-1,1)))
            info["entryFlightPathAngleDeg"]=float(actual_angle);info["targetConditionSatisfied"] &= radial_speed<0
            if target.get("entryFlightPathAngleDeg") is not None:
                info["targetConditionSatisfied"] &= abs(actual_angle-target["entryFlightPathAngleDeg"])<=_number(target.get("entryAngleToleranceDeg"),1.)
            self.events.append({"type":"EARTH_ENTRY_INTERFACE","elapsedDays":self.seconds/DAY_SECONDS,"altitudeKm":boundary-body.radius_km,"flightPathAngleDeg":float(actual_angle),"state":self.snapshot()})
        info["requiredSectionDeltaVKmS"]=sum(b["deltaVKmS"] for b in self.maneuvers[leg_burn_begin:])
        info['targetConditionSatisfied']=bool(info['targetConditionSatisfied'])
        self.sections.append(info);return info

    def solar_passage(self,target):
        origin=self.local_body or "sun";begin=len(self.points)-1;burn_start=len(self.maneuvers)
        if self.local_body and self.local_body!="sun":self.escape(self.local_body,-self.v,20.,"EARTH_ESCAPE" if origin=="earth" else "BODY_ESCAPE")
        radius=float(np.linalg.norm(self.r));peri=SUN_RADIUS_KM+_number(target.get("flybyAltitudeKm"),(self.values.get("mission") or {}).get("targetPerihelionAu",.05)*AU_KM-SUN_RADIUS_KM)
        if not SUN_RADIUS_KM<peri<radius:raise ValueError("Solar-Perihel muss außerhalb der Sonne und innerhalb des Startradius liegen.")
        radial=unit(self.r);tangent=unit(self.v-np.dot(self.v,radial)*radial)
        self.burn(sqrt(2*MU_SUN*peri/(radius*(radius+peri)))*tangent,"SUNDIVER_INJECTION")
        self.coast(2*pi*sqrt(((radius+peri)/2)**3/MU_SUN),"sun","SOLAR_APPROACH",periapsis=True)
        peri_index=len(self.points)-1;entry_day=self.seconds/DAY_SECONDS;actual_peri=float(np.linalg.norm(self.r))
        burn=_number(target.get("burnDeltaVKmS"),(self.values.get("mission") or {}).get("oberthDeltaVKmS",0))
        desired_exit=target.get('desiredExitSpeedKmS')
        if desired_exit is not None:
            requested=float(desired_exit)
            if not isfinite(requested) or requested<=0:raise ValueError('Solar-Austrittsgeschwindigkeit muss positiv sein.')
            desired_peri=sqrt(requested**2+2*MU_SUN*(1/actual_peri-1/AU_KM))
            burn=desired_peri-float(np.linalg.norm(self.v))
            if abs(burn)>_number(target.get('maximumBurnDeltaVKmS'),0)+1e-8:
                self.vehicle_valid=False;self.warnings.append('Solar-Austrittsgeschwindigkeit erfordert mehr Oberth-Δv als konfiguriert.')
        if burn:self.burn(self.v+burn*unit(self.v),"SOLAR_OBERTH")
        completed_revs=0.
        if target['type']=='body_orbit':
            self.burn(sqrt(MU_SUN/actual_peri)*unit(self.v),'SOLAR_ORBIT_INSERTION')
            period=2*pi*sqrt(actual_peri**3/MU_SUN);completed_revs=_number(target.get('requiredRevolutions'),1)
            if completed_revs:self.coast(completed_revs*period,'sun','BOUND_ORBIT',count=max(120,int(completed_revs*180)))
        el=elements(self.r,self.v,MU_SUN)
        exit_radius=AU_KM if desired_exit is not None else min(radius,AU_KM)
        if target['type'] in {'body','body_orbit'}:reached=True
        else:
            at_aphelion=el['bound'] and el['apoapsisRadiusKm']<=exit_radius*(1+1e-7)
            outward=self.coast(max(4*DAY_SECONDS,2*pi*sqrt(((radius+peri)/2)**3/MU_SUN)),"sun","SOLAR_EXIT",radius=None if at_aphelion else exit_radius,direction=1,aphelion=at_aphelion)
            reached=any(e['type'] in {'radius-crossing','aphelion'} for e in outward['events'])
        info={"id":target.get("id",f"solar-{len(self.sections)+1}"),"originId":origin,"targetId":"sun","targetName":"Sonne","sectionType":"physical-solar-oberth",
              "transferStartIndex":begin,"entryIndex":peri_index,"periapsisIndex":peri_index,"exitIndex":len(self.points)-1,"entryDay":entry_day,"periapsisDay":entry_day,"exitDay":self.seconds/DAY_SECONDS,
              "entryPositionKm":self.points[peri_index]["positionKm"],"entryDirection":unit(self.points[peri_index]["positionKm"]).tolist(),"exitPositionKm":self.r.tolist(),"exitVelocityKmS":self.v.tolist(),
              "minimumAltitudeKm":actual_peri-SUN_RADIUS_KM,"periapsisResidualKm":abs(actual_peri-peri),"requiredTransitionDeltaVKmS":sum(b["deltaVKmS"] for b in self.maneuvers[burn_start:]),"requiredPassageDeltaVKmS":burn,
              "requiredSectionDeltaVKmS":sum(b["deltaVKmS"] for b in self.maneuvers[burn_start:]),"lambertEndpointResidualKm":abs(actual_peri-peri),"arrivalVInfinityKmS":0.,"passage":{"mode":"direct"},
              "corridor":{"enabled":False,"centerDirection":unit(self.points[peri_index]["positionKm"]).tolist(),"horizontalHalfAngleDeg":0.,"verticalHalfAngleDeg":0.,"rotationDeg":0.,"entryInsideCorridor":True},
              "targetConditionSatisfied":reached and abs(actual_peri-peri)<.1,"orbitElements":el,'completedRevolutions':completed_revs}
        if desired_exit is not None:
            actual_exit=float(np.linalg.norm(self.v));residual=abs(actual_exit-float(desired_exit));at_radius=abs(np.linalg.norm(self.r)-AU_KM)<.1
            info['solarExitSpeedKmS']=actual_exit;info['solarExitSpeedResidualKmS']=residual
            info['targetConditionSatisfied'] &= at_radius and residual<=.25
            self.solar_boundary={'definition':'outbound 1 AU geometric crossing','radiusAu':1.,'desiredExitSpeedKmS':float(desired_exit),'actualExitSpeedKmS':actual_exit,'speedResidualKmS':residual,'toleranceKmS':.25,'speedBoundaryReached':bool(at_radius and residual<=.25),'energeticallyReachable':abs(burn)<=_number(target.get('maximumBurnDeltaVKmS'),0)+1e-8,'requiredOberthVectorDeltaVKmS':abs(burn),'availableOberthDeltaVKmS':target.get('maximumBurnDeltaVKmS'),'entryElapsedDays':entry_day,'perihelionElapsedDays':entry_day,'perihelionPositionKm':self.points[peri_index]['positionKm'],'entryPositionKm':self.points[peri_index]['positionKm'],'entryDate':self.points[peri_index]['epochUtc'],'perihelionDateTime':self.points[peri_index]['epochUtc']}
        flux=1361/(actual_peri/AU_KM)**2;info['maximumSolarFluxWm2']=flux
        mission=self.values.get('mission') or {}
        if mission.get('heatshieldLimitWm2') is not None and (flux>float(mission['heatshieldLimitWm2']) or mission.get('heatshieldEnabled') is False):
            self.vehicle_valid=False;self.warnings.append('Solarpassage überschreitet den konfigurierten thermischen Schutz.')
        corridor=_parse_entry_corridor(target.get('entryCorridor') or {})
        if corridor['enabled']:
            horizontal,vertical=_corridor_coordinates_deg(tuple(info['entryDirection']),tuple(corridor['centerDirection']),corridor['rotationDeg'])
            inside=abs(horizontal)<=corridor['horizontalHalfAngleDeg'] and abs(vertical)<=corridor['verticalHalfAngleDeg']
            info['corridor']={**corridor,'entryInsideCorridor':bool(inside)};info['targetConditionSatisfied'] &= bool(inside)
        info['passage']=target.get('passage',{'mode':'direct'})
        self.sections.append(info);self.local_body=None;return info

    def encounter(self,body,target,info):
        p,w=self.body(body.id);relative_r=self.r-p;relative_v=self.v-w;mu=body.mass_kg*G_KM3_KG_S2
        if mu<=0:raise ValueError(f"Für {body.name} fehlt ein belastbares GM für lokale Dynamik.")
        el=elements(relative_r,relative_v,mu)
        if np.dot(relative_r,relative_v)>=0:info["targetConditionSatisfied"]=False;self.warnings.append("Ankunft befindet sich auf dem ausgehenden Ast.");return
        if el["periapsisRadiusKm"]<=body.radius_km:raise ValueError(f"Ankunftsbahn kollidiert mit {body.name}.")
        result=self.coast(max(30*DAY_SECONDS,10*float(np.linalg.norm(relative_r))/max(np.linalg.norm(relative_v),.01)),body.id,"LOCAL_APPROACH",periapsis=True)
        if not any(e["type"]=="periapsis" for e in result["events"]):info["targetConditionSatisfied"]=False;return
        info["periapsisIndex"]=len(self.points)-1;info["periapsisDay"]=self.seconds/DAY_SECONDS
        p,w=self.body(body.id);r=self.r-p;v=self.v-w;rp=float(np.linalg.norm(r));info["minimumAltitudeKm"]=rp-body.radius_km
        requested=body.radius_km+_number(target.get("periapsisAltitudeKm",target.get("orbitAltitudeKm",target.get("flybyAltitudeKm"))),100)
        info["periapsisResidualKm"]=abs(rp-requested)
        info["targetConditionSatisfied"] &= abs(rp-requested)<=_number(self.values["constraints"].get("orbitAltitudeToleranceKm"),1.)
        if target["type"]=="body_orbit":
            apo=body.radius_km+_number(target.get("apoapsisAltitudeKm",target.get("orbitAltitudeKm")),100)
            if apo<rp-1.:info["targetConditionSatisfied"]=False;apo=rp
            tangent=unit(v-np.dot(v,unit(r))*unit(r));speed=sqrt(mu*(2/rp-2/(rp+max(apo,rp))))
            if target.get('inclinationDeg') is not None:
                inc,node=np.radians([float(target['inclinationDeg']),float(target.get('raanDeg',0))]);wanted=np.array([sin(inc)*sin(node),-sin(inc)*cos(node),cos(inc)])
                if abs(np.dot(unit(r),wanted))>1e-5:info['targetConditionSatisfied']=False
                else:tangent=unit(np.cross(wanted,unit(r)))
            if target.get("orbitDirection")=="retrograde":tangent=-tangent
            before_dv=sum(b["deltaVKmS"] for b in self.maneuvers)
            self.burn(w+speed*tangent,target.get("captureBurnName","ORBIT_INSERTION"))
            orbit=elements(r,self.v-w,mu);revs=_number(target.get("requiredRevolutions"),1.)
            if revs<0:raise ValueError("Anzahl Umläufe muss >= 0 sein.")
            orbit_begin=len(self.points)-1
            if revs:self.coast(revs*orbit["periodSeconds"],body.id,"BOUND_ORBIT",count=max(120,int(revs*180)))
            p,w=self.body(body.id);end_elements=elements(self.r-p,self.v-w,mu)
            normal=unit(np.cross(r,v));angles=[]
            for sample in self.points[orbit_begin:]:
                elapsed=(utc(sample['epochUtc'])-self.epoch).total_seconds();bp,_=self.body(body.id,elapsed)
                rr=unit(vector(sample['positionKm'])-bp)
                angles.append(np.arctan2(np.dot(rr,np.cross(normal,unit(r))),np.dot(rr,unit(r))))
            measured_revs=abs(float(np.unwrap(angles)[-1]-np.unwrap(angles)[0]))/(2*pi) if len(angles)>1 else 0.
            info["orbitElements"]=end_elements;info["completedRevolutions"]=measured_revs
            info['targetConditionSatisfied'] &= measured_revs+1e-6>=revs
            info["targetConditionSatisfied"] &= end_elements["bound"] and end_elements["periapsisRadiusKm"]>body.radius_km
            if target.get("inclinationDeg") is not None:info["targetConditionSatisfied"] &= abs(end_elements["inclinationDeg"]-target["inclinationDeg"])<=_number(self.values["constraints"].get("inclinationToleranceDeg"),.1)
            if target.get("raanDeg") is not None:
                measured=end_elements["raanDeg"]
                info["targetConditionSatisfied"] &= measured is not None and abs((measured-target["raanDeg"]+180)%360-180)<=_number(self.values["constraints"].get("raanToleranceDeg"),.1)
            info["requiredPassageDeltaVKmS"]=sum(b["deltaVKmS"] for b in self.maneuvers)-before_dv
            self.events.append({"type":"ORBIT_COMPLETED","bodyId":body.id,"completedRevolutions":revs,"elapsedDays":self.seconds/DAY_SECONDS,"orbitElements":end_elements})
        else:
            if target.get("burnDeltaVKmS"):
                self.burn(w+(np.linalg.norm(v)+_number(target["burnDeltaVKmS"],0))*unit(v),"POWERED_FLYBY")
            boundary=_entry_radius(body,self.catalog)
            self.coast(max(60*DAY_SECONDS,10*boundary/max(np.linalg.norm(v),.1)),body.id,"FLYBY_EXIT",radius=boundary,direction=1)
            p,w=self.body(body.id);out_el=elements(self.r-p,self.v-w,mu)
            info["flybyEnergyResidualKm2S2"]=abs(out_el["specificEnergyKm2S2"]-el["specificEnergyKm2S2"])
            info["targetConditionSatisfied"] &= out_el["specificEnergyKm2S2"]>0
            self.local_body=None
        info["exitIndex"]=len(self.points)-1;info["exitDay"]=self.seconds/DAY_SECONDS
        info["exitPositionKm"]=self.r.tolist();info["exitVelocityKmS"]=self.v.tolist()

    def outbound(self,target):
        begin=len(self.points)-1;burn_begin=len(self.maneuvers);origin=self.sections[-1]['targetId'] if self.sections else self.values['start'].get('bodyId','sun')
        if self.local_body and self.local_body!="sun":self.escape(self.local_body,self.r,1.)
        initial_v=self.v.copy();radius=float(np.linalg.norm(self.r));desired=_number(target.get("vInfinityKmS"),15.)
        if target["type"]=="direction":
            direction=vector(_direction(target));speed=sqrt(desired**2+2*MU_SUN/radius)
            def asymptote(v):
                result=_solar_asymptote_direction(tuple(self.r),tuple(v))
                if result is None and np.dot(self.r,v)>0 and np.linalg.norm(np.cross(self.r,v))<1e-5:return unit(self.r)
                return vector(result) if result else None
            def residual(v):
                a=asymptote(v);energy=np.dot(v,v)/2-MU_SUN/radius
                return np.r_[((a-direction) if a is not None else np.ones(3)*10), (energy-desired**2/2)/max(speed**2,1)]
            guess=direction*speed+unit(np.cross(direction,[0,0,1]) if abs(direction[2])<.9 else np.cross(direction,[0,1,0]))*.001
            fit=least_squares(residual,guess,max_nfev=100,xtol=1e-11,ftol=1e-11,gtol=1e-11)
            self.burn(fit.x,"DIRECTION_INJECTION");achieved=asymptote(self.v)
            alignment=angle(achieved,direction) if achieved is not None else 180.
            distance=_number(target.get("distanceAU"),50.)
        else:
            radial=unit(self.r);vr=float(np.dot(self.v,radial));vt2=float(np.dot(self.v,self.v)-vr*vr)
            if float(np.dot(self.v,self.v)/2-MU_SUN/radius)<desired**2/2:
                required_vr=sqrt(max(0.,desired**2+2*MU_SUN/radius-vt2))
                self.burn(self.v+(required_vr-vr)*radial,"SOLAR_ESCAPE_INJECTION")
            if target["type"]=="zone":
                zone=ZONE_DEFINITIONS.get(target.get("zoneId"))
                if not zone:raise ValueError("Unbekannte Zielzone.")
                target.update(zone);distance=zone["outerRadiusAU"] if target.get("crossingEdge")=="outer" else zone["innerRadiusAU"]
            else:
                boundary=BOUNDARY_DEFINITIONS.get(target.get("boundaryId"),{})
                distance=_number(target.get("distanceAU",boundary.get("radiusAU")),0)
                if distance<=0:raise ValueError("Grenzradius muss positiv sein.")
                target.update(boundary)
            alignment=None
        if distance*AU_KM<=np.linalg.norm(self.r):raise ValueError("Start liegt bereits außerhalb der angeforderten Grenze; kein ausgehender Übertritt.")
        horizon=self.values["simulation"]["propagationYears"]*365.25*DAY_SECONDS
        result=self.coast(horizon,"sun","OUTBOUND",radius=distance*AU_KM,direction=1)
        reached=any(e["type"]=="radius-crossing" for e in result["events"])
        if target["type"]=="direction":
            reached &= alignment<=_number(self.values["constraints"].get("targetToleranceDeg"),5.)
            target["direction"]=direction.tolist();self.target_alignment=alignment
        self.events.append({"type":"ZONE_ENTRY" if target["type"]=="zone" else "BOUNDARY_REACHED","elapsedDays":self.seconds/DAY_SECONDS,"distanceAU":float(np.linalg.norm(self.r))/AU_KM,"occurred":bool(reached)})
        if target['type']=='zone' and reached and target.get('arrivalMode')=='crossing' and distance==target['innerRadiusAU']:
            target['zoneEntryDate']=timestamp(self.epoch+timedelta(seconds=self.seconds));target['zoneEntryDistanceAU']=distance
            distance=target['outerRadiusAU'];entry_seconds=self.seconds
            crossing=self.coast(max(1.,horizon-result['durationSeconds']),'sun','ZONE_TRAVERSE',radius=distance*AU_KM,direction=1)
            reached=any(e['type']=='radius-crossing' for e in crossing['events'])
            if reached:
                target['zoneExitDate']=timestamp(self.epoch+timedelta(seconds=self.seconds));target['zoneExitDistanceAU']=distance
                self.events.append({'type':'ZONE_EXIT','elapsedDays':self.seconds/DAY_SECONDS,'distanceAU':distance,'occurred':True})
        self.target_reached=bool(reached);target["distanceAU"]=distance
        if target.get('id'):
            index=len(self.points)-1;position=self.r.tolist();direction_now=unit(self.r).tolist()
            self.sections.append({'id':target['id'],'originId':origin,'targetId':target.get('bodyId',target['type']),'targetName':target.get('bodyId',target['type']),
                'sectionType':'interstellar-asymptote' if target['type']=='direction' else target['type'],'hypothetical':target['type']=='direction',
                'transferStartIndex':begin,'entryIndex':index,'periapsisIndex':index,'exitIndex':index,'entryDay':self.seconds/DAY_SECONDS,'periapsisDay':self.seconds/DAY_SECONDS,'exitDay':self.seconds/DAY_SECONDS,
                'entryPositionKm':position,'exitPositionKm':position,'exitVelocityKmS':self.v.tolist(),'entryDirection':direction_now,
                'requiredTransitionDeltaVKmS':sum(b['deltaVKmS'] for b in self.maneuvers[burn_begin:]),'requiredPassageDeltaVKmS':0.,'requiredSectionDeltaVKmS':sum(b['deltaVKmS'] for b in self.maneuvers[burn_begin:]),
                'lambertEndpointResidualKm':abs(float(np.linalg.norm(self.r))-distance*AU_KM),'targetConditionSatisfied':bool(reached),
                'corridor':{'enabled':False,'entryInsideCorridor':True},'passage':{'mode':'direct'},'arrivalVInfinityKmS':desired})

    def to_result(self,target,mode):
        chain=continuity(self.segments);total=self.ascent_dv+sum(b["deltaVKmS"] for b in self.maneuvers)
        departure_vinf=0.;c3=0.
        if self.maneuvers:
            first=self.maneuvers[0]['stateAfter']
            center=self.values['start'].get('bodyId') or self.values['start'].get('centerBodyId','sun')
            if self.values['start']['type']=='launch_site':center='earth'
            body=self.get_body(center);elapsed=(utc(first['epochUtc'])-self.epoch).total_seconds();p,w=self.body(center,elapsed)
            c3=2*elements(vector(first['positionKm'])-p,vector(first['velocityKmS'])-w,body.mass_kg*G_KM3_KG_S2)['specificEnergyKm2S2']
            departure_vinf=sqrt(max(0.,c3))
        summary={"totalFlightDays":self.seconds/DAY_SECONDS,"totalDeltaVKmS":total,
            "requiredInjectionDeltaVKmS":self.maneuvers[0]["deltaVKmS"] if self.maneuvers else 0.,'ascentDeltaVKmS':self.ascent_dv,
            "c3Km2S2":c3,"departureVInfinityKmS":departure_vinf,
            "arrivalVInfinityKmS":self.sections[-1].get("arrivalVInfinityKmS",0) if self.sections else 0.,
            "finalHeliocentricSpeedKmS":float(np.linalg.norm(self.v)),"targetReached":self.target_reached,
            "targetReachedDate":timestamp(self.epoch+timedelta(seconds=self.seconds)) if self.target_reached else None,
            "targetReachedDistanceAU":float(np.linalg.norm(self.r))/AU_KM,"endpointResidualKm":max([s.get("lambertEndpointResidualKm",0) for s in self.sections] or [0]),
            "model":('continuous Sun/planet/major-moon gravity with corrected transfer' if self.full_gravity else 'event-resolved patched conics')+'; geometric SPICE ephemerides; impulsive maneuvers',
            "vehicleValidated":self.mass is not None,"vehicleFeasible":self.vehicle_valid if self.mass is not None else None,
            "targetAlignmentDeg":getattr(self,"target_alignment",None),"calculationVersion":SCHEMA_VERSION}
        metrics={**summary,"flightDays":summary["totalFlightDays"]}
        constraints_ok,reasons=_constraints(metrics,self.values["constraints"]);self.warnings.extend(reasons)
        qualities={}
        for body_id in self.bodies:
            qualities[body_id]=body_quality(body_id,_mission_epoch_days(timestamp(self.epoch))*DAY_SECONDS)
            end_quality=body_quality(body_id,_mission_epoch_days(timestamp(self.epoch+timedelta(seconds=self.seconds)))*DAY_SECONDS)
            from planner.generic_route_planner import KNOWN_MOON_RADII_KM
            physical=self.catalog[body_id]
            qualities[body_id]['physicalPropertiesKnown']=physical.mass_kg>0 and (physical.kind!='moon' or body_id in KNOWN_MOON_RADII_KM)
            qualities[body_id]["coversMissionInterval"]=qualities[body_id]["available"] and end_quality["available"]
        precise=all(q["available"] and q["centerExact"] and q["coversMissionInterval"] and q["physicalPropertiesKnown"] for q in qualities.values())
        if not precise:self.warnings.append("Ephemeriden oder Körpermittelpunkte unvollständig: nur Modellvorschau, keine bestätigte Mission.")
        if self.mass is None:self.warnings.append("Ideales Impulsmodell; kein konkretes Fahrzeug und kein Treibstoffnachweis konfiguriert.")
        validation={"numericallyConverged":True,"targetReached":self.target_reached,"constraintsSatisfied":constraints_ok,
            "collisionFree":not self.collision,"stateContinuous":chain["stateChain"],"ephemeridesValidated":precise,"ephemerides":qualities,
            "vehicleValidated":self.mass is not None,"vehicleFeasible":self.vehicle_valid,
            "minimumSolarRadiusKm":min(np.linalg.norm(p["positionKm"]) for p in self.points)}
        validation.update(sunRadiusKm=SUN_RADIUS_KM,minimumSolarAltitudeKm=validation['minimumSolarRadiusKm']-SUN_RADIUS_KM)
        summary["feasible"]=bool(self.target_reached and constraints_ok and chain["stateChain"] and not self.collision and self.vehicle_valid and precise)
        summary["status"]=("valid" if self.mass is not None else 'model_valid') if summary["feasible"] else "data_unavailable" if not precise else "infeasible" if not constraints_ok or not self.vehicle_valid else "no_solution_found"
        summary['flightReady']=bool(summary['feasible'] and self.mass is not None)
        if self.sections:
            summary["entryInsideCorridor"]=all(s["corridor"]["entryInsideCorridor"] for s in self.sections)
            summary['entryCorridorTargeted']=any(s['corridor']['enabled'] for s in self.sections)
        nodes=[{"id":"start","kind":"start","positionKm":self.initial["positionKm"]}]
        legs=[]
        for i,section in enumerate(self.sections):
            ident=section["id"];nodes.append({"id":ident,"kind":"body","bodyId":section["targetId"]})
            section_segments=[s["id"] for s in self.segments if s["startIndex"]>=section["transferStartIndex"] and s["endIndex"]<=section["exitIndex"]]
            legs.append({"id":f"leg-{i+1}","from":nodes[-2]["id"],"to":ident,"physicalSegments":section_segments})
        if not self.sections or target["type"] in {"direction","zone","boundary","state_vector"}:
            nodes.append({"id":"target","kind":target["type"],**target})
            legs.append({"id":"terminal-leg","from":nodes[-2]["id"],"to":"target","physicalSegments":[s["id"] for s in self.segments]})
        result={"schemaVersion":SCHEMA_VERSION,"calculationBuild":CALCULATION_BUILD,"mode":mode,"input":self.values,"start":{"type":self.values["start"]["type"],"bodyId":self.values["start"].get("bodyId"),"date":timestamp(self.epoch),**self.initial},
            "target":target,"trajectory":self.points,"segments":self.segments,"maneuvers":self.maneuvers,"events":self.events,
            "guide":{"mode":mode,"nodes":nodes,"legs":legs},"summary":summary,"validation":validation,"continuity":chain,
            "warnings":list(dict.fromkeys(self.warnings)),"routeSections":self.sections,"stateChain":{"continuousPosition":chain["stateChain"],"exitStateFeedsNextSection":chain["stateChain"],"checks":chain["checks"]}}
        # Phase guide endpoints are taken from the real segment states.
        previous='phase-start';result['guide']['nodes'].append({'id':previous,'kind':'start','label':'Anfangszustand','state':self.initial})
        for segment in self.segments:
            node='phase-'+segment['id'];phase=segment['phase']
            kind='burn' if segment['id'].startswith('maneuver-') else 'orbit' if phase=='BOUND_ORBIT' else 'event' if phase.endswith('SEPARATION') else 'state'
            result['guide']['nodes'].append({'id':node,'kind':kind,'label':segment['label'],'state':segment['endState']})
            result['guide']['legs'].append({'id':'phase-leg-'+segment['id'],'from':previous,'to':node,'physicalSegments':[segment['id']]})
            previous=node
        terminal=next((n for n in result['guide']['nodes'] if n['id']=='target'),None)
        if terminal:
            result['guide']['nodes'].remove(terminal);result['guide']['nodes'].append(terminal)
        if hasattr(self,'solar_boundary'):result['solarBoundary']=self.solar_boundary
        if self.sections and self.sections[-1]['targetId'] in self.catalog:
            last=self.sections[-1];p,_=self.body(last["targetId"],last['entryDay']*DAY_SECONDS)
            result["target"].update({"positionKm":p.tolist(),"entryPositionKm":last["entryPositionKm"]})
            result["entryCorridor"]={**last["corridor"],"actualEntryDirection":last["entryDirection"],"actualEntryPositionKm":last["entryPositionKm"]}
        participants={self.values['start'].get('bodyId') or ('earth' if self.values['start']['type']=='launch_site' else self.values['start'].get('centerBodyId','sun'))}
        participants.update(s['targetId'] for s in self.sections if s['targetId'] in self.catalog)
        participants.update(self.catalog[b].parent_id for b in list(participants) if b in self.catalog and self.catalog[b].parent_id)
        epochs=sorted({float(p['elapsedDays']) for p in self.points})
        tracks={}
        for body_id in sorted(participants-{'sun'}):
            if body_id not in self.catalog:continue
            tracks[body_id]=[{'elapsedDays':day,'positionKm':p.tolist(),'velocityKmS':v.tolist()}
                for day in epochs for p,v in [self.body(body_id,day*DAY_SECONDS)]]
        result['bodyEphemerides']={'epochUtc':timestamp(self.epoch),'frame':'ECLIPJ2000','centerBodyId':'sun','tracks':tracks,'interpolation':'cubic Hermite display only; exact sampled events'}
        for key in ('zoneEntryDate' ,'zoneExitDate','zoneEntryDistanceAU','zoneExitDistanceAU'):
            if key in target:result[key]=target[key]
        return result


def _plan_once(values,departure,arrival=None,branch_index=0):
    values=deepcopy(values)
    if arrival is not None:values["_arrivalDate"]=timestamp(arrival)
    route=PhysicalRoute(values,departure);target=deepcopy(values["target"])
    for waypoint in values["waypoints"]:
        kind=waypoint.get("type")
        if kind in {"body_flyby","body_orbit","solar_oberth"}:
            local={**waypoint,"type":"flyby" if kind!="body_orbit" else "body_orbit","bodyId":"sun" if kind=="solar_oberth" else waypoint.get("bodyId")}
            if kind=="solar_oberth":local["flybyAltitudeKm"]=_number(waypoint.get("perihelionAU"),(values.get("mission") or {}).get("targetPerihelionAU",.05))*AU_KM-SUN_RADIUS_KM
            arrival_date=waypoint.get('targetDate') or (route.epoch+timedelta(days=float(waypoint['encounterDay'])) if waypoint.get('encounterDay') is not None else None)
            route.transfer(local,arrival_date,departure_kind=waypoint.get("departureBurnName","WAYPOINT_INJECTION"))
        elif kind=="zone_crossing":route.outbound({**waypoint,"type":"zone"})
        elif kind in {"manual_point","deep_space_maneuver"}:
            if waypoint.get("positionKm") is not None:_state_transfer(route,{**waypoint,"type":"state_vector"},waypoint.get("targetDate"),rendezvous=False)
            elif waypoint.get("elapsedDays") is not None:
                duration=_number(waypoint["elapsedDays"],0)*DAY_SECONDS-route.seconds
                if duration<=0:raise ValueError("Manöverzeitpunkt liegt vor dem aktuellen Zustand.")
                route.coast(duration,"sun","DEEP_SPACE_COAST")
            else:raise ValueError("Manueller Wegpunkt benötigt Position/Zeitpunkt oder Manöverzeit.")
            delta=waypoint.get("deltaVVectorKmS")
            if delta is not None:route.burn(route.v+vector(delta),"DEEP_SPACE_MANEUVER")
            elif waypoint.get("burnDeltaVKmS"):route.burn(route.v+unit(route.v)*_number(waypoint["burnDeltaVKmS"],0),"DEEP_SPACE_MANEUVER")
        else:raise ValueError(f"Nicht unterstützter Wegpunkttyp '{kind}'.")
    if target["type"] in {"body","body_orbit","flyby","earth_reentry"}:
        section=route.transfer(target,arrival or target.get("targetDate"),branch_index)
        route.target_reached=all(s["targetConditionSatisfied"] for s in route.sections)
    elif target["type"]=="state_vector":_state_transfer(route,target,target.get("targetDate"),rendezvous=True)
    else:route.outbound(target)
    mode="multi-leg" if values["waypoints"] else "body-to-body" if target["type"] in {"body","body_orbit"} else target["type"]
    route.target_reached &= all(s["targetConditionSatisfied"] for s in route.sections)
    return route.to_result(target,mode)


def _state_transfer(route,target,date,rendezvous=True):
    end=utc(date);duration=(end-route.epoch).total_seconds()-route.seconds
    if duration<=0:raise ValueError("Zielzeitpunkt muss nach dem aktuellen Zustand liegen.")
    r,v=rotate_inertial(target["positionKm"],target.get("velocityKmS",[0,0,0]),target.get("frame","ECLIPJ2000"),"ECLIPJ2000")
    center=target.get("centerBodyId","sun")
    if center!="sun":p,w=route.body(center,(end-route.epoch).total_seconds());r+=p;v+=w
    if route.local_body and route.local_body!="sun":route.escape(route.local_body,r-route.r)
    duration=(end-route.epoch).total_seconds()-route.seconds
    branches=_lambert_candidates(tuple(route.r),tuple(r),duration)
    selected=min(branches,key=lambda b:np.linalg.norm(vector(b["departure"])-route.v)+(np.linalg.norm(vector(b["arrival"])-v) if rendezvous else 0))
    if route.full_gravity:
        acceleration=route.gravity('sun',route.seconds);begin_r=route.r.copy()
        def shooting(v0):
            shot=propagate_conic((begin_r,v0),duration,MU_SUN,sample_count=2,gravity_acceleration=acceleration)
            return (vector(shot['finalPositionKm'])-r)/1000.
        fit=least_squares(shooting,vector(selected['departure']),diff_step=1e-5,max_nfev=35,xtol=1e-10,ftol=1e-10,gtol=1e-10)
        selected={**selected,'departure':fit.x.tolist()}
    route.burn(selected["departure"],"STATE_VECTOR_DEPARTURE");route.coast(duration,"sun","STATE_VECTOR_TRANSFER")
    dr=float(np.linalg.norm(route.r-r))
    if rendezvous and target.get("velocityKmS") is not None:route.burn(v,"RENDEZVOUS_MATCH")
    dv=float(np.linalg.norm(route.v-v)) if target.get("velocityKmS") is not None else 0.
    route.target_reached=dr<=_number(route.values["constraints"].get("positionToleranceKm"),.1) and dv<=_number(route.values["constraints"].get("velocityToleranceKmS"),1e-6)
    target["positionResidualKm"]=dr;target["velocityResidualKmS"]=dv


def calculate_trajectory_plan(values=None,include_mission_result=False):
    values=_normalized_input(values)
    if values.get("missionTemplate")=="earth_moon_orbit_return":
        from planner.lunar_mission import calculate_lunar_return
        return calculate_lunar_return(values)
    search=values["searchWindow"];departures=_date_range(search["departureStartDate"],search["departureEndDate"],search["departureStepDays"],"departure")
    targets=values["target"];arrivals=[None]
    if targets["type"] in {"body","body_orbit","flyby"} and not values["waypoints"]:
        if targets.get("targetDate"):arrivals=[utc(targets["targetDate"])]
        elif search.get("arrivalStartDate") and search.get("arrivalEndDate"):
            arrivals=_date_range(search["arrivalStartDate"],search["arrivalEndDate"],search.get("arrivalStepDays",30),"arrival")
        else:
            minimum=_number(values["constraints"].get("minFlightDays"),3 if targets.get("bodyId")=="earth-moon" else 120)
            maximum=_number(values["constraints"].get("maxFlightDays"),minimum*2 if minimum<20 else 1200)
            step=_number(search.get("arrivalStepDays"),max(1,(maximum-minimum)/12))
            if minimum<=0 or maximum<minimum or step<=0:raise ValueError("Ungültige Flugzeitgrenzen.")
            arrivals=list(np.arange(minimum,maximum+step*.001,step))
    if len(departures)*len(arrivals)>4000:raise ValueError("Das kombinierte Suchraster überschreitet 4.000 Datumspaare.")
    candidates=[];failures=[]
    for departure in departures:
        for arrival in arrivals:
            arrival=departure+timedelta(days=float(arrival)) if isinstance(arrival,(float,np.floating,int)) else arrival
            if arrival and arrival<=departure:continue
            for branch in ((0,1) if targets['type'] in {'body','body_orbit','flyby'} else (0,)):
              try:
                result=_plan_once(values,departure,arrival,branch)
                summary=result["summary"];candidate={"id":f"candidate-{uuid4().hex[:12]}","departureDate":timestamp(departure),"arrivalDate":summary.get("targetReachedDate") or timestamp(departure+timedelta(days=summary["totalFlightDays"])),"flightDays":summary["totalFlightDays"],**{k:v for k,v in summary.items() if k not in {"totalFlightDays","model"}},"warnings":result["warnings"]}
                candidate["score"]=calculate_candidate_score(candidate,values["optimizationMode"],values.get("scoreWeights"))
                candidate['branchIndex']=branch
                candidates.append((candidate,result))
                if len(candidates)>250:
                    candidates.sort(key=lambda item:(not item[0]["feasible"],not item[0]["targetReached"],item[0]["score"]))
                    candidates=candidates[:250]
              except (ValueError,RuntimeError) as error:
                # A body start with no specified phase is a family of parking
                # states. Resolve that remaining degree of freedom physically.
                if values['start']['type'] in {'body','orbit'} and not any(values['start'].get(k) is not None for k in ('phaseDeg','inclinationDeg','raanDeg')) and 'kollidiert' in str(error):
                    recovered=False
                    for offset in (.02,-.02,.1,-.1,.3,-.3):
                        try:
                            retry={**values,'_parkingPhaseOffsetRad':offset}
                            result=_plan_once(retry,departure,arrival,branch);summary=result['summary']
                            candidate={'id':f'candidate-{uuid4().hex[:12]}','departureDate':timestamp(departure),'arrivalDate':summary.get('targetReachedDate') or timestamp(departure+timedelta(days=summary['totalFlightDays'])),'flightDays':summary['totalFlightDays'],**summary,'warnings':result['warnings'],'branchIndex':branch,'parkingPhaseOffsetRad':offset}
                            candidate['score']=calculate_candidate_score(candidate,values['optimizationMode'],values.get('scoreWeights'));candidates.append((candidate,result));recovered=True;break
                        except (ValueError,RuntimeError):continue
                    if recovered:continue
                failures.append(str(error))
    if not candidates:raise ValueError("Keine gültige numerische Route im Suchfenster: "+"; ".join(dict.fromkeys(failures))[:800])
    candidates.sort(key=lambda item:(not item[0]["feasible"],not item[0]["targetReached"],item[0]["score"]))
    best,result=candidates[0];result["bestCandidate"]=best;result["candidates"]=[c for c,_ in candidates[:250]]
    result["searchDiagnostics"]={"evaluatedPairs":len(departures)*len(arrivals),"solvedCandidates":len(candidates),"rejectedCandidates":len(failures),"rejectionReasons":list(dict.fromkeys(failures))[:20],"exhaustiveWithinGrid":False,'branchScope':'Two departure-cost-ranked Lambert branches per terminal transfer; no claim of a global multirevolution/multileg optimum.'}
    if values["simulation"]["includeUncertainty"]:result["warnings"].append("Keine Eingangskovarianz konfiguriert; numerische Toleranzen sind keine statistische Unsicherheit.")
    if values["simulation"]["includeAudit"]:
        result["audit"]=_audit_result(result,values)
    return result


def _audit_result(result,inputs):
    return write_route_audit({"inputs":inputs,"constants":{"AU_KM":AU_KM,"MU_SUN_KM3_S2":MU_SUN},"coordinateTransform":{"frame":"ECLIPJ2000","time":"UTC/ET","units":{"position":"km","velocity":"km/s"}},"calculationVersion":SCHEMA_VERSION,"calculationBuild":CALCULATION_BUILD,"maneuvers":result["maneuvers"],"events":result["events"],"segments":result["segments"],"continuity":result["continuity"],"validation":result["validation"],"summary":result["summary"]})


def calculate_body_to_body_transfer(values):return calculate_trajectory_plan(values)
def calculate_multi_leg_transfer(values):return calculate_trajectory_plan(values)
def calculate_flyby_target_route(values):return calculate_trajectory_plan(values)
def calculate_zone_target_route(values):return calculate_trajectory_plan(values)
def calculate_boundary_target_route(values):return calculate_trajectory_plan(values)
def calculate_direction_target_route(values):return calculate_trajectory_plan(values)
def calculate_state_vector_target_route(values):return calculate_trajectory_plan(values)
def calculate_vector_angle_deg(first,second):return angle(first,second)


def _start_state(values,date):
    route=PhysicalRoute(_normalized_input(values),date)
    return tuple(route.r),tuple(route.v),values["start"].get("bodyId")


def calculate_section_route(values):
    sections=values.get("routeSections")
    if not isinstance(sections,list) or not sections or any(not isinstance(s,dict) for s in sections):raise ValueError("Mindestens ein gültiger Routenabschnitt ist erforderlich.")
    for left,right in zip(sections,sections[1:]):
        if left["targetId"]!=right["originId"]:raise ValueError("Abschnitt beginnt nicht am Endpunkt des vorherigen Abschnitts.")
    mission=values.get("mission") or {};start_date=mission.get("startDate") or timestamp(utc(__import__('datetime').datetime.now(__import__('datetime').timezone.utc)))
    waypoints=[];last_target=None
    for section in sections:
        passage=parse_route_passage(section.get("passage"));target_id=section["targetId"]
        from planner.interstellar_targets import INTERSTELLAR_ROUTE_TARGETS,interstellar_direction
        if target_id in INTERSTELLAR_ROUTE_TARGETS:
            if section is not sections[-1]:raise ValueError("Richtungsziel darf nur der letzte Abschnitt sein.")
            last_target={"id":section.get('id',f'leg-{len(waypoints)+1}'),'bodyId':target_id,"type":"direction","direction":list(interstellar_direction(target_id)),"distanceAU":50};continue
        local={"id":section["id"],"type":"body_orbit" if passage["mode"]!="direct" else "body","bodyId":target_id,"entryCorridor":section.get("corridor") or {},"passage":passage,"orbitAltitudeKm":section.get("orbitAltitudeKm",100),"requiredRevolutions":passage["orbitAngleDeg"]/360 if passage["mode"]!="direct" else 0,"orbitDirection":passage["orbitDirection"],"flightDays":section.get("flightDays",3 if target_id=="earth-moon" or section["originId"]=="earth-moon" else 260)}
        if section is sections[-1]:last_target=local
        else:waypoints.append({**local,"type":"body_orbit" if local["type"]=="body_orbit" else "body_flyby"})
    constraints={**values.get("lambertConstraints",{}),**values.get("constraints",{})}
    # A perihelion burn is one maneuver, never a budget for the entire mission.
    if mission.get("maxTotalDeltaVKmS") is not None:constraints.setdefault("maxTotalDeltaVKmS",mission["maxTotalDeltaVKmS"])
    result=calculate_trajectory_plan({"start":{"type":"orbit","bodyId":sections[0]["originId"],"orbitAltitudeKm":mission.get("parkingOrbitAltitudeKm",400),"startDate":start_date},"target":last_target,"waypoints":waypoints,"constraints":constraints,"mission":mission,"simulation":{"includeAudit":False,"includeUncertainty":False,"sampleTrajectoryPoints":180,"highFidelityNBody":values.get('highFidelityNBody',False)}})
    for requested,calculated in zip(sections,result['routeSections']):
        passage=parse_route_passage(requested.get('passage'))
        unsupported=passage['entryBehavior']=='radial' or passage['exitBehavior']=='radial' or (requested is sections[-1] and passage['exitBehavior'] in {'tangential-accelerate','tangential-retrograde'})
        if unsupported:
            calculated['targetConditionSatisfied']=False
            result['summary'].update(feasible=False,flightReady=False,status='no_solution_found')
            result['warnings'].append(f"{calculated['id']}: Diese explizite Eintritts-/Austrittsbedingung ist im gekoppelten Planner nicht gelöst; keine bestätigte Route.")
        budget=sum(_number(requested.get(k),0) for k in ('deltaVMinusKmS','deltaVPlusKmS'))
        calculated['availableSectionDeltaVKmS']=budget
        calculated['sectionBudgetSatisfied']=calculated['requiredSectionDeltaVKmS']<=budget+1e-8
        if not calculated['sectionBudgetSatisfied']:
            result['summary'].update(feasible=False,flightReady=False,status='infeasible')
            result['validation']['sectionBudgetsSatisfied']=False
            result['warnings'].append(f"{calculated['id']}: Abschnitt benötigt {calculated['requiredSectionDeltaVKmS']:.3f} km/s, konfiguriert {budget:.3f} km/s.")
    result['audit']=_audit_result(result,values)
    return section_result_adapter(result,start_date)


def section_result_adapter(result,start_date=None):
    summary=result["summary"];sections=result["routeSections"];last=sections[-1] if sections else {}
    v=vector(result["trajectory"][-1]["velocityKmS"]);speed=float(np.linalg.norm(v))
    old={**summary,"flybyMode":"multi-section","feasibleWithConfiguredBurn":summary["feasible"],
         "availableInjectionDeltaVKmS":result["input"]["constraints"].get("maxTotalDeltaVKmS",0),
         "solarDepartureInjectionApplied":summary["feasible"],"targetCorrectionDeltaVKmS":sum(m["deltaVKmS"] for m in result["maneuvers"][1:]),
         "targetInjectionApplied":summary["feasible"] and len(result["maneuvers"])>1,"passiveTargeting":summary["feasible"] and not result["maneuvers"],
         "incomingExcessSpeedKmS":summary["arrivalVInfinityKmS"],"heliocentricSpeedBeforeKmS":float(np.linalg.norm(result["start"]["velocityKmS"])),
         "heliocentricSpeedAfterKmS":speed,"speedGainKmS":speed-float(np.linalg.norm(result["start"]["velocityKmS"])),
         "turnAngleDeg":0.,"courseChangeDeg":0.,"targetAlignmentDeg":summary.get("targetAlignmentDeg") or 0.,"actualTargetAlignmentDeg":summary.get("targetAlignmentDeg") or 0.,
         "periapsisSpeedKmS":speed,"observationWindowHours":0.,"entryInsideCorridor":summary.get("entryInsideCorridor",True),"warnings":result["warnings"],"totalTransitionDeltaVKmS":summary["totalDeltaVKmS"]}
    for section in sections:
        section.setdefault("lookaheadAlignmentDeg",0.);section.setdefault("predictedPassiveTurnDeg",0.)
        section.setdefault("desiredDepartureDirection",section["entryDirection"]);section.setdefault("predictedOutgoingDirection",unit(section["exitVelocityKmS"]).tolist())
        section.setdefault("requestedPassageAngleDeg",section.get("passage",{}).get("orbitAngleDeg",0));section.setdefault("selectedPassageAngleDeg",section.get("completedRevolutions",0)*360)
    return {**result,"genericTrajectoryPlan":result,"summary":old,"startDate":start_date or result["start"]["date"],
            "totalFlightDays":summary["totalFlightDays"],"outgoingDirection":(v/speed).tolist() if speed else [0,0,0],
            "waypoint":{"id":last.get("targetId",result["target"]["type"]),"name":last.get("targetName",result["target"]["type"]),"encounterDay":last.get("entryDay",summary["totalFlightDays"]),"entryDay":last.get("entryDay",summary["totalFlightDays"]),"exitDay":last.get("exitDay",summary["totalFlightDays"]),"flybyAltitudeKm":last.get("minimumAltitudeKm",0),"trajectoryIndex":last.get("entryIndex",len(result["trajectory"])-1),"positionKm":last.get("entryPositionKm",result["trajectory"][-1]["positionKm"])}}
