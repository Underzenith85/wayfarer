"""Explicit migration and continuation of complete, immutable critical self-wound contexts."""

import hashlib
from typing import Literal

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.combat import Encounter
from wayfarer.engine.simulation.combat.critical import CriticalMiss, load_critical
from wayfarer.engine.simulation.health.injury import (
    DisableLocation,
    Wound,
    apply_injury,
    apply_location_effect,
)
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class Continuation(Record):
    kind: Literal["critical-continuation-v1"] = "critical-continuation-v1"
    critical_id: str
    context_digest: str
    status: Literal["migrated", "resolved"]
    location: HumanLocation | None = None
    dice: tuple[int, ...] = ()
    injury: int = 0
    incoming_injury: int = 0


def context(
    runtime: RulesContext, state: PlayState, encounter: Encounter, critical_id: str
) -> CriticalMiss:
    from wayfarer.engine.simulation.combat.melee import build, catalog

    saved = load_critical(state.resources, critical_id)
    if (
        saved is None
        or saved.encounter_id != encounter.id
        or encounter.blocked_reason != saved.blocker
    ):
        raise ValidationError("Critical context does not match this blocked encounter")
    if len(saved.weapons) != 1 or saved.anatomy != "human":
        raise ValidationError("Ambiguous legacy critical context cannot be migrated")
    if saved.table_total not in (5, 6, 15):
        raise ValidationError("This critical table result requires another continuation adapter")
    if (
        saved.table_total in (5, 6)
        and saved.weapons[0].mode.damage.damage_type in ("imp", "pi-", "pi", "pi+", "pi++")
        and len(saved.table_rolls) == 1
    ):
        raise ValidationError("An unresolved critical reroll requires another continuation adapter")
    if not {saved.item_id, *saved.held_item_ids} <= {i for i, _ in saved.hand_bindings}:
        raise ValidationError("Ambiguous legacy grips cannot be migrated")
    if {p for p, _ in saved.limb_dr} != {"left-arm", "right-arm", "left-leg", "right-leg"}:
        raise ValidationError("Original limb protection is incomplete")
    if build(runtime, state, saved.subject_id).revision != saved.build_revision:
        raise ConflictError("Original critical build is no longer active")
    if (
        hashlib.sha256(catalog(runtime).model_dump_json().encode()).hexdigest()
        != saved.catalog_digest
    ):
        raise ConflictError("Original critical catalog is no longer active")
    item = next((i for i in state.resources.items if i.id == saved.item_id), None)
    if item is None or item.owner_id != saved.subject_id:
        raise ConflictError("Original critical weapon custody changed")
    return saved


