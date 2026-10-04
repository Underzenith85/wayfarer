"""Real Paralyze casting/contact changes current equipment and arm eligibility."""

from pathlib import Path
from typing import Literal

import pytest
from support.paralyze_limb import cast, fixture, revision
from support.runtime import build_play

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.limb_spell_state import contact_results
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    EndEncounter,
    StartEncounter,
    TakeCombatTurn,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("equipment,retain", [("shield", True), ("staff", True), ("staff", False)])
async def test_actual_paralyze_contact_changes_current_arm_equipment_and_recovers_at_deadline(
    tmp_path: Path, backend: str, equipment: Literal["shield", "staff"], retain: bool
) -> None:
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
        (2, 3, 3, 1, 3, 3, 3, 4, 4, 4) + (extra if equipment == "staff" else ())
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
    assert result.outcome == "paralyzed" and result.location == "left-arm" and result.injury == 0
    assert result.hp_before == result.hp_after == 9
    assert len(result.grip_checks) == int(equipment == "staff")
    assert result.dropped_item_ids == (
        ("defender-implement",) if equipment == "staff" and not retain else ()
    )
    assert disabled(final.resources, "b") == {"left-arm"}
    current = next(p for p in final.encounters[0].participants if p.actor_id == "b")
    if equipment == "shield":
        current_dodge, _ = standard_defense_value(play.rules_context, final, current, "dodge")
        assert original_dodge is not None and current_dodge is not None
        assert original_dodge.value - current_dodge.value == 1
        with pytest.raises(ValidationError, match="No available skill/equipment"):
            standard_defense_value(
                play.rules_context, final, current, "block", "defender-implement"
            )
    else:
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
    play.rng = RecordedDice(())
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == accepted
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    assert play._load(await play.store.read(cid)) == final
    assert result.recovery_at is not None
    await service.execute(
        cid,
        EndEncounter(
            id="disengage",
            actor_id="gm",
            expected_revision=final.revision,
            encounter_id="fight",
            reason="disengaged",
        ),
        principal_id="gm",
    )
    ended = play._load(await play.store.read(cid))
    assert play.rng.exhausted()
    waited = await play.execute(
        cid,
        Wait(
            id="before-deadline",
            actor_id="b",
            expected_revision=await revision(play, cid),
            ticks=result.recovery_at - 1 - ended.resources.game_time,
        ),
        principal_id="b",
    )
    assert waited.status == "committed", waited
    almost = play._load(await play.store.read(cid))
    assert almost.resources.game_time == result.recovery_at - 1
    assert disabled(almost.resources, "b") == {"left-arm"}
    await play.execute(
        cid,
        Wait(id="deadline", actor_id="b", expected_revision=await revision(play, cid), ticks=1),
        principal_id="b",
    )
    expired = play._load(await play.store.read(cid))
    assert disabled(expired.resources, "b") == set()
    recovered_actor = next(p for p in expired.encounters[0].participants if p.actor_id == "b")
    assert recovered_actor.hand_bindings == current.hand_bindings
    if equipment == "staff" and retain:
        recovered_parry, _ = standard_defense_value(
            play.rules_context, expired, recovered_actor, "parry", "defender-implement"
        )
        assert recovered_parry is not None
    item = next(i for i in expired.resources.items if i.id == "defender-implement")
    assert item.equipped == retain and item.ready == retain
    assert await play.store.read(cid) == await play.store.replay(cid)
