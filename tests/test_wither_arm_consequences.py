"""Reducer diagnostics; these do not substitute for actual Wither casting/contact."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_staff_melee_contact import _charged_encounter

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.wither_cripple_effects import apply_wither_arm
from wayfarer.errors import ConflictError


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("major_succeeds,retain", [(True, True), (True, False), (False, False)])
async def test_withering_one_point_is_permanent_major_wound_and_drops_once(
    tmp_path: Path, backend: str, major_succeeds: bool, retain: bool
) -> None:
    cid, play, _ = await _charged_encounter(tmp_path, backend, defender_item="staff")
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury is not None and hp.maximum == 10
    rolls: tuple[int, ...] = (1, 2, 2, 2) if major_succeeds else (1, 4, 4, 4)
    if major_succeeds:
        rolls += (1, 1, 1) if retain else (6, 6, 6)
    dice = RecordedDice(rolls)
    final, encounter, result = apply_wither_arm(
        replace(play.rules_context, rng=dice),
        state,
        state.encounters[0],
        effect_id="diagnostic-wither",
        actor_id="b",
        location="left-arm",
    )
    assert dice.exhausted()
    updated = next(p for p in final.resources.pools if p.id == "hp:b")
    assert updated.injury is not None
    assert result.dice == (1,) and result.injury == 1
    assert result.hp_before == hp.current and result.hp_after == hp.current - 1
    assert result.injury_check_reasons == ("major-wound",)
    assert len(result.grip_checks) == int(major_succeeds)
    assert updated.injury.stunned == (not major_succeeds)
    assert updated.injury.prone == (not major_succeeds)
    fact = updated.injury.lasting_injuries[-1]
    assert fact.kind == "crippled" and fact.duration == "permanent"
    assert fact.injury == 0 and fact.recovery_at is None
    assert updated.injury.lasting_injuries[:-1] == hp.injury.lasting_injuries
    assert "left-arm" in disabled(final.resources, "b")
    assert result.dropped_item_ids == (() if retain else ("defender-implement",))
    item = next(i for i in final.resources.items if i.id == "defender-implement")
    assert item.ready == retain and item.equipped == retain
    actor = next(p for p in encounter.participants if p.actor_id == "b")
    assert bool(actor.hand_bindings) == retain

    # Canonical recovery diagnostic: ordinary full HP recovery and a long clock
    # interval do not supply a magical limb-restoration effect.
    healed, amount = restore_hp(final.resources, updated, 100, kind="natural")
    assert amount == 1 and healed.current == healed.maximum
    recovered = final.resources.model_copy(
        update={
            "pools": tuple(healed if p.id == healed.id else p for p in final.resources.pools),
            "game_time": final.resources.game_time + 86400 * 365,
        }
    )
    assert "left-arm" in disabled(recovered, "b")
    assert healed.injury is not None and fact in healed.injury.lasting_injuries
    retry_dice = RecordedDice(())
    with pytest.raises(ConflictError, match="immutable"):
        apply_wither_arm(
            replace(play.rules_context, rng=retry_dice),
            final,
            encounter,
            effect_id="diagnostic-wither",
            actor_id="b",
            location="left-arm",
        )
    assert retry_dice.exhausted()
