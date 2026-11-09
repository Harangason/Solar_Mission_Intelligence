/** Fit the complete system without distorting orbital inclinations or aspect ratio. */
export function fitOrbitViewport(width: number, height: number, extent = 30, side = false) {
  const aspect = Math.max(0.1, width / Math.max(1, height))
  const halfHeight = Math.max(side ? 5 : extent, extent / aspect)
  const halfWidth = halfHeight * aspect
  return { x: -halfWidth, y: -halfHeight, width: halfWidth * 2, height: halfHeight * 2 }
}

export function advanceOrbitDays(days: number, elapsedSeconds: number, daysPerSecond: number) {
  // A suspended tab must not jump forward by months on return.
  return days + Math.max(0, Math.min(0.1, elapsedSeconds)) * daysPerSecond
}
