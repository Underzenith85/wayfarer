"""A real failed spell crosses the current FP threshold before rooted defense."""

from pathlib import Path

import pytest
from support.rooted_feet import cast, fixture, revision
from test_rooted_feet_fatigue import failed_haste

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.rooted_feet_state import effects
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_registered_haste_changes_current_rooted_dodge_without_reroll(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(
        tmp_path, backend, subject_dx=12, subject_ht=16, subject_fp=6, combat_weapons=True
    )
    await cast(play, cid)
    before = play._load(await play.store.read(cid))
    original = effects(before.resources)["root"]
    assert original.status == "active"
    fp = next(p for p in before.resources.pools if p.id == "fp:b")
    assert (fp.current, fp.maximum) == (6, 16)
    participant = Combatant(actor_id="b", initiative=10, reach=1, movement_allowance=5)
    with combat_generation(frozenset({"rooted-dodge-health-trait-composition"})):
        healthy, _ = standard_defense_value(play.rules_context, before, participant, "dodge")
    assert healthy is not None and healthy.value == 5

    await failed_haste(play, cid)
    after = play._load(await play.store.read(cid))
    fp = next(p for p in after.resources.pools if p.id == "fp:b")
    assert (fp.current, fp.maximum) == (5, 16)
    assert not active_spells(after.resources)
    assert effects(after.resources)["root"] == original
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()

    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
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
    state = play._load(await play.store.read(cid))
    while state.encounters[0].current_actor_id != "a":
        actor = state.encounters[0].current_actor_id
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="pass-" + actor,
                actor_id=actor,
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3, 2, 2, 2, 1))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        principal_id="a",
    )
    command = ChooseDefense(
        id="defense",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="dodge",
    )
    result = await combat.execute(cid, command, principal_id="b")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 2
    assert result.injury.defense.outcome == "failure"
    assert (
        effects(play._load(await play.store.read(cid)).resources)["root"].original_check
        == original.original_check
    )
    assert play.rng.exhausted()
    saved = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    assert play.rng.exhausted()
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