def continue_critical(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    critical_id: str,
    command_id: str,
    stage: Literal["migrate", "resume"],
) -> tuple[PlayState, Encounter, Continuation]:
    saved = context(runtime, state, encounter, critical_id)
    digest = hashlib.sha256(saved.model_dump_json().encode()).hexdigest()
    prior = next(
        (
            Continuation.model_validate_json(e.kind)
            for e in reversed(state.resources.events)
            if e.id.startswith("critical-continuation:") and e.target_id == critical_id
        ),
        None,
    )
    if stage == "migrate":
        if prior is not None:
            raise ConflictError("Critical continuation is already migrated")
        result = Continuation(critical_id=critical_id, context_digest=digest, status="migrated")
    else:
        if prior is None or prior.status != "migrated" or prior.context_digest != digest:
            raise ConflictError("Resume requires an explicit migration of the unchanged context")
        weapon = saved.weapons[0]
        dice: tuple[int, ...]
        injury = 0
        if saved.table_total == 15:
            hands = tuple(h for i, h in saved.hand_bindings if i == saved.item_id)
            die = draw_dice(runtime.rng, 1)[0] if len(hands) > 1 else None
            hand = hands[0 if die is None or die <= 3 else 1]
            arm: Literal["left-arm", "right-arm"] = (
                "left-arm" if hand == "left-hand" else "right-arm"
            )
            location: HumanLocation = arm
            dice = (die,) if die else ()
            resources, _ = apply_location_effect(
                state.resources,
                DisableLocation(
                    id="continued-limb:" + critical_id,
                    actor_id=saved.subject_id,
                    expected_revision=state.resources.revision,
                    location=arm,
                    duration_seconds=1800,
                ),
                system=True,
            )
        else:
            body, side = (draw_dice(runtime.rng, 1)[0] for _ in range(2))
            location = (
                ("right-arm" if side <= 3 else "left-arm")
                if body <= 3
                else ("right-leg" if side <= 3 else "left-leg")
            )
            damage_dice = draw_dice(runtime.rng, weapon.dice)
            dice = (body, side, *damage_dice)
            basic = max(
                0 if weapon.mode.damage.damage_type == "cr" else 1, sum(damage_dice) + weapon.adds
            )
            if saved.table_total == 6:
                basic //= 2
            from wayfarer.engine.simulation.combat.melee import build

            stats = build(runtime, state, saved.subject_id).statistics
            assert stats is not None
            resources, wound = apply_injury(
                state.resources,
                Wound(
                    id="continued-limb:" + critical_id,
                    actor_id=saved.subject_id,
                    expected_revision=state.resources.revision,
                    basic_damage=basic,
                    resistance=dict(saved.limb_dr)[location],
                    damage_type=weapon.mode.damage.damage_type,
                    location=location,
                    armor_divisor=weapon.mode.damage.armor_divisor,
                    tight_beam=weapon.mode.damage.tight_beam,
                ),
                ht=saved.ht,
                dx=stats.dx,
                rng=runtime.rng,
                system=True,
                held_item_ids=saved.held_item_ids,
                held_item_locations=saved.hand_bindings,
            )
            injury = wound.injury
        incoming_injury = 0
        if saved.incoming:
            incoming = saved.incoming
            incoming_dice = draw_dice(runtime.rng, incoming.dice)
            dice += incoming_dice
            resources, wound = apply_injury(
                resources,
                Wound(
                    id="continued-incoming:" + critical_id,
                    actor_id=saved.subject_id,
                    expected_revision=resources.revision,
                    basic_damage=max(
                        0 if incoming.damage_type == "cr" else 1, sum(incoming_dice) + incoming.adds
                    ),
                    resistance=incoming.resistance,
                    damage_type=incoming.damage_type,
                    location=incoming.hit_location,
                    armor_divisor=incoming.armor_divisor,
                    tight_beam=incoming.tight_beam,
                ),
                ht=incoming.ht,
                dx=incoming.dx,
                rng=runtime.rng,
                system=True,
                held_item_ids=incoming.held_item_ids,
                held_item_locations=incoming.hand_bindings,
                shield_item_ids=incoming.shield_item_ids,
            )
            incoming_injury = wound.injury
        state = state.model_copy(update={"resources": resources})
        from wayfarer.engine.simulation.combat.objects.combat import synchronize

        encounter = synchronize(state, encounter).model_copy(update={"blocked_reason": None})
        pool = next(p for p in resources.pools if p.id == f"hp:{saved.subject_id}")
        assert pool.injury is not None
        if pool.injury.prone:
            encounter = encounter.model_copy(
                update={
                    "participants": tuple(
                        p.model_copy(update={"posture": "prone"})
                        if p.actor_id == saved.subject_id
                        else p
                        for p in encounter.participants
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
                        if a.actor_id == saved.subject_id
                        else a
                        for a in state.actors
                    )
                }
            )
        result = Continuation(
            critical_id=critical_id,
            context_digest=digest,
            status="resolved",
            location=location,
            dice=dice,
            injury=injury,
            incoming_injury=incoming_injury,
        )
    resources = state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id="critical-continuation:" + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.resources.game_time,
                    target_id=critical_id,
                    kind=result.model_dump_json(),
                ),
            )
        }
    )
    return state.model_copy(update={"resources": resources}), encounter, result
