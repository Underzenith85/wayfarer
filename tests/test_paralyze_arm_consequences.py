"""Canonical consequence diagnostics; real Paralyze producer is tested separately."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_staff_melee_contact import _charged_encounter

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.unarmed.fighters import free_hands
from wayfarer.engine.simulation.combat.unarmed.records import Grip
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.limb_cripple_effects import apply_paralyze_arm
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "equipment,retain", [(None, False), ("shield", True), ("staff", True), ("staff", False)]
)
async def test_paralyze_arm_drop_grip_and_shield_consequence_diagnostic(
    tmp_path: Path,
    backend: str,
    equipment: Literal["staff", "shield"] | None,
    retain: bool,
) -> None:
    cid, play, _ = await _charged_encounter(tmp_path, backend, defender_item=equipment)
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury is not None
    before = hp.model_dump_json()
    dice = (
        RecordedDice((1, 1, 1) if retain else (6, 6, 6))
        if equipment == "staff"
        else RecordedDice(())
    )
    context = replace(play.rules_context, rng=dice)
    final, encounter, result = apply_paralyze_arm(
        context,
        state,
        state.encounters[0],
        effect_id="diagnostic-paralyze",
        actor_id="b",
        location="left-arm",
    )
    assert dice.exhausted()
    updated_hp = next(p for p in final.resources.pools if p.id == "hp:b")
    assert updated_hp.current == hp.current and updated_hp.maximum == hp.maximum
    assert updated_hp.injury is not None
    fact = updated_hp.injury.lasting_injuries[-1]
    assert fact.kind == "crippled" and fact.injury == 0 and fact.duration == "timed"
    assert fact.recovery_at == state.resources.game_time + 60
    assert len(result.grip_checks) == int(equipment == "staff")
    assert result.dropped_item_ids == (
        ("defender-implement",) if equipment == "staff" and not retain else ()
    )
    actor = next(p for p in encounter.participants if p.actor_id == "b")
    if equipment is not None:
        item = next(i for i in final.resources.items if i.id == "defender-implement")
        assert item.ready == retain and item.equipped == retain
        assert bool(actor.hand_bindings) == retain
    assert "left-arm" in disabled(final.resources, "b")
    assert "right-arm" not in disabled(final.resources, "b")
    assert hp.model_dump_json() == before
    final = final.model_copy(update={"encounters": (encounter,)})
    if equipment == "shield":
        original_actor = next(p for p in state.encounters[0].participants if p.actor_id == "b")
        original_dodge, _ = standard_defense_value(context, state, original_actor, "dodge")
        current_dodge, _ = standard_defense_value(context, final, actor, "dodge")
        assert original_dodge is not None and current_dodge is not None
        assert original_dodge.value - current_dodge.value == 1
        original_block, _ = standard_defense_value(
            context, state, original_actor, "block", "defender-implement"
        )
        assert original_block is not None
        with pytest.raises(ValidationError, match="No available skill/equipment"):
            standard_defense_value(context, final, actor, "block", "defender-implement")
    if equipment == "staff":
        with pytest.raises(ValidationError, match="crippled|ready weapon"):
            mode(context, final, "b", "defender-implement", "staff-thrust")
    with pytest.raises(ConflictError, match="immutable"):
        apply_paralyze_arm(
            context,
            final,
            encounter,
            effect_id="diagnostic-paralyze",
            actor_id="b",
            location="left-arm",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paralyze_arm_expiry_does_not_heal_older_cripple_diagnostic(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await _charged_encounter(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury is not None
    older = LastingInjury(
        id="older-arm",
        location="left-arm",
        kind="crippled",
        duration="permanent",
        inflicted_at=0,
        injury=1,
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(
                            update={
                                "injury": hp.injury.model_copy(
                                    update={"lasting_injuries": (older,)}
                                )
                            }
                        )
                        if p.id == hp.id
                        else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    final, encounter, result = apply_paralyze_arm(
        replace(play.rules_context, rng=RecordedDice(())),
        state,
        state.encounters[0],
        effect_id="timed-right",
        actor_id="b",
        location="right-arm",
    )
    before_expiry = final.resources.model_copy(update={"game_time": result.recovery_at - 1})
    expired = final.resources.model_copy(update={"game_time": result.recovery_at})
    assert disabled(before_expiry, "b") == {"left-arm", "right-arm"}
    assert disabled(expired, "b") == {"left-arm"}
    assert next(p for p in encounter.participants if p.actor_id == "b").posture == "standing"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paralyze_arm_releases_actual_grip_without_recreating_it_on_expiry_diagnostic(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await _charged_encounter(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    grip = Grip(id="canonical-grip", holder_id="b", target_id="a", hands=("left-hand",))
    encounter = state.encounters[0].model_copy(update={"grips": (grip,)})
    state = state.model_copy(update={"encounters": (encounter,)})
    final, encounter, result = apply_paralyze_arm(
        replace(play.rules_context, rng=RecordedDice(())),
        state,
        encounter,
        effect_id="grip-arm",
        actor_id="b",
        location="left-arm",
    )
    assert encounter.grips == ()
    assert free_hands(final, encounter, "b") == ("right-hand",)
    assert not next(p for p in encounter.participants if p.actor_id == "a").grappled
    final = final.model_copy(
        update={"resources": final.resources.model_copy(update={"game_time": result.recovery_at})}
    )
    assert free_hands(final, encounter, "b") == ("left-hand", "right-hand")
    assert encounter.grips == ()
