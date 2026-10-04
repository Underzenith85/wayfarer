"""Incoming real unarmed and thrown attacks consume captured Rooted Dodge composition."""

from pathlib import Path

import pytest
from support.rooted_feet import cast, fixture, revision
from support.runtime import played

from wayfarer import validation
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import RangedSituation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.persistence.command_inputs import replay_payload


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("incoming", ["unarmed", "ranged"])
@pytest.mark.parametrize(
    "low,expected,defended", [(False, 5, True), (True, 2, True), (False, 5, False)]
)
async def test_actual_incoming_routes_use_current_health_and_reflexes_dodge(
    tmp_path: Path, backend: str, incoming: str, low: bool, expected: int, defended: bool
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        ranged_weapon=incoming == "ranged",
        subject_st=12,
        subject_ht=12,
        subject_dx=12,
        subject_hp=3 if low else 12,
        subject_fp=3 if low else 12,
        subject_purchases=(Purchase(definition_id="trait:combat-reflexes"),),
    )
    await cast(play, cid)
    service = CombatService(play)
    await service.execute(
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
            ranged_situations=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=1),)
            if incoming == "ranged"
            else (),
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    while state.encounters[0].current_actor_id != "a":
        actor = state.encounters[0].current_actor_id
        await service.execute(
            cid,
            TakeCombatTurn(
                id="wait-" + actor,
                actor_id=actor,
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    if incoming == "unarmed":
        await service.execute(
            cid,
            TakeUnarmedTurn(
                id="incoming",
                actor_id="a",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                action="punch",
                target_id="b",
                hands=("right-hand",),
                enter_close_combat=True,
            ),
            principal_id="a",
        )
    else:
        await service.execute(
            cid,
            TakeCombatTurn(
                id="incoming",
                actor_id="a",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                maneuver="attack",
                item_id="hatchet-a",
                mode_id="thrown",
                target_id="b",
            ),
            principal_id="a",
        )
    before = play._load(await play.store.read(cid))
    hp = next(p.current for p in before.resources.pools if p.id == "hp:b")
    checkpoint = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ConflictError, ValidationError)):
        await service.execute(
            cid,
            ChooseDefense(
                id="late-retreat",
                actor_id="b",
                expected_revision=before.revision,
                encounter_id="fight",
                defense="dodge",
                basic_retreat=True,
            ),
            principal_id="b",
        )
    assert play.rng.exhausted()
    assert await play.store.read(cid) == checkpoint
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice(
        (3, 3, 3, 1, 1, 1, 3, 3, 3) if defended else (3, 3, 3, 3, 3, 3, 6) + (3, 3, 3) * 10
    )
    result = await service.execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=before.revision,
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="b",
    )
    if incoming == "unarmed":
        assert result.unarmed is not None
        assert result.unarmed.checks[1].effective_target == expected
        assert result.unarmed.won == (not defended)
        injury = result.unarmed.injury
    else:
        assert result.injury is not None and result.injury.defense is not None
        assert result.injury.defense.effective_target == expected
        injury = result.injury.injury
    after = play._load(await play.store.read(cid))
    assert (injury == 0) == defended
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == hp - injury
    for command_id in ("incoming", "response"):
        record = next(r for r in await played(play.store, cid) if r.command_id == command_id)
        payload = validation.mapping(replay_payload(record.command_input or "{}"))
        assert "rooted-dodge-health-trait-composition" in validation.sequence(
            payload["combat_protocol_features"]
        )
    assert await play.store.read(cid) == await play.store.replay(cid)
