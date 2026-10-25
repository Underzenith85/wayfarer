"""B236/B366/B481: a new enchanting commitment cannot overlap pending concentration."""

import json
import secrets
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_enchanting_noncombat_nighttime import configured as blueprint
from test_enchanting_source import source_recipe
from test_statistics import profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import CampaignRules, PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.supernatural.abilities import MODIFIERS, definition
from wayfarer.engine.rules.supernatural.ability_types import AbilitySpec
from wayfarer.engine.rules.types.recovery import RecoveryTask
from wayfarer.engine.simulation.ability_types import AbilityChannel, AbilityCommand, AbilityRules
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.magic.bindings import SpellChannel
from wayfarer.engine.simulation.magic.enchanting import EnergyContribution
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    EnchantmentOutcome,
    InterruptEnchanting,
)
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.world import Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
Family = Literal["spell", "ability"]
Method = Literal["slow-and-sure", "quick-and-dirty"]


async def fixture(
    path: Path,
    backend: str,
    *,
    method: Method = "slow-and-sure",
    resting: bool = False,
    low_fp: bool = False,
    other_care: bool = True,
    one_enchanter: bool = False,
    rest_actors: tuple[str, ...] = ("a", "b"),
) -> tuple[Campaign, PlayService]:
    _, donor, foundation = await blueprint(
        path / "blueprint", enroll=False, retain_source_spells=True
    )
    old = donor.engine.reviewer.compiler
    ability = AbilitySpec(definition_id="trait:night-reading", kind="mind-reading")
    package = replace(
        profile_package(PROFILE), definitions=(*old.definitions.values(), definition(ability))
    )
    package = replace(
        package,
        sources=package.sources
        + tuple(
            source
            for source in enchantment_package().sources
            if source.id not in {existing.id for existing in package.sources}
        ),
    )
    catalog = RulesCatalog((package,))
    rules = CampaignRules(
        package.edition,
        (PackagePin(package.id, package.version, package.digest),),
        old.policy.id,
        old.policy.version,
    )
    compiler = CharacterCompiler(
        catalog,
        rules,
        replace(old.policy, point_budget=500),
        statistics_profile=PROFILE,
        trait_runtime_hooks=frozenset(
            ["ability:mind-reading"]
            + [modifier.runtime_hook for modifier in MODIFIERS if modifier.runtime_hook]
        ),
    )
    world = replace(
        foundation.world,
        facts=foundation.world.facts
        + (
            Fact("visible-a", "a", "visible", "enchanter a"),
            Fact("visible-b", "b", "visible", "enchanter b"),
            Fact("thought-a", "a", "surface-thought", "A source thought"),
            Fact("thought-b", "b", "surface-thought", "B source thought"),
        ),
    )
    world = world.learn("a", "visible-b").learn("b", "visible-a")
    runtime = donor.engine.rules
    assert runtime.spells and runtime.enchanting
    runtime = runtime.model_copy(
        update={
            "spells": runtime.spells.model_copy(
                update={
                    "channels": tuple(
                        SpellChannel(
                            id="light-" + actor,
                            actor_id=actor,
                            target_id=actor,
                            location_id="forge",
                            spell_id="light",
                        )
                        for actor in ("a", "b")
                    )
                }
            ),
            "abilities": AbilityRules(
                id="reading",
                version=1,
                abilities=(ability,),
                channels=tuple(
                    AbilityChannel(
                        id="read-" + actor,
                        ability_id=ability.definition_id,
                        actor_id=actor,
                        target_id=target,
                        location_id="forge",
                        thought_fact_ids=("thought-" + target,),
                    )
                    for actor, target in (("a", "b"), ("b", "a"))
                ),
            ),
            "enchanting": runtime.enchanting.model_copy(
                update={"recipes": (source_recipe("staff").model_copy(update={"method": method}),)}
            ),
        }
    )
    engine = ActionEngine(
        PowerReviewer(compiler, donor.engine.reviewer.policy, donor.engine.reviewer.gm_ids),
        ResourceEngine(
            world, catalog, rules, compiler.policy, tuple(donor.engine.resources.specs.values())
        ),
        runtime,
    )
    play = build_play(
        path / "actual", engine, backend=backend, rng=secrets, seeds=lambda: f"{1:064x}"
    )
    initial = campaign(engine)
    actors = tuple(
        ActorSetup(
            actor_id=actor.actor_id,
            proposal=actor.proposal.model_copy(
                update={
                    "draft": actor.proposal.draft.model_copy(
                        update={
                            "purchases": actor.proposal.draft.purchases
                            + (Purchase(definition_id=ability.definition_id),)
                        }
                    )
                }
            ),
        )
        for actor in foundation.actors
    )
    for actor in actors:
        review = engine.reviewer.review(actor.proposal)
        assert review.compilation.build is not None, review.compilation.diagnostics
    seed = foundation.resources
    if resting:
        seed = seed.model_copy(
            update={
                "recovery_tasks": tuple(
                    RecoveryTask(
                        id="source-rest-" + actor,
                        actor_id=actor,
                        target_id=actor,
                        profile_id=PROFILE,
                        kind="rest",
                        ordinary_entitlement=5,
                        start=0,
                        due=600,
                    )
                    for actor in rest_actors
                )
                + (
                    (
                        RecoveryTask(
                            id="source-natural",
                            actor_id="a",
                            target_id="a",
                            profile_id=PROFILE,
                            kind="natural",
                            start=0,
                            due=86400,
                        ),
                    )
                    if other_care
                    else ()
                ),
            }
        )
    state = play.initial_state(initial, world, seed, actors)
    if resting or low_fp:
        # The scenario starts with ordinary FP already spent; no receipt is forged.
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            pool.model_copy(update={"current": 5})
                            if pool.id.startswith("fp:")
                            else pool
                            for pool in state.resources.pools
                        )
                    }
                )
            }
        )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    service = EnchantmentService(play)
    await service.declare_staff(
        initial["id"],
        DeclareStaffConstruction(
            id="staff",
            actor_id="a",
            expected_revision=0,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                form="wand",
                length_yards=Fraction(1),
                material="reed",
                once_living=True,
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        initial["id"],
        CreateEnchantment(
            id="project",
            actor_id="a",
            expected_revision=1,
            project_id="staff-project",
            recipe_id="source-staff",
            target_item_id="blade",
            enchanter_ids=("a",) if one_enchanter else ("a", "b"),
        ),
        principal_id="gm",
    )
    return initial, play


def work(
    revision: int, *, identifier: str = "work", method: Method = "slow-and-sure"
) -> BeginEnchanting:
    return BeginEnchanting(
        id=identifier,
        actor_id="a",
        expected_revision=revision,
        project_id="staff-project",
        contributions=tuple(EnergyContribution(actor_id=actor, fp=10, hp=5) for actor in ("a", "b"))
        if method == "quick-and-dirty"
        else (),
    )


async def start_pending(play: PlayService, cid: str, family: Family, actor: str) -> None:
    state = play._load(await play.store.read(cid))
    if family == "spell":
        result = await SpellService(play).execute(
            cid,
            SpellCommand(
                id="pending-" + actor,
                actor_id=actor,
                expected_revision=state.revision,
                kind="start",
                spell_id="light",
                channel_id="light-" + actor,
                cast_id="personal-" + actor,
            ),
            principal_id=actor,
        )
        assert result.outcome == "casting" and result.energy_spent == 0
    else:
        ability_result = await AbilityService(play).execute(
            cid,
            AbilityCommand(
                id="pending-" + actor,
                actor_id=actor,
                expected_revision=state.revision,
                ability_id="trait:night-reading",
                channel_id="read-" + actor,
            ),
            principal_id=actor,
        )
        assert ability_result.outcome == "concentrating"


async def finish_pending(
    play: PlayService, cid: str, family: Family, actor: str, *, cancel: bool = True
) -> None:
    state = play._load(await play.store.read(cid))
    if not cancel:
        await play.execute(
            cid,
            Wait(id="cast-time", actor_id=actor, expected_revision=state.revision, ticks=1),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    if family == "spell":
        await SpellService(play).execute(
            cid,
            SpellCommand(
                id="finish-" + actor,
                actor_id=actor,
                expected_revision=state.revision,
                kind="cancel" if cancel else "complete",
                spell_id="light",
                channel_id="light-" + actor,
                cast_id="personal-" + actor,
            ),
            principal_id=actor,
        )
    else:
        await AbilityService(play).execute(
            cid,
            AbilityCommand(
                id="finish-" + actor,
                actor_id=actor,
                expected_revision=state.revision,
                kind="cancel" if cancel else "resolve",
                ability_id="trait:night-reading",
                channel_id="read-" + actor,
            ),
            principal_id=actor,
        )


async def replay(initial: Campaign, play: PlayService, path: Path) -> None:
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    records = [
        record for record in await play.store.history(cid) if record.command_id != "setup:seed"
    ]
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, path),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert play._load(replayed) == play._load(await play.store.read(cid))
    assert play._load(replayed) == play._load(await play.store.replay(cid))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["spell", "ability"])
