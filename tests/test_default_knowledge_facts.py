"""B173 explicit GM policy adoption and independent societal knowledge facts."""

import json
import secrets
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import played
from test_default_roll_staff_host import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.default_knowledge import (
    AdoptDefaultKnowledge,
    DeclareDefaultKnowledge,
    DefaultKnowledge,
    adopted,
    identifier,
    known_skills,
    observations,
)
from wayfarer.engine.simulation.campaign.default_knowledge_projection import project
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.views import campaign_view
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_gm_adopts_unknown_then_grants_and_revokes_without_purchasing_training(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, True)
    state = play._load(await play.store.read(cid))
    actor = next(a for a in state.actors if a.actor_id == "a")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=cid, actor_id="a"
    )
    assert any(v.target == "skill:staff" and v.value == 5 for v in build.sheet.values)
    assert (
        not adopted(state, "a")
        and project(play.engine.reviewer.compiler, state, "a", build) == build
    )
    pools, time = state.resources.pools, state.resources.game_time
    play.rng = secrets
    service = DefaultKnowledgeService(play)
    adoption = AdoptDefaultKnowledge(
        id="adopt",
        actor_id="gm",
        expected_revision=state.revision,
        performer_id="a",
        reason="GM adopts B173 default eligibility; no society knowledge is yet established",
    )
    receipt = await service.execute(cid, adoption, principal_id="gm")
    assert receipt.adoption and receipt.skill_ids == ()
    state = play._load(await play.store.read(cid))
    assert adopted(state, "a") and not known_skills(state, "a")
    assert "skill:staff" not in {
        v.target for v in project(play.engine.reviewer.compiler, state, "a", build).sheet.values
    }
    grant = DeclareDefaultKnowledge(
        id="staff-known",
        actor_id="gm",
        expected_revision=state.revision,
        performer_id="a",
        skill_ids=("skill:staff",),
        society_known=True,
        reason="The GM establishes that ordinary staff use is known in this actor's society",
    )
    granted = await service.execute(cid, grant, principal_id="gm")
    assert granted.skill_ids == ("skill:staff",) and not granted.adoption
    state = play._load(await play.store.read(cid))
    assert known_skills(state, "a") == frozenset({"skill:staff"})
    assert (
        next(
            v.value
            for v in project(play.engine.reviewer.compiler, state, "a", build).sheet.values
            if v.target == "skill:staff"
        )
        == 5
    )
    revoked = grant.model_copy(
        update={"id": "staff-unknown", "expected_revision": state.revision, "society_known": False}
    )
    await service.execute(cid, revoked, principal_id="gm")
    final = await play.store.read(cid)
    state = play._load(final)
    assert not known_skills(state, "a") and state.resources.pools == pools
    assert state.resources.game_time == time
    assert next(a for a in state.actors if a.actor_id == "a") == actor
    assert await service.execute(cid, adoption, principal_id="gm") == receipt
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    for member in state.members:
        if member.role != "gm":
            assert "skill-default-knowledge:" not in json.dumps(
                campaign_view(state, member, play.engine.rules.combat)
            )
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_explicit_actor_baseline_does_not_change_learned_skills_or_other_actors(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, True, trained=True)
    state = play._load(await play.store.read(cid))
    actor = next(a for a in state.actors if a.actor_id == "a")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=cid, actor_id="a"
    )
    assert next(v.value for v in build.sheet.values if v.target == "skill:staff") == 9
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="adopt",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            known_skill_ids=("skill:two-handed-sword",),
            reason="Explicit GM society baseline",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    projected = project(play.engine.reviewer.compiler, state, "a", build)
    assert next(v.value for v in projected.sheet.values if v.target == "skill:staff") == 9
    assert projected.revision == build.revision and projected.purchases == build.purchases
    assert known_skills(state, "a") == frozenset({"skill:two-handed-sword"})
    assert not adopted(state, "b")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fact_authority_current_actor_concrete_defaults_and_explicit_adoption(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, True)
    state = play._load(await play.store.read(cid))
    command = AdoptDefaultKnowledge(
        id="adopt",
        actor_id="gm",
        expected_revision=state.revision,
        performer_id="a",
        reason="GM baseline",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await DefaultKnowledgeService(play).execute(cid, command, principal_id="a")
    for ids in (("equipment:leather-armor",), ("skill:missing",), ("skill:staff", "skill:staff")):
        with pytest.raises(ValidationError):
            await DefaultKnowledgeService(play).execute(
                cid, command.model_copy(update={"known_skill_ids": ids}), principal_id="gm"
            )
    with pytest.raises(ValidationError, match="adoption"):
        await DefaultKnowledgeService(play).execute(
            cid,
            DeclareDefaultKnowledge(
                id="not-adopted",
                actor_id="gm",
                expected_revision=state.revision,
                performer_id="a",
                skill_ids=("skill:staff",),
                society_known=True,
                reason="No implicit adoption",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("bad", [True, False, 0, 2, 1.0, "1"])
def test_observation_generation_is_exact_integer(bad: object) -> None:
    with pytest.raises(SchemaError):
        DefaultKnowledge.model_validate(
            dict(
                command_id="c",
                performer_id="a",
                principal_id="gm",
                configuration_digest="0" * 64,
                skill_ids=("skill:staff",),
                society_known=True,
                reason="GM fact",
                generation=bad,
            )
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_private_facts_fail_closed_on_duplicate_wrong_target_or_genesis_injection(
    tmp_path: Path, backend: str
) -> None:
    from test_actions import campaign, world

    cid, play, _ = await prepare(tmp_path, backend, True)
    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="adopt",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            reason="Explicit adoption",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id == identifier("adopt"))
    for events in (
        (event, event),
        (event.model_copy(update={"target_id": "b"}),),
        (event.model_copy(update={"id": identifier("forged")}),),
    ):
        with pytest.raises(ValidationError, match="identity"):
            observations(state.resources.model_copy(update={"events": events}))
    with pytest.raises(ValidationError, match="execution receipts"):
        play.initial_state(campaign(play.engine), world(), ResourceState(events=(event,)), ())


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_configuration_migration_retains_adoption_but_requires_current_skill_facts(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, True)
    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="adopt",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            known_skill_ids=("skill:staff",),
            reason="Explicit GM baseline under this pinned configuration",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    actor = next(a for a in state.actors if a.actor_id == "a")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=cid, actor_id="a"
    )
    migrated = state.model_copy(update={"configuration_digest": "next-explicit-configuration"})
    assert adopted(migrated, "a") and not known_skills(migrated, "a")
    assert "skill:staff" not in {
        v.target for v in project(play.engine.reviewer.compiler, migrated, "a", build).sheet.values
    }
