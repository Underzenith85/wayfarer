"""An initial larger Step resumes only its remaining canonical movement before casting."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from test_great_haste_initial_step import initial
from test_great_haste_named_step import prepare_named
from test_power_maintenance_lifecycle import change

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.maneuvers import WaitTrigger
from wayfarer.engine.simulation.combat.unarmed.records import UnarmedReaction
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.great_haste_step_state import leases
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.world import Fact
from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_initial_two_yard_wait_remaining_path_restart_retry_seed(
    tmp_path: Path, backend: str, named: bool
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11)
    await change(
        cid,
        play,
        "wait-caster-known",
        lambda state: state.model_copy(
            update={
                "world": replace(
                    state.world,
                    facts=state.world.facts + (Fact("caster-known", "a", "present", "dock"),),
                ).learn("b", "caster-known")
            }
        ),
    )
    combat = CombatService(play)
    for actor in ("a", "a", "b", "b"):
        state = play._load(await play.store.read(cid))
        wait = actor == "b" and state.encounters[0].maneuver_budget is not None
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="prep-" + str(state.revision),
                actor_id=actor,
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="wait" if wait else "do_nothing",
                wait_trigger=WaitTrigger(
                    actor_id="a",
                    action="move",
                    reaction="attack",
                    reaction_target_id="a",
                    unarmed=UnarmedReaction(action="kick"),
                )
                if wait
                else None,
            ),
            principal_id=actor,
        )
    before = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(before)
    play.rng, play.seeds = secrets, lambda: "00" * 32
    selected = initial(state.revision, named=named, path=(Hex(q=1, r=0), Hex(q=1, r=1)))
    paused = await GreatHasteService(play).execute(cid, selected, principal_id="alice")
    assert paused.outcome == "paused" and paused.energy_spent == 0
    state = play._load(await play.store.read(cid))
    assert "larger" not in latest(state.resources)
    assert next(p.position for p in state.encounters[0].participants if p.actor_id == "a") == Hex(
        q=1, r=0
    )
    assert not leases(state.resources)[selected.id].completed
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="decline-initial",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    resumed = PlayService(play.store, play.engine)
    resumed.rng, resumed.seeds = secrets, lambda: "00" * 32
    command = ResumeInterruptedTurn(
        id="resume-initial", actor_id="a", expected_revision=state.revision, encounter_id="fight"
    )
    result = await CombatService(resumed).execute(cid, command, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    assert next(p.position for p in state.encounters[0].participants if p.actor_id == "a") == Hex(
        q=1, r=1
    )
    assert latest(state.resources)["larger"].concentration_seconds == 1
    assert leases(state.resources)[selected.id].completed
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    retry = PlayService(play.store, play.engine)
    retry.rng = RecordedDice([])
    assert await GreatHasteService(retry).execute(cid, selected, principal_id="alice") == paused
    assert await CombatService(retry).execute(cid, command, principal_id="a") == result
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        before,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=play._load(before).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "initial-wait-seed"),
    )
    assert len(checks) == 3 and all(check.folded and check.reexecuted for check in checks)
    assert play._load(replayed) == state and await play.store.read(cid) == final
