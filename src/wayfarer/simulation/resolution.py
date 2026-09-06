"""Pure demo state transition. No persistence, HTTP or provider imports."""

from wayfarer.character import builder
from wayfarer.models import Action, Campaign, Event
from wayfarer.rules import checks


def resolve(s: Campaign, action: Action, text: str) -> Event:
    result = None
    if action == "ask":
        outcome = "You can inspect the docks, talk to the ferryman, follow a discovered lead, or rest. Questions do not spend time."
    elif s["complete"]:
        outcome = "The courier is safe. This prototype adventure is complete; start another campaign in the Scenario studio."
    elif action == "rest":
        s["fp"] = min(s["character"]["attributes"]["HT"], s["fp"] + 1)
        s["minutes"] += 30
        outcome = "You rest for thirty minutes and recover up to one fatigue point."
    elif action == "sneak" and not s["discoveries"]:
        outcome = "You need a lead before approaching the courier. Inspect the docks or talk to the ferryman."
    elif s["fp"] <= 0:
        outcome = "You are exhausted. Rest before attempting another check."
    else:
        skill = {"observe": "Observation", "talk": "Diplomacy", "sneak": "Stealth"}[action]
        target = builder.validate(s["character"])["levels"].get(skill)
        if target is None:
            outcome = f"You have not trained {skill}. Untrained checks are outside this prototype ruleset."
        else:
            result = checks.roll(target)
            s["minutes"] += 10
            if result["success"]:
                if action in ("observe", "talk"):
                    clue = s["scenario"]["clue"]
                    if clue not in s["discoveries"]:
                        s["discoveries"].append(clue)
                    if action == "talk" and "Ferryman trusts you" not in s["flags"]:
                        s["flags"].append("Ferryman trusts you")
                    outcome = clue
                else:
                    s["location"] = "Customs house"
                    s["complete"] = True
                    s["discoveries"].append(s["scenario"]["secret"])
                    s["inventory"].append("Courier’s letter")
                    outcome = (
                        s["scenario"]["secret"]
                        + " You secure the courier’s letter. Objective complete."
                    )
            else:
                s["fp"] -= 1
                outcome = "The attempt fails. You lose one fatigue point and ten minutes pass. No new information is discovered."
    event: Event = {"input": text, "action": action, "outcome": outcome, "roll": result}
    s["messages"].extend(
        [
            {"role": "player", "text": text},
            {"role": "gm", "text": outcome, "roll": result, "action": action},
        ]
    )
    s["revision"] += 1
    return event
