"""Active-Symptoms old inputs, seeded replay, exact retries and scoped generation."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_gurps_melee import attack, setup
from test_social_dispatch import prepare as social_prepare
from test_symptom_attribute_consumers import penalize

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.orchestration.symptom_generations import (
    KEY,
    capture,
    correct_symptom_attributes,
    symptom_generation,
)
from wayfarer.persistence.command_inputs import ORIGINAL_INPUT, stamp
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.replay import command_text


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("generation_style", ["legacy", "flat", "current"])
async def test_active_symptoms_old_and_current_seeded_records_reexecute_exactly(
    tmp_path: Path, backend: str, generation_style: str
) -> None:
    legacy = generation_style == "legacy"
    cid, original = await setup(tmp_path / "setup", "gurps-basic-set-4e-2004", human=True)
    await attack(cid, original)
    initial = await original.store.read(cid)
    state = penalize(penalize(original._load(initial), "a"), "b")
    original.commit(initial, state)
    source = build_play(tmp_path / "source", original.engine, seeds=lambda: "00" * 32)
    await seed_campaign(source.store, initial)
    value = ChooseDefense(
        id="historical-defense",
        actor_id="b",
        expected_revision=initial["revision"],
        encounter_id="fight",
        defense="parry",
    )
    if generation_style != "current":
        # Cover both pre-correction bytes/target13 and the first corrected
        # generation, which retained only a canonical metadata-bearing object.
        plan = CombatService(source).plan(cid, value)
        payload = plan.payload if legacy else stamp(plan.payload, retain_original=False)
        with symptom_generation(not legacy):
            await commit_command(
                source,
                cid,
                value.id,
                value.expected_revision,
                payload,
                plan.resolve,
                actor_id="b",
                rng=source.rng,
            )
    else:
        await CombatService(source).execute(cid, value, principal_id="b")
    record = (await source.store.history(cid))[-1]
    result = CombatResult.model_validate_json(record.event["outcome"])
    assert record.reexecutable and result.injury
    assert result.injury.attack.effective_target == (13 if legacy else 9)
    assert result.injury.attack.dice == (4, 4, 2)
    assert result.injury.attack.outcome.succeeded is legacy
    assert (KEY not in json.loads(command_text(record))) is legacy
    assert (ORIGINAL_INPUT in json.loads(command_text(record))) is (generation_style == "current")
    clone = build_play(tmp_path / "replay", original.engine, backend=backend)
    await seed_campaign(clone.store, initial)
    await execute_recorded(clone, record)
    after = await clone.store.read(cid)
    assert after == record.state_after == await clone.store.replay(cid)
    replayed = (await clone.store.history(cid))[-1]
    assert replayed.event == record.event
    assert replayed.entropy_seed == record.entropy_seed
    assert command_text(replayed) == command_text(record)
    assert replayed.payload_hash == record.payload_hash
    history, stream = await clone.store.history(cid), await clone.store.stream(cid)
    restarted = PlayService(clone.store, clone.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, value, principal_id="b") == result
    with pytest.raises(ConflictError, match="different input"):
        await CombatService(restarted).execute(
            cid, value.model_copy(update={"defense": "none"}), principal_id="b"
        )
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(restarted).execute(cid, value, principal_id="a")
    assert await clone.store.read(cid) == after
    assert await clone.store.history(cid) == history
    assert await clone.store.stream(cid) == stream


async def test_generation_context_resets_on_failure_and_is_task_local() -> None:
    entered, resume = asyncio.Event(), asyncio.Event()

    async def old() -> None:
        with pytest.raises(RuntimeError), symptom_generation(False):
            assert not correct_symptom_attributes()
            entered.set()
            await resume.wait()
            assert not correct_symptom_attributes()
            raise RuntimeError("failed command")
        assert correct_symptom_attributes()

    async def current() -> None:
        await entered.wait()
        assert correct_symptom_attributes()
        resume.set()

    await asyncio.gather(old(), current())
    assert correct_symptom_attributes()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_indexed_input_distinguishes_absent_legacy_and_current_rows(
    tmp_path: Path, backend: str
) -> None:
    cid, original = await setup(tmp_path / "setup", "gurps-basic-set-4e-2004")
    store = open_store(tmp_path, backend=backend)
    initial = await original.store.read(cid)
    await seed_campaign(store, initial)
    assert await store.command_input(cid, "absent") is None
    seed = await store.command_input(cid, "setup:seed")
    assert seed and seed.text and seed.payload_hash == payload_digest({"input": seed.text})
    assert await store.command_input(cid + "-other", "setup:seed") is None
    # Legacy databases can have a receipt hash with no recoverable input. This
    # indexed lookup must not confuse that row with a command that never ran.
    db = await store._connect()
    try:
        placeholder = "?" if backend == "sqlite" else "%s"
        await db.execute(
            f"UPDATE command_log SET command_input=NULL WHERE campaign={placeholder} AND command_id={placeholder}",
            (cid, "setup:seed"),
        )
        await db.commit()
    finally:
        await db.close()
    assert await store.command_input(cid, "setup:seed") == CommandInput(seed.payload_hash, None)
    text = '{ "operation": "seed", "campaign": "different" }'
    captured, generation = await capture(store, cid, "setup:seed", text)
    assert captured == text and not generation


@pytest.mark.parametrize("bad", [None, 0, 2, True, "1"])
async def test_future_or_malformed_generation_rejects_before_resolution(
    tmp_path: Path, bad: object
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    before = await play.store.read(cid)
    record = (await play.store.history(cid))[-1]
    payload = json.loads(command_text(record))
    payload[KEY] = bad
    encoded = json.dumps(payload)
    invalid = replace(
        record, command_input=encoded, payload_hash=payload_digest({"input": encoded})
    )
    with (
        replay_inputs(invalid),
        pytest.raises(ValidationError, match="Unsupported recorded Symptoms"),
    ):
        # Includes the context captured by a plan before submit is entered.
        _ = play.rules_context
    assert correct_symptom_attributes()
    assert await play.store.read(cid) == before


def fright_context(
    play: PlayService, state: PlayState, command: SocialCommand
) -> ResolvedInteraction:
    compiled = build(play.rules_context, state, command.subject_id, defensive=True)
    assert compiled.statistics
    return ResolvedInteraction(
        SocialContext(
            "gurps-basic-set-4e-2004",
            compiled.statistics.will,
            will=compiled.statistics.will,
            ht=compiled.statistics.ht,
        )
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [True, False])
async def test_fright_historical_inputs_and_current_checks_keep_private_receipts(
    tmp_path: Path, backend: str, legacy: bool
) -> None:
    cid, original = await social_prepare(tmp_path / "setup")
    initial = await original.store.read(cid)
    original.commit(initial, penalize(original._load(initial), "a", "iq"))
    source = build_play(tmp_path / "source", original.engine, seeds=lambda: "00" * 32)
    await seed_campaign(source.store, initial)
    value = SocialCommand(
        id="fright",
        actor_id="a",
        subject_id="a",
        kind="fright",
        trigger_id="fear",
        expected_revision=initial["revision"],
    )
    service = SocialService(source, fright_context)
    if legacy:
        plan = service.plan(source, source._load(initial), value, principal_id="gm")
        with symptom_generation(False):
            await commit_command(
                source,
                cid,
                value.id,
                value.expected_revision,
                plan.payload,
                plan.resolve,
                actor_id="gm",
                rng=source.rng,
            )
    else:
        await service.execute(cid, value, principal_id="gm")
    record = (await source.store.history(cid))[-1]
    after = source._load(record.state_after)
    private = next(
        json.loads(e.kind)["private"] for e in after.resources.events if e.id.startswith("social:")
    )
    assert private["check"]["effective_target"] == (6 if legacy else 10)
    assert private["check"]["dice"] == [4, 4, 2]
    assert private["check"]["outcome"] == ("failure" if legacy else "success")
    clone = build_play(tmp_path / "clone", original.engine, backend=backend)
    await seed_campaign(clone.store, initial)
    with replay_inputs(record):
        # SocialService owns a scenario resolver; offline generic dispatch does
        # not advertise this family. Its explicit trusted resolver is replayed.
        await SocialService(clone, fright_context).execute(cid, value, principal_id="gm")
    assert await clone.store.read(cid) == record.state_after == await clone.store.replay(cid)
    assert command_text((await clone.store.history(cid))[-1]) == command_text(record)
    saved = await clone.store.read(cid)
    clone.rng = RecordedDice([])

    def forbidden(
        play: PlayService, state: PlayState, command: SocialCommand
    ) -> ResolvedInteraction:
        raise AssertionError("A completed Fright Check cannot be recomputed")

    retry = SocialService(clone, forbidden)
    await retry.execute(cid, value, principal_id="gm")
    with pytest.raises(ValidationError, match="director authority"):
        await retry.execute(cid, value, principal_id="alice")
    with pytest.raises(ConflictError, match="different input"):
        await retry.execute(
            cid, value.model_copy(update={"trigger_id": "different-fear"}), principal_id="gm"
        )
    assert await clone.store.read(cid) == saved
