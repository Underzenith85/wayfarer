"""Paid cross-actor Haste composes with Rooted in actual ranged/unarmed hosts."""

from pathlib import Path
from typing import Literal

import pytest
from support.rooted_feet import revision
from support.rooted_haste import fixture, prepare_composition
from support.runtime import played

from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import RangedSituation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.haste_effects import bonus
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
@pytest.mark.parametrize("order", ["before", "after"])
@pytest.mark.parametrize("defended", [False, True])
async def test_actual_incoming_hosts_use_paid_haste_before_rooted_half(
    tmp_path: Path, backend: str, incoming: str, order: Literal["before", "after"], defended: bool
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        ranged_weapon=incoming == "ranged",
        combat_weapons=False,
        subject_dx=14,
        subject_ht=10,
    )
    initial = play._load(await play.store.read(cid))
    await prepare_composition(play, cid, energy=3, order=order)
    composed = play._load(await play.store.read(cid))
    assert bonus(composed.resources, "b") == 3
    assert movement(play.rules_context, composed, "b") == 0
    assert composed.resources.game_time == initial.resources.game_time + 3
    assert next(p.current for p in composed.resources.pools if p.id == "fp:a") == 5
    expected = 6  # source score: floor((canonical Dodge9 + actual Haste3) / 2)
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
    defense_dice = (1, 2, 2) if defended else (3, 3, 3)
    play.rng = RecordedDice(
        (3, 3, 3) + defense_dice + (() if defended else ((6,) if incoming == "unarmed" else (1,)))
    )
    command = ChooseDefense(
        id="response",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="dodge",
    )
    result = await service.execute(cid, command, principal_id="b")
    if incoming == "unarmed":
        assert result.unarmed is not None
        assert result.unarmed.checks[1].effective_target == expected
        assert result.unarmed.won == (not defended)
        injury = result.unarmed.injury
    else:
        assert result.injury is not None and result.injury.defense is not None
        assert result.injury.defense.effective_target == expected
        injury = result.injury.injury
    assert play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    assert (injury == 0) == defended
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == hp - injury
    for command_id in ("incoming", "response"):
        record = next(r for r in await played(play.store, cid) if r.command_id == command_id)
        payload = validation.mapping(replay_payload(record.command_input or "{}"))
        assert "rooted-dodge-haste-composition" in validation.sequence(
            payload["combat_protocol_features"]
        )
    assert movement(play.rules_context, after, "b") == 0
    assert await play.store.read(cid) == await play.store.replay(cid)
    saved, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    assert play.rng.exhausted()
    assert (
        await play.store.read(cid) == saved
        and await play.store.history(cid) == history
        and await play.store.stream(cid) == stream
    )
    while after.encounters[0].current_actor_id != "b":
        actor = after.encounters[0].current_actor_id
        await service.execute(
            cid,
            TakeCombatTurn(
                id="later-" + actor,
                actor_id=actor,
                expected_revision=after.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        after = play._load(await play.store.read(cid))
    saved, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="Rooted Feet"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="haste-cannot-free-step",
                actor_id="b",
                expected_revision=after.revision,
                encounter_id="fight",
                maneuver="move",
                destination=GridPoint(x=2, y=0),
            ),
            principal_id="b",
        )
    assert play.rng.exhausted()
    assert (
        await play.store.read(cid) == saved
        and await play.store.history(cid) == history
        and await play.store.stream(cid) == stream
    )