@pytest.mark.parametrize("actor", ["a", "b"])
@pytest.mark.parametrize("method", ["slow-and-sure", "quick-and-dirty"])
async def test_every_participant_pending_concentration_precedes_new_work_costs(
    tmp_path: Path, backend: str, family: Family, actor: str, method: Method
) -> None:
    initial, play = await fixture(tmp_path, backend, method=method)
    cid = initial["id"]
    await start_pending(play, cid, family, actor)
    before = await play.store.read(cid)
    state = play._load(before)
    command = work(state.revision, method=method)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="already concentrating"):
        await EnchantmentService(play).execute(cid, command, principal_id="gm")
    assert play.rng.exhausted() and await play.store.read(cid) == before
    assert await play.store.command_input(cid, command.id) is None
    play.rng = secrets
    await finish_pending(play, cid, family, actor)
    state = play._load(await play.store.read(cid))
    accepted = command.model_copy(update={"expected_revision": state.revision})
    result = await EnchantmentService(play).execute(cid, accepted, principal_id="gm")
    assert result.status == "work-started"
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await EnchantmentService(restarted).execute(cid, accepted, principal_id="gm") == result
    await replay(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["spell", "ability"])
async def test_ongoing_nonconcentrating_effects_allow_begin_and_actual_resume(
    tmp_path: Path, backend: str, family: Family
) -> None:
    initial, play = await fixture(tmp_path, backend)
    cid = initial["id"]
    await start_pending(play, cid, family, "b")
    await finish_pending(play, cid, family, "b", cancel=False)
    state = play._load(await play.store.read(cid))
    result = await EnchantmentService(play).execute(cid, work(state.revision), principal_id="gm")
    assert result.status == "work-started"
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="elapsed",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=3600,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        InterruptEnchanting(
            id="interrupt",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
        ),
        principal_id="gm",
    )
    await start_pending(play, cid, family, "a")
    before = await play.store.read(cid)
    state = play._load(before)
    resume = work(state.revision, identifier="resume")
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="already concentrating"):
        await EnchantmentService(play).execute(cid, resume, principal_id="gm")
    assert play.rng.exhausted() and await play.store.read(cid) == before
    play.rng = secrets
    await finish_pending(play, cid, family, "a")
    state = play._load(await play.store.read(cid))
    result = await EnchantmentService(play).execute(
        cid, resume.model_copy(update={"expected_revision": state.revision}), principal_id="gm"
    )
    assert result.status == "work-started"
    await replay(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["spell", "ability"])
