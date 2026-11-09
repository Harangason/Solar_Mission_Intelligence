import type { useOrbitalClock } from '../useOrbitalClock'

export function OrbitalAnimationControls({ clock }: { clock: ReturnType<typeof useOrbitalClock> }) {
  return <div className="orbital-animation-controls" role="group" aria-label="Bahnanimation">
    <button type="button" className={clock.playing ? 'active' : ''} aria-pressed={clock.playing} onClick={clock.toggle}>
      {clock.playing ? 'Ⅱ Pause' : '▶ Bahnen animieren'}
    </button>
    <label>Tempo <select aria-label="Animationstempo" value={clock.speed} onChange={event => clock.setSpeed(Number(event.target.value))}>
      <option value={1}>1 Tag / s</option><option value={10}>10 Tage / s</option><option value={30}>30 Tage / s</option><option value={100}>100 Tage / s</option>
    </select></label>
    <button type="button" onClick={clock.reset}>Zeit zurücksetzen</button>
    <time dateTime={new Date(clock.timestampMs).toISOString()}>{new Date(clock.timestampMs).toLocaleDateString('de-DE', { timeZone: 'UTC' })}</time>
  </div>
}
