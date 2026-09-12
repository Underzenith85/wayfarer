"""B376 weapon-on-weapon parry breakage inside the existing combat transaction."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.rules.types.object import residual_definition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.combat import Combatant, Encounter
from wayfarer.engine.simulation.combat.critical import Die
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.resources import Item, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class HeavyParryResult(Record):
    kind: Literal["heavy-parry-v1"] = "heavy-parry-v1"
    item_id: str
    incoming_item_id: str
    incoming_weight: int = Field(gt=0)
    weapon_weight: int = Field(gt=0)
    quality: Literal["cheap", "good", "fine", "very-fine"]
    breakage_threshold: int
    die: Die
    residual_die: Die | None = None
    broken: bool
    stopped: bool


def require_breakage(entry: EquipmentProfile, item: Item) -> None:
    """Reject unmodelled contact before any attack or defense dice are drawn."""
    if entry.durability is None or item.condition is None or entry.parry_quality is None:
        raise ValidationError("Heavy parry requires pinned weapon durability and parry quality")


def resolve_heavy_parry(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    defender: Combatant,
    item_id: str,
) -> tuple[PlayState, Combatant, tuple[int, ...], bool]:
    from wayfarer.engine.simulation.combat.melee import heavy_parry_weight
    from wayfarer.engine.simulation.combat.objects.combat import effective_entry, synchronize

    pending = encounter.pending_defense
    assert pending is not None
    weight = heavy_parry_weight(runtime, state, defender)
    if weight is None:
        return state, defender, (), True
    event_id = "heavy-parry:" + hashlib.sha256(f"{pending.id}:{item_id}".encode()).hexdigest()
    prior = next((e for e in state.resources.events if e.id == event_id), None)
    if prior:
        saved = HeavyParryResult.model_validate_json(prior.kind)
        if saved.item_id != item_id or saved.incoming_item_id != pending.weapon_id:
            raise ConflictError("Recorded heavy parry cannot be replaced")
    else:
        item = next(i for i in state.resources.items if i.id == item_id)
        entry = effective_entry(runtime, item)
        weapon_weight = entry.weight_millipounds
        if not isinstance(weapon_weight, int):
            raise ValidationError("Parrying weapons require integral millipound weight")
        if weapon_weight == 0 or weight < 3 * weapon_weight:
            return state, defender, (), True
        require_breakage(entry, item)
        assert entry.parry_quality is not None
        assert entry.durability is not None and item.condition is not None
        quality = {"cheap": 2, "good": 0, "fine": -1, "very-fine": -2}[entry.parry_quality]
        threshold = weight // weapon_weight - 1 + quality
        die = draw_dice(runtime.rng, 1)[0]
        broken = die <= threshold
        residual = (
            draw_dice(runtime.rng, 1)[0]
            if broken and entry.durability.residual_definitions
            else None
        )
        condition = (
            item.condition.model_copy(update={"disabled": True, "residual_roll": residual})
            if broken
            else item.condition
        )
        usable = not broken or residual_definition(entry.durability, condition) is not None
        updated = item.model_copy(update={"condition": condition, "ready": usable})
        saved = HeavyParryResult(
            item_id=item_id,
            incoming_item_id=pending.weapon_id,
            incoming_weight=weight,
            weapon_weight=weapon_weight,
            quality=entry.parry_quality,
            breakage_threshold=threshold,
            die=die,
            residual_die=residual,
            broken=broken,
            stopped=threshold <= 6,
        )
        resources = state.resources.model_copy(
            update={
                "items": tuple(updated if i.id == item_id else i for i in state.resources.items),
                "events": state.resources.events
                + (
                    ResourceEvent(
                        id=event_id,
                        at=state.resources.game_time,
                        target_id=item_id,
                        kind=saved.model_dump_json(),
                    ),
                ),
            }
        )
        runtime.resources.validate(resources)
        state = state.model_copy(update={"resources": resources})
    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                defender if p.actor_id == defender.actor_id else p for p in encounter.participants
            )
        }
    )
    defender = next(
        p for p in synchronize(state, encounter).participants if p.actor_id == defender.actor_id
    )
    return (
        state,
        defender,
        (saved.die,) + ((saved.residual_die,) if saved.residual_die else ()),
        saved.stopped,
    )
