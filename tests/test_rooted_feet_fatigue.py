"""Real failed Haste spends FP and changes subsequent Rooted resistance ST."""

from pathlib import Path
from typing import Literal

import pytest
from support.rooted_feet import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
from wayfarer.engine.simulation.magic.rooted_feet_state import TryRootedFeetEscape, effects, escapes
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.rooted_feet import RootedFeetService


async def failed_haste(play: PlayService, cid: str, *, attempt: int = 0) -> None:
    prefix = "haste-" + str(attempt)
    before = next(
        p.current for p in play._load(await play.store.read(cid)).resources.pools if p.id == "fp:b"
    )
    service = HasteService(play)
    await service.execute(
        cid,
        DeclareHasteChannel(
            id=prefix + "-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=HasteChannel(id=prefix, actor_id="b", target_id="b", location_id="dock"),
        ),
        principal_id="gm",
    )

    def command(
        identity: str, kind: Literal["start", "concentrate", "complete"], rev: int
    ) -> RuntimeSpellCommand:
        return RuntimeSpellCommand(
            id=prefix + identity,
            actor_id="b",
            expected_revision=rev,
            kind=kind,
            spell_id="haste",
            cast_id=prefix,
            channel_id=prefix,
            energy=1,
        )

    await service.execute(
        cid, command("haste-start", "start", await revision(play, cid)), principal_id="bob"
    )
    await play.execute(
        cid,
        Wait(
            id=prefix + "-wait-one",
            actor_id="b",
            expected_revision=await revision(play, cid),
            ticks=1,
        ),
        principal_id="b",
    )
    await service.execute(
        cid,
        command("haste-concentrate", "concentrate", await revision(play, cid)),
        principal_id="bob",
    )
    await play.execute(
        cid,
        Wait(
            id=prefix + "-wait-two",
            actor_id="b",
            expected_revision=await revision(play, cid),
            ticks=1,
        ),
        principal_id="b",
    )
    play.rng = RecordedDice((6, 6, 5))
    result = await service.execute(
        cid, command("haste-complete", "complete", await revision(play, cid)), principal_id="bob"
    )
    assert result.outcome == "failed" and result.energy_spent == 1
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == before - 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("phase", ["initial", "escape"])
async def test_current_fp_changes_actual_initial_or_escape_strength(
    tmp_path: Path, backend: str, phase: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, subject_fp=4)
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "fp:b"
        )
        == 4
    )
    if phase == "escape":
        await cast(play, cid)
    await failed_haste(play, cid)
    if phase == "initial":
        await cast(play, cid, dice=(3, 3, 3, 1, 2, 3))
        effect = effects(play._load(await play.store.read(cid)).resources)["root"]
        assert effect.initial_resistance is not None
        assert effect.initial_resistance.base_target == 5
        assert effect.status == "active"
    else:
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
        while state.encounters[0].current_actor_id != "b":
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
        play.rng = RecordedDice((1, 1, 2))
        result = await RootedFeetService(play).execute(
            cid,
            TryRootedFeetEscape(
                id="escape",
                actor_id="b",
                expected_revision=await revision(play, cid),
                effect_id="root",
                encounter_id="fight",
            ),
            principal_id="bob",
        )
        assert result.outcome == "retained"
        attempt = escapes(play._load(await play.store.read(cid)).resources)[-1]
        assert attempt.check.base_target == 0
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_zero_fp_escape_refuses_before_roll(tmp_path: Path, backend: str) -> None:
    from wayfarer.errors import ConflictError

    cid, play, _ = await fixture(tmp_path, backend, subject_fp=4)
    await cast(play, cid)
    for attempt in range(4):
        await failed_haste(play, cid, attempt=attempt)
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 0
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
    while state.encounters[0].current_actor_id != "b":
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
    saved, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="nonpositive FP"):
        await RootedFeetService(play).execute(
            cid,
            TryRootedFeetEscape(
                id="escape",
                actor_id="b",
                expected_revision=await revision(play, cid),
                effect_id="root",
                encounter_id="fight",
            ),
            principal_id="bob",
        )
    assert play.rng.exhausted()
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
