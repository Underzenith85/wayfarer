"""B236/B366 selected Step execution, canonical Wait, and historical admission."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_great_haste_combat_concentration import prepare_combat

from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.great_haste_state import CastGreatHaste, GreatHasteReceipt
from wayfarer.engine.simulation.magic.great_haste_step_state import StepCastGreatHaste
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_casting_does_not_infer_a_step_and_rejects_unbound_selection(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_combat(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    fields = dict(
        id="cast",
        actor_id="a",
        expected_revision=before.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
    )
    with pytest.raises(ValidationError, match="Extra inputs"):
        CastGreatHaste.model_validate({**fields, "destination": {"x": 3, "y": 2}})
    assert play._load(await play.store.read(cid)) == before
    command = CastGreatHaste.model_validate(fields)
    result = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert result.outcome == "casting"
    after = play._load(await play.store.read(cid))
    assert tuple((p.actor_id, p.position) for p in after.encounters[0].participants) == tuple(
        (p.actor_id, p.position) for p in before.encounters[0].participants
    )
    assert latest(after.resources)["great"].concentration_seconds == 1
    assert after.resources.game_time == before.resources.game_time
    assert after.encounters[0].current_actor_id == "a"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_step_moves_and_consumes_one_casting_opportunity(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.magic.great_haste_step_state import (
        CastingStep,
        StepCastGreatHaste,
    )

    cid, play = await prepare_combat(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    command = StepCastGreatHaste(
        id="selected",
        actor_id="a",
        expected_revision=before.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
        step=CastingStep(destination=GridPoint(x=3, y=2)),
    )
    result = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert result.outcome == "casting"
    after = play._load(await play.store.read(cid))
    assert next(
        p.position for p in after.encounters[0].participants if p.actor_id == "a"
    ) == GridPoint(x=3, y=2)
    assert after.revision == after.resources.revision == before.revision + 1
    assert latest(after.resources)["great"].concentration_seconds == 1
    assert after.resources.game_time == 0
    assert after.encounters[0].current_actor_id == "a"
    assert await GreatHasteService(play).execute(cid, command, principal_id="alice") == result
    assert play._load(await play.store.read(cid)) == after
    from wayfarer.errors import ConflictError

    with pytest.raises(ConflictError):
        await GreatHasteService(play).execute(
            cid,
            command.model_copy(update={"step": CastingStep(destination=GridPoint(x=2, y=3))}),
            principal_id="alice",
        )
    assert play._load(await play.store.read(cid)) == after


async def paused_hex_step(
    tmp_path: Path, backend: str, *, seeded: bool = False, continuing: bool = False
) -> tuple[str, PlayService, PlayState, StepCastGreatHaste, GreatHasteReceipt, Campaign]:
    from wayfarer.engine.simulation.combat.maneuvers import WaitTrigger
    from wayfarer.engine.simulation.combat.unarmed.records import UnarmedReaction
    from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
    from wayfarer.engine.simulation.magic.great_haste_step_state import (
        CastingStep,
        StepCastGreatHaste,
    )
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn

    board = HexBattlefield(
        id="dock",
        location_id="dock",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
        cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(-1, 4) for r in range(-1, 2)),
    )
    cid, play = await prepare_combat(tmp_path, backend, battlefield=board)
    from dataclasses import replace

    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.world import Fact

    await change(
        cid,
        play,
        "waiter-knowledge",
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
    for index, actor in enumerate(("a", "a", "b", "b")):
        state = play._load(await play.store.read(cid))
        if continuing and index < 2:
            await GreatHasteService(play).execute(
                cid,
                CastGreatHaste(
                    id="initial-" + str(index),
                    actor_id="a",
                    expected_revision=state.revision,
                    operation="start" if index == 0 else "concentrate",
                    channel_id="great-haste",
                    cast_id="great",
                ),
                principal_id="alice",
            )
            continue
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="setup-" + str(index),
                actor_id=actor,
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="wait" if index == 3 else "do_nothing",
                wait_trigger=WaitTrigger(
                    actor_id="a",
                    action="move",
                    reaction="attack",
                    reaction_target_id="a",
                    unarmed=UnarmedReaction(action="kick"),
                )
                if index == 3
                else None,
            ),
            principal_id=actor,
        )
    initial = await play.store.read(cid)
    before = play._load(initial)
    if seeded:
        import secrets

        play.rng = secrets
        play.seeds = lambda: "00" * 32
    command = StepCastGreatHaste(
        id="paused-step",
        actor_id="a",
        expected_revision=before.revision,
        operation="concentrate" if continuing else "start",
        channel_id="great-haste",
        cast_id="great",
        step=CastingStep(hex_path=(Hex(q=1, r=0),)),
    )
    receipt = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    return cid, play, before, command, receipt, initial


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_step_wait_pauses_before_casting_and_resumes_once(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.hex_geometry import Hex
    from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn

    cid, play, before, command, receipt, _ = await paused_hex_step(tmp_path, backend)
    assert receipt.outcome == "paused"
    paused = play._load(await play.store.read(cid))
    assert "great" not in latest(paused.resources)
    assert paused.resources.game_time == before.resources.game_time
    assert paused.encounters[0].wait_interrupt is not None
    assert next(p.position for p in paused.encounters[0].participants if p.actor_id == "a") == Hex(
        q=1, r=0
    )
    assert await GreatHasteService(play).execute(cid, command, principal_id="alice") == receipt
    assert play._load(await play.store.read(cid)) == paused
    combat = CombatService(play)
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="decline",
            actor_id="b",
            expected_revision=paused.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    ready = play._load(await play.store.read(cid))
    assert (
        ready.encounters[0].wait_interrupt is not None and ready.encounters[0].wait_interrupt.ready
    )
    resume = ResumeInterruptedTurn(
        id="resume", actor_id="a", expected_revision=ready.revision, encounter_id="fight"
    )
    await combat.execute(cid, resume, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert latest(after.resources)["great"].concentration_seconds == 1
    assert next(p.position for p in after.encounters[0].participants if p.actor_id == "a") == Hex(
        q=1, r=0
    )
    assert after.resources.game_time == before.resources.game_time
    assert after.encounters[0].current_actor_id == "a"
    await combat.execute(cid, resume, principal_id="a")
    assert play._load(await play.store.read(cid)) == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_step_pause_restart_seeded_reexecution_and_cancellation(
    tmp_path: Path, backend: str, cancel: bool
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn
    from wayfarer.persistence.replay import verify_commands

    cid, play, before, command, receipt, initial = await paused_hex_step(
        tmp_path, backend, seeded=True
    )
    # Reexecute the already accepted pause from its exact checkpoint using a
    # fresh recorded seed; subsequent operations share that authenticated replay.
    play.rng = secrets
    play.seeds = lambda: "00" * 32
    restarted = PlayService(play.store, play.engine, seeds=play.seeds)
    restarted.rng = secrets
    assert await GreatHasteService(restarted).execute(cid, command, principal_id="alice") == receipt
    state = play._load(await play.store.read(cid))
    await CombatService(restarted).execute(
        cid,
        TakeCombatTurn(
            id="decline-seed",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    ready = play._load(await play.store.read(cid))
    resume = ResumeInterruptedTurn(
        id="resume-seed",
        actor_id="a",
        expected_revision=ready.revision,
        encounter_id="fight",
        cancel=cancel,
    )
    result = await CombatService(restarted).execute(cid, resume, principal_id="a")
    final = await play.store.read(cid)
    assert await CombatService(restarted).execute(cid, resume, principal_id="a") == result
    assert await play.store.read(cid) == final
    state = play._load(final)
    assert state.revision == state.resources.revision == ready.revision + 1
    assert (
        ("great" not in latest(state.resources))
        if cancel
        else latest(state.resources)["great"].concentration_seconds == 1
    )
    assert state.resources.game_time == before.resources.game_time
    records = [
        r for r in await play.store.history(cid) if r.expected_revision >= initial["revision"]
    ]
    identifiers = {r.command_id for r in records}
    reexecuted, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == len(records) and all(c.folded and c.reexecuted for c in checks)
    from wayfarer.engine.simulation.events import document

    assert document(reexecuted) == document(final)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_illegal_selected_step_rolls_back_every_canonical_change(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.magic.great_haste_step_state import (
        CastingStep,
        StepCastGreatHaste,
    )
    from wayfarer.errors import ValidationError

    cid, play = await prepare_combat(tmp_path, backend)
    before = await play.store.read(cid)
    state = play._load(before)
    for index, destination in enumerate((GridPoint(x=5, y=2), GridPoint(x=1, y=2))):
        command = StepCastGreatHaste(
            id="illegal-" + str(index),
            actor_id="a",
            expected_revision=state.revision,
            operation="start",
            channel_id="great-haste",
            cast_id="great",
            step=CastingStep(destination=destination),
        )
        with pytest.raises(ValidationError):
            await GreatHasteService(play).execute(cid, command, principal_id="alice")
        assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("will_success", [False, True])
async def test_actual_wait_defense_resolves_concentration_once_before_step_completion(
    tmp_path: Path, backend: str, will_success: bool
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.orchestration.combat import (
        ChooseDefense,
        CombatService,
        ResumeInterruptedTurn,
        TakeUnarmedTurn,
    )

    cid, play, before, command, receipt, _ = await paused_hex_step(
        tmp_path, backend, continuing=True
    )
    assert receipt.outcome == "paused"
    paused = play._load(await play.store.read(cid))
    assert latest(paused.resources)["great"].concentration_seconds == 2
    assert paused.resources.game_time == 1
    combat = CombatService(play)
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="reaction",
            actor_id="b",
            expected_revision=paused.revision,
            encounter_id="fight",
            target_id="a",
            action="kick",
        ),
        principal_id="b",
    )
    pending = play._load(await play.store.read(cid))
    assert pending.encounters[0].pending_unarmed is not None
    play.rng = RecordedDice((1, 2, 3, 1, 2, 3) + ((1, 1, 1) if will_success else (6, 6, 6)))
    await combat.execute(
        cid,
        ChooseDefense(
            id="defend",
            actor_id="a",
            expected_revision=pending.revision,
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()
    ready = play._load(await play.store.read(cid))
    assert (
        ready.encounters[0].wait_interrupt is not None and ready.encounters[0].wait_interrupt.ready
    )
    assert (
        next(
            p.maneuver_state.concentrating
            for p in ready.encounters[0].participants
            if p.actor_id == "a"
        )
        is will_success
    )
    play.rng = RecordedDice((3, 3, 3) if will_success else ())
    await combat.execute(
        cid,
        ResumeInterruptedTurn(
            id="resume-after-defense",
            actor_id="a",
            expected_revision=ready.revision,
            encounter_id="fight",
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    effect = latest(after.resources)["great"]
    assert effect.phase == ("active" if will_success else "ended")
    assert effect.concentration_seconds == (3 if will_success else 2)
    fp = next(p for p in after.resources.pools if p.id == "fp:a")
    assert fp.current == (5 if will_success else 10)
    assert after.resources.game_time == 1
    assert after.encounters[0].current_actor_id == "a"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_final_selected_step_uses_moved_range_and_canonical_payment(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.magic.great_haste_step_state import CastingStep
    from wayfarer.engine.simulation.magic.spell_state import parse_event
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn

    cid, play = await prepare_combat(tmp_path, backend)
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await GreatHasteService(play).execute(
            cid,
            CastGreatHaste(
                id="cast-" + str(index),
                actor_id="a",
                expected_revision=state.revision,
                operation="start" if index == 0 else "concentrate",
                channel_id="great-haste",
                cast_id="great",
            ),
            principal_id="alice",
        )
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="other-" + str(index),
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    command = StepCastGreatHaste(
        id="final-step",
        actor_id="a",
        expected_revision=before.revision,
        operation="concentrate",
        channel_id="great-haste",
        cast_id="great",
        step=CastingStep(destination=GridPoint(x=3, y=2)),
    )
    result = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert result.outcome == "active" and result.energy_spent == 5
    assert play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    event = next(
        e
        for e in reversed(after.resources.events)
        if e.id.startswith("runtime-spell:") and parse_event(e).effect.cast_id == "great"
    )
    assert parse_event(event).result.checks[-1].effective_target == 10
    assert latest(after.resources)["great"].expires_at == 11
    assert after.resources.game_time == 1 and after.encounters[0].current_actor_id == "a"
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.phase == "acting" and hp.injury.turn == 2
    assert after.resources.revision == after.revision == before.revision + 1


def test_selected_step_generation_rejects_mismatched_or_future_pairing() -> None:
    import json

    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, features
    from wayfarer.persistence.events import CommandInput, payload_digest

    for generation, kind in (
        (1, "step-great-haste"),
        (2, "cast-great-haste"),
        (3, "step-great-haste"),
    ):
        original = json.dumps(
            {"operation": "great-haste", "generation": generation, "command": {"kind": kind}},
            sort_keys=True,
        )
        payload = json.loads(original)
        payload[KEY] = 1
        payload[ORIGINAL] = original
        text = json.dumps(payload, sort_keys=True)
        with pytest.raises(ValidationError, match="generation"):
            features(CommandInput(payload_digest({"input": text}), text))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "tamper", ["none", "wrong-lease", "changed-digest", "late-hp", "identical-new-event"]
)
async def test_distraction_witness_cannot_swallow_unadjudicated_current_state(
    tmp_path: Path, backend: str, tamper: str
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.great_haste_step_state import (
        RESOLVED,
        ResolvedStepDistraction,
    )
    from wayfarer.engine.simulation.magic.spell_state import interrupt_spells
    from wayfarer.orchestration.combat import (
        ChooseDefense,
        CombatService,
        ResumeInterruptedTurn,
        TakeUnarmedTurn,
    )

    cid, play, _, _, _, _ = await paused_hex_step(tmp_path, backend, continuing=True)
    await change(
        cid,
        play,
        "prior-distraction",
        lambda state: state.model_copy(
            update={
                "resources": interrupt_spells(
                    state.resources, "a", "prior-distraction", distraction=True
                )
            }
        ),
    )
    state = play._load(await play.store.read(cid))
    combat = CombatService(play)
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="reaction",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            target_id="a",
            action="kick",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 2, 3, 1, 2, 3, 1, 1, 1))
    await combat.execute(
        cid,
        ChooseDefense(
            id="defend",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()

    def changed(current: PlayState) -> PlayState:
        resources = current.resources
        events = []
        for event in resources.events:
            if event.id.startswith(RESOLVED):
                if tamper == "none":
                    continue
                record = ResolvedStepDistraction.model_validate_json(event.kind)
                if tamper == "wrong-lease":
                    record = record.model_copy(update={"lease_id": "another-lease"})
                elif tamper == "changed-digest":
                    record = record.model_copy(update={"effect_digest": "00" * 32})
                event = event.model_copy(update={"kind": record.model_dump_json()})
            events.append(event)
        resources = resources.model_copy(update={"events": tuple(events)})
        if tamper == "late-hp":
            resources = resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 9}) if p.id == "hp:a" else p
                        for p in resources.pools
                    )
                }
            )
        elif tamper == "identical-new-event":
            resources = interrupt_spells(
                resources, "a", "later-identical-distraction", distraction=True
            )
        return current.model_copy(update={"resources": resources})

    await change(cid, play, "adversarial-witness", changed)
    ready = play._load(await play.store.read(cid))
    assert latest(ready.resources)["great"].distracted
    # A stale or mismatched witness cannot erase the pending spell check. Its
    # failure prevents casting/payment, despite the earlier successful defense.
    play.rng = RecordedDice((6, 6, 6))
    await combat.execute(
        cid,
        ResumeInterruptedTurn(
            id="resume-tampered",
            actor_id="a",
            expected_revision=ready.revision,
            encounter_id="fight",
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    assert latest(after.resources)["great"].phase == "ended"
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("terrain", ["blocked", "elevation", "overlong"])
async def test_hex_step_terrain_rejects_before_any_cast_or_lease_commit(
    tmp_path: Path, backend: str, terrain: str
) -> None:
    from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
    from wayfarer.engine.simulation.magic.great_haste_step_state import CastingStep
    from wayfarer.errors import ValidationError

    board = HexBattlefield(
        id="dock",
        location_id="dock",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
        cells=tuple(
            Cell(
                position=Hex(q=q, r=r),
                blocked=terrain == "blocked" and (q, r) == (1, 0),
                elevation=3 if terrain == "elevation" and (q, r) == (1, 0) else 0,
            )
            for q in range(3)
            for r in range(2)
        ),
    )
    cid, play = await prepare_combat(tmp_path, backend, battlefield=board)
    before = await play.store.read(cid)
    state = play._load(before)
    path = (Hex(q=1, r=0), Hex(q=1, r=1)) if terrain == "overlong" else (Hex(q=1, r=0),)
    command = StepCastGreatHaste(
        id="terrain",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
        step=CastingStep(hex_path=path),
    )
    with pytest.raises(ValidationError):
        await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_wait_injury_captures_adjudicated_hp_for_completion(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.great_haste_step_state import distractions
    from wayfarer.orchestration.combat import (
        ChooseDefense,
        CombatService,
        ResumeInterruptedTurn,
        TakeUnarmedTurn,
    )

    cid, play, _, command, _, _ = await paused_hex_step(tmp_path, backend, continuing=True)
    state = play._load(await play.store.read(cid))
    combat = CombatService(play)
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="injury-reaction",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            target_id="a",
            action="kick",
        ),
        principal_id="b",
    )
    pending = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 2, 3, 3, 1, 1, 1))
    await combat.execute(
        cid,
        ChooseDefense(
            id="injury-response",
            actor_id="a",
            expected_revision=pending.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()
    ready = play._load(await play.store.read(cid))
    hp = next(p for p in ready.resources.pools if p.id == "hp:a")
    assert hp.current < 10
    witness = distractions(ready.resources)[command.id]
    assert witness.hp == hp.current and witness.check.outcome.succeeded
    play.rng = RecordedDice((1, 2, 3))
    await combat.execute(
        cid,
        ResumeInterruptedTurn(
            id="injury-resume", actor_id="a", expected_revision=ready.revision, encounter_id="fight"
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    assert latest(after.resources)["great"].phase == "active"
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 5
    assert after.revision == after.resources.revision == ready.revision + 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_step_private_lease_cannot_be_authored_in_genesis_or_projection(
    tmp_path: Path, backend: str
) -> None:
    import json

    from test_actions import campaign, world

    from wayfarer.engine.simulation.magic.great_haste_step_state import PREFIX
    from wayfarer.engine.simulation.resources import ResourceState
    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.views import campaign_view

    cid, play, _, _, _, _ = await paused_hex_step(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    events = tuple(e for e in state.resources.events if e.id.startswith(PREFIX))
    assert events
    for member in state.members:
        assert PREFIX not in json.dumps(campaign_view(state, member, play.engine.rules.combat))
    with pytest.raises(ValidationError, match="supernatural execution receipts"):
        play.initial_state(campaign(play.engine), world(), ResourceState(events=events), ())


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_changed_real_clock_rejects_paused_step_without_rebinding(
    tmp_path: Path, backend: str
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.resources import Advance
    from wayfarer.errors import ConflictError
    from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn

    cid, play, _, _, _, _ = await paused_hex_step(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    combat = CombatService(play)
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="decline-clock",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    # Canonical domain clock under the store lock; this deliberately is not a
    # public Wait or registered command-family seeded replay claim.
    await change(
        cid,
        play,
        "outside-clock",
        lambda current: play.advance_clock(
            current,
            Advance(
                id="outside-clock:time",
                actor_id="a",
                expected_revision=current.resources.revision,
                to=current.resources.game_time + 1,
            ),
            RecordedDice(()),
        ),
    )
    before = await play.store.read(cid)
    state = play._load(before)
    with pytest.raises(ConflictError, match="turn or clock changed"):
        await combat.execute(
            cid,
            ResumeInterruptedTurn(
                id="resume-clock",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before


@pytest.fixture(autouse=True)
def historical_generation_two_step_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """This established suite exercises the captured generation-two Step contract."""
    from wayfarer.orchestration import great_haste

    async def recorded_two_steps(store: object, cid: str, command_id: str) -> bool:
        return False

    monkeypatch.setattr(great_haste, "ritual_steps", recorded_two_steps)
