"""Private B244/B421 functional arm crippling, without invented HP injury.

Paralyze's considered-crippled effect uses ordinary arm/drop/grip consequences.
Its zero-HP effect does not introduce a damage-caused major-wound check; the
physical packet continues to own all of its normal injury checks.
"""

from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.hit_locations import require_location
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class LimbCrippleResult(Record):
    lasting_id: str
    recovery_at: int
    dropped_item_ids: tuple[str, ...] = ()
    grip_checks: tuple[CheckTrace, ...] = ()


def apply_paralyze_arm(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    effect_id: str,
    actor_id: str,
    location: Literal["left-arm", "right-arm"],
    duration_seconds: int = 60,
) -> tuple[PlayState, Encounter, LimbCrippleResult]:
    """Apply one accepted source effect; family receipts own exact command retries."""
    if duration_seconds != 60 or location not in ("left-arm", "right-arm"):
        raise ValidationError("Paralyze arm requires its source-fixed limb and minute")
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    actor = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if hp is None or hp.injury is None or actor is None:
        raise ValidationError("Paralyze arm requires current injury and encounter identity")
    require_location(hp.injury, location)
    if not effect_id or any(w.id == effect_id for w in hp.injury.lasting_injuries):
        raise ConflictError("Paralyze arm effect identity is immutable")
    compiled = build(runtime, state, actor_id)
    if compiled.statistics is None:
        raise ValidationError("Arm grip retention requires current compiled DX")
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
            and entries[items[item_id].definition_id].shield is None
        )
    )
    dropped: list[str] = []
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
    recovery_at = state.resources.game_time + duration_seconds
    wound = LastingInjury(
        id=effect_id,
        location=location,
        kind="crippled",
        duration="timed",
        inflicted_at=state.resources.game_time,
        recovery_at=recovery_at,
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
        LimbCrippleResult(
            lasting_id=effect_id,
            recovery_at=recovery_at,
            dropped_item_ids=tuple(dropped),
            grip_checks=tuple(checks),
        ),
    )
