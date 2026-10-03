"""B168/B173/B178 defaults resolve into actual B484 durability transactions."""

from pathlib import Path

import pytest
from support.armoury_defaults import declare, fixture, revision, select, wait
from test_issue_818_armoury_acceptance import begin

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.equipment.repair_defaults import (
    SelectRepairDefault,
)
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("after_start", [False, True])
async def test_build_change_before_start_refuses_but_accepted_skill_stays_captured(
    tmp_path: Path, backend: str, after_start: bool
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.engine.simulation.actions import PlayState

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    await select(play, cid, "attribute:iq")
    if after_start:
        play.rng = RecordedDice((1,))
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")

    def approve_new_build(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "b")
        proposal = actor.proposal.model_copy(
            update={
                "draft": actor.proposal.draft.model_copy(
                    update={
                        "purchases": actor.proposal.draft.purchases
                        + (
                            Purchase(
                                definition_id="skill:armoury-body-armor",
                                amount=4,
                                technology_level=4,
                            ),
                        )
                    }
                )
            }
        )
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=cid,
            actor_id="b",
            revision=state.revision + 1,
            approver_id="gm",
            reason="authenticated fixture build change",
        )
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"proposal": proposal, "approval": approval})
                    if a.actor_id == "b"
                    else a
                    for a in state.actors
                ),
                "approvals": state.approvals + (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            o.model_copy(
                                update={
                                    "definitions": o.definitions + ("skill:armoury-body-armor",)
                                }
                            )
                            if o.actor_id == "b"
                            else o
                            for o in state.resources.owners
                        )
                    }
                ),
            }
        )

    await change(play, cid, approve_new_build)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    if not after_start:
        with pytest.raises(ValidationError, match="current verified"):
            await CombatService(play).execute(
                cid, begin(await revision(play, cid)), principal_id="b"
            )
        assert await play.store.read(cid) == saved and play.rng.exhausted()
        return
    await wait(play, cid, 1800)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert (
        task.skill == 4
        and task.restored_hp == 1
        and task.check
        and task.check.effective_target == 4
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selection_rollback_authority_stale_retry_privacy_and_genesis(
    tmp_path: Path, backend: str
) -> None:
    import json

    from support.runtime import build_runtime
    from test_actions import campaign
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    command = SelectRepairDefault(
        id="choice",
        actor_id="b",
        item_id="repair-target",
        start_command_id="repair",
        source_id="attribute:iq",
        repair_time_method="extra-2",
        expected_revision=await revision(play, cid),
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await ArmouryService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="b"
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await ArmouryService(failing).execute(cid, command, principal_id="b")
    assert (
        saved == await play.store.read(cid)
        and history == await play.store.history(cid)
        and stream == await play.store.stream(cid)
    )
    plan = await ArmouryService(play).execute(cid, command, principal_id="b")
    saved = await play.store.read(cid)
    assert await ArmouryService(play).execute(cid, command, principal_id="b") == plan
    assert saved == await play.store.read(cid)
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid,
            command.model_copy(update={"source_id": "skill:engineer-small-arms"}),
            principal_id="b",
        )
    view = json.dumps(await build_runtime(play).read(cid, principal_id="a"))
    assert (
        "armoury-training:" not in view
        and "armoury-default:" not in view
        and plan.equipment_digest not in view
    )
    state = play._load(saved)
    for prefix in ("armoury-training:", "armoury-default:"):
        forged = state.resources.model_copy(
            update={
                "revision": 0,
                "events": (ResourceEvent(id=prefix + "forged", at=0, target_id="b", kind="{}"),),
            }
        )
        with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
            play.initial_state(
                campaign(play.engine),
                state.world,
                forged,
                tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
            )
    assert saved == await play.store.read(cid)


def test_old_private_command_absence_has_exact_historical_bytes() -> None:
    from wayfarer.engine.simulation.equipment.armoury_context import DeclareArmouryFamiliarity
    from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts

    assert (
        AssessRepairParts(
            id="a", actor_id="b", expected_revision=2, item_id="target"
        ).model_dump_json()
        == '{"id":"a","actor_id":"b","expected_revision":2,"kind":"assess-repair-parts","item_id":"target"}'
    )
    assert (
        "repair_start_command_id"
        not in DeclareArmouryFamiliarity(
            id="f",
            actor_id="gm",
            expected_revision=2,
            performer_id="b",
            item_id="target",
            basis="known-model",
        ).model_dump_json()
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("problem", ["owner", "condition", "tool", "stock"])
async def test_current_physical_setup_refuses_before_parts_rng(
    tmp_path: Path, backend: str, problem: str
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.engine.simulation.actions import PlayState

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    await select(play, cid, "attribute:iq")

    def invalidate(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"owner_id": "a"})
                            if problem == "owner" and i.id == "repair-target"
                            else i.model_copy(update={"condition": ObjectCondition(hp=1)})
                            if problem == "condition" and i.id == "repair-target"
                            else i.model_copy(update={"quantity": 1})
                            if problem == "stock" and i.id == "parts-b"
                            else i
                            for i in state.resources.items
                            if not (problem == "tool" and i.id == "tool-b")
                        )
                    }
                )
            }
        )

    await change(play, cid, invalidate)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ValidationError, ConflictError)):
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert saved == await play.store.read(cid) and play.rng.exhausted()