async def test_historical_absence_keeps_original_overlap_retry_and_seed_replay(
    tmp_path: Path, backend: str, family: Family
) -> None:
    initial, play = await fixture(tmp_path, backend)
    cid = initial["id"]
    await start_pending(play, cid, family, "a")
    state = play._load(await play.store.read(cid))
    command = work(state.revision)
    service = EnchantmentService(play)
    # Execute exactly the old native plan: absence preserves the historical entry behavior.
    result = await submit(
        play, cid, service.plan(play, state, command, principal_id="gm"), principal_id="gm"
    )
    assert isinstance(result, EnchantmentOutcome) and result.status == "work-started"
    prior = await play.store.command_input(cid, command.id)
    assert prior and prior.text and "enchantment_work_admission_generation" not in prior.text
    after = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await service.execute(cid, command, principal_id="gm") == result
    assert play.rng.exhausted() and await play.store.read(cid) == after
    await replay(initial, play, tmp_path / "historical-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_new_work_authority_stale_payload_and_native_retry(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await fixture(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    command = work(state.revision)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="director authority"):
        await EnchantmentService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await EnchantmentService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="gm"
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()
    play.rng = secrets
    result = await EnchantmentService(play).execute(cid, command, principal_id="gm")
    raw = await play.store.command_input(cid, command.id)
    assert raw and raw.text
    encoded = replay_payload(raw.text)
    assert isinstance(encoded, dict) and encoded.get("enchantment_work_admission_generation") == 1
    final = await play.store.read(cid)
    with pytest.raises(ConflictError):
        await EnchantmentService(play).execute(
            cid, command.model_copy(update={"extra_energy": 1}), principal_id="gm"
        )
    assert await play.store.read(cid) == final
    assert await EnchantmentService(play).execute(cid, command, principal_id="gm") == result
    await replay(initial, play, tmp_path / "new-seed")


async def test_concentration_generation_is_strict_and_digest_bound(tmp_path: Path) -> None:
    from wayfarer.orchestration.enchantment_generations import (
        WORK_ADMISSION_KEY,
        current_work_admission,
    )
    from wayfarer.orchestration.replay_inputs import replay_inputs

    # A real accepted begin provides all command identity, envelope and pipeline wrappers.
    initial, play = await fixture(tmp_path / "source", "sqlite")
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(cid, work(state.revision), principal_id="gm")
    record = (await play.store.history(cid))[-1]
    assert record.command_input
    from wayfarer.persistence.events import payload_digest

    raw = replay_payload(record.command_input)
    assert isinstance(raw, dict)
    with replay_inputs(replace(record, payload_hash="0" * 64)):
        with pytest.raises(ValidationError, match="digest"):
            await current_work_admission(play, cid, record.command_id, eligible=True)
    for value in (None, True, "1", 0, 2):
        encoded = json.dumps(raw | {WORK_ADMISSION_KEY: value})
        forged = replace(
            record, command_input=encoded, payload_hash=payload_digest({"input": encoded})
        )
        with replay_inputs(forged):
            with pytest.raises(ValidationError, match="generation"):
                await current_work_admission(play, cid, record.command_id, eligible=True)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_real_pending_rest_work_interruption_preserves_other_tasks_and_history(
    tmp_path: Path,
    backend: str,
    legacy: bool,
) -> None:
    # Trusted genesis supplies real pending recovery tasks, not fabricated receipts.
    initial, play = await fixture(tmp_path, backend, resting=True, one_enchanter=True)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    natural = next(task for task in state.resources.recovery_tasks if task.kind == "natural")
    command = work(state.revision)
    invalid = command.model_copy(
        update={"contributions": (EnergyContribution(actor_id="a", fp=1),)}
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="does not spend FP or HP"):
        await EnchantmentService(play).execute(cid, invalid, principal_id="gm")
    assert play.rng.exhausted() and await play.store.read(cid) == before
    play.rng = secrets
    if legacy:
        service = EnchantmentService(play)
        await submit(
            play, cid, service.plan(play, state, command, principal_id="gm"), principal_id="gm"
        )
    else:
        await EnchantmentService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    rest = {task.actor_id: task for task in state.resources.recovery_tasks if task.kind == "rest"}
    assert rest["a"].status == ("pending" if legacy else "interrupted")
    assert rest["a"].interrupted_at == (None if legacy else 0)
    assert rest["b"].status == "pending" and rest["b"].interrupted_at is None
    assert (
        next(task for task in state.resources.recovery_tasks if task.kind == "natural") == natural
    )
    advance = AdvanceEnchanting(
        id="work600",
        actor_id="a",
        expected_revision=state.revision,
        project_id="staff-project",
        work_id="work",
        to=600,
    )
    await EnchantmentService(play).execute(cid, advance, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == (
        6 if legacy else 5
    )
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:b") == 6
    await replay(initial, play, tmp_path / "work-rest-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_offshift_rest_remains_legal_after_work_admission(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
    from wayfarer.orchestration.medical import CareEnvironment, MedicalService

    # Use the actual campaign produced by the source fixture.
    initial, play = await fixture(
        tmp_path / "actual",
        backend,
        resting=True,
        one_enchanter=True,
        rest_actors=("a",),
        other_care=False,
    )
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(cid, work(state.revision), principal_id="gm")
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="night",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=28800,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    care = BeginRecovery(
        id="night-rest",
        actor_id="a",
        expected_revision=state.revision,
        kind="rest",
        target_id="a",
        seconds=600,
    )
    result = await MedicalService(play, lambda *_: CareEnvironment()).execute(
        cid, care, principal_id="a"
    )
    assert result.status == "pending"
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="rest600",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=29400,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 6


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_actual_medical_rest_then_work_has_captured_genesis_replay(
    tmp_path: Path,
    backend: str,
    legacy: bool,
) -> None:
    from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
    from wayfarer.orchestration.medical import CareEnvironment, MedicalService

    initial, play = await fixture(tmp_path, backend, low_fp=True, one_enchanter=True)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    care = BeginRecovery(
        id="real-rest",
        actor_id="a",
        expected_revision=state.revision,
        kind="rest",
        target_id="a",
        seconds=600,
    )
    medicine = MedicalService(play, lambda *_: CareEnvironment())
    accepted = await medicine.execute(cid, care, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert state.resources.recovery_tasks[0].status == "pending"
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 5
    command = work(state.revision)
    if legacy:
        service = EnchantmentService(play)
        await submit(
            play, cid, service.plan(play, state, command, principal_id="gm"), principal_id="gm"
        )
    else:
        await EnchantmentService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert state.resources.recovery_tasks[0].status == ("pending" if legacy else "interrupted")
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await medicine.execute(cid, care, principal_id="a") == accepted
    assert play.rng.exhausted() and await play.store.read(cid) == before
    if not legacy:

        def unavailable(*_args: object) -> CareEnvironment:
            raise AssertionError("Rejected rest must not query the environment")

        illegal = care.model_copy(update={"id": "work-rest", "expected_revision": state.revision})
        with pytest.raises(ConflictError, match="committed to enchanting"):
            await MedicalService(play, unavailable).execute(cid, illegal, principal_id="a")
        assert play.rng.exhausted() and await play.store.read(cid) == before
        assert await play.store.command_input(cid, illegal.id) is None
    play.rng = secrets
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="real-work600",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=600,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == (
        6 if legacy else 5
    )
    await replay(initial, play, tmp_path / "real-rest-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("seconds", [600, 82800, 82801])
async def test_future_resume_preserves_only_recovery_that_fits_before_work(
    tmp_path: Path,
    backend: str,
    seconds: int,
) -> None:
    from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
    from wayfarer.orchestration.medical import CareEnvironment, MedicalService

    initial, play = await fixture(tmp_path, backend, low_fp=True, one_enchanter=True)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(cid, work(state.revision), principal_id="gm")
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="before-break",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=3600,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        InterruptEnchanting(
            id="break", actor_id="a", expected_revision=state.revision, project_id="staff-project"
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    care = BeginRecovery(
        id="future-rest",
        actor_id="a",
        expected_revision=state.revision,
        kind="rest",
        target_id="a",
        seconds=seconds,
    )
    medicine = MedicalService(play, lambda *_: CareEnvironment())
    await medicine.execute(cid, care, principal_id="a")
    state = play._load(await play.store.read(cid))
    resume = work(state.revision, identifier="future-resume")
    rest = state.resources.recovery_tasks[0]
    if seconds == 82801:
        before = await play.store.read(cid)
        play.rng = RecordedDice(())
        with pytest.raises(ConflictError, match="overlaps the next"):
            await EnchantmentService(play).execute(cid, resume, principal_id="gm")
        assert play.rng.exhausted() and await play.store.read(cid) == before
        play.rng = secrets
        await play.execute(
            cid,
            Wait(id="rest-started", actor_id="a", expected_revision=state.revision, ticks=1),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        await medicine.execute(
            cid,
            FinishRecovery(
                id="stop-rest", actor_id="a", expected_revision=state.revision, task_id=care.id
            ),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        resume = resume.model_copy(update={"expected_revision": state.revision})
    await EnchantmentService(play).execute(cid, resume, principal_id="gm")
    state = play._load(await play.store.read(cid))
    if seconds != 82801:
        assert state.resources.recovery_tasks[0] == rest
        await EnchantmentService(play).execute(
            cid,
            AdvanceEnchanting(
                id="earn-rest",
                actor_id="a",
                expected_revision=state.revision,
                project_id="staff-project",
                work_id=resume.id,
                to=3600 + seconds,
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
        finished = await medicine.execute(
            cid,
            FinishRecovery(
                id="finish-rest", actor_id="a", expected_revision=state.revision, task_id=care.id
            ),
            principal_id="a",
        )
        assert finished.fp_recovered == (1 if seconds == 600 else 5)
        state = play._load(await play.store.read(cid))
        assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == (
            6 if seconds == 600 else 10
        )
    await replay(initial, play, tmp_path / "future-rest-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_medical_offshift_interval_crossing_refuses_before_resolver(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
    from wayfarer.orchestration.medical import CareEnvironment, MedicalService

    initial, play = await fixture(tmp_path / "actual", backend, low_fp=True, one_enchanter=True)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(cid, work(state.revision), principal_id="gm")
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="night-now",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=28800,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))

    def unavailable(*_args: object) -> CareEnvironment:
        raise AssertionError("Invalid complete interval cannot query rest facts")

    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    command = BeginRecovery(
        id="crossing-rest",
        actor_id="a",
        expected_revision=state.revision,
        kind="rest",
        target_id="a",
        seconds=57601,
    )
    with pytest.raises(ConflictError, match="overlaps the next"):
        await MedicalService(play, unavailable).execute(cid, command, principal_id="a")
    assert play.rng.exhausted() and await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rest_replay_facts_are_strict_and_unmarked_medical_is_not_guessed(
    tmp_path: Path, backend: str
) -> None:
    from pydantic import ValidationError as ModelValidationError

    from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
    from wayfarer.orchestration.medical import CareEnvironment, MedicalService
    from wayfarer.orchestration.medical_rest_generations import ENVIRONMENT, KEY
    from wayfarer.orchestration.replay_inputs import replay_inputs
    from wayfarer.persistence.events import payload_digest

    initial, play = await fixture(tmp_path, backend, low_fp=True)
    cid = initial["id"]
    before = await play.store.read(cid)
    state = play._load(before)
    command = BeginRecovery(
        id="captured-rest",
        actor_id="a",
        expected_revision=state.revision,
        kind="rest",
        target_id="a",
        seconds=600,
    )
    medical = MedicalService(play, lambda *_: CareEnvironment(food=True))
    await medical.execute(cid, command, principal_id="a")
    record = (await play.store.history(cid))[-1]
    assert record.command_input
    source = replay_payload(record.command_input)
    assert isinstance(source, dict)
    facts = source[ENVIRONMENT]
    assert isinstance(facts, dict) and facts["food"] is True
    invalid_inputs = [source | {KEY: value} for value in (None, True, "1", 0, 2)]
    invalid_inputs += [
        source | {ENVIRONMENT: facts | {"unknown": True}},
        source | {ENVIRONMENT: {}},
        source | {ENVIRONMENT: facts | {"food": 1}},
    ]
    play.rng = RecordedDice(())
    after = await play.store.read(cid)
    for payload in invalid_inputs:
        text = json.dumps(payload)
        forged = replace(record, command_input=text, payload_hash=payload_digest({"input": text}))
        with replay_inputs(forged):
            with pytest.raises((ValidationError, ModelValidationError)):
                await medical.execute(cid, command, principal_id="a")
        assert play.rng.exhausted() and await play.store.read(cid) == after
    # The replay adapter must fail instead of supplying ordinary defaults for old care.
    unmarked = {key: value for key, value in source.items() if key not in {KEY, ENVIRONMENT}}
    text = json.dumps(unmarked)
    old = replace(record, command_input=text, payload_hash=payload_digest({"input": text}))
    with pytest.raises(ValidationError, match="Unmarked medical"):
        await FixtureExecutor(play.engine, tmp_path / "no-guess")(before, old)
