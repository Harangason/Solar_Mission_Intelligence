"""Allowlisted actions the interaction AI may propose to the UI."""

from __future__ import annotations

from typing import Any


ALLOWED_INTERACTION_ACTIONS = {
    "focus-route-section",
    "set-projection",
    "run-route-solver",
}
ALLOWED_PROJECTIONS = {"corridor", "side", "top"}


def validate_interaction_actions(
    actions: object,
    mission_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reject unknown or malformed actions instead of forwarding them to the UI."""
    if not isinstance(actions, list):
        raise ValueError("KI-Aktionen muessen als Liste vorliegen.")

    section_ids = {
        str(section.get("id"))
        for section in mission_state.get("routeSections", [])
        if isinstance(section, dict) and section.get("id")
    }
    validated: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            raise ValueError("Eine KI-Aktion ist ungueltig.")
        action_type = item.get("type")
        if action_type not in ALLOWED_INTERACTION_ACTIONS:
            raise ValueError(f"Nicht erlaubte KI-Aktion: {action_type!r}")
        if item.get("requiresConfirmation") is not True:
            raise ValueError("KI-Aktionen erfordern immer eine Nutzerbestaetigung.")

        section_id = item.get("sectionId")
        projection = item.get("projection")
        if action_type == "focus-route-section":
            if section_id not in section_ids:
                raise ValueError("Die KI referenziert einen unbekannten Routenabschnitt.")
            projection = None
        elif action_type == "set-projection":
            if projection not in ALLOWED_PROJECTIONS:
                raise ValueError("Die KI referenziert eine unbekannte 2D-Ansicht.")
            section_id = None
        else:
            if not section_ids:
                raise ValueError("Ohne Routenabschnitt kann kein Solverlauf vorgeschlagen werden.")
            section_id = None
            projection = None

        validated.append({
            "type": action_type,
            "sectionId": section_id,
            "projection": projection,
            "requiresConfirmation": True,
        })
    return validated
