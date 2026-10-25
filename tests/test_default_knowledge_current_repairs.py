"""B173 genuine specialized GM proof and explicit current observation chronology."""

import secrets
from pathlib import Path

import pytest
from support.armoury_defaults import TARGETS, declare, fixture, revision, select, wait
from test_default_knowledge_hosts import adopt
from test_issue_818_armoury_acceptance import begin

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.default_knowledge import (
    DeclareDefaultKnowledge,
    adopted,
    observations,
)
from wayfarer.engine.simulation.campaign.default_knowledge_sources import current_skills
from wayfarer.engine.simulation.equipment.repair_defaults import (
    TRAINING_PREFIX,
    ArmouryTraining,
    DeclareArmouryTraining,
    selected,
)
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import ValidationError
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService

ARMOR = "skill:armoury-body-armor"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1, 2])
async def test_specialized_gm_knowledge_is_sufficient_without_generic_adoption(
    tmp_path: Path, backend: str, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", version > 0)
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    state = play._load(await play.store.read(cid))
    actors, pools, clock = state.actors, state.resources.pools, state.resources.game_time
    await declare(play, cid)
    state = play._load(await play.store.read(cid))
    assert not adopted(state, "b") and not observations(state.resources)
    plan = await select(play, cid, "attribute:iq")
    assert plan.skill_id == ARMOR and plan.default_modifier == -5
    state = play._load(await play.store.read(cid))
    assert state.actors == actors and state.resources.pools == pools
    assert state.resources.game_time == clock and not adopted(state, "b")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "order,permitted",
    [
        ("specific-unknown", True),
        ("specific-negative", False),
        ("negative-specific", True),
        ("specific-empty-generic-positive", True),
        ("generic-positive-specific-empty", True),
        ("generic-without-training", False),
    ],
)
async def test_current_generic_and_specific_facts_use_authoritative_chronology(
    tmp_path: Path, backend: str, order: str, permitted: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    if order.startswith("specific-"):
        state = play._load(await play.store.read(cid))
        await ArmouryService(play).execute(
            cid,
            DeclareArmouryTraining(
                id="specific-before",
                actor_id="gm",
                performer_id="b",
                expected_revision=state.revision,
                build_revision=build(play.rules_context, state, "b").revision,
                personal_technology_level=4,
                society_known_skills=() if "empty" in order else TARGETS,
            ),
            principal_id="gm",
        )
    await adopt(
        play,
        cid,
        (ARMOR,) if "positive" in order or order == "generic-without-training" else (),
        actor_id="b",
    )
    if "negative" in order:
        await DefaultKnowledgeService(play).execute(
            cid,
            DeclareDefaultKnowledge(
                id="generic-negative",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                performer_id="b",
                skill_ids=(ARMOR,),
                society_known=False,
                reason="The GM explicitly corrects current societal knowledge",
            ),
            principal_id="gm",
        )
    if order.endswith("-specific") or order == "generic-positive-specific-empty":
        state = play._load(await play.store.read(cid))
        await ArmouryService(play).execute(
            cid,
            DeclareArmouryTraining(
                id="specific-after",
                actor_id="gm",
                performer_id="b",
                expected_revision=state.revision,
                build_revision=build(play.rules_context, state, "b").revision,
                personal_technology_level=4,
                society_known_skills=() if "empty" in order else TARGETS,
            ),
            principal_id="gm",
        )
    before = await play.store.read(cid)
    state = play._load(before)
    assert (ARMOR in current_skills(state, "b")) == (order != "specific-negative")
    play.rng = RecordedDice(())
    if permitted:
        selected = await select(play, cid, "attribute:iq")
        assert selected.skill_id == ARMOR and selected.default_modifier == -5
    else:
        with pytest.raises(ValidationError, match="not known|verified training"):
            await select(play, cid, "attribute:iq")
        assert await play.store.read(cid) == before
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1, 2])
async def test_accepted_repair_plan_and_original_keep_captured_proof_after_revocation(
    tmp_path: Path, backend: str, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", version > 0)
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    plan = await select(play, cid, "attribute:iq")
    command = begin(await revision(play, cid))
    play.rng = RecordedDice((1,))
    accepted = await CombatService(play).execute(cid, command, principal_id="b")
    monkeypatch.setattr(generations, "ACTIVE", True)
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    original = tasks(play._load(await play.store.read(cid)).resources)[0]
    play.rng = secrets
    await adopt(play, cid, actor_id="b")
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="withdraw-after-original",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            performer_id="b",
            skill_ids=(ARMOR,),
            society_known=False,
            reason="Future default attempts require a new genuine positive observation",
        ),
        principal_id="gm",
    )
    await wait(play, cid, 1800)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        command.model_copy(
            update={
                "id": "finish",
                "stage": "finish",
                "task_id": "repair",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    completed = tasks(state.resources)[0]
    assert completed.status == "completed" and completed.skill == original.skill == 4
    assert plan.default_modifier == -5 and completed.restored_hp == 1
    item = next(item for item in state.resources.items if item.id == "repair-target")
    assert item.condition and item.condition.hp == 1
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)
    final = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, command, principal_id="b") == accepted
    assert await play.store.read(cid) == final and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("forgery", ["identity", "owner", "duplicate", "future", "skill"])
