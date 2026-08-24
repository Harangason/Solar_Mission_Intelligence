export interface ValidatableRouteSection {
  id: string
  originId: string
  targetId: string
  deltaVMinusKmS: number
  deltaVPlusKmS: number
}

export function routeSectionsBlockReason(sections: ValidatableRouteSection[]) {
  const ids = new Set<string>()
  for (let index = 0; index < sections.length; index += 1) {
    const section = sections[index]
    if (!section.id || !section.originId || !section.targetId) {
      return `Routenabschnitt ${index + 1} hat keinen gültigen Start- oder Zielpunkt.`
    }
    if (ids.has(section.id)) {
      return `Die Abschnitts-ID '${section.id}' wird mehrfach verwendet.`
    }
    ids.add(section.id)
    if (section.originId === section.targetId) {
      return `Routenabschnitt ${index + 1} beginnt und endet bei '${section.originId}'.`
    }
    const previous = sections[index - 1]
    if (previous && section.originId !== previous.targetId) {
      return `Routenabschnitt ${index + 1} beginnt bei '${section.originId}', erwartet wird '${previous.targetId}'.`
    }
    if (!Number.isFinite(section.deltaVMinusKmS) || section.deltaVMinusKmS < 0
      || !Number.isFinite(section.deltaVPlusKmS) || section.deltaVPlusKmS < 0) {
      return `Routenabschnitt ${index + 1} enthält ein ungültiges Δv-Budget.`
    }
  }
  return null
}
