"""Approved purchase facts survive unrelated live skill recomputation."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign, world
from test_background_skill_admission import skill_package, skill_proposal
from test_enchanting_noncombat_nighttime import configured as magic_fixture
from test_unusual_background_admission import configured, decision

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.skills import SkillError
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.randomness import RNG_ALGORITHM
from wayfarer.engine.rules.types.background_admission import UNUSUAL_BACKGROUND_ID
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import (
    ActionRules,
    ActorSetup,
    CheckRule,
    Inspect,
    PlayState,
    Social,
)
from wayfarer.engine.simulation.campaign.activities import LongTaskRule
from wayfarer.engine.simulation.campaign.adjudication import RulingAlternative, RulingPolicy
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.noncombat_generations import skill_prerequisite_context
from wayfarer.orchestration.play import ApproveCharacter, PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.task_records import (
    BeginTaskCheck,
    BeginTaskWork,
    BindLongTask,
    ChooseSecretTaskCheck,
    ChooseTaskCheck,
    PrepareSecretTaskCheck,
    SecretTaskPending,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandRecord, payload_digest
from wayfarer.persistence.replay import verify_commands


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def reexecute(initial: Campaign, play: PlayService, path: Path) -> None:
    final, checks = await verify_commands(
        initial,
        await played(play.store, initial["id"]),
        await play.store.stream(initial["id"]),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, path),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(initial["id"]) == await play.store.replay(initial["id"])


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_lawful_unenrolled_mage_inspects_with_real_equipment_and_task_modifiers(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await magic_fixture(
        tmp_path,
        backend,
        enroll=False,
        retain_source_spells=True,
        equipment_modifier=1,
        check_modifier=-2,
    )
    cid = initial["id"]
    before = play._load(initial)
    assert before.resources.enchantment_projects == ()
    command = Inspect(id="mage-inspect", actor_id="a", expected_revision=0, target_id="forge")
    # The historical computation loses Magery while recomputing every spell,
    # even though the character compiler approved the complete purchase chain.
    with pytest.raises(SkillError, match="prerequisite"):
        play.engine.prepare_task_check(before, command)
    assert (await play.preview(cid, command, principal_id="a")).status == "feasible"
    result, duplicate = await asyncio.gather(
        *(play.execute(cid, command, principal_id="a") for _ in range(2))
    )
    assert result == duplicate and result.check is not None
    assert result.check.base_target == 18 and result.check.effective_target == 16
    assert result.check.total == 9 and result.check.outcome.succeeded
    assert result.revealed_fact_ids == ("night-clue",)
    after = play._load(await play.store.read(cid))
    assert after.resources.game_time == 10 and after.resources.enchantment_projects == ()
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10
    assert "night-clue" in {f.id for f in after.world.perspective("a").facts}
    stored = await play.store.command_input(cid, command.id)
    assert stored is not None and stored.text is not None
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict) and payload["skill_prerequisite_context_generation"] == 1
    restarted = build_play(tmp_path / "restart", play.engine, store=play.store)
    assert await restarted.execute(cid, command, principal_id="a") == result
    await reexecute(initial, play, tmp_path / "reexecute")


async def background_fixture(
    path: Path,
    backend: str,
    *,
    trained: bool,
    paid: bool = True,
    allowed: bool = True,
    deferred: bool = False,
    automatic_ruling: bool = False,
    authored_modifier: int = -1,
    ruling_modifier: int = 2,
) -> tuple[Campaign, PlayService, PlayState]:
    package, reviewer = configured(
        decision(10, allowed=allowed).model_copy(
            update={"benefits": ("skill:observation", "skill:diplomacy")}
        ),
        base=skill_package(),
    )
    compiler = reviewer.compiler
    resources = ResourceEngine(
        world(), RulesCatalog((package,)), compiler.rules, compiler.policy, ()
    )
    authored: tuple[tuple[Literal["inspect", "social"], str, str, str], ...] = (
        ("inspect", "chest", "skill:observation", "clue"),
        ("social", "b", "skill:diplomacy", "promise"),
    )
    reducer = ActionEngine(
        reviewer,
        resources,
        ActionRules(
            id="background-checks",
            version=1,
            party=PartyRules(id="background-party", version=1) if deferred else None,
            scenes=SceneRules(
                id="background-scenes",
                version=1,
                scenes=(Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),),
            )
            if deferred
            else None,
            adjudication=RulingPolicy(
                id="background-rulings",
                version=1,
                automatic=automatic_ruling,
                automatic_maximum=2,
                alternatives=(
                    RulingAlternative(
                        id="measured",
                        label="Measured appeal",
                        check_rule_id="social",
                        modifier=ruling_modifier,
                    ),
                ),
            )
            if deferred
            else None,
            checks=tuple(
                CheckRule(
                    id=kind,
                    action=kind,
                    target_id=target,
                    definition_id=skill,
                    package_id=package.id,
                    package_version=package.version,
                    duration=3,
                    modifier=authored_modifier,
                    reveal_fact_ids=(fact,),
                )
                for kind, target, skill, fact in authored
            ),
        ),
    )
    purchases = (
        *((Purchase(definition_id=UNUSUAL_BACKGROUND_ID),) if paid else ()),
        *(
            (
                Purchase(definition_id="skill:observation", amount=1),
                Purchase(definition_id="skill:diplomacy", amount=4),
            )
            if trained
            else ()
        ),
    )
    proposal = skill_proposal(*purchases)
    play = build_play(path, reducer, backend=backend, seeds=lambda: f"{1:064x}")
    initial = campaign(reducer)
    genesis = play.initial_state(
        initial,
        world(),
        ResourceState(
            owners=tuple(
                Owner(actor_id=a, capacity=100) for a in ("a", "b") if a == "a" or deferred
            )
        ),
        (ActorSetup(actor_id="a", proposal=proposal, aware_of=("chest", "b")),)
        + (
            (ActorSetup(actor_id="b", proposal=skill_proposal(), aware_of=("a",)),)
            if deferred
            else ()
        ),
    )
    initial["play_json"] = genesis.model_dump_json()
    await seed_campaign(play.store, initial)
    if paid:
        await play.approve(
            initial["id"],
            ApproveCharacter(
                id="approve-background",
                actor_id="gm",
                target_actor_id="a",
                expected_revision=0,
                reason="Approve the purchased exceptional skill access and its surcharge",
            ),
            principal_id="gm",
        )
    return initial, play, genesis


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trained", [False, True])
@pytest.mark.parametrize("kind,fact", [("inspect", "clue"), ("social", "promise")])
async def test_paid_background_defaults_and_trained_skills_reach_live_public_checks(
    tmp_path: Path, backend: str, trained: bool, kind: Literal["inspect", "social"], fact: str
) -> None:
    initial, play, _ = await background_fixture(tmp_path, backend, trained=trained)
    cid = initial["id"]
    if not trained:
        await DefaultKnowledgeService(play).execute(
            cid,
            AdoptDefaultKnowledge(
                id="society-background-independent",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                performer_id="a",
                known_skill_ids=("skill:observation" if kind == "inspect" else "skill:diplomacy",),
                reason="The GM establishes familiarity independently of paid exceptional access",
            ),
            principal_id="gm",
        )
    command = (Inspect if kind == "inspect" else Social)(
        id="background-check",
        actor_id="a",
        expected_revision=await revision(play, cid),
        target_id="chest" if kind == "inspect" else "b",
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await play.execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await play.execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before
    result = await play.execute(cid, command, principal_id="a")
    assert result.check is not None
    expected = (9 if kind == "inspect" else 10) if trained else (5 if kind == "inspect" else 4)
    assert result.check.base_target == expected and result.check.effective_target == expected - 1
    succeeded = trained and kind == "social"
    assert result.check.total == 9 and result.check.outcome.succeeded == succeeded
    after = play._load(await play.store.read(cid))
    assert after.resources.game_time == 3
    assert (fact in {f.id for f in after.world.perspective("a").facts}) == succeeded
    await reexecute(initial, play, tmp_path / "reexecute")


async def test_forbidden_background_cannot_become_an_authored_check(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="implemented catalog skill"):
        await background_fixture(tmp_path, "sqlite", trained=False, paid=False, allowed=False)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unpaid_background_does_not_gain_a_default_from_live_context(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await background_fixture(tmp_path, backend, trained=False, paid=False)
    cid = initial["id"]
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="society-does-not-pay-background",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            performer_id="a",
            known_skill_ids=("skill:observation",),
            reason="Real cultural knowledge does not supply the required background payment",
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    result = await play.execute(
        cid,
        Inspect(
            id="unpaid",
            actor_id="a",
            expected_revision=await revision(play, cid),
            target_id="chest",
        ),
        principal_id="a",
    )
    assert result.status == "rejected" and result.code == "check.skill_required"
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_mage_ordinary_and_secret_staged_checks_keep_prepared_context_and_actual_discovery(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    initial, play, _ = await magic_fixture(
        tmp_path, backend, enroll=False, retain_source_spells=True
    )
    cid = initial["id"]
    begun = await TaskService(play).execute(
        cid,
        (PrepareSecretTaskCheck if secret else BeginTaskCheck)(
            id="prepare-observation",
            actor_id="a",
            expected_revision=0,
            check_id="night-observation",
        ),
        principal_id="gm" if secret else "a",
    )
    prepared = play._load(await play.store.read(cid))
    assert prepared.resources.game_time == 10 and begun.status == "pending"
    pending = snapshot(prepared).pending
    assert pending is not None and begun.pending_id is not None
    command: ChooseSecretTaskCheck | ChooseTaskCheck
    if secret:
        assert isinstance(pending, SecretTaskPending) and pending.skill_context_generation == 1
        command = ChooseSecretTaskCheck(
            id="select-observation",
            actor_id="a",
            expected_revision=prepared.revision,
            pending_id=begun.pending_id,
            choice="resolve",
        )
    else:
        assert begun.check is not None and begun.check.effective_target == 17
        command = ChooseTaskCheck(
            id="select-observation",
            actor_id="a",
            expected_revision=prepared.revision,
            pending_id=begun.pending_id,
            kind="accept-check",
        )
    result = await TaskService(play).execute(cid, command, principal_id="gm" if secret else "a")
    assert result.check is not None and result.check.effective_target == 17
    assert result.action is not None and result.action.revealed_fact_ids == ("night-clue",)
    after = play._load(await play.store.read(cid))
    assert after.resources.game_time == 10 and snapshot(after).pending is None
    assert "night-clue" in {f.id for f in after.world.perspective("a").facts}
    restarted = build_play(tmp_path / "restart", play.engine, store=play.store)
    assert (
        await TaskService(restarted).execute(cid, command, principal_id="gm" if secret else "a")
        == result
    )
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_mage_long_task_uses_approved_context_for_actual_selected_work(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await magic_fixture(
        tmp_path, backend, enroll=False, retain_source_spells=True
    )
    cid = initial["id"]
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind-investigation",
            actor_id="a",
            expected_revision=0,
            location_id="forge",
            rule=LongTaskRule(
                id="investigation", target_id="skill:observation", required_man_hours=1
            ),
        ),
        principal_id="gm",
    )
    begun = await TaskService(play).execute(
        cid,
        BeginTaskWork(
            id="work-investigation",
            actor_id="a",
            expected_revision=await revision(play, cid),
            task_id="investigation",
            seconds=3600,
        ),
        principal_id="a",
    )
    assert begun.check is not None and begun.check.effective_target == 17
    assert begun.pending_id is not None
    result = await TaskService(play).execute(
        cid,
        ChooseTaskCheck(
            id="accept-investigation",
            actor_id="a",
            expected_revision=await revision(play, cid),
            pending_id=begun.pending_id,
            kind="accept-check",
        ),
        principal_id="a",
    )
    assert result.activity is not None and result.activity.progress == 1
    assert play._load(await play.store.read(cid)).resources.game_time == 3600
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_background_secret_social_selection_uses_prepared_admission(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await background_fixture(tmp_path, backend, trained=True)
    cid = initial["id"]
    prepared = await TaskService(play).execute(
        cid,
        PrepareSecretTaskCheck(
            id="prepare-background-social",
            actor_id="a",
            expected_revision=await revision(play, cid),
            check_id="social",
        ),
        principal_id="gm",
    )
    assert prepared.pending_id is not None
    result = await TaskService(play).execute(
        cid,
        ChooseSecretTaskCheck(
            id="select-background-social",
            actor_id="a",
            expected_revision=await revision(play, cid),
            pending_id=prepared.pending_id,
            choice="resolve",
        ),
        principal_id="gm",
    )
    assert result.check is not None and result.check.effective_target == result.check.total == 9
    assert result.action is not None and result.action.revealed_fact_ids == ("promise",)
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legacy_secret_pending_retains_its_prepared_generation_under_fresh_selection(
    tmp_path: Path, backend: str
) -> None:
    # A historical-envelope diagnostic uses the same actual public producer,
    # with the new context metadata absent. The build requires no such facts.
    initial, play, _ = await magic_fixture(tmp_path, backend, enroll=False)
    cid = initial["id"]
    command = PrepareSecretTaskCheck(
        id="legacy-secret", actor_id="a", expected_revision=0, check_id="night-observation"
    )
    encoded = json.dumps(
        {
            "operation": "task-host",
            "principal_id": "gm",
            "command": command.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    historical = CommandRecord(
        campaign_id=cid,
        command_id=command.id,
        actor_id="gm",
        expected_revision=0,
        resulting_revision=1,
        payload_hash=payload_digest({"input": encoded}),
        rules_version="1",
        event={"action": "resource", "outcome": "historical envelope diagnostic"},
        state_after=initial,
        entropy_seed=f"{1:064x}",
        rng_algorithm=RNG_ALGORITHM,
        recorded_at_us=1_700_000_000_000_000,
        command_input=encoded,
    )
    with replay_inputs(historical):
        opened = await TaskService(play).execute(cid, command, principal_id="gm")
    before = play._load(await play.store.read(cid))
    pending = snapshot(before).pending
    assert isinstance(pending, SecretTaskPending) and pending.skill_context_generation is None
    assert "skill_context_generation" not in pending.model_dump()
    assert opened.pending_id is not None
    selection = ChooseSecretTaskCheck(
        id="fresh-legacy-selection",
        actor_id="a",
        expected_revision=before.revision,
        pending_id=opened.pending_id,
        choice="resolve",
    )
    result = await TaskService(play).execute(cid, selection, principal_id="gm")
    assert result.check is not None and result.check.effective_target == 17
    record = await play.store.command_input(cid, selection.id)
    assert record is not None and record.text is not None
    payload = replay_payload(record.text)
    assert isinstance(payload, dict) and payload["skill_prerequisite_context_generation"] == 1
    assert await TaskService(play).execute(cid, command, principal_id="gm") == opened
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("invalid", [True, False, 2, "1", None])
async def test_invalid_recorded_skill_context_generation_refuses(
    tmp_path: Path, invalid: object
) -> None:
    initial, play, _ = await magic_fixture(tmp_path, enroll=False, retain_source_spells=True)
    cid = initial["id"]
    command = Inspect(id="context-check", actor_id="a", expected_revision=0, target_id="forge")
    await play.execute(cid, command, principal_id="a")
    record = next(r for r in await played(play.store, cid) if r.command_id == command.id)
    assert record.command_input is not None
    payload = replay_payload(record.command_input)
    assert isinstance(payload, dict)
    payload["skill_prerequisite_context_generation"] = invalid
    encoded = json.dumps(payload, sort_keys=True)
    with replay_inputs(
        replace(record, command_input=encoded, payload_hash=payload_digest({"input": encoded}))
    ):
        with pytest.raises(ValidationError, match="skill-prerequisite-context generation"):
            await skill_prerequisite_context(play, cid, command.id)


async def test_private_secret_context_is_strict_and_cannot_be_supplied_as_a_command(
    tmp_path: Path,
) -> None:
    initial, play, _ = await magic_fixture(tmp_path, enroll=False, retain_source_spells=True)
    command = PrepareSecretTaskCheck(
        id="strict-secret", actor_id="a", expected_revision=0, check_id="night-observation"
    )
    await TaskService(play).execute(initial["id"], command, principal_id="gm")
    pending = snapshot(play._load(await play.store.read(initial["id"]))).pending
    assert isinstance(pending, SecretTaskPending)
    for invalid in (True, False, 2, "1"):
        with pytest.raises(SchemaError, match="skill-context generation"):
            SecretTaskPending.model_validate(
                pending.model_dump() | {"skill_context_generation": invalid}
            )
    with pytest.raises(SchemaError, match="Extra inputs"):
        PrepareSecretTaskCheck.model_validate(
            command.model_dump() | {"skill_context_generation": 1}
        )


async def test_power_approval_replay_rechecks_gm_principal(tmp_path: Path) -> None:
    initial, play, _ = await background_fixture(tmp_path, "sqlite", trained=False)
    record = next(
        r for r in await played(play.store, initial["id"]) if r.command_id == "approve-background"
    )
    isolated = build_play(tmp_path / "isolated", play.engine)
    await seed_campaign(isolated.store, initial)
    with pytest.raises(ValidationError, match="GM authority"):
        await execute_recorded(isolated, replace(record, actor_id="a"))
    assert await isolated.store.read(initial["id"]) == initial