async def test_restored_specialized_knowledge_cannot_forge_current_permission(
    tmp_path: Path, backend: str, forgery: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    before = await play.store.read(cid)
    state = play._load(before)
    event = next(event for event in state.resources.events if event.id.startswith(TRAINING_PREFIX))
    if forgery == "duplicate":
        events = state.resources.events + (event,)
    else:
        facts = ArmouryTraining.model_validate_json(event.kind)
        updates: dict[str, dict[str, object]] = {
            "identity": {"id": TRAINING_PREFIX + "0" * 64},
            "owner": {"target_id": "a"},
            "future": {"at": state.resources.game_time + 1},
            "skill": {
                "kind": facts.model_copy(
                    update={"society_known_skills": ("skill:unrelated",)}
                ).model_dump_json()
            },
        }
        forged = event.model_copy(update=updates[forgery])
        events = tuple(forged if old.id == event.id else old for old in state.resources.events)
    restored = state.model_copy(
        update={"resources": state.resources.model_copy(update={"events": events})}
    )
    with pytest.raises(ValidationError, match="source identity"):
        current_skills(restored, "b")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("generic_positive", [False, True])
async def test_replaced_training_snapshot_expires_only_its_own_knowledge_support(
    tmp_path: Path, backend: str, generic_positive: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    if generic_positive:
        await adopt(play, cid, (ARMOR,), actor_id="b")
    state = play._load(await play.store.read(cid))
    await ArmouryService(play).execute(
        cid,
        DeclareArmouryTraining(
            id="replacement-training",
            actor_id="gm",
            performer_id="b",
            expected_revision=state.revision,
            build_revision=build(play.rules_context, state, "b").revision,
            personal_technology_level=4,
            society_known_skills=(),
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    state = play._load(before)
    assert (ARMOR in current_skills(state, "b")) is generic_positive
    play.rng = RecordedDice(())
    if generic_positive:
        assert (await select(play, cid, "attribute:iq")).skill_id == ARMOR
    else:
        with pytest.raises(ValidationError, match="not known"):
            await select(play, cid, "attribute:iq")
        assert await play.store.read(cid) == before
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_plan_stays_immutable_but_fresh_start_rechecks_current_truth(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    plan = await select(play, cid, "attribute:iq")
    await adopt(play, cid, actor_id="b")
    for known in (False, True):
        play.rng = secrets
        await DefaultKnowledgeService(play).execute(
            cid,
            DeclareDefaultKnowledge(
                id="current-positive" if known else "current-negative",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                performer_id="b",
                skill_ids=(ARMOR,),
                society_known=known,
                reason="The GM corrects the current fact before unrolled repair work",
            ),
            principal_id="gm",
        )
        before = await play.store.read(cid)
        command = begin(await revision(play, cid))
        if not known:
            play.rng = RecordedDice(())
            with pytest.raises(ValidationError, match="not known"):
                await CombatService(play).execute(cid, command, principal_id="b")
            assert await play.store.read(cid) == before and play.rng.exhausted()
        else:
            play.rng = RecordedDice((1,))
            await CombatService(play).execute(cid, command, principal_id="b")
            assert tasks(play._load(await play.store.read(cid)).resources)[0].parts_die == 1
    final = play._load(await play.store.read(cid))
    assert selected(final.resources, "repair") == plan
    assert plan.training_command_id == "training"
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
