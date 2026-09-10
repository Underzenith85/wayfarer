"""V2 close-combat choices without widening the frozen v1 projection."""

import hashlib

from wayfarer.errors import WayfarerError
from wayfarer.orchestration.combat import ResolveChokeEffects, TakeUnarmedTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import preview, visible_actors
from wayfarer.orchestration.unarmed import guard_control, validate_choke_resolution
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.resources import Record


class CloseCombatChoice(Record):
    label: str
    command: TakeUnarmedTurn | ResolveChokeEffects


def close_combat_choices(
    play: PlayService, state: PlayState, actor_id: str
) -> tuple[CloseCombatChoice, ...]:
    if state.lifecycle != "active":
        return ()
    result: list[CloseCombatChoice] = []
    names = {e.id: e.name for e in state.world.entities}
    for encounter in state.encounters:
        if (
            encounter.status != "active"
            or encounter.hex_battlefield is None
            or actor_id not in encounter.turn_order
        ):
            continue
        visible = visible_actors(state, encounter, actor_id)
        for target in encounter.participants:
            if target.actor_id == actor_id or target.actor_id not in visible:
                continue
            for skill in ("skill:judo", "skill:wrestling"):
                command = TakeUnarmedTurn.model_validate(
                    {
                        "id": "choke-choice:"
                        + hashlib.sha256(
                            f"{state.campaign_id}:{state.revision}:{encounter.id}:{actor_id}:{target.actor_id}:{skill}".encode()
                        ).hexdigest(),
                        "actor_id": actor_id,
                        "expected_revision": state.revision,
                        "encounter_id": encounter.id,
                        "action": "grapple",
                        "target_id": target.actor_id,
                        "skill": skill,
                        "hands": ("left-hand", "right-hand"),
                        "location": "neck",
                        "enter_close_combat": True,
                        "choke_hold": True,
                    }
                )
                try:
                    preview(play, state, encounter, command)
                except WayfarerError, ValueError:
                    continue
                result.append(
                    CloseCombatChoice(
                        label=f"Choke Hold {names[target.actor_id]} ({skill.removeprefix('skill:').title()})",
                        command=command,
                    )
                )
        for grip in encounter.grips:
            if grip.target_id != actor_id or grip.hazard_id is None:
                continue
            resolve = ResolveChokeEffects(
                id="choke-settle:"
                + hashlib.sha256(
                    f"{state.campaign_id}:{state.revision}:{grip.id}".encode()
                ).hexdigest(),
                actor_id=actor_id,
                expected_revision=state.revision,
                encounter_id=encounter.id,
                grip_id=grip.id,
            )
            try:
                guard_control(encounter, resolve, state)
                validate_choke_resolution(state, encounter, resolve)
            except WayfarerError, ValueError:
                continue
            result.append(CloseCombatChoice(label="Resolve suffocation", command=resolve))
    return tuple(result)
