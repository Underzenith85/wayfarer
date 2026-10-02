"""B236/B38 actual combat casting, subjective timing, and admission oracles."""

from pathlib import Path
from typing import Literal

import pytest
from test_great_haste_execution import prepare
from test_power_maintenance_lifecycle import change

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.magic.great_haste_state import CastGreatHaste
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService


async def prepare_combat(
    tmp_path: Path,
    backend: str,
    *,
    caster_atr: bool = True,
    self_subject: bool = False,
    battlefield: HexBattlefield | None = None,
    known_subject: bool = True,
) -> tuple[str, PlayService]:
    cid, play = await prepare(
        tmp_path, backend, native_atr=1, self_subject=self_subject, battlefield=battlefield
    )

    def approve_caster_atr(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "a")
        proposal = actor.proposal.model_copy(
            update={
                "draft": actor.proposal.draft.model_copy(
                    update={
                        "purchases": actor.proposal.draft.purchases
                        + (Purchase(definition_id="advantage:altered-time-rate", amount=1),)
                    }
                )
            }
        )
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=cid,
            actor_id="a",
            revision=state.revision + 1,
            approver_id="gm",
            reason="Native ATR source timing fixture",
        )
        changed = actor.model_copy(update={"proposal": proposal, "approval": approval})
        return state.model_copy(
            update={
                "actors": tuple(changed if a.actor_id == "a" else a for a in state.actors),
                "approvals": state.approvals + (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            owner.model_copy(
                                update={
                                    "definitions": owner.definitions
                                    + ("advantage:altered-time-rate",)
                                }
                            )
                            if owner.actor_id == "a"
                            else owner
                            for owner in state.resources.owners
                        )
                    }
                ),
            }
        )

    if caster_atr:
        await change(cid, play, "native-caster-atr", approve_caster_atr)
    if known_subject:
        await change(
            cid,
            play,
            "subject-knowledge",
            lambda state: state.model_copy(update={"world": state.world.learn("a", "promise")}),
        )
    state = play._load(await play.store.read(cid))
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(
                    actor_id="a",
                    position=Hex(q=0, r=0) if battlefield else GridPoint(x=2, y=2),
                    hex_facing=0 if battlefield else None,
                    facing="west",
                ),
                Placement(
                    actor_id="b",
                    position=Hex(q=2, r=0) if battlefield else GridPoint(x=1, y=2),
                    hex_facing=3 if battlefield else None,
                    facing="east",
                ),
            ),
        ),
        principal_id="gm",
    )
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_native_atr_casts_three_subjective_seconds_in_two_real_turns(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_combat(tmp_path, backend)
    combat = CombatService(play)
    service = GreatHasteService(play)
    operations: tuple[Literal["start", "concentrate"], ...] = ("start", "concentrate")
    for index, operation in enumerate(operations):
        state = play._load(await play.store.read(cid))
        result = await service.execute(
            cid,
            CastGreatHaste(
                id="cast-" + str(index),
                actor_id="a",
                expected_revision=state.revision,
                operation=operation,
                channel_id="great-haste",
                cast_id="great",
            ),
            principal_id="alice",
        )
        assert result.outcome == "casting"
        state = play._load(await play.store.read(cid))
        assert latest(state.resources)["great"].concentration_seconds == index + 1
        assert state.resources.game_time == 0
        assert state.encounters[0].current_actor_id == ("a" if index == 0 else "b")
        injury = next(p.injury for p in state.resources.pools if p.id == "hp:a")
        assert injury is not None and injury.turn == 1
        assert injury.phase == ("acting" if index == 0 else "between")
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await combat.execute(
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
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 1 and state.encounters[0].current_actor_id == "a"
    play.rng = RecordedDice((3, 3, 3))
    result = await service.execute(
        cid,
        CastGreatHaste(
            id="final",
            actor_id="a",
            expected_revision=state.revision,
            operation="concentrate",
            channel_id="great-haste",
            cast_id="great",
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    assert result.outcome == "active" and result.energy_spent == 5
    assert state.resources.game_time == 1 and state.encounters[0].current_actor_id == "a"
    assert latest(state.resources)["great"].expires_at == 11
    injury = next(p.injury for p in state.resources.pools if p.id == "hp:a")
    assert injury is not None and injury.turn == 2 and injury.phase == "acting"
    assert state.resources.revision == state.revision
    before_final = await play.store.read(cid)
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="remaining-native",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    injury = next(p.injury for p in state.resources.pools if p.id == "hp:a")
    assert injury is not None and injury.turn == 2 and injury.phase == "between"
    assert state.resources.game_time == 1 and state.encounters[0].current_actor_id == "b"
    assert state.revision == before_final["revision"] + 1 == state.resources.revision


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_combat_cast_retry_restart_seeded_reexecution_and_legacy_absence(
    tmp_path: Path, backend: str
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.errors import ConflictError, ValidationError
    from wayfarer.orchestration.pipeline import submit
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare_combat(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: "00" * 32
    state = play._load(initial)
    service = GreatHasteService(play)
    first = CastGreatHaste(
        id="first",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
    )
    with pytest.raises(ValidationError, match="subjective concentration"):
        await submit(play, cid, service.plan(state, first, "alice"), principal_id="alice")
    assert await play.store.read(cid) == initial
    result = await service.execute(cid, first, principal_id="alice")
    after = await play.store.read(cid)
    assert await service.execute(cid, first, principal_id="alice") == result
    assert await play.store.read(cid) == after
    restarted = PlayService(play.store, play.engine, seeds=play.seeds)
    restarted.rng = secrets
    assert await GreatHasteService(restarted).execute(cid, first, principal_id="alice") == result
    with pytest.raises(ConflictError):
        await GreatHasteService(restarted).execute(
            cid, first.model_copy(update={"cast_id": "changed"}), principal_id="alice"
        )
    state = play._load(after)
    await GreatHasteService(restarted).execute(
        cid,
        CastGreatHaste(
            id="second",
            actor_id="a",
            expected_revision=state.revision,
            operation="concentrate",
            channel_id="great-haste",
            cast_id="great",
        ),
        principal_id="alice",
    )
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await CombatService(restarted).execute(
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
    state = play._load(await play.store.read(cid))
    result = await GreatHasteService(restarted).execute(
        cid,
        CastGreatHaste(
            id="final",
            actor_id="a",
            expected_revision=state.revision,
            operation="concentrate",
            channel_id="great-haste",
            cast_id="great",
        ),
        principal_id="alice",
    )
    assert result.outcome == "active"
    records = [
        r
        for r in await play.store.history(cid)
        if r.expected_revision >= initial["revision"] and r.command_id != "setup:seed"
    ]
    identifiers = {r.command_id for r in records}
    final, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ordinary_caster_requires_three_real_concentrate_turns(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_combat(tmp_path, backend, caster_atr=False)
    for index in range(3):
        state = play._load(await play.store.read(cid))
        play.rng = RecordedDice((3, 3, 3))
        result = await GreatHasteService(play).execute(
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
        state = play._load(await play.store.read(cid))
        assert state.resources.game_time == index
        assert state.encounters[0].current_actor_id == "b"
        assert result.outcome == ("active" if index == 2 else "casting")
        if index < 2:
            for turn in range(2):
                state = play._load(await play.store.read(cid))
                await CombatService(play).execute(
                    cid,
                    TakeCombatTurn(
                        id=f"other-{index}-{turn}",
                        actor_id="b",
                        expected_revision=state.revision,
                        encounter_id="fight",
                        maneuver="do_nothing",
                    ),
                    principal_id="b",
                )
    assert latest(state.resources)["great"].expires_at == 12
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 5


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("interrupt", ["move", "clock"])
async def test_intervening_maneuver_or_external_clock_cannot_bank_casting_seconds(
    tmp_path: Path, backend: str, interrupt: str
) -> None:
    from wayfarer.engine.simulation.resources import Advance
    from wayfarer.errors import ConflictError

    cid, play = await prepare_combat(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    service = GreatHasteService(play)
    await service.execute(
        cid,
        CastGreatHaste(
            id="first",
            actor_id="a",
            expected_revision=state.revision,
            operation="start",
            channel_id="great-haste",
            cast_id="great",
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    if interrupt == "move":
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="move",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="move",
                destination=GridPoint(x=2, y=3),
            ),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        assert latest(state.resources)["great"].phase == "ended"
    else:
        await change(
            cid,
            play,
            "external-clock",
            lambda current: play.advance_clock(
                current,
                Advance(
                    id="external-clock:time",
                    actor_id="a",
                    expected_revision=current.resources.revision,
                    to=current.resources.game_time + 1,
                ),
                RecordedDice(()),
            ),
        )
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    if interrupt == "clock":
        assert (state.resources.game_time, state.encounters[0].round) == (1, 1)
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            CastGreatHaste(
                id="continue",
                actor_id="a",
                expected_revision=state.revision,
                operation="concentrate",
                channel_id="great-haste",
                cast_id="great",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_combat_self_activation_and_explicit_completion_fail_closed(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await prepare_combat(tmp_path, backend, self_subject=True)
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    command = CastGreatHaste(
        id="cast",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
    )
    play.rng = RecordedDice(())
    for operation, match in (("start", "mid-turn activation"), ("complete", "final Concentrate")):
        with pytest.raises(ValidationError, match=match):
            await GreatHasteService(play).execute(
                cid, command.model_copy(update={"operation": operation}), principal_id="alice"
            )
        assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cast_origin_cannot_enter_genesis_or_player_projection(
    tmp_path: Path, backend: str
) -> None:
    import json

    from test_actions import campaign

    from wayfarer.engine.simulation.magic.great_haste_casting import PREFIX
    from wayfarer.engine.simulation.resources import ResourceState
    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.views import campaign_view

    cid, play = await prepare_combat(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    await GreatHasteService(play).execute(
        cid,
        CastGreatHaste(
            id="first",
            actor_id="a",
            expected_revision=state.revision,
            operation="start",
            channel_id="great-haste",
            cast_id="great",
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    origin = next(e for e in state.resources.events if e.id.startswith(PREFIX))
    for member in state.members:
        encoded = json.dumps(campaign_view(state, member, play.engine.rules.combat))
        assert PREFIX not in encoded and "great_haste_combat_generation" not in encoded
    with pytest.raises(ValidationError, match="supernatural execution receipts"):
        play.initial_state(campaign(play.engine), state.world, ResourceState(events=(origin,)), ())


def test_great_haste_timing_scope_is_private_and_restored() -> None:
    from pydantic import ValidationError as SchemaError

    from wayfarer.engine.simulation.magic.great_haste_casting import enabled, subjective_casting
    from wayfarer.errors import ValidationError
    from wayfarer.persistence.command_inputs import great_haste_intent

    assert not enabled()
    with pytest.raises(RuntimeError), subjective_casting():
        assert enabled()
        raise RuntimeError("abort")
    assert not enabled()
    with pytest.raises(SchemaError):
        CastGreatHaste.model_validate(
            {
                "id": "one",
                "actor_id": "a",
                "expected_revision": 0,
                "operation": "start",
                "channel_id": "great-haste",
                "cast_id": "great",
                "great_haste_combat_generation": 1,
            }
        )
    with pytest.raises(ValidationError, match="generation"):
        great_haste_intent(
            '{"operation":"great-haste","great_haste_combat_generation":true,"great_haste_original_input":"{}"}'
        )


def test_authenticated_generation_retains_exact_original_request_bytes() -> None:
    import json

    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, features
    from wayfarer.persistence.command_inputs import great_haste_intent, same_input, stamp
    from wayfarer.persistence.events import CommandInput, payload_digest

    original = '{"operation": "great-haste", "generation": 1, "principal_id": "alice", "command": {"id": "cast"}}'
    assert not features(CommandInput(payload_digest({"input": original}), original))
    payload = json.loads(original)
    payload[KEY], payload[ORIGINAL] = 1, original
    wrapped = json.dumps(payload, sort_keys=True)
    recorded = stamp(wrapped)
    record = CommandInput(payload_digest({"input": recorded}), recorded)
    assert features(record)
    assert great_haste_intent(wrapped) == original
    assert same_input(record, original)
    assert not same_input(
        record, json.dumps(json.loads(original), sort_keys=True, separators=(",", ":"))
    )
    with pytest.raises(ValidationError, match="digest"):
        features(CommandInput("0" * 64, recorded))
    payload[ORIGINAL] = '{"operation":"great-haste","command":{"id":"changed"}}'
    malformed = json.dumps(payload, sort_keys=True)
    with pytest.raises(ValidationError, match="generation"):
        features(CommandInput(payload_digest({"input": malformed}), malformed))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_active_daze_blocks_combat_cast_before_any_opportunity_or_dice(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.spell_state import (
        RuntimeSpellEffect,
        RuntimeSpellEvent,
        SpellResult,
        event_id,
    )
    from wayfarer.engine.simulation.resources import ResourceEvent
    from wayfarer.errors import ValidationError

    cid, play = await prepare_combat(tmp_path, backend)

    def daze(state: PlayState) -> PlayState:
        effect = RuntimeSpellEffect(
            cast_id="dazed",
            actor_id="b",
            target_id="a",
            spell_id="daze",
            build_revision="fixture",
            phase="active",
            started_at=0,
            ready_at=0,
            expires_at=100,
            skill=20,
            cost=3,
            maintenance=2,
            hp_at_start=10,
            execute_effects=True,
        )
        event = ResourceEvent(
            id=event_id("daze-fixture", "daze"),
            at=0,
            target_id="a",
            kind=RuntimeSpellEvent(
                effect=effect, result=SpellResult(outcome="active")
            ).model_dump_json(),
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"events": state.resources.events + (event,)}
                )
            }
        )

    await change(cid, play, "daze-fixture", daze)
    before = await play.store.read(cid)
    state = play._load(before)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Dazed"):
        await GreatHasteService(play).execute(
            cid,
            CastGreatHaste(
                id="first",
                actor_id="a",
                expected_revision=state.revision,
                operation="start",
                channel_id="great-haste",
                cast_id="great",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_recorded_legacy_outside_cast_retains_real_clock_and_seeded_replay(
    tmp_path: Path, backend: str
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.engine.simulation.actions import Wait
    from wayfarer.orchestration.great_haste_generation import KEY
    from wayfarer.orchestration.pipeline import submit
    from wayfarer.persistence.replay import command_text, verify_commands

    cid, play = await prepare(tmp_path, backend, seeded=True)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: "00" * 32
    service = GreatHasteService(play)
    operations: tuple[Literal["start", "concentrate", "complete"], ...] = (
        "start",
        "concentrate",
        "concentrate",
        "complete",
    )
    for index, operation in enumerate(operations):
        state = play._load(await play.store.read(cid))
        if index:
            await play.execute(
                cid,
                Wait(
                    id="wait-" + str(index), actor_id="a", expected_revision=state.revision, ticks=1
                ),
                principal_id="a",
            )
            state = play._load(await play.store.read(cid))
        command = CastGreatHaste(
            id="cast-" + str(index),
            actor_id="a",
            expected_revision=state.revision,
            operation=operation,
            channel_id="great-haste",
            cast_id="great",
        )
        result = await submit(
            play, cid, service.plan(state, command, "alice"), principal_id="alice"
        )
        after = await play.store.read(cid)
        assert await service.execute(cid, command, principal_id="alice") == result
        assert await play.store.read(cid) == after
    state = play._load(after)
    assert result.outcome == "active" and state.resources.game_time == 3
    assert latest(state.resources)["great"].expires_at == 13
    records = [
        r
        for r in await play.store.history(cid)
        if r.expected_revision >= initial["revision"] and r.command_id != "setup:seed"
    ]
    assert all(KEY not in command_text(r) for r in records)
    identifiers = {r.command_id for r in records}
    final, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy-reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_changed_caster_binding_rejects_new_progress_but_exact_retry_stays_valid(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ConflictError

    cid, play = await prepare_combat(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    command = CastGreatHaste(
        id="first",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
    )
    service = GreatHasteService(play)
    accepted = await service.execute(cid, command, principal_id="alice")

    def change_build(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "a")
        draft = actor.proposal.draft.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(update={"amount": 13}) if p.definition_id == "attribute:iq" else p
                    for p in actor.proposal.draft.purchases
                )
            }
        )
        proposal = actor.proposal.model_copy(update={"draft": draft})
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=cid,
            actor_id="a",
            revision=state.revision + 1,
            approver_id="gm",
            reason="Changed canonical caster build fixture",
        )
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"proposal": proposal, "approval": approval})
                    if a.actor_id == "a"
                    else a
                    for a in state.actors
                ),
                "approvals": state.approvals + (approval,),
            }
        )

    await change(cid, play, "caster-build", change_build)
    before = await play.store.read(cid)
    assert await service.execute(cid, command, principal_id="alice") == accepted
    assert await play.store.read(cid) == before
    state = play._load(before)
    with pytest.raises(ConflictError, match="binding changed"):
        await service.execute(
            cid,
            command.model_copy(
                update={
                    "id": "next",
                    "expected_revision": state.revision,
                    "operation": "concentrate",
                }
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("blocked", [False, True])
async def test_current_hex_line_of_sight_controls_combat_cast_admission(
    tmp_path: Path, backend: str, blocked: bool
) -> None:
    from wayfarer.errors import ValidationError

    board = HexBattlefield(
        id="dock",
        location_id="dock",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
        cells=tuple(
            Cell(position=Hex(q=q, r=0), opaque_height=3 if blocked and q == 1 else 0)
            for q in range(3)
        ),
    )
    cid, play = await prepare_combat(tmp_path, backend, battlefield=board)
    before = play._load(await play.store.read(cid))
    command = CastGreatHaste(
        id="hex-cast",
        actor_id="a",
        expected_revision=before.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="hex-great",
    )
    service = GreatHasteService(play)
    if blocked:
        with pytest.raises(ValidationError, match="currently visible"):
            await service.execute(cid, command, principal_id="alice")
        assert play._load(await play.store.read(cid)) == before
    else:
        result = await service.execute(cid, command, principal_id="alice")
        assert result.outcome == "casting"
        after = play._load(await play.store.read(cid))
        assert latest(after.resources)["hex-great"].concentration_seconds == 1
        assert after.resources.game_time == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unknown_current_subject_rejects_before_consuming_opportunity(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await prepare_combat(tmp_path, backend, known_subject=False)
    before = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="currently visible"):
        await GreatHasteService(play).execute(
            cid,
            CastGreatHaste(
                id="unknown",
                actor_id="a",
                expected_revision=before.revision,
                operation="start",
                channel_id="great-haste",
                cast_id="unknown",
            ),
            principal_id="alice",
        )
    assert play._load(await play.store.read(cid)) == before
