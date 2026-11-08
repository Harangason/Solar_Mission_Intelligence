import type { GenericTrajectoryPlannerResult } from './types'
import type { WaypointRouteResult } from './components/PlannedWaypointRoute'

export function trajectoryToWaypointRoute(trajectoryPlan: GenericTrajectoryPlannerResult): WaypointRouteResult {
    const legacy = trajectoryPlan.legacyRoute
    if (legacy && typeof legacy === 'object' && 'trajectory' in legacy && 'summary' in legacy) {
      return legacy as WaypointRouteResult
    } else {
      const trajectory = trajectoryPlan.trajectory
      const finalPoint = trajectory.at(-1)
      const targetPosition = trajectoryPlan.target.positionKm ?? finalPoint?.positionKm ?? [0, 0, 0]
      const finalVelocity = finalPoint?.velocityKmS ?? [0, 0, 0]
      const finalSpeed = Math.hypot(...finalVelocity)
      const outgoingDirection = finalSpeed > 0
        ? finalVelocity.map((component) => component / finalSpeed) as [number, number, number]
        : [1, 0, 0] as [number, number, number]
      const minimumSolarRadiusKm = Math.min(...trajectory.map((point) => Math.hypot(...point.positionKm)))
      return {
        schemaVersion: trajectoryPlan.schemaVersion,
        bodyEphemerides: trajectoryPlan.bodyEphemerides,
        stateChain: trajectoryPlan.stateChain,
        routeSections: trajectoryPlan.routeSections,
        genericTrajectoryPlan: trajectoryPlan,
        startDate: trajectoryPlan.start.date,
        genericTarget: trajectoryPlan.target,
        totalFlightDays: trajectoryPlan.summary.totalFlightDays,
        warnings: trajectoryPlan.warnings,
        trajectory,
        segments: trajectoryPlan.segments,
        waypoint: {
          id: trajectoryPlan.target.bodyId ?? trajectoryPlan.target.zoneId ?? trajectoryPlan.target.boundaryId ?? trajectoryPlan.target.type,
          name: trajectoryPlan.target.bodyId ?? trajectoryPlan.target.zoneId ?? trajectoryPlan.target.boundaryId ?? trajectoryPlan.target.type,
          encounterDay: trajectoryPlan.summary.totalFlightDays,
          flybyAltitudeKm: 0,
          trajectoryIndex: Math.max(0, trajectory.length - 1),
          positionKm: targetPosition,
        },
        outgoingDirection,
        validation: {
          collisionFree: trajectoryPlan.validation?.collisionFree === true,
          minimumSolarRadiusKm,
          sunRadiusKm: 696_340,
          minimumSolarAltitudeKm: minimumSolarRadiusKm - 696_340,
        },
        summary: {
          flybyMode: 'multi-section',
          requiredInjectionDeltaVKmS: trajectoryPlan.summary.requiredInjectionDeltaVKmS ?? trajectoryPlan.summary.totalDeltaVKmS ?? 0,
          availableInjectionDeltaVKmS: Number((trajectoryPlan.input.constraints as Record<string, unknown> | undefined)?.maxTotalDeltaVKmS ?? 0),
          solarDepartureInjectionApplied: trajectoryPlan.summary.feasible,
          incomingExcessSpeedKmS: trajectoryPlan.summary.departureVInfinityKmS ?? 0,
          turnAngleDeg: 0,
          heliocentricSpeedBeforeKmS: Math.hypot(...trajectoryPlan.start.velocityKmS),
          heliocentricSpeedAfterKmS: trajectoryPlan.summary.finalHeliocentricSpeedKmS ?? finalSpeed,
          speedGainKmS: (trajectoryPlan.summary.finalHeliocentricSpeedKmS ?? finalSpeed) - Math.hypot(...trajectoryPlan.start.velocityKmS),
          targetCorrectionDeltaVKmS: Math.max(0, (trajectoryPlan.summary.totalDeltaVKmS ?? 0) - (trajectoryPlan.summary.requiredInjectionDeltaVKmS ?? 0)),
          targetInjectionApplied: trajectoryPlan.summary.feasible && (trajectoryPlan.maneuvers?.length ?? 0) > 1,
          passiveTargeting: trajectoryPlan.summary.feasible && (trajectoryPlan.maneuvers?.length ?? 0) === 0,
          courseChangeDeg: trajectoryPlan.summary.targetAlignmentDeg ?? 0,
          periapsisSpeedKmS: trajectoryPlan.summary.finalHeliocentricSpeedKmS ?? finalSpeed,
          observationWindowHours: 0,
          targetAlignmentDeg: trajectoryPlan.summary.targetAlignmentDeg ?? 0,
          feasibleWithConfiguredBurn: trajectoryPlan.summary.feasible && trajectoryPlan.summary.vehicleValidated === true && trajectoryPlan.summary.vehicleFeasible === true,
          warnings: trajectoryPlan.warnings,
          model: trajectoryPlan.summary.model,
        },
        audit: trajectoryPlan.audit as WaypointRouteResult['audit'],
      }
    }
}
