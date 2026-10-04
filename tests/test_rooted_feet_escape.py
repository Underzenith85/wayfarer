"""Real subject turns use the original casting roll, without a second cast."""

from pathlib import Path

import pytest
from support.rooted_feet import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.rooted_feet_state import TryRootedFeetEscape, effects, escapes
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.rooted_feet import RootedFeetService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("critical", [False, True])
async def test_actual_turn_failed_retry_next_turn_and_tie_release(
    tmp_path: Path, backend: str, critical: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, subject_st=20 if critical else 10)
    await cast(play, cid, dice=(1, 1, 1) if critical else (3, 3, 3, 6, 6, 6))
    original = effects(play._load(await play.store.read(cid)).resources)["root"].original_check
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="encounter",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            ),
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    # Resolve actual other actors' ordinary turns until the subject acts.
    while state.encounters[0].current_actor_id != "b":
        actor = state.encounters[0].current_actor_id
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="first-" + actor,
                actor_id=actor,
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    service = RootedFeetService(play)
    command = TryRootedFeetEscape(
        id="escape",
        actor_id="b",
        expected_revision=state.revision,
        effect_id="root",
        encounter_id="fight",
    )
    play.rng = RecordedDice((6, 6, 6))
    result = await service.execute(cid, command, principal_id="bob")
    assert result.outcome == "retained"
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await service.execute(cid, command, principal_id="bob") == result
    with pytest.raises(ConflictError, match="already attempted"):
        await service.execute(
            cid,
            command.model_copy(
                update={"id": "free-query", "expected_revision": await revision(play, cid)}
            ),
            principal_id="bob",
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="subject-turn",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    while state.encounters[0].current_actor_id != "b":
        actor = state.encounters[0].current_actor_id
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="next-" + actor,
                actor_id=actor,
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 3, 3) if critical else (1, 1, 2))
    released = await service.execute(
        cid,
        command.model_copy(update={"id": "next-escape", "expected_revision": state.revision}),
        principal_id="bob",
    )
    assert released.outcome == "escaped" and play.rng.exhausted()
    resources = play._load(await play.store.read(cid)).resources
    assert effects(resources)["root"].original_check == original
    assert effects(resources)["root"].status == "escaped"
    assert len(escapes(resources)) == 2
    assert escapes(resources)[-1].check.margin == original.margin
