"""Authoritative living-human grips and lasting injury effects for melee."""

import hashlib
from typing import Literal

from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.location_types import Hand, HitLocation, HumanLocation, disabled_locations
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Combatant, Encounter, Posture
from wayfarer.simulation.gurps_equipment import MeleeMode, RangedMode
from wayfarer.simulation.hit_locations import part, require_location, wound_factor
from wayfarer.simulation.injury import ResolveCrippling, apply_injury


def disabled(state: PlayState, actor_id: str) -> frozenset[HumanLocation]:
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    return (
        disabled_locations(
            hp.injury.lasting_injuries,
            now=state.resources.game_time,
            full_hp=hp.current >= hp.maximum,
        )
        if hp.injury
        else frozenset()
    )


def unavailable_hand(locations: frozenset[HumanLocation], hand: Hand) -> bool:
    return hand in locations or hand.replace("hand", "arm") in locations


def from_behind(attacker: Combatant, defender: Combatant) -> bool:
    from wayfarer.simulation.combat import GridPoint
    from wayfarer.simulation.hex_geometry import Hex, arc
    from wayfarer.simulation.tactical import pose

    if isinstance(attacker.position, Hex) and isinstance(defender.position, Hex):
        return arc(pose(defender), attacker.position) == "rear"
    if not isinstance(attacker.position, GridPoint) or not isinstance(defender.position, GridPoint):
        raise ValidationError("Mixed battlefield coordinates")
    x, y = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}[defender.facing]
    return (attacker.position.x - defender.position.x) * x + (
        attacker.position.y - defender.position.y
    ) * y < 0


def validate_target(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    attacker_id: str,
    defender_id: str,
    selected: MeleeMode | RangedMode,
    location: HitLocation | None,
) -> None:
    """Reject unsupported location intent before consciousness/exertion dice."""
    from wayfarer.orchestration.gurps_melee import catalog

    attacker = next((p for p in encounter.participants if p.actor_id == attacker_id), None)
    defender = next((p for p in encounter.participants if p.actor_id == defender_id), None)
    if attacker is None or defender is None:
        raise ValidationError("Attack requires encounter participants")
    occupied_hands = {h for g in encounter.grips if g.holder_id == attacker_id for h in g.hands}
    occupied_hands.update(
        "left-hand" if g.location == "left-arm" else "right-hand"
        for g in encounter.grips
        if g.target_id == attacker_id and g.location in ("left-arm", "right-arm")
    )
    if any(h in occupied_hands for _, h in attacker.hand_bindings):
        raise ValidationError("Selected weapon hand is controlled by a grapple")
    if isinstance(selected, RangedMode):
        if attacker.position == defender.position or any(
            attacker_id in (g.holder_id, g.target_id) for g in encounter.grips
        ):
            raise ValidationError(
                "Ranged attacks while in close combat require further integration"
            )
    elif attacker.position == defender.position and 0 not in selected.reach:
        raise ValidationError("Weapon does not support close-combat reach")
    hp = next(p for p in state.resources.pools if p.id == f"hp:{defender_id}")
    if hp.injury is None:
        raise ValidationError("GURPS injury requires explicit migration")
    require_location(hp.injury, location)
    if location in ("left-eye", "right-eye") and from_behind(attacker, defender):
        raise ValidationError("Eyes cannot be targeted from behind")
    if location is not None and location != "random":
        wound_factor(location, selected.damage.damage_type, tight_beam=selected.damage.tight_beam)
    if location is not None:
        _validate_bindings(play, state, defender)
        entries = {e.definition_id: e for e in catalog(play).entries}
        held = {
            i.id
            for i in state.resources.items
            if i.id in defender.ready_item_ids
            and (entries[i.definition_id].modes or entries[i.definition_id].shield)
        }
        if not held <= {i for i, _ in defender.hand_bindings}:
            raise ValidationError(
                "Location attacks require explicit defender weapon/shield hand bindings"
            )


def validate_posture(state: PlayState, actor_id: str, posture: Posture | None) -> None:
    if posture == "standing" and any(part(p) in ("leg", "foot") for p in disabled(state, actor_id)):
        raise ValidationError(
            "Crippled leg or foot requires a supported posture; standing support is unavailable"
        )


