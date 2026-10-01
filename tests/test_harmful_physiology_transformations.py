"""B83/B130/B161 body changes settle old-body debt and preserve observed facts."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_play
from test_actions import campaign, world
from test_harmful_physiology_persistence import clock, condition, observation, prepare, trait
from test_statistics import gurps_draft, profile_package
from test_transformations import attachment_routes
from trait_support import options, trait_compiler

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.movement_forms import RUNTIME_HOOKS as FORM_HOOKS
from wayfarer.engine.rules.traits.movement_forms import package as forms_package
from wayfarer.engine.rules.traits.physiology import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.transformations import (
    TraitRoute,
    TransformationRecord,
    TransformationRule,
    TransformationRules,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.engine.simulation.traits.harmful_physiology_state import (
    AdvancePhysiology,
    conditions,
)
from wayfarer.engine.simulation.traits.physiology import history
from wayfarer.orchestration.advancement import ApplyMigration, MigrationService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.transformations import TransformationService


async def transform(
    play: PlayService, cid: str, operation: str, **fields: object
) -> TransformationRecord:
    state = play._load(await play.store.read(cid))
    command: dict[str, object] = dict(
        operation=operation,
        id=f"form:{operation}:{state.revision}",
        actor_id="a",
        expected_revision=state.revision,
    )
    command.update(fields)
    return await TransformationService(play).execute(cid, command, principal_id="gm")


async def start(play: PlayService, cid: str) -> TransformationRecord:
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval
    proposed = await transform(
        play,
        cid,
        "propose",
        rule_id="body",
        expected_build_revision=state.actors[0].approval.build_revision,
    )
    return await transform(play, cid, "approve", proposal_id=proposed.proposal_id, reason="body")


async def advance(play: PlayService, cid: str, to: int) -> None:
    revision = (await play.store.read(cid))["revision"]
    await HarmfulPhysiologyService(play).execute(
        cid,
        AdvancePhysiology(
            id=f"advance:{revision}", actor_id="a", expected_revision=revision, to=to
        ),
        principal_id="gm",
    )


async def prepare_form(
    path: Path,
    backend: str = "sqlite",
    *,
    source: str = "weakness",
    frequency: str = "minute",
    absent: bool = False,
    kind: Literal["alternate-form", "morph"] = "alternate-form",
    native_st: int = 10,
    target_st: int = 20,
    target_frequency: str | None = None,
) -> tuple[str, PlayService]:
    compiled = trait_compiler(
        "harmful-forms", PROFILE, package(), forms_package(), hooks=RUNTIME_HOOKS | FORM_HOOKS
    )
    # Independent B161 common Weakness values and B130 common Dependency prices.
    prices = (
        {"minute": -40, "five-minutes": -20, "thirty-minutes": -10}
        if source == "weakness"
        else {"minute": -50, "hour": -40}
    )
    target_frequency = target_frequency or frequency
    delta = (
        (target_st - native_st) * 10
        + (0 if absent else prices[target_frequency])
        - prices[frequency]
    )
    native_cost, target_cost = max(0, -delta), max(0, delta)
    shape = Purchase(
        definition_id="advantage:" + kind,
        trait=options(**{"native-template-cost": native_cost, "target-template-cost": target_cost}),
    )
    native = gurps_draft(shape, trait(source, frequency), st_level=native_st)
    target = gurps_draft(
        shape, *((trait(source, target_frequency),) if not absent else ()), st_level=target_st
    )
    rule = TransformationRule(
        id="body",
        actor_id="a",
        kind=kind,
        source_ref="B83",
        target=target,
        target_body_id="a:form",
        target_body=HumanBody(anatomy="human"),
        form_model_id="b" if kind == "morph" else None,
        forced_reversion_influence="dispel magic",
        native_template_cost=native_cost,
        target_template_cost=target_cost,
        treatment_seconds=10,
        reversible=True,
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body") for p in native.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
    )
    base = profile_package(PROFILE)
    combined = replace(
        base,
        id="package:test-harmful-forms",
        definitions=base.definitions + package().definitions + forms_package().definitions,
    )
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="forms", version=1), frozenset({"gm"})),
        ResourceEngine(world(), RulesCatalog((combined,)), compiled.rules, compiled.policy, ()),
        ActionRules(
            id="forms",
            version=1,
            maximum_wait=10000,
            transformations=TransformationRules(id="forms", version=1, transformations=(rule,)),
        ),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    await seed_play(
        play,
        initial,
        world().learn("a", "promise"),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100),),
            pools=(
                Pool(
                    id="hp:a",
                    current=native_st,
                    maximum=native_st,
                    injury=InjuryStatus(profile_id=PROFILE),
                ),
            ),
        ),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=native),
                aware_of=("b",),
                body=HumanBody(anatomy="human"),
            ),
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    return initial["id"], play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_timed_expiry_settles_old_frequency_once_before_new_body(
    tmp_path: Path, backend: str
) -> None:
    original = gurps_draft(trait(frequency="five-minutes"))
    rule = TransformationRule(
        id="body",
        actor_id="a",
        kind="supernatural-affliction",
        source_ref="B296",
        target=gurps_draft(trait()),
        target_body_id="a:temporary",
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body") for p in original.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
        voluntary=False,
        expires_after_seconds=60,
        curable=True,
    )
    cid, play = await prepare(
        tmp_path,
        backend,
        frequency="five-minutes",
        transformations=TransformationRules(id="forms", version=1, transformations=(rule,)),
    )
    active = await start(play, cid)
    await HarmfulPhysiologyService(play).execute(cid, observation(2), principal_id="gm")
    await clock(play, cid, 60)
    play.rng = RecordedDice((2,))
    state = play._load(await play.store.read(cid))
    command = dict(
        operation="resolve",
        id="expiry",
        actor_id="a",
        expected_revision=state.revision,
        proposal_id=active.proposal_id,
        resolution="expire",
    )
    service = TransformationService(play)
    result = await service.execute(cid, command, principal_id="gm")
    assert result.status == "reverted"
    state = play._load(await play.store.read(cid))
    assert state.resources.pools[0].current == 8
    assert len(history(state.resources)) == 1
    interval = history(state.resources)[0].interval
    assert interval is not None and interval.due == 60
    assert conditions(state.resources)[0].due == 360
    assert await service.execute(cid, command, principal_id="gm") == result
    play.rng = RecordedDice(())
    await advance(play, cid, 359)
    assert play._load(await play.store.read(cid)).resources.pools[0].current == 8
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["alternate-form", "morph"])
async def test_forced_return_charges_big_body_then_scales_injury(
    tmp_path: Path, backend: str, kind: Literal["alternate-form", "morph"]
) -> None:
    cid, play = await prepare_form(tmp_path, backend, kind=kind)
    active = await start(play, cid)
    await clock(play, cid, 10)
    active = await transform(
        play, cid, "resolve", proposal_id=active.proposal_id, resolution="complete"
    )
    revision = (await play.store.read(cid))["revision"]
    await HarmfulPhysiologyService(play).execute(cid, observation(revision), principal_id="gm")
    await clock(play, cid, 70, identifier="owed")
    play.rng = RecordedDice((6,))
    result = await transform(
        play,
        cid,
        "resolve",
        proposal_id=active.proposal_id,
        resolution="force",
        influence="dispel magic",
    )
    assert result.status == "reverted"
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert (hp.maximum, hp.current) == (10, 7)  # B83: 6/20 lost becomes 3/10.
    injury = history(state.resources)[0].outcome
    assert (injury.hp_before, injury.hp_after) == (20, 14)
    assert conditions(state.resources)[0].due == 130
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_dormant_weakness_keeps_changes_and_only_future_return_exposure(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_form(tmp_path, backend, absent=True)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    active = await start(play, cid)
    await clock(play, cid, 10)
    active = await transform(
        play, cid, "resolve", proposal_id=active.proposal_id, resolution="complete"
    )
    assert (await condition(play, cid)).dormant
    await advance(play, cid, 1000)
    revision = (await play.store.read(cid))["revision"]
    await service.execute(
        cid, observation(revision, identifier="leave", present=False), principal_id="gm"
    )
    await transform(
        play,
        cid,
        "resolve",
        proposal_id=active.proposal_id,
        resolution="force",
        influence="dispel magic",
    )
    assert (await condition(play, cid)).due is None and not (await condition(play, cid)).present
    revision = (await play.store.read(cid))["revision"]
    await service.execute(cid, observation(revision, identifier="return"), principal_id="gm")
    play.rng = RecordedDice((2,))
    await advance(play, cid, 1060)
    assert len(history(play._load(await play.store.read(cid)).resources)) == 1
    assert (await condition(play, cid)).due == 1120


async def test_partial_dependency_contact_survives_dormant_body_and_interruption(
    tmp_path: Path,
) -> None:
    cid, play = await prepare_form(tmp_path, source="dependency", frequency="hour", absent=True)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(source="dependency", contact=True), principal_id="gm")
    await clock(play, cid, 300)
    active = await start(play, cid)
    await clock(play, cid, 310, identifier="treatment")
    active = await transform(
        play, cid, "resolve", proposal_id=active.proposal_id, resolution="complete"
    )
    revision = (await play.store.read(cid))["revision"]
    await service.execute(
        cid,
        observation(revision, identifier="pause", source="dependency", contact=True, present=False),
        principal_id="gm",
    )
    assert (await condition(play, cid)).contact_seconds == 310
    await advance(play, cid, 400)
    revision = (await play.store.read(cid))["revision"]
    await service.execute(
        cid,
        observation(revision, identifier="resume", source="dependency", contact=True),
        principal_id="gm",
    )
    await advance(play, cid, 500)
    await transform(
        play,
        cid,
        "resolve",
        proposal_id=active.proposal_id,
        resolution="force",
        influence="dispel magic",
    )
    # Independent B130 oracle: 310 contact before pause + 100 since resume;
    # 190 more seconds required, regardless of the two build changes.
    assert (await condition(play, cid)).contact_due == 690
    await advance(play, cid, 690)
    assert (await condition(play, cid)).due is None
    assert (await condition(play, cid)).contact_seconds == 0
    assert history(play._load(await play.store.read(cid)).resources) == ()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rules_migration_can_introduce_forms_without_resetting_live_debt(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await HarmfulPhysiologyService(play).execute(cid, observation(), principal_id="gm")
    original = gurps_draft(trait())
    rule = TransformationRule(
        id="body",
        actor_id="a",
        kind="body-modification",
        source_ref="B294",
        target=original,
        target_body_id="a:other",
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body") for p in original.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
    )
    target = PlayService(
        play.store,
        ActionEngine(
            play.engine.reviewer,
            play.engine.resources,
            play.engine.rules.model_copy(
                update={
                    "transformations": TransformationRules(
                        id="forms", version=1, transformations=(rule,)
                    )
                }
            ),
        ),
        rng=RecordedDice(()),
    )
    await MigrationService(play, target).apply(
        cid,
        ApplyMigration(
            id="migration",
            actor_id="gm",
            expected_revision=1,
            expected_from_digest=play.engine.digest,
            reason="add forms",
        ),
        principal_id="gm",
    )
    assert (await condition(target, cid)).due == 60
    target.rng = RecordedDice((2,))
    await advance(target, cid, 60)
    assert len(history(target._load(await target.store.read(cid)).resources)) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_owed_major_wound_interrupts_completion_and_commits_knockout(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_form(tmp_path, backend)
    await HarmfulPhysiologyService(play).execute(cid, observation(), principal_id="gm")
    await clock(play, cid, 50)
    treatment = await start(play, cid)
    await clock(play, cid, 60, identifier="ready")
    play.rng = RecordedDice((6, 6, 6, 6))  # 6 injury; HT roll 18 knocks out native HP 10.
    result = await transform(
        play, cid, "resolve", proposal_id=treatment.proposal_id, resolution="complete"
    )
    assert result.status == "interrupted"
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert (hp.maximum, hp.current) == (10, 4)
    assert hp.injury is not None and hp.injury.unconscious and hp.injury.prone
    assert len(history(state.resources)) == 1 and not state.advancement
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fatal_schedule_automatically_returns_dead_native_body_atomically(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_form(tmp_path, backend, source="dependency")
    active = await start(play, cid)
    await clock(play, cid, 10)
    await transform(play, cid, "resolve", proposal_id=active.proposal_id, resolution="complete")
    revision = (await play.store.read(cid))["revision"]
    await HarmfulPhysiologyService(play).execute(
        cid, observation(revision, source="dependency", present=False), principal_id="gm"
    )
    play.rng = RecordedDice((6, 6, 6))
    await advance(play, cid, 2470)
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert (hp.maximum, hp.current) == (10, -10)
    assert hp.injury is not None and hp.injury.dead
    assert state.transformations.records[-1].status == "reverted"
    assert len(history(state.resources)) == 40
    assert conditions(state.resources)[0].retired
    assert all(entry.revision <= state.revision for entry in state.advancement)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_multitick_clock_refreshes_frequency_after_automatic_knockout_return(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_form(
        tmp_path,
        backend,
        native_st=20,
        target_st=10,
        frequency="five-minutes",
        target_frequency="minute",
    )
    await HarmfulPhysiologyService(play).execute(cid, observation(), principal_id="gm")
    active = await start(play, cid)
    await clock(play, cid, 10)
    await transform(play, cid, "resolve", proposal_id=active.proposal_id, resolution="complete")
    assert (await condition(play, cid)).due == 70
    play.rng = RecordedDice((6, 6, 6, 6, 2))
    await advance(play, cid, 370)
    state = play._load(await play.store.read(cid))
    injuries = tuple(i.outcome for i in history(state.resources))
    assert [(i.hp_before, i.hp_after) for i in injuries] == [(10, 4), (8, 6)]
    assert state.resources.game_time == 370
    assert state.transformations.records[-1].status == "reverted"
    assert (await condition(play, cid)).due == 670
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.unconscious
    assert (hp.maximum, hp.current) == (20, 6)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_forced_return_and_interval_alias_race_spend_one_interval(
    tmp_path: Path, backend: str
) -> None:
    import asyncio

    from wayfarer.engine.simulation.traits.harmful_physiology_state import SettlePhysiology
    from wayfarer.errors import ConflictError

    cid, play = await prepare_form(tmp_path, backend)
    active = await start(play, cid)
    await clock(play, cid, 10)
    active = await transform(
        play, cid, "resolve", proposal_id=active.proposal_id, resolution="complete"
    )
    revision = (await play.store.read(cid))["revision"]
    await HarmfulPhysiologyService(play).execute(cid, observation(revision), principal_id="gm")
    await clock(play, cid, 70, identifier="owed")
    revision = (await play.store.read(cid))["revision"]
    first = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((2,)))
    second = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((2,)))
    force = dict(
        operation="resolve",
        id="force",
        actor_id="a",
        expected_revision=revision,
        proposal_id=active.proposal_id,
        resolution="force",
        influence="dispel magic",
    )
    settle = SettlePhysiology(
        id="alias",
        actor_id="a",
        expected_revision=revision,
        interval_id=(await condition(play, cid)).interval_id,
    )
    results = await asyncio.gather(
        TransformationService(first).execute(cid, force, principal_id="gm"),
        HarmfulPhysiologyService(second).execute(cid, settle, principal_id="gm"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    if isinstance(results[0], ConflictError):
        force["expected_revision"] = revision + 1
        await TransformationService(first).execute(cid, force, principal_id="gm")
    restart = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    result = await TransformationService(restart).execute(cid, force, principal_id="gm")
    assert result.status == "reverted"
    state = restart._load(await restart.store.read(cid))
    assert len(history(state.resources)) == 1
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 9
    assert (await condition(restart, cid)).due == 130
    with pytest.raises(ConflictError):
        await HarmfulPhysiologyService(restart).execute(
            cid,
            settle.model_copy(update={"id": "second-alias", "expected_revision": state.revision}),
            principal_id="gm",
        )
    assert await restart.store.read(cid) == await restart.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_transform_harm_seeded_reexecution_and_failed_commit_rollback(
    tmp_path: Path, backend: str
) -> None:
    import secrets

    from support.runtime import played
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.orchestration.pipeline import submit
    from wayfarer.orchestration.transformations import ResolveTransformation
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare_form(tmp_path, backend)
    play.rng = secrets
    initial = await play.store.read(cid)
    active = await start(play, cid)
    await clock(play, cid, 10)
    active = await transform(
        play, cid, "resolve", proposal_id=active.proposal_id, resolution="complete"
    )
    revision = (await play.store.read(cid))["revision"]
    await HarmfulPhysiologyService(play).execute(cid, observation(revision), principal_id="gm")
    await clock(play, cid, 70, identifier="owed")
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    records = await played(play.store, cid)
    command = ResolveTransformation(
        id="force",
        actor_id="a",
        expected_revision=before["revision"],
        proposal_id=active.proposal_id,
        resolution="force",
        influence="dispel magic",
    )
    failing = FailingCommitPlay(play.store, play.engine, rng=secrets)
    state = failing._load(before)
    with pytest.raises(RuntimeError, match="crash after candidate checkpoint"):
        await submit(
            failing,
            cid,
            TransformationService(failing).plan(
                cid,
                failing,
                state,
                command,
                command.model_dump(mode="json"),
                principal_id="gm",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.stream(cid) == stream and await played(play.store, cid) == records
    await TransformationService(play).execute(
        cid, command.model_dump(mode="json"), principal_id="gm"
    )
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid)


@pytest.mark.parametrize("retry", [False, True])
async def test_forced_due_settlement_rechecks_current_director_seat(
    tmp_path: Path, retry: bool
) -> None:
    from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm

    from wayfarer.errors import ValidationError

    cid, original = await prepare_form(tmp_path)
    active = await start(original, cid)
    await clock(original, cid, 10)
    active = await transform(
        original, cid, "resolve", proposal_id=active.proposal_id, resolution="complete"
    )
    revision = (await original.store.read(cid))["revision"]
    await HarmfulPhysiologyService(original).execute(cid, observation(revision), principal_id="gm")
    await clock(original, cid, 70, identifier="owed")
    revision = (await original.store.read(cid))["revision"]
    command = dict(
        operation="resolve",
        id="force",
        actor_id="a",
        expected_revision=revision,
        proposal_id=active.proposal_id,
        resolution="force",
        influence="dispel magic",
    )
    original.rng = RecordedDice((2,))
    if retry:
        await TransformationService(original).execute(cid, command, principal_id="gm")
        revision += 1
    if not retry:
        command["expected_revision"] = revision + 1
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, revision)
    with pytest.raises(ValidationError, match="director authority"):
        await TransformationService(play).execute(cid, command, principal_id="gm")
    state = original._load(await original.store.read(cid))
    assert state.revision == revision + 1
    assert len(history(state.resources)) == int(retry)
    assert state.transformations.records[-1].status == ("reverted" if retry else "active")


@pytest.mark.parametrize("prefix", ["harmful-physiology:", "physiology:"])
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authored_genesis_cannot_inject_private_physiology_receipts(
    tmp_path: Path, prefix: str, backend: str
) -> None:
    from wayfarer.errors import NotFoundError, ValidationError

    cid, play = await prepare(tmp_path, backend)
    await HarmfulPhysiologyService(play).execute(cid, observation(), principal_id="gm")
    play.rng = RecordedDice((2,))
    await advance(play, cid, 60)
    recorded = await play.store.read(cid)
    state = play._load(recorded)
    copied = next(event for event in state.resources.events if event.id.startswith(prefix))
    initial = campaign(play.engine)
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        await seed_play(
            play,
            initial,
            world(),
            ResourceState(
                owners=(Owner(actor_id="a", capacity=100),),
                pools=(
                    Pool(
                        id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)
                    ),
                ),
                events=(copied,),
            ),
            (ActorSetup(actor_id="a", proposal=state.actors[0].proposal),),
        )
    with pytest.raises(NotFoundError):
        await play.store.read(initial["id"])
    assert await play.store.read(cid) == recorded == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("treatment", [0, 60])
async def test_fatal_debt_interrupts_nonform_approval_or_completion(
    tmp_path: Path, backend: str, treatment: int
) -> None:
    original = gurps_draft(trait("dependency"))
    rule = TransformationRule(
        id="body",
        actor_id="a",
        kind="body-modification",
        source_ref="B294",
        target=gurps_draft(st_level=11),
        target_body_id="a:modified",
        treatment_seconds=treatment,
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body") for p in original.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
    )
    cid, play = await prepare(
        tmp_path,
        backend,
        source="dependency",
        transformations=TransformationRules(id="forms", version=1, transformations=(rule,)),
    )
    await HarmfulPhysiologyService(play).execute(
        cid, observation(source="dependency", present=False), principal_id="gm"
    )
    await advance(play, cid, 1140)
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval is not None
    proposed = await transform(
        play,
        cid,
        "propose",
        rule_id="body",
        expected_build_revision=state.actors[0].approval.build_revision,
    )
    if treatment:
        await transform(play, cid, "approve", proposal_id=proposed.proposal_id, reason="treatment")
    await clock(play, cid, 1200)
    play.rng = RecordedDice((6, 6, 6))
    if treatment:
        result = await transform(
            play, cid, "resolve", proposal_id=proposed.proposal_id, resolution="complete"
        )
    else:
        result = await transform(
            play, cid, "approve", proposal_id=proposed.proposal_id, reason="instant change"
        )
    assert result.status == "interrupted"
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.dead
    assert (hp.maximum, hp.current) == (10, -10)
    assert state.actors[0].proposal.draft == original
    assert len(history(state.resources)) == 20 and state.advancement == ()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("replacement", [None, "hour"])
async def test_calendar_history_survives_dormant_and_replaced_bodies(
    tmp_path: Path,
    replacement: str | None,
) -> None:
    from wayfarer.engine.simulation.traits.harmful_physiology_state import DeclarePhysiologyCalendar
    from wayfarer.engine.simulation.traits.physiology_calendar import PhysiologyCalendar
    from wayfarer.errors import ConflictError

    original = gurps_draft(trait("dependency", "month"))
    rule = TransformationRule(
        id="body",
        actor_id="a",
        kind="supernatural-affliction",
        source_ref="B296",
        target=gurps_draft(*((trait("dependency", replacement),) if replacement else ())),
        target_body_id="a:temporary",
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body") for p in original.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
        voluntary=False,
        expires_after_seconds=10,
        curable=True,
    )
    cid, play = await prepare(
        tmp_path,
        source="dependency",
        frequency="month",
        transformations=TransformationRules(id="forms", version=1, transformations=(rule,)),
    )
    service = HarmfulPhysiologyService(play)
    cal = PhysiologyCalendar(month_boundaries=(0, 31 * 86400, 59 * 86400, 90 * 86400))
    await service.execute(
        cid,
        DeclarePhysiologyCalendar(id="calendar", actor_id="gm", expected_revision=0, calendar=cal),
        principal_id="gm",
    )
    await service.execute(cid, observation(1, source="dependency"), principal_id="gm")
    active = await start(play, cid)
    assert (await condition(play, cid)).dormant == (replacement is None)
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="extend"):
        await service.execute(
            cid,
            DeclarePhysiologyCalendar(
                id="rewrite",
                actor_id="gm",
                expected_revision=before["revision"],
                calendar=cal.model_copy(
                    update={"month_boundaries": (0, 30 * 86400, 59 * 86400, 90 * 86400)}
                ),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    await service.execute(
        cid,
        DeclarePhysiologyCalendar(
            id="extend",
            actor_id="gm",
            expected_revision=before["revision"],
            calendar=cal.model_copy(
                update={"month_boundaries": cal.month_boundaries + (120 * 86400,)}
            ),
        ),
        principal_id="gm",
    )
    await clock(play, cid, 10)
    await transform(play, cid, "resolve", proposal_id=active.proposal_id, resolution="expire")
    assert (
        await condition(play, cid)
    ).due == 32 * 86400  # Original Jan 1 dose, no invented new dose.


async def test_lower_contact_requirement_consumes_existing_credit_without_past_deadline(
    tmp_path: Path,
) -> None:
    original = gurps_draft(trait("dependency", "day"))
    rule = TransformationRule(
        id="body",
        actor_id="a",
        kind="supernatural-affliction",
        source_ref="B296",
        target=gurps_draft(trait("dependency", "hour")),
        target_body_id="a:temporary",
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body") for p in original.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
        voluntary=False,
        expires_after_seconds=10000,
        curable=True,
    )
    cid, play = await prepare(
        tmp_path,
        source="dependency",
        frequency="day",
        transformations=TransformationRules(id="forms", version=1, transformations=(rule,)),
    )
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(source="dependency", contact=True), principal_id="gm")
    await clock(play, cid, 900)
    await service.execute(
        cid,
        observation(2, identifier="leave", source="dependency", contact=True, present=False),
        principal_id="gm",
    )
    assert (await condition(play, cid)).contact_seconds == 900
    await start(play, cid)
    # The observed 900 seconds exceed the new hourly requirement of 600.
    # They satisfy it once, with grace measured from the last actual contact.
    assert (await condition(play, cid)).contact_seconds == 0
    assert (await condition(play, cid)).due == 5100
    await advance(play, cid, 1000)
    revision = (await play.store.read(cid))["revision"]
    await service.execute(
        cid,
        observation(revision, identifier="return", source="dependency", contact=True),
        principal_id="gm",
    )
    assert (await condition(play, cid)).contact_due == 1600
    await advance(play, cid, 1600)
    assert (await condition(play, cid)).due is None
