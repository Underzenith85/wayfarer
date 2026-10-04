"""Real Paralyze casting/contact changes current equipment and arm eligibility."""

from pathlib import Path

import pytest
from support.paralyze_limb import cast, revision
from support.runtime import build_play
from support.wither_limb import fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.limb_spell_state import contact_results, read_contact
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
    generations,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_real_buckler_contact_saved_generation_survives_new_feature_settlement(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, equipment_target="buckler")
    active = generations.ACTIVE
    if legacy:
        monkeypatch.setattr(generations, "ACTIVE", active - {"paralyze-buckler-drop"})
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
    pending_state = play._load(await play.store.read(cid))
    pending = pending_state.encounters[0].pending_defense
    assert pending is not None
    contact = read_contact(pending_state.resources, pending.id)
    assert contact is not None and contact.generation == (2 if legacy else 4)
    # New feature capture at defense must not rewrite an accepted old contact.
    monkeypatch.setattr(generations, "ACTIVE", active)
    play.rng = RecordedDice((2, 3, 3, 1, 3, 3, 3, 4, 4, 4))
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
    assert result.grip_checks == ()
    assert result.dropped_item_ids == (() if legacy else ("defender-implement",))
    assert disabled(final.resources, "b") == {"left-arm"}
    current = next(p for p in final.encounters[0].participants if p.actor_id == "b")
    item = next(i for i in final.resources.items if i.id == "defender-implement")
    assert item.ready == legacy and item.equipped == legacy
    assert current.hand_bindings == (before_actor.hand_bindings if legacy else ())
    assert len(result.dropped_item_ids) == (0 if legacy else 1)
    with pytest.raises(ValidationError, match="No available skill/equipment"):
        standard_defense_value(play.rules_context, final, current, "block", "defender-implement")
    play.rng = RecordedDice(())
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == accepted
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    assert play._load(await play.store.read(cid)) == final