def _validate_bindings(play: PlayService, state: PlayState, participant: Combatant) -> None:
    from wayfarer.orchestration.gurps_melee import catalog

    items = {
        i.id: i
        for i in state.resources.items
        if i.owner_id == participant.actor_id and i.ready and i.equipped
    }
    entries = {e.definition_id: e for e in catalog(play).entries}
    if len({h for _, h in participant.hand_bindings}) != len(participant.hand_bindings):
        raise ValidationError("Each hand holds at most one item")
    for item_id, _ in participant.hand_bindings:
        if item_id not in items or not (
            entries[items[item_id].definition_id].modes
            or entries[items[item_id].definition_id].shield
        ):
            raise ValidationError("Grip requires an owned ready weapon or shield")


def bind_initial_hands(play: PlayService, state: PlayState, encounter: Encounter) -> Encounter:
    actors = {a.actor_id: a for a in state.actors}
    live = {i.id for i in state.resources.items if i.ready and i.equipped}
    participants = tuple(
        p.model_copy(
            update={
                "hand_bindings": tuple(
                    (i, h) for i, h in actors[p.actor_id].held_item_hands if i in live
                )
            }
        )
        for p in encounter.participants
    )
    for participant in participants:
        _validate_bindings(play, state, participant)
    return encounter.model_copy(update={"participants": participants})


def bind_ready_hand(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    item_id: str,
    hand: Hand | Literal["both"] | None,
) -> Encounter:
    participant = next(p for p in encounter.participants if p.actor_id == actor_id)
    if hand is None:
        if disabled(state, actor_id):
            raise ValidationError("Ready requires an explicit usable hand after crippling")
        return encounter
    hands: tuple[Hand, ...] = ("left-hand", "right-hand") if hand == "both" else (hand,)
    from wayfarer.orchestration.gurps_melee import catalog

    item = next(i for i in state.resources.items if i.id == item_id)
    shield = next(e.shield for e in catalog(play).entries if e.definition_id == item.definition_id)
    unavailable = disabled(state, actor_id)
    if any(
        h.replace("hand", "arm") in unavailable if shield else unavailable_hand(unavailable, h)
        for h in hands
    ):
        raise ValidationError("Cannot ready equipment in a crippled hand or arm")
    # Remove bindings for equipment that is no longer held; preserve the other hand.
    live = {
        i.id for i in state.resources.items if i.owner_id == actor_id and i.ready and i.equipped
    }
    bindings = tuple((i, h) for i, h in participant.hand_bindings if i in live and i != item_id)
    participant = participant.model_copy(
        update={"hand_bindings": bindings + tuple((item_id, h) for h in hands)}
    )
    _validate_bindings(play, state, participant)
    return encounter.model_copy(
        update={
            "participants": tuple(
                participant if p.actor_id == actor_id else p for p in encounter.participants
            )
        }
    )


def item_hands(state: PlayState, actor_id: str, item_id: str) -> tuple[Hand, ...]:
    participant = next(
        (
            p
            for e in state.encounters
            if e.status == "active"
            for p in e.participants
            if p.actor_id == actor_id
        ),
        None,
    )
    if participant:
        return tuple(h for i, h in participant.hand_bindings if i == item_id)
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    return tuple(h for i, h in actor.held_item_hands if i == item_id)


def settle_crippling(
    play: PlayService, state: PlayState, encounter: Encounter, command_id: str
) -> PlayState:
    """B422: roll duration once at combat end, saved inside the combat CAS."""
    from wayfarer.orchestration.gurps_melee import build

    resources = state.resources
    for participant in encounter.participants:
        pool = next(p for p in resources.pools if p.id == f"hp:{participant.actor_id}")
        if pool.injury is None or pool.injury.dead:
            continue
        for wound in pool.injury.lasting_injuries:
            if wound.duration != "pending":
                continue
            compiled = build(play, state, participant.actor_id)
            assert compiled.statistics is not None
            resources, _ = apply_injury(
                resources,
                ResolveCrippling(
                    id="crippling:"
                    + hashlib.sha256(f"{command_id}:{wound.id}".encode()).hexdigest(),
                    actor_id=participant.actor_id,
                    expected_revision=resources.revision,
                    injury_id=wound.id,
                ),
                ht=compiled.statistics.ht,
                rng=play.rng,
                system=True,
            )
    return state.model_copy(update={"resources": resources})
