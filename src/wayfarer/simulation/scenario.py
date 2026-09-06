"""Fixed demo scenario and validation."""

from wayfarer import validation


def scenario() -> dict[str, str]:
    return {
        "title": "The Lantern at Blackwater",
        "premise": "A courier vanished before the last ferry. A blue lantern still burns at the abandoned customs house.",
        "location": "Blackwater docks",
        "contact": "Iven, the ferryman",
        "clue": "A torn ferry manifest points to the customs house.",
        "secret": "The courier is sheltering a witness inside the customs house.",
        "objective": "Find the missing courier",
    }


def validate_scenario(s: object) -> None:
    validation.scenario(s)
