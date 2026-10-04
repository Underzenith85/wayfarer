"""Two paid Haste spells and exact independent Rooted/Haste deadline consumers."""

import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.rooted_feet import revision
from support.rooted_haste import cast_haste, cast_rooted, fixture, prepare_composition
from support.runtime import build_runtime, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.haste_effects import bonus
from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect, effects
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_two_actual_paid_haste_casts_use_strongest_not_sum(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original = await fixture(tmp_path, backend, subject_dx=18, subject_ht=10)
    await cast_haste(play, cid, energy=1, identity="first")
    await cast_haste(play, cid, energy=3, identity="second")
    state = play._load(await play.store.read(cid))
    assert (
        latest(state.resources)["first"].phase
        == latest(state.resources)["second"].phase
        == "active"
    )
    assert bonus(state.resources, "b") == 3
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 4
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 10
    await cast_rooted(play, cid)
    # Base Dodge 10 makes strongest +3 distinguishable from summed +4:
    # floor((10+3)/2)=6, while the erroneous sum would produce 7.
    assert await _incoming_dodge(play, cid) == 6
    await _reexecute(play, cid, original, tmp_path)


async def _reexecute(play: PlayService, cid: str, original: Campaign, path: Path) -> None:
    saved = await play.store.read(cid)
    state = play._load(saved)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        original,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("order", ["before", "after"])
@pytest.mark.parametrize("boundary", ["earlier", "later"])
async def test_actual_exact_deadlines_change_dodge_and_restore_movement(
    tmp_path: Path, backend: str, order: Literal["before", "after"], boundary: str
) -> None:
    cid, play, original = await fixture(tmp_path, backend, subject_dx=14, subject_ht=10)
    await prepare_composition(play, cid, energy=3, order=order)
    state = play._load(await play.store.read(cid))
    root = effects(state.resources)["root"]
    haste = latest(state.resources)["haste"]
    assert haste.expires_at is not None
    assert root.original_check is not None
    deadline = (min if boundary == "earlier" else max)(root.expires_at, haste.expires_at)
    runtime = build_runtime(play)
    await runtime.submit_json(
        cid,
        Wait(
            id="to-boundary-minus-one",
            actor_id="b",
            expected_revision=await revision(play, cid),
            ticks=deadline - state.resources.game_time - 1,
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    state = play._load(await play.store.read(cid))
    # Both effects are live immediately before the first deadline.
    if boundary == "earlier":
        assert movement(play.rules_context, state, "b") == 0 and bonus(state.resources, "b") == 3
    await runtime.submit_json(
        cid,
        Wait(
            id="to-boundary", actor_id="b", expected_revision=await revision(play, cid), ticks=1
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    state = play._load(await play.store.read(cid))
    rooted = root.expires_at > deadline
    hasted = haste.expires_at > deadline
    assert (active_effect(state.resources, "b") is not None) == rooted
    assert bonus(state.resources, "b") == (3 if hasted else 0)
    assert movement(play.rules_context, state, "b") == (0 if rooted else 6 + (3 if hasted else 0))
    assert effects(state.resources)["root"].original_check == root.original_check
    actual = await _incoming_dodge(play, cid)
    expected = 9 + (3 if hasted else 0)
    if rooted:
        expected //= 2
    assert actual == expected
    await _reexecute(play, cid, original, tmp_path)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_move_refused_until_root_deadline_while_haste_survives(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.actions import Move

    cid, play, original = await fixture(tmp_path, backend)
    await prepare_composition(play, cid, energy=3, order="after")
    state = play._load(await play.store.read(cid))
    root = effects(state.resources)["root"]
    haste = latest(state.resources)["haste"]
    assert haste.expires_at is not None and haste.expires_at > root.expires_at
    result = await play.execute(
        cid,
        Move(
            id="blocked-walk",
            actor_id="b",
            expected_revision=await revision(play, cid),
            destination_id="alley",
        ),
        principal_id="b",
    )
    assert result.code == "move.rooted"
    assert (
        next(
            e.location_id
            for e in play._load(await play.store.read(cid)).world.entities
            if e.id == "b"
        )
        == "dock"
    )
    await build_runtime(play).submit_json(
        cid,
        Wait(
            id="root-deadline",
            actor_id="b",
            expected_revision=await revision(play, cid),
            ticks=root.expires_at - state.resources.game_time,
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    state = play._load(await play.store.read(cid))
    assert active_effect(state.resources, "b") is None and bonus(state.resources, "b") == 3
    from wayfarer.orchestration.scenes import SceneService, TravelScene

    await SceneService(play).execute(
        cid,
        TravelScene(
            id="free-walk",
            actor_id="b",
            expected_revision=await revision(play, cid),
            exit_id="to-alley",
        ),
        principal_id="b",
    )
    assert (
        next(
            e.location_id
            for e in play._load(await play.store.read(cid)).world.entities
            if e.id == "b"
        )
        == "alley"
    )
    await _reexecute(play, cid, original, tmp_path)


async def _incoming_dodge(play: PlayService, cid: str) -> int:
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
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    while state.encounters[0].current_actor_id != "a":
        actor = state.encounters[0].current_actor_id
        await service.execute(
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
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await service.execute(
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
    result = await service.execute(
        cid,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="b",
    )
    assert result.injury is not None and result.injury.defense is not None
    return result.injury.defense.effective_target
