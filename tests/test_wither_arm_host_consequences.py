"""Real Wither casting/contact changes current equipment and arm eligibility."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play
from support.wither_limb import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.wither_spell_state import contact_results
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "equipment,retain,major_succeeds",
    [
        ("shield", True, True),
        ("shield", True, False),
        ("buckler", False, True),
        ("buckler", False, False),
        ("staff", True, True),
        ("staff", False, True),
        ("staff", False, False),
    ],
)
async def test_actual_wither_contact_applies_permanent_arm_and_ordered_health_equipment(
    tmp_path: Path,
    backend: str,
    equipment: Literal["shield", "staff", "buckler"],
    retain: bool,
    major_succeeds: bool,
) -> None:
    if equipment == "buckler":
        cid, play, _ = await fixture(tmp_path, backend, equipment_target="buckler")
    else:
        cid, play, _ = await fixture(tmp_path, backend, defender_item=equipment)
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="arm-encounter",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    before = play._load(await play.store.read(cid))
    before_actor = next(p for p in before.encounters[0].participants if p.actor_id == "b")
    original_dodge, _ = standard_defense_value(play.rules_context, before, before_actor, "dodge")
    await service.execute(
        cid,
        TakeCombatTurn(
            id="arm-contact",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="staff-thrust",
            target_id="b",
            hit_location="left-arm",
        ),
        principal_id="a",
    )
    extra = (1, 1, 1) if retain else (6, 6, 6)
    play.rng = RecordedDice(
        (2, 3, 3, 1, 3, 3, 3, 4, 4, 4, 1)
        + ((2, 2, 2) if major_succeeds else (4, 4, 4))
        + (extra if equipment == "staff" and major_succeeds else ())
    )
    response = ChooseDefense(
        id="arm-response",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="none",
    )
    accepted = await service.execute(cid, response, principal_id="b")
    assert play.rng.exhausted()
    final = play._load(await play.store.read(cid))
    result = contact_results(final.resources)[0]
    assert result.outcome == "withered" and result.location == "left-arm" and result.injury == 1
    assert result.hp_before == 9 and result.hp_after == 8
    assert len(result.grip_checks) == int(equipment == "staff" and major_succeeds)
    assert result.dropped_item_ids == (
        ("defender-implement",) if equipment in ("staff", "buckler") and not retain else ()
    )
    assert result.dice == (1,) and result.injury_check_reasons == ("major-wound",)
    hp = next(p for p in final.resources.pools if p.id == "hp:b")
    assert hp.injury is not None and hp.injury.stunned == (not major_succeeds)
    fact = hp.injury.lasting_injuries[-1]
    assert fact.duration == "permanent" and fact.kind == "crippled"
    assert fact.injury == 0 and fact.recovery_at is None
    assert disabled(final.resources, "b") == {"left-arm"}
    current = next(p for p in final.encounters[0].participants if p.actor_id == "b")
    if equipment == "shield":
        current_dodge, _ = standard_defense_value(play.rules_context, final, current, "dodge")
        assert original_dodge is not None and current_dodge is not None
        assert original_dodge.value - current_dodge.value == (1 if major_succeeds else 8)
        # Failed HT adds the existing prone(-3) and stunned(-4) penalties
        # independently of the crippled shield arm's lost DB point.
        with pytest.raises(ValidationError, match="No available skill/equipment"):
            standard_defense_value(
                play.rules_context, final, current, "block", "defender-implement"
            )
        shield = next(i for i in final.resources.items if i.id == "defender-implement")
        assert shield.ready and shield.equipped
        assert current.hand_bindings == before_actor.hand_bindings
    elif equipment == "staff":
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="crippled|ready weapon"):
            await service.execute(
                cid,
                TakeCombatTurn(
                    id="invalid-arm-attack",
                    actor_id="b",
                    expected_revision=final.revision,
                    encounter_id="fight",
                    maneuver="attack",
                    item_id="defender-implement",
                    mode_id="staff-thrust",
                    target_id="a",
                ),
                principal_id="b",
            )
        assert play.rng.exhausted() and play._load(await play.store.read(cid)) == final
    if equipment == "buckler":
        buckler = next(i for i in final.resources.items if i.id == "defender-implement")
        assert not buckler.ready and not buckler.equipped
        assert current.hand_bindings == ()
        assert len(result.dropped_item_ids) == 1 and result.grip_checks == ()
    play.rng = RecordedDice(())
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == accepted
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    assert play._load(await play.store.read(cid)) == final
