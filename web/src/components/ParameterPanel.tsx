import { useMemo, useState } from 'react'

import type { LaunchOptimizationResult } from '../launchOptimizer'
import type {
  MissionConfig,
  MissionResult,
  MoonData,
  PlanetData,
  TrajectoryPoint,
  VisualConfig,
} from '../types'
import { PropulsionWizard } from './PropulsionWizard'
import { TrajectoryPlannerPanel } from './TrajectoryPlannerPanel'

interface NumberFieldProps {
  label: string
  value: number
  min: number
  max: number
  step?: number
  unit?: string
  onChange: (value: number) => void
}

function NumberField({ label, value, min, max, step = 1, unit, onChange }: NumberFieldProps) {
  const invalid = value < min || value > max || !Number.isFinite(value)
  return (
    <label className={`parameter-field ${invalid ? 'invalid' : ''}`}>
      <span>{label}</span>
      <span className="field-input">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(event) => onChange(event.target.valueAsNumber)}
        />
        {unit && <small>{unit}</small>}
      </span>
    </label>
  )
}

interface RangeFieldProps extends NumberFieldProps {}

function RangeField({ label, value, min, max, step = 1, unit, onChange }: RangeFieldProps) {
  return (
    <label className="range-field">
      <span>{label}<output>{value.toLocaleString('de-DE')} {unit}</output></span>
      <input type="range" value={value} min={min} max={max} step={step} onChange={(event) => onChange(event.target.valueAsNumber)} />
    </label>
  )
}

interface ToggleProps {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}

function Toggle({ label, checked, onChange }: ToggleProps) {
  return (
    <label className="toggle-field">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  )
}

interface ParameterPanelProps {
  planets: PlanetData[]
  moons: MoonData[]
  moonCounts: Record<string, number>
  selectedPlanet: PlanetData | null
  selectedObject: string
  selectedMoons: MoonData[]
  selectedMoon: MoonData | null
  currentPoint: TrajectoryPoint | null
  visual: VisualConfig
  draft: MissionConfig
  result: MissionResult | null
  elapsedDays: number
  canPlay: boolean
  energyDeficit?: LaunchOptimizationResult['solarEnergyFeasibility']
  onSelectPlanet: (planet: PlanetData) => void
  onSelectObject: (object: string) => void
  onSelectMoon: (moon: MoonData) => void
  onVisualChange: (visual: VisualConfig) => void
  onDraftChange: (config: MissionConfig) => void
  onApplyTrajectoryPlan: (result: import('../types').GenericTrajectoryPlannerResult) => void
  showTrajectoryPlanner?: boolean
}

