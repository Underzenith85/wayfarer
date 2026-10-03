"""B237 ongoing named-Step admission uses ritual base skill and low mana only."""

from pathlib import Path
from typing import Literal

import pytest
from test_great_haste_named_step import command, prepare_named
from test_power_maintenance_lifecycle import change

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.great_haste_state import CHANNEL, GreatHasteChannel
from wayfarer.engine.simulation.magic.great_haste_step_state import StepCastGreatHaste
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import ValidationError
from wayfarer.orchestration.great_haste import GreatHasteService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize(
    ("amount", "mana", "allowed"),
    [(4, "normal", False), (16, "normal", True), (32, "low", False), (36, "low", True)],
)
async def test_ongoing_named_step_ritual_boundary_is_atomic(
    tmp_path: Path,
    backend: str,
    named: bool,
    amount: int,
    mana: Literal["normal", "low"],
    allowed: bool,
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=False, amount=amount)

    def environment(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "events": tuple(
                            event.model_copy(
                                update={
                                    "kind": GreatHasteChannel.model_validate_json(event.kind)
                                    .model_copy(update={"mana": mana})
                                    .model_dump_json()
                                }
                            )
                            if event.id.startswith(CHANNEL)
                            else event
                            for event in state.resources.events
                        )
                    }
                )
            }
        )

    await change(cid, play, "current-ritual-mana", environment)
    service = GreatHasteService(play)
    play.rng = RecordedDice([])
    state = play._load(await play.store.read(cid))
    first_command = command(state.revision, 0)
    first_selected = (
        first_command
        if named
        else StepCastGreatHaste.model_validate(
            first_command.model_dump(exclude={"kind", "known_fact_id"})
        )
    )
    first = await service.execute(cid, first_selected, principal_id="alice")
    assert first.outcome == "casting"
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    named_selected = command(state.revision, 1)
    selected = (
        named_selected
        if named
        else StepCastGreatHaste.model_validate(
            named_selected.model_dump(exclude={"kind", "known_fact_id"})
        )
    )
    if allowed:
        result = await service.execute(cid, selected, principal_id="alice")
        assert result.outcome == "casting"
        assert (
            latest(play._load(await play.store.read(cid)).resources)[
                "named-step"
            ].concentration_seconds
            == 2
        )
    else:
        with pytest.raises(ValidationError, match="ritual base skill 15"):
            await service.execute(cid, selected, principal_id="alice")
        assert await play.store.read(cid) == before
        assert await play.store.history(cid) == history
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_ongoing_ritual_one_yard_cap_despite_actual_two_yard_step(
    tmp_path: Path, backend: str, named: bool
) -> None:
    from wayfarer.engine.simulation.hex_geometry import Hex
    from wayfarer.engine.simulation.magic.great_haste_step_state import CastingStep

    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11)
    state = play._load(await play.store.read(cid))
    build = play.rules_context.approved_build(state, "a")
    assert next(v.value for v in build.sheet.values if v.target == "secondary:basic-move") == 11
    service = GreatHasteService(play)
    play.rng = RecordedDice([])
    initial = await play.store.read(cid)
    initial_history = await play.store.history(cid)
    initial_selected = command(state.revision, 0).model_copy(
        update={"step": CastingStep(hex_path=(Hex(q=0, r=1), Hex(q=1, r=1)))}
    )
    with pytest.raises(ValidationError, match="at most one yard"):
        await service.execute(
            cid,
            initial_selected
            if named
            else StepCastGreatHaste.model_validate(
                initial_selected.model_dump(exclude={"kind", "known_fact_id"})
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == initial
    assert await play.store.history(cid) == initial_history
    assert play.rng.exhausted()
    first = command(state.revision, 0)
    await service.execute(
        cid,
        first
        if named
        else StepCastGreatHaste.model_validate(first.model_dump(exclude={"kind", "known_fact_id"})),
        principal_id="alice",
    )
    before = await play.store.read(cid)
    state = play._load(before)
    selected = command(state.revision, 1).model_copy(
        update={"step": CastingStep(hex_path=(Hex(q=0, r=0), Hex(q=1, r=0)))}
    )
    with pytest.raises(ValidationError, match="at most one yard"):
        await service.execute(
            cid,
            selected
            if named
            else StepCastGreatHaste.model_validate(
                selected.model_dump(exclude={"kind", "known_fact_id"})
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("historical", [False, True])
async def test_plain_step_new_five_and_recorded_two_seeded_reexecution(
    tmp_path: Path, backend: str, historical: bool
) -> None:
    import json
    import secrets

    from test_great_haste_named_step import other_turns

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.orchestration.pipeline import submit
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare_named(
        tmp_path, backend, blocked=False, amount=4 if historical else 16
    )
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: "00" * 32
    service = GreatHasteService(play)
    for index in range(3):
        if index == 2:
            await other_turns(cid, play)
        state = play._load(await play.store.read(cid))
        selected = StepCastGreatHaste.model_validate(
            command(state.revision, index, blocked=False).model_dump(
                exclude={"kind", "known_fact_id"}
            )
        )
        if historical:
            await submit(
                play,
                cid,
                service.plan(state, selected, "alice", combat_casting=True, ritual_step=False),
                principal_id="alice",
            )
        else:
            await service.execute(cid, selected, principal_id="alice")
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    markers = [
        json.loads(record.command_input)["generation"]
        for record in records
        if record.command_input
        and json.loads(record.command_input).get("operation") == "great-haste"
    ]
    assert markers == [2 if historical else 5] * 3
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "plain-seeded"),
    )
    assert len(checks) == 5 and all(check.folded and check.reexecuted for check in checks)
    assert play._load(replayed) == play._load(final)


@pytest.mark.parametrize(("generation", "named"), [(5, True), (4, False), (6, False)])
def test_new_step_generations_refuse_wrong_carrier_and_future(generation: int, named: bool) -> None:
    import json

    from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, features
    from wayfarer.persistence.events import CommandInput, payload_digest

    selected = command(0, 0)
    encoded = (
        selected.model_dump(mode="json")
        if named
        else StepCastGreatHaste.model_validate(
            selected.model_dump(exclude={"kind", "known_fact_id"})
        ).model_dump(mode="json")
    )
    payload = {
        "operation": "great-haste",
        "generation": generation,
        "principal_id": "alice",
        "command": encoded,
    }
    payload[ORIGINAL] = json.dumps(payload, sort_keys=True)
    payload[KEY] = 1
    text = json.dumps(payload, sort_keys=True)
    with pytest.raises(ValidationError, match="generation"):
        features(CommandInput(payload_digest({"input": text}), text))
