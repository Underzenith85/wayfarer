"""Private B244/B421 damaging permanent arm cripple, separate from Paralyze."""

from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.hit_locations import require_location
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.melee_spell_admission import admit_target
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class WitherLimbResult(Record):
    lasting_id: str
    dice: tuple[int, ...]
    injury: int
    hp_before: int
    hp_after: int
    injury_checks: tuple[CheckTrace, ...] = ()
    injury_check_reasons: tuple[str, ...] = ()
    dropped_item_ids: tuple[str, ...] = ()
    grip_checks: tuple[CheckTrace, ...] = ()


def apply_wither_arm(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    effect_id: str,
    actor_id: str,
    location: Literal["left-arm", "right-arm"],
) -> tuple[PlayState, Encounter, WitherLimbResult]:
    """Apply one accepted source effect; family receipts own exact command retries."""
    if location not in ("left-arm", "right-arm"):
        raise ValidationError("Wither arm requires its source-fixed limb")
    admit_target(runtime, state, actor_id)
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    actor = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if hp is None or hp.injury is None or actor is None:
        raise ValidationError("Wither arm requires current injury and encounter identity")
    if hp.maximum < 10:
        raise ValidationError("Wither arm requires an admitted HP maximum of at least ten")
    require_location(hp.injury, location)
    if not effect_id or any(w.id == effect_id for w in hp.injury.lasting_injuries):
        raise ConflictError("Wither arm effect identity is immutable")
    compiled = build(runtime, state, actor_id)
    if compiled.statistics is None:
        raise ValidationError("Arm grip retention requires current compiled DX")
    hp_before = hp.current
    dice = draw_dice(runtime.rng, 1)
    profiles = {e.definition_id: e for e in catalog(runtime).entries}
    current_items = {i.id: i for i in state.resources.items}
    # B287: ordinary shields are strapped; only bucklers drop like weapons.
    # Hand occupancy alone is not a freely held shield attachment claim.
    held = tuple(
        dict.fromkeys(
            i
            for i, _ in actor.hand_bindings
            if i in current_items
            and (
                (shield := profiles[current_items[i].definition_id].shield) is None
                or shield.buckler
            )
        )
    )
    resources, injury_result = apply_injury(
        state.resources,
        Wound(
            id=effect_id + ":injury",
            actor_id=actor_id,
            expected_revision=state.resources.revision,
            basic_damage=sum(dice),
            resistance=0,
            damage_type="cr",
        ),
        ht=compiled.statistics.ht,
        rng=runtime.rng,
        system=True,
        held_item_ids=held,
        force_major_wound=True,
    )
    state = state.model_copy(update={"resources": resources})
    hp = next(p for p in resources.pools if p.id == "hp:" + actor_id)
    assert hp.injury is not None
    items = {i.id: i for i in state.resources.items}
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    hand = location.replace("arm", "hand")
    affected = tuple(
        dict.fromkeys(
            item_id
            for item_id, held_hand in actor.hand_bindings
            if held_hand == hand
            and item_id in items
            and items[item_id].owner_id == actor_id
            and items[item_id].equipped
            and ((shield := entries[items[item_id].definition_id].shield) is None or shield.buckler)
        )
    )
    dropped: list[str] = list(injury_result.dropped_ready_items)
    checks: list[CheckTrace] = []
    for item_id in affected:
        if sum(i == item_id for i, _ in actor.hand_bindings) > 1:
            grip = success_roll(
                hp.injury.profile_id,
                compiled.statistics.dx,
                check_modifiers(state.resources, actor_id, "dx"),
                rng=runtime.rng,
            )
            checks.append(grip)
            if grip.outcome.succeeded:
                continue
        dropped.append(item_id)
    wound = LastingInjury(
        id=effect_id,
        location=location,
        kind="crippled",
        duration="permanent",
        inflicted_at=state.resources.game_time,
        injury=0,
    )
    updated_hp = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={"lasting_injuries": hp.injury.lasting_injuries + (wound,)}
            )
        }
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        updated_hp if p.id == hp.id else p for p in state.resources.pools
                    ),
                    "items": tuple(
                        i.model_copy(update={"ready": False, "equipped": False})
                        if i.id in dropped
                        else i
                        for i in state.resources.items
                    ),
                }
            )
        }
    )
    # deferred: the contact family and CombatEngine share a runtime context cycle.
    from wayfarer.engine.simulation.combat.settlement import reconcile_equipment

    # deferred: the unarmed control aggregate shares the contact runtime context cycle.
    from wayfarer.engine.simulation.combat.unarmed.fighters import settle_control

    state, encounter = reconcile_equipment(state, encounter)
    encounter = settle_control(state, encounter)
    return (
        state,
        encounter,
        WitherLimbResult(
            lasting_id=effect_id,
            dice=dice,
            injury=injury_result.injury,
            hp_before=hp_before,
            hp_after=hp.current,
            injury_checks=tuple(c.check for c in injury_result.checks),
            injury_check_reasons=tuple(c.reason for c in injury_result.checks),
            dropped_item_ids=tuple(dropped),
            grip_checks=tuple(checks),
        ),
    )