export function ParameterPanel({
  planets,
  moons,
  moonCounts,
  selectedPlanet,
  selectedObject,
  selectedMoons,
  selectedMoon,
  currentPoint,
  visual,
  draft,
  result,
  elapsedDays,
  canPlay,
  energyDeficit,
  onSelectPlanet,
  onSelectObject,
  onSelectMoon,
  onVisualChange,
  onDraftChange,
  onApplyTrajectoryPlan,
  showTrajectoryPlanner = true,
}: ParameterPanelProps) {
  const [moonSearch, setMoonSearch] = useState('')
  const [propulsionWizardOpen, setPropulsionWizardOpen] = useState(false)
  const filteredMoons = useMemo(() => {
    const query = moonSearch.trim().toLocaleLowerCase('de-DE')
    return selectedMoons.filter((moon) => !query
      || moon.name.toLocaleLowerCase('de-DE').includes(query)
      || moon.provisionalDesignation?.toLocaleLowerCase('de-DE').includes(query))
  }, [moonSearch, selectedMoons])
  const updateMission = <K extends keyof MissionConfig>(key: K, value: MissionConfig[K]) => {
    onDraftChange({ ...draft, [key]: value })
  }
  const updateVisual = <K extends keyof VisualConfig>(key: K, value: VisualConfig[K]) => {
    onVisualChange({ ...visual, [key]: value })
  }
  const enabledPropulsionModules = draft.propulsionModules.filter((module) => module.enabled)
  const propulsionMassKg = enabledPropulsionModules.reduce(
    (sum, module) => sum + module.dryMassKg + module.propellantMassKg,
    0,
  )
  const updateElectricSailMission = <K extends keyof MissionConfig>(
    key: K,
    value: MissionConfig[K],
    parameterKey?: string,
  ) => {
    const propulsionModules = draft.propulsionModules.map((module) => module.type === 'electric_sail'
      ? {
          ...module,
          ...(key === 'electricSailEnabled' ? { enabled: Boolean(value) } : {}),
          parameters: parameterKey ? { ...module.parameters, [parameterKey]: value as number } : module.parameters,
        }
      : module)
    onDraftChange({ ...draft, [key]: value, propulsionModules })
  }
  const selectedPlanetSpeed = selectedPlanet ? 29.78 / Math.sqrt(selectedPlanet.distanceAu) : 0
  const earthAngle = elapsedDays / 365.25 * Math.PI * 2
  const startPosition = result?.trajectory[0]?.positionKm
  const earthPositionKm: [number, number, number] | null = startPosition ? [
    startPosition[0] * Math.cos(earthAngle) - startPosition[1] * Math.sin(earthAngle),
    startPosition[0] * Math.sin(earthAngle) + startPosition[1] * Math.cos(earthAngle),
    startPosition[2],
  ] : null
  const distanceFromEarthAu = currentPoint && earthPositionKm ? Math.hypot(
    currentPoint.positionKm[0] - earthPositionKm[0],
    currentPoint.positionKm[1] - earthPositionKm[1],
    currentPoint.positionKm[2] - earthPositionKm[2],
  ) / 149_597_870.7 : null
  const afterBurn = currentPoint ? ['PAYLOAD_SEPARATION', 'ELECTRIC_SAIL_DEPLOYMENT', 'ELECTRIC_SAIL_PROPULSION', 'DEEP_SPACE_CRUISE', 'MISSION_COMPLETE'].includes(currentPoint.phase) : false
  const simulatedRadius = selectedPlanet
    ? selectedPlanet.radiusKm * visual.planetScale
    : 0

  return (
    <aside className="planet-panel parameter-panel" aria-label="Objekt- und Simulationsparameter">
      <p className="eyebrow">Objektdaten & Simulation</p>

      {showTrajectoryPlanner && <TrajectoryPlannerPanel
        planets={planets}
        moons={moons}
        defaultStartDate={draft.startDate}
        onApply={onApplyTrajectoryPlan}
      />}

      <details>
        <summary>Objekt</summary>
        <div className="object-list" aria-label="Objekt auswählen">
          <button className={selectedObject === 'sun' ? 'selected' : ''} type="button" onClick={() => onSelectObject('sun')}>Sonne</button>
          {planets.map((planet) => (
            <button className={selectedObject === planet.id ? 'selected' : ''} key={planet.id} type="button" onClick={() => onSelectPlanet(planet)}>
              {planet.name} <small>{moonCounts[planet.id] ?? 0}</small>
            </button>
          ))}
          <button className={selectedObject === 'probe' ? 'selected' : ''} type="button" onClick={() => onSelectObject('probe')}>Sonde</button>
          <button className={selectedObject === 'carrier' ? 'selected' : ''} type="button" onClick={() => onSelectObject('carrier')}>Träger</button>
          <button className={selectedObject === 'sail' ? 'selected' : ''} type="button" onClick={() => onSelectObject('sail')}>Electric Sail</button>
          <button className={selectedObject === 'energy_sources' ? 'selected' : ''} type="button" onClick={() => onSelectObject('energy_sources')}>Energiequellen</button>
        </div>

        {selectedObject === 'sun' && <p className="object-card"><strong>Sonne</strong><span>Zentralkörper · Radius 696.340 km</span><span>Masse 1,9885 × 10³⁰ kg</span></p>}
        {selectedPlanet && selectedObject === selectedPlanet.id && (
          <dl className="compact-data">
            <div><dt>Name / Typ</dt><dd>{selectedPlanet.name} · Planet</dd></div>
            <div><dt>Radius real</dt><dd>{selectedPlanet.radiusKm.toLocaleString('de-DE')} km</dd></div>
            <div><dt>Radius-Skalierung</dt><dd>{simulatedRadius.toLocaleString('de-DE', { maximumFractionDigits: 0 })}</dd></div>
            <div><dt>Masse</dt><dd>{selectedPlanet.massKg.toExponential(3)} kg</dd></div>
            <div><dt>Sonnenabstand</dt><dd>{selectedPlanet.distanceAu.toLocaleString('de-DE')} AE</dd></div>
            <div><dt>Umlauf / Tempo</dt><dd>{selectedPlanet.orbitalPeriodDays.toLocaleString('de-DE')} d · ≈ {selectedPlanetSpeed.toFixed(2)} km/s</dd></div>
            <div><dt>Neigung / e</dt><dd>{(selectedPlanet.inclinationDeg ?? 0).toFixed(2)}° · {(selectedPlanet.eccentricity ?? 0).toFixed(4)}</dd></div>
            <div><dt>Oberfläche</dt><dd><a href="https://space.jpl.nasa.gov/tmaps/" target="_blank" rel="noreferrer">NASA/JPL-Texturkarte</a></dd></div>
          </dl>
        )}
        {selectedObject === 'probe' && currentPoint && result && (
          <dl className="compact-data">
            <div><dt>Phase</dt><dd>{currentPoint.phase.replaceAll('_', ' ')}</dd></div>
            <div><dt>Zeit</dt><dd>{elapsedDays.toFixed(1)} Tage</dd></div>
            <div><dt>Geschwindigkeit</dt><dd>{Math.hypot(...currentPoint.velocityKmS).toFixed(2)} km/s</dd></div>
            <div><dt>Sonnenabstand</dt><dd>{(Math.hypot(...currentPoint.positionKm) / 149_597_870.7).toFixed(2)} AE</dd></div>
            <div><dt>Erddistanz</dt><dd>{distanceFromEarthAu?.toFixed(2)} AE</dd></div>
            <div><dt>Masse</dt><dd>{currentPoint.massKg.toFixed(1)} kg</dd></div>
            <div><dt>Aktive Stufe</dt><dd>{afterBurn ? 'Nutzlastsonde' : 'Oberth-Träger'}</dd></div>
            <div><dt>Treibstoff</dt><dd>{afterBurn ? Math.max(0, result.config.propellantMassKg - result.summary.propellantUsedKg).toFixed(0) : result.config.propellantMassKg.toFixed(0)} kg</dd></div>
            <div><dt>Power Mode</dt><dd>{currentPoint.phase.includes('SAIL') ? 'high-voltage' : 'cruise'}</dd></div>
            <div><dt>Electric Sail</dt><dd>{currentPoint.phase === 'ELECTRIC_SAIL_PROPULSION' || currentPoint.phase === 'DEEP_SPACE_CRUISE' ? 'aktiv' : 'inaktiv'}</dd></div>
          </dl>
        )}
        {selectedObject === 'probe' && (!currentPoint || !result) && <p className="object-card"><strong>Sonde</strong>{canPlay ? <><span>Wegpunktroute geladen.</span><span>Position wird kontinuierlich auf der Routentimeline interpoliert.</span></> : <><span>Noch keine Bahn berechnet.</span><span>Die Berechnung startet erst über „Simulation aktualisieren“.</span></>}</p>}
        {selectedObject === 'carrier' && <p className="object-card"><strong>Solar-Oberth-Träger</strong><span>{draft.carrierMassKg.toLocaleString('de-DE')} kg trocken</span><span>Entsorgung: {draft.carrierDisposal}</span></p>}
        {selectedObject === 'sail' && <p className="object-card"><strong>Electric Sail</strong><span>{draft.tetherCount} Tethers · {draft.tetherLengthKm} km</span><span>{draft.instrumentedTetherCount} Sensor-Tethers · {draft.tetherVoltageKv} kV</span></p>}
        {selectedObject === 'energy_sources' && (
          <section className="energy-source-panel" aria-label="Energiequellen und Antriebskopplung">
            <div className="object-card energy-delta-v-card">
              <strong>Antriebsrandbedingung am Perihel</strong>
              <span>Aktuelles Manöver: {draft.oberthDeltaVKmS.toFixed(2)} km/s in {draft.burnDurationSeconds.toLocaleString('de-DE')} s · Triebwerk-Isp {draft.engineIspSeconds.toLocaleString('de-DE')} s.</span>
              {energyDeficit
                ? <>
                    <span className={energyDeficit.energeticallyReachable ? 'status-ok' : 'route-warning'}>
                      {energyDeficit.energeticallyReachable
                        ? `Das konfigurierte Oberth-Δv erreicht ${energyDeficit.desiredExitSpeedKmS.toFixed(2)} km/s bei 1 AE.`
                        : `${energyDeficit.additionalDeltaVRequiredKmS.toFixed(2)} km/s Oberth-Δv fehlen; mindestens ${energyDeficit.minimumOberthDeltaVForDesiredSpeedKmS.toFixed(2)} km/s werden benötigt.`}
                    </span>
                    <span>Das ist ein fehlender Geschwindigkeitsimpuls des Triebwerks – kein unmittelbar fehlender elektrischer Wattwert.</span>
                  </>
                : <span>Nach einer Routenoptimierung erscheint hier der fehlende oder verfügbare Geschwindigkeitsimpuls.</span>}
              <span>Eine Energiequelle versorgt ein Triebwerk. Erst Schub, spezifischer Impuls, Treibstoffmasse und Burn-Dauer bestimmen das erreichbare Δv.</span>
            </div>
            <div className="energy-source-list">
              {[
                { id: 'chemical', name: 'Chemische Reaktionsenergie', readiness: 'einsatzbereit', output: 'Hochschub · gespeicherter Treibstoff', links: ['chemical', 'solid_kick_stage', 'solar_oberth'], drives: 'Chemischer Antrieb, Kick-Stufe, Solar-Oberth' },
                { id: 'solar', name: 'Solar-elektrisch', readiness: 'einsatzbereit', output: 'kW-Klasse bei 1 AE · ∝ 1/r²', links: ['ion', 'hall', 'electric_sail'], drives: 'Ion, Hall, Electric Sail' },
                { id: 'rtg', name: 'Radioisotop / RTG', readiness: 'einsatzbereit', output: 'W bis niedrige kW · Dauerleistung', links: [], drives: 'Avionik; nur sehr kleine elektrische Antriebe' },
                { id: 'fission', name: 'Kernspaltungsreaktor', readiness: 'demonstriert', output: '200 kW im Missionsmodell', links: ['nuclear_electric', 'ion', 'hall'], drives: 'Nuklear-elektrisch, Ion, Hall' },
                { id: 'fusion', name: 'Fusionsquelle', readiness: 'konzeptionell', output: '1 GW im Zukunftsmodell', links: ['fusion'], drives: 'Fusion Drive oder elektrische Hochleistungsantriebe' },
              ].map((source) => {
                const linked = draft.propulsionModules.some((module) => source.links.includes(module.type) && module.enabled)
                return (
                  <article className="energy-source-card" key={source.id}>
                    <header><strong>{source.name}</strong><small className={linked ? 'status-ok' : ''}>{linked ? 'gekoppelt' : source.readiness}</small></header>
                    <span>{source.output}</span>
                    <span>Triebwerk: {source.drives}</span>
                  </article>
                )
              })}
            </div>
            <p className="parameter-warning">Fusion ist im Modell ein Zukunftsszenario. Hohe Reaktorleistung allein garantiert weder genügend Sofortschub noch das nötige Oberth-Δv.</p>
          </section>
        )}

        {selectedPlanet && selectedObject === selectedPlanet.id && selectedMoons.length > 0 && (
          <section className="moon-catalogue" aria-label={`Monde von ${selectedPlanet.name}`}>
            <div className="moon-heading"><h3>Monde</h3><span>{selectedMoons.length} bekannt</span></div>
            <input type="search" value={moonSearch} placeholder="Mond suchen …" onChange={(event) => setMoonSearch(event.target.value)} />
            <div className="moon-list">
              {filteredMoons.map((moon) => (
                <button className={selectedMoon?.id === moon.id ? 'selected' : ''} key={moon.id} type="button" onClick={() => onSelectMoon(moon)}>
                  <span>{moon.name}</span><small>{moon.orbitSource ? 'JPL-Bahn' : 'Katalog'}</small>
                </button>
              ))}
            </div>
          </section>
        )}
      </details>

      <details>
        <summary>Darstellung</summary>
        <RangeField label="Bahnskalierung" value={visual.orbitScale} min={1} max={20} step={0.5} unit="×√AE" onChange={(value) => updateVisual('orbitScale', value)} />
        <RangeField label="Bahnneigungen sichtbar" value={visual.inclinationScale} min={1} max={10} step={0.5} unit="× vertikal" onChange={(value) => updateVisual('inclinationScale', value)} />
        <RangeField label="Körpergröße proportional" value={visual.planetScale} min={0.5} max={20} step={0.5} unit="× gemeinsam" onChange={(value) => updateVisual('planetScale', value)} />
        <RangeField label="Sondengröße" value={visual.probeScale} min={1} max={100} unit="×" onChange={(value) => updateVisual('probeScale', value)} />
        <RangeField label="Saturnringe" value={visual.saturnRingScale} min={0.5} max={3} step={0.1} unit="×" onChange={(value) => updateVisual('saturnRingScale', value)} />
      </details>

      <details>
        <summary>Mission</summary>
        <NumberField label="Parkbahnhöhe" value={draft.parkingOrbitAltitudeKm} min={101} max={100_000} unit="km" onChange={(value) => updateMission('parkingOrbitAltitudeKm', value)} />
        <NumberField label="Nutzlast" value={draft.payloadMassKg} min={1} max={10_000} unit="kg" onChange={(value) => updateMission('payloadMassKg', value)} />
        <NumberField label="Trägermasse" value={draft.carrierMassKg} min={1} max={50_000} unit="kg" onChange={(value) => updateMission('carrierMassKg', value)} />
        <NumberField label="Hitzeschild" value={draft.heatshieldMassKg} min={1} max={20_000} unit="kg" onChange={(value) => updateMission('heatshieldMassKg', value)} />
        <NumberField label="Treibstoff" value={draft.propellantMassKg} min={1} max={100_000} unit="kg" onChange={(value) => updateMission('propellantMassKg', value)} />
        <NumberField label="Missionsdauer" value={draft.missionYears} min={1} max={20} unit="Jahre" onChange={(value) => updateMission('missionYears', value)} />
      </details>

      <details>
        <summary>Solar-Oberth</summary>
        <NumberField label="Ziel-Perihel" value={draft.targetPerihelionAu} min={0.0047} max={0.99} step={0.005} unit="AE" onChange={(value) => updateMission('targetPerihelionAu', value)} />
        <NumberField label="Burn-Δv" value={draft.oberthDeltaVKmS} min={0} max={30} step={0.1} unit="km/s" onChange={(value) => updateMission('oberthDeltaVKmS', value)} />
        <NumberField label="Burn-Dauer" value={draft.burnDurationSeconds} min={1} max={3_600} unit="s" onChange={(value) => updateMission('burnDurationSeconds', value)} />
        <NumberField label="Triebwerk Isp" value={draft.engineIspSeconds} min={100} max={2_000} unit="s" onChange={(value) => updateMission('engineIspSeconds', value)} />
        <NumberField label="Hitzeschildlimit" value={draft.heatshieldLimitWm2} min={1_361} max={5_000_000} unit="W/m²" onChange={(value) => updateMission('heatshieldLimitWm2', value)} />
        <label className="parameter-field"><span>Trägerentsorgung</span><select value={draft.carrierDisposal} onChange={(event) => updateMission('carrierDisposal', event.target.value as MissionConfig['carrierDisposal'])}><option value="safe_orbit">Sichere Sonnenbahn</option><option value="solar_orbit">Sonnenorbit</option><option value="sun_impact">Sonnenkollision</option></select></label>
        {draft.targetPerihelionAu < 0.05 && <p className="parameter-warning">Warnung: extreme thermische Belastung.</p>}
      </details>

      <details>
        <summary>Navigation &amp; N-Körper</summary>
        <Toggle label="Planetenstörungen aktiv" checked={draft.nBodyEnabled} onChange={(value) => updateMission('nBodyEnabled', value)} />
        <Toggle label="Kalman-Navigation aktiv" checked={draft.kalmanEnabled} onChange={(value) => updateMission('kalmanEnabled', value)} />
        <NumberField label="Navigationszyklus" value={draft.navigationCycleHours} min={0.25} max={168} step={0.25} unit="h" onChange={(value) => updateMission('navigationCycleHours', value)} />
        <NumberField label="Positionsmessrauschen" value={draft.positionMeasurementNoiseKm} min={0.001} max={100_000} step={1} unit="km" onChange={(value) => updateMission('positionMeasurementNoiseKm', value)} />
        <NumberField label="Geschwindigkeitsrauschen" value={draft.velocityMeasurementNoiseKmS} min={0.000001} max={10} step={0.001} unit="km/s" onChange={(value) => updateMission('velocityMeasurementNoiseKmS', value)} />
      </details>

      <details>
        <summary>Stufen & Trennung</summary>
        <Toggle label="Startstufe aktiv" checked={draft.launchStageEnabled} onChange={(value) => updateMission('launchStageEnabled', value)} />
        <Toggle label="Oberth-Träger aktiv" checked={draft.carrierEnabled} onChange={(value) => updateMission('carrierEnabled', value)} />
        <Toggle label="Hitzeschild aktiv" checked={draft.heatshieldEnabled} onChange={(value) => updateMission('heatshieldEnabled', value)} />
        <Toggle label="Kick-Stufe aktiv" checked={draft.kickStageEnabled} onChange={(value) => updateMission('kickStageEnabled', value)} />
        <NumberField label="Trennimpuls" value={draft.separationDeltaVKmS} min={0} max={0.02} step={0.001} unit="km/s" onChange={(value) => updateMission('separationDeltaVKmS', value)} />
      </details>

      <details>
        <summary>Antriebe · modular</summary>
        <details className="propulsion-subsection">
          <summary>Electric Sail · Missionssystem</summary>
          <Toggle label="Electric Sail aktiv" checked={draft.electricSailEnabled} onChange={(value) => updateElectricSailMission('electricSailEnabled', value)} />
          <NumberField label="Tethers gesamt" value={draft.tetherCount} min={1} max={200} onChange={(value) => updateElectricSailMission('tetherCount', value, 'totalTetherCount')} />
          <NumberField label="Instrumentiert" value={draft.instrumentedTetherCount} min={0} max={draft.tetherCount} onChange={(value) => updateElectricSailMission('instrumentedTetherCount', value, 'instrumentedTetherCount')} />
          <NumberField label="Tether-Länge" value={draft.tetherLengthKm} min={1} max={100} unit="km" onChange={(value) => updateElectricSailMission('tetherLengthKm', value, 'tetherLengthKm')} />
          <NumberField label="Spannung" value={draft.tetherVoltageKv} min={1} max={100} unit="kV" onChange={(value) => updateElectricSailMission('tetherVoltageKv', value, 'tetherVoltageKV')} />
          <NumberField label="Spinrate" value={draft.spinRateRpm} min={0.1} max={10} step={0.1} unit="rpm" onChange={(value) => updateElectricSailMission('spinRateRpm', value, 'spinRateRpm')} />
          <Toggle label="Endmassen aktiv" checked={draft.endMassesEnabled} onChange={(value) => updateMission('endMassesEnabled', value)} />
          <Toggle label="Glasfaser-Kommunikation" checked={draft.fiberCommunicationEnabled} onChange={(value) => updateMission('fiberCommunicationEnabled', value)} />
          <Toggle label="Sensor-Endknoten" checked={draft.sensorNodesEnabled} onChange={(value) => updateMission('sensorNodesEnabled', value)} />
          <NumberField label="Schub bei 1 AE" value={draft.sailAccelerationMmS2} min={0} max={2} step={0.01} unit="mm/s²" onChange={(value) => updateMission('sailAccelerationMmS2', value)} />
        </details>
        <div className="propulsion-panel-summary">
          <dl className="compact-data">
            <div><dt>Ausgewählt</dt><dd>{enabledPropulsionModules.length} Module</dd></div>
            <div><dt>Modulmasse</dt><dd>{propulsionMassKg.toLocaleString('de-DE')} kg</dd></div>
            <div><dt>Szenario</dt><dd>{draft.theoreticalPropulsionMode ? 'Theoretisch' : 'Physikalisch'}</dd></div>
          </dl>
          <div className="propulsion-panel-tags" aria-label="Ausgewählte Antriebsmodule">
            {enabledPropulsionModules.map((module) => <span key={module.id}>{module.name}</span>)}
            {enabledPropulsionModules.length === 0 && <span className="empty">Keine Module ausgewählt</span>}
          </div>
          <button type="button" className="propulsion-wizard-open" onClick={() => setPropulsionWizardOpen(true)}>
            Antriebskombination konfigurieren
          </button>
        </div>
      </details>

      {propulsionWizardOpen && (
        <PropulsionWizard
          config={draft}
          onCancel={() => setPropulsionWizardOpen(false)}
          onApply={(next) => {
            onDraftChange(next)
            setPropulsionWizardOpen(false)
          }}
        />
      )}

      <details>
        <summary>Anzeige</summary>
        <Toggle label="Planeten" checked={visual.showPlanets} onChange={(value) => updateVisual('showPlanets', value)} />
        <Toggle label="Bahnlinien" checked={visual.showOrbits} onChange={(value) => updateVisual('showOrbits', value)} />
        <Toggle label="Sondenbahn" checked={visual.showTrajectory} onChange={(value) => updateVisual('showTrajectory', value)} />
        <Toggle label="Stufen" checked={visual.showStages} onChange={(value) => updateVisual('showStages', value)} />
        <Toggle label="Abgetrennte Stufen" checked={visual.showDetachedStages} onChange={(value) => updateVisual('showDetachedStages', value)} />
        <Toggle label="Solar-Oberth-Burn" checked={visual.showBurn} onChange={(value) => updateVisual('showBurn', value)} />
        <Toggle label="Electric-Sail-Tethers" checked={visual.showSail} onChange={(value) => updateVisual('showSail', value)} />
        <Toggle label="Sensor-Tethers hervorheben" checked={visual.highlightSensorTethers} onChange={(value) => updateVisual('highlightSensorTethers', value)} />
        <Toggle label="Labels" checked={visual.showLabels} onChange={(value) => updateVisual('showLabels', value)} />
        <Toggle label="Geschwindigkeitsvektor" checked={visual.showVectors} onChange={(value) => updateVisual('showVectors', value)} />
        <Toggle label="Kraftvektor" checked={visual.showForceVectors} onChange={(value) => updateVisual('showForceVectors', value)} />
        <Toggle label="Skalierungshinweis" checked={visual.showScaleNotice} onChange={(value) => updateVisual('showScaleNotice', value)} />
      </details>

      {result && <details>
        <summary>Ergebnis</summary>
        <dl className="compact-data result-data">
          <div><dt>Status</dt><dd className={result.summary.status === 'SUCCESS' ? 'status-ok' : 'status-warn'}>{result.summary.status}</dd></div>
          <div><dt>Perihel</dt><dd>{result.summary.perihelionAu.toFixed(4)} AE</dd></div>
          <div><dt>Vor / nach Burn</dt><dd>{result.summary.preBurnSpeedKmS.toFixed(1)} / {result.summary.postBurnSpeedKmS.toFixed(1)} km/s</dd></div>
          <div><dt>Δv erreicht</dt><dd>{result.summary.achievedBurnDeltaVKmS.toFixed(2)} km/s</dd></div>
          <div><dt>Treibstoff</dt><dd>{result.summary.propellantUsedKg.toFixed(0)} kg</dd></div>
          {[1, 5, 10].map((year) => result.summary.distanceAuByYear[String(year)] !== undefined && <div key={year}><dt>Nach {year} Jahr{year > 1 ? 'en' : ''}</dt><dd>{result.summary.distanceAuByYear[String(year)].toFixed(2)} AE</dd></div>)}
          {result.summary.distanceAuByYear['10'] !== undefined && <div><dt>Voyager-Referenz (10 J.)</dt><dd>≈ 35,8 AE</dd></div>}
          <div><dt>E-Sail Zusatz</dt><dd>≈ {result.summary.electricSailGainKmS.toFixed(2)} km/s</dd></div>
          <div><dt>Kalman-Zyklen</dt><dd>{result.summary.navigationCycles.toLocaleString('de-DE')}</dd></div>
          <div><dt>Positionsunsicherheit</dt><dd>± {result.summary.positionUncertaintyKm.toFixed(2)} km</dd></div>
          <div><dt>Geschwindigkeitsunsicherheit</dt><dd>± {result.summary.velocityUncertaintyKmS.toFixed(5)} km/s</dd></div>
          <div><dt>Max. Planetenstörung</dt><dd>{result.summary.maxPlanetaryPerturbationMmS2.toExponential(2)} mm/s²</dd></div>
          <div><dt>Bis Saturndistanz</dt><dd>{result.summary.timeToSaturnDays === null ? 'nicht erreicht' : `${result.summary.timeToSaturnDays.toFixed(0)} Tage`}</dd></div>
          <div><dt>Bis Voyagerdistanz</dt><dd>{result.summary.timeToVoyagerDistanceDays === null ? 'nicht erreicht' : `${result.summary.timeToVoyagerDistanceDays.toFixed(0)} Tage`}</dd></div>
        </dl>
        <div className="propulsion-comparison-wrap">
          <table className="propulsion-comparison">
            <thead><tr><th>Antrieb</th><th>TRL</th><th>Schub</th><th>Leistung</th><th>Verbrauch</th><th>Δv</th></tr></thead>
            <tbody>{result.summary.propulsionReport.filter((module) => module.enabled).map((module) => (
              <tr key={module.id}><td>{module.name}</td><td>{module.readiness}</td><td>{module.peakThrustN.toExponential(2)} N</td><td>{module.powerRequiredW.toExponential(2)} W</td><td>{module.propellantUsedKg.toFixed(2)} kg</td><td>{module.deltaVDeliveredKmS.toFixed(3)} km/s</td></tr>
            ))}</tbody>
          </table>
        </div>
        {result.summary.warnings.map((warning) => <p className="parameter-warning" key={warning}>{warning}</p>)}
      </details>}

      {result && <details>
        <summary>Events · {result.events.length}</summary>
        <ol className="event-log">
          {result.events.map((event) => <li className={event.warningLevel} key={`${event.name}-${event.elapsedDays}`}><time>Tag {event.elapsedDays.toFixed(1)}</time><strong>{event.name}</strong><span>{event.description}</span></li>)}
        </ol>
      </details>}

    </aside>
  )
}
