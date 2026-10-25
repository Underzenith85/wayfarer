"""Selected B379: erosion follows a physical armor item after the current hit."""

from decimal import Decimal

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.equipment.basic.armor import ARMOR
from wayfarer.engine.simulation.equipment.corrosion_state import (
    PREFIX,
    CorrosionProtection,
    erosion,
    profile_digest,
    projected_profile,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import Item, Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError

PLATE = next(e for e in ARMOR if e.definition_id == "equipment:lorica-segmentata")


def projected_dr(state: ResourceState, item: Item) -> int:
    armor = projected_profile(state, item, PLATE).armor
    assert armor is not None
    return armor.dr


def total_loss(state: ResourceState, item_id: str) -> int:
    value = erosion(state, item_id)
    assert value is not None
    return value.total_loss


def initial() -> ResourceState:
    return ResourceState(
        items=(Item(id="plate", definition_id=PLATE.definition_id, owner_id="b", equipped=True),),
        pools=(
            Pool(
                id="hp:b",
                current=100,
                maximum=100,
                injury=InjuryStatus(
                    profile_id="gurps-basic-set-4e-2004",
                ),
            ),
        ),
    )


def context(state: ResourceState) -> CorrosionProtection:
    prior = erosion(state, "plate")
    return CorrosionProtection(
        actor_id="b",
        item_id="plate",
        definition_id=PLATE.definition_id,
        profile_digest=profile_digest(PLATE),
        original_dr=5,
        previous_loss=0 if prior is None else prior.total_loss,
    )


def hit(state: ResourceState, damage: int, identifier: str) -> ResourceState:
    protection = context(state)
    result, injury = apply_injury(
        state,
        Wound(
            id=identifier,
            actor_id="b",
            expected_revision=state.revision,
            damage_type="cor",
            basic_damage=damage,
            resistance=protection.resistance,
        ),
        ht=10,
        rng=RecordedDice(()),
        system=True,
        corrosion=protection,
    )
    assert injury.effective_resistance == protection.resistance
    assert injury.injury == max(0, damage - protection.resistance)
    return result


def test_stopped_corrosion_erodes_only_future_hits_and_retries_exactly() -> None:
    before = initial()
    value = context(before)
    command = Wound(
        id="acid",
        actor_id="b",
        expected_revision=0,
        damage_type="cor",
        basic_damage=5,
        resistance=5,
    )
    state, result = apply_injury(
        before, command, ht=10, rng=RecordedDice(()), system=True, corrosion=value
    )
    assert result.injury == 0 and result.effective_resistance == 5 and state.pools == before.pools
    item = state.items[0]
    assert projected_dr(state, item) == 4
    assert PLATE.armor is not None and PLATE.armor.dr == 5 and state.items == before.items
    assert len([e for e in state.events if e.id.startswith(PREFIX)]) == 1
    assert apply_injury(
        state, command, ht=10, rng=RecordedDice(()), system=True, corrosion=value
    ) == (state, result)
    with pytest.raises(ConflictError, match="different payload"):
        apply_injury(state, command, ht=10, rng=RecordedDice(()), system=True)


def test_sub_five_hits_do_not_accumulate_and_loss_saturates_without_negative_dr() -> None:
    state = hit(initial(), 4, "one")
    state = hit(state, 4, "two")
    assert erosion(state, "plate") is None
    state = hit(state, 25, "big")
    assert total_loss(state, "plate") == 5
    assert projected_dr(state, state.items[0]) == 0
    state = hit(state, 5, "bare")
    assert total_loss(state, "plate") == 5
    assert len([e for e in state.events if e.id.startswith(PREFIX)]) == 1


def test_physical_identity_survives_transfer_reequip_time_and_hp_condition_changes() -> None:
    state = hit(initial(), 5, "acid")
    original = state.items[0]
    transferred = original.model_copy(update={"owner_id": "a", "equipped": False})
    state = state.model_copy(update={"items": (transferred,), "game_time": 86400})
    assert projected_dr(state, transferred) == 4
    repaired = transferred.model_copy(
        update={"equipped": True, "condition": ObjectCondition(hp=12)}
    )
    state = state.model_copy(update={"items": (repaired,)})
    assert projected_dr(state, repaired) == 4
    replacement = repaired.model_copy(update={"id": "replacement"})
    assert projected_dr(state, replacement) == 5
    altered = PLATE.model_copy(update={"price": PLATE.price + 1})
    with pytest.raises(ConflictError, match="pinned physical"):
        projected_profile(state, repaired, altered)


@pytest.mark.parametrize(
    "wrong", ["custody", "equipped", "prior", "resistance", "divisor", "location"]
)
def test_changed_or_unsupported_injury_context_refuses_before_dice(wrong: str) -> None:
    state = initial()
    value = context(state)
    command = Wound(
        id="acid",
        actor_id="b",
        expected_revision=0,
        basic_damage=5,
        resistance=5,
        damage_type="cor",
    )
    if wrong in ("custody", "equipped"):
        state = state.model_copy(
            update={
                "items": (
                    state.items[0].model_copy(
                        update={
                            "owner_id": "a" if wrong == "custody" else "b",
                            "equipped": wrong != "equipped",
                        }
                    ),
                )
            }
        )
    elif wrong == "prior":
        value = value.model_copy(update={"previous_loss": 1})
    elif wrong == "resistance":
        command = command.model_copy(update={"resistance": 4})
    elif wrong == "divisor":
        command = command.model_copy(update={"armor_divisor": Decimal(2)})
    else:
        command = command.model_copy(update={"location": "left-arm"})
    dice = RecordedDice(())
    with pytest.raises((ConflictError, ValidationError)):
        apply_injury(state, command, ht=10, rng=dice, system=True, corrosion=value)
    assert dice.exhausted() and erosion(state, "plate") is None


def test_unmarked_historical_corrosion_retains_its_hp_only_receipt() -> None:
    before = initial()
    command = Wound(
        id="old", actor_id="b", expected_revision=0, damage_type="cor", basic_damage=5, resistance=5
    )
    state, result = apply_injury(before, command, ht=10, rng=RecordedDice(()), system=True)
    assert result.injury == 0 and erosion(state, "plate") is None
    assert apply_injury(state, command, ht=10, rng=RecordedDice(()), system=True) == (state, result)
