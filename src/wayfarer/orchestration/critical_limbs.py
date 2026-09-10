"""Critical self-wounds reuse the authoritative location injury reducer."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.location_types import HumanLocation
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Encounter
from wayfarer.simulation.critical import Die, TableRoll
from wayfarer.simulation.gurps_equipment import MeleeMode, RangedMode, WeaponMode
from wayfarer.simulation.injury import DisableLocation, Wound, apply_injury, apply_location_effect
from wayfarer.simulation.resources import Record, ResourceEvent


class CriticalLimbResult(Record):
    kind: Literal["critical-limb-v1"] = "critical-limb-v1"
    # A ranged self-hit reroll can lead to a resistant-weapon confirmation roll.
    table_rolls: tuple[TableRoll, ...] = Field(min_length=1, max_length=3)
    location: HumanLocation | None = None
    location_dice: tuple[Die, ...] = ()
    damage_dice: tuple[Die, ...] = ()
    injury: int = Field(default=0, ge=0)
    lasting_injury_ids: tuple[str, ...] = ()
    resolved: bool = False


def resolve_limb(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    *,
    table: tuple[int, ...],
    defender_item: str | None,
    blocker: str,
    parry_mode_id: str | None = None,
) -> tuple[PlayState, Encounter, CriticalLimbResult]:
    """Missing anatomy/grips/mode preserves the exact original blocker, before dice."""
    from wayfarer.orchestration.gurps_melee import build, catalog

    result = CriticalLimbResult.model_validate({"table_rolls": (table,)})
    if sum(table) not in (5, 6, 15):
        return state, encounter, result
    pending = encounter.pending_defense
    assert pending is not None
    command_id = "critical-limb:" + hashlib.sha256(pending.id.encode()).hexdigest()
    previous = next((e for e in state.resources.events if e.id == command_id), None)
    if previous is not None:
        saved = CriticalLimbResult.model_validate_json(previous.kind)
        if saved.table_rolls[0] != table:
            raise ConflictError("Critical limb table cannot be replaced")
        return state, encounter, saved
    parrying = blocker.endswith(":defender")
    actor_id = pending.defender_id if parrying else pending.attacker_id
    item_id = defender_item if parrying else pending.weapon_id
    subject = next(p for p in encounter.participants if p.actor_id == actor_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.anatomy != "human":
        return state, encounter, result
    hands = tuple(h for i, h in subject.hand_bindings if i == item_id)
    if not hands:
        return state, encounter, result
    entries = {e.definition_id: e for e in catalog(play).entries}
    item = next(i for i in state.resources.items if i.id == item_id)
    modes: tuple[WeaponMode, ...] = tuple(
        m
        for m in entries[item.definition_id].modes
        if parrying
        and isinstance(m, MeleeMode)
        and m.parry is not None
        and (parry_mode_id is None or m.id == parry_mode_id)
    )
    if not parrying:
        modes = tuple(
            m
            for m in entries[item.definition_id].modes
            if isinstance(m, (MeleeMode, RangedMode)) and m.id == pending.mode_id
        )
    if sum(table) in (5, 6) and len(modes) != 1:
        return state, encounter, result
    held = tuple(
        i.id
        for i in state.resources.items
        if i.owner_id == actor_id
        and i.ready
        and i.equipped
        and (entries[i.definition_id].modes or entries[i.definition_id].shield)
    )
    if not set(held) <= {i for i, _ in subject.hand_bindings}:
        return state, encounter, result
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    stats = compiled.statistics
    rolls: tuple[tuple[int, ...], ...] = (table,)
    if sum(table) in (5, 6) and (
        isinstance(modes[0], RangedMode)
        or modes[0].damage.damage_type in ("imp", "pi-", "pi", "pi+", "pi++")
    ):
        table = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        rolls += (table,)
        result = result.model_copy(update={"table_rolls": rolls})
    number = sum(table)
    if number == 15:
        die = play.rng.randbelow(6) + 1 if len(hands) == 2 else None
        hand = hands[0 if die is None or die <= 3 else 1]
        arm: Literal["left-arm", "right-arm"] = "left-arm" if hand == "left-hand" else "right-arm"
        resources, injury = apply_location_effect(
            state.resources,
            DisableLocation(
                id=command_id,
                actor_id=actor_id,
                expected_revision=state.resources.revision,
                location=arm,
                duration_seconds=1800,
            ),
            system=True,
        )
        result = result.model_copy(
            update={
                "location": arm,
                "location_dice": (die,) if die else (),
                "lasting_injury_ids": injury.lasting_injury_ids,
                "resolved": True,
            }
        )
    elif number in (5, 6):
        mode = modes[0]
        body_die, side_die = (play.rng.randbelow(6) + 1 for _ in range(2))
        location: HumanLocation = (
            ("right-arm" if side_die <= 3 else "left-arm")
            if body_die <= 3
            else ("right-leg" if side_die <= 3 else "left-leg")
        )
        expression = stats.swing if mode.damage.basis == "swing" else stats.thrust
        if isinstance(mode, RangedMode) and mode.rated_strength is not None:
            from wayfarer.character.statistics import damage as strength_damage

            expression = strength_damage(catalog(play).profile_id, mode.rated_strength.st)[0]
        dice = tuple(play.rng.randbelow(6) + 1 for _ in range(mode.damage.dice or expression.dice))
        damage = max(
            0 if mode.damage.damage_type == "cr" else 1,
            sum(dice) + mode.damage.adds + (0 if mode.damage.basis == "fixed" else expression.add),
        )
        if number == 6:
            damage //= 2
        armor = tuple(
            entries[i.definition_id].armor
            for i in state.resources.items
            if i.owner_id == actor_id
            and i.equipped
            and (i.condition is None or not i.condition.disabled)
        )
        dr = max(
            (
                a.dr
                for a in armor
                if a is not None
                and (
                    location in a.locations or ("arms" if body_die <= 3 else "legs") in a.locations
                )
            ),
            default=0,
        )
        if play.engine.rules.abilities is not None:
            from wayfarer.simulation.abilities import damage_resistance

            dr += damage_resistance(state.resources, actor_id, build_revision=compiled.revision)
        resources, injury = apply_injury(
            state.resources,
            Wound(
                id=command_id,
                actor_id=actor_id,
                expected_revision=state.resources.revision,
                basic_damage=damage,
                resistance=dr,
                damage_type=mode.damage.damage_type,
                location=location,
                armor_divisor=mode.damage.armor_divisor,
                tight_beam=mode.damage.tight_beam,
            ),
            ht=stats.ht,
            dx=stats.dx,
            rng=play.rng,
            system=True,
            held_item_ids=held,
            held_item_locations=subject.hand_bindings,
            shield_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.id in held and entries[i.definition_id].shield
            ),
        )
        result = result.model_copy(
            update={
                "location": location,
                "location_dice": (body_die, side_die),
                "damage_dice": dice,
                "injury": injury.injury,
                "lasting_injury_ids": injury.lasting_injury_ids,
                "resolved": True,
            }
        )
    else:
        resources = state.resources
    if result.resolved or len(rolls) > 1:
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=command_id,
                        at=resources.game_time,
                        target_id=actor_id,
                        kind=result.model_dump_json(),
                    ),
                )
            }
        )
        state = state.model_copy(update={"resources": resources})
    if result.resolved:
        pool = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
        assert pool.injury is not None
        ready = tuple(
            i.id for i in resources.items if i.owner_id == actor_id and i.ready and i.equipped
        )
        subject = subject.model_copy(
            update={
                "posture": "prone" if pool.injury.prone else subject.posture,
                "ready_item_ids": ready,
                "hand_bindings": tuple((i, h) for i, h in subject.hand_bindings if i in ready),
            }
        )
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    subject if p.actor_id == actor_id else p for p in encounter.participants
                )
            }
        )
        if pool.injury.incapacitated:
            state = state.model_copy(
                update={
                    "actors": tuple(
                        a.model_copy(
                            update={
                                "conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))
                            }
                        )
                        if a.actor_id == actor_id
                        else a
                        for a in state.actors
                    )
                }
            )
    return state, encounter, result
