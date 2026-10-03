"""Authenticated physical facts stay private and current before dice."""

import json
from pathlib import Path

import pytest
from support.armoury_defaults import declare, fixture, revision, select, wait
from test_armoury_tools import choose, observe
from test_issue_818_armoury_acceptance import begin

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.equipment.tool_context import DeclareRepairTools, SelectRepairTools
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_observation_selection_authority_retry_rollback_and_privacy(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import build_play, build_runtime
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    command = DeclareRepairTools(
        id="tools-facts",
        actor_id="gm",
        performer_id="b",
        tool_id="tool-b",
        quality="fine",
        expected_revision=await revision(play, cid),
    )
    before = await play.store.read(cid)
    for principal in ("a", "b"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await ArmouryService(play).execute(cid, command, principal_id=principal)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError):
        await ArmouryService(failing).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before
    value = await ArmouryService(play).execute(cid, command, principal_id="gm")
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await ArmouryService(restarted).execute(cid, command, principal_id="gm") == value
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, command.model_copy(update={"quality": "good"}), principal_id="gm"
        )
    choice = SelectRepairTools(
        id="tools-choice",
        actor_id="b",
        item_id="repair-target",
        tool_id="tool-b",
        start_command_id="repair",
        expected_revision=await revision(play, cid),
    )
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await ArmouryService(play).execute(cid, choice, principal_id="a")
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, choice.model_copy(update={"expected_revision": 0}), principal_id="b"
        )
    with pytest.raises(RuntimeError):
        await ArmouryService(failing).execute(cid, choice, principal_id="b")
    assert await play.store.read(cid) == before
    plan = await ArmouryService(play).execute(cid, choice, principal_id="b")
    assert await ArmouryService(restarted).execute(cid, choice, principal_id="b") == plan
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, choice.model_copy(update={"id": "reroute"}), principal_id="b"
        )
    for actor in ("a", "b"):
        view = json.dumps(await build_runtime(play).read(cid, principal_id=actor))
        assert "armoury-tools:" not in view and "armoury-tool-selection:" not in view
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("stage", ["start", "finish"])
@pytest.mark.parametrize("mutation", ["owner", "ground", "observation"])
async def test_changed_current_kit_refuses_before_material_or_dice(
    tmp_path: Path, backend: str, stage: str, mutation: str
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.engine.rules.types.object import GroundPosition
    from wayfarer.engine.simulation.actions import PlayState

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    await observe(play, cid, "fine")
    await choose(play, cid)
    await select(play, cid, "attribute:iq")
    if stage == "finish":
        play.rng = RecordedDice((1,))
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
        await wait(play, cid, 1800)
    if mutation == "observation":
        await ArmouryService(play).execute(
            cid,
            DeclareRepairTools(
                id="changed-tools",
                actor_id="gm",
                performer_id="b",
                tool_id="tool-b",
                quality="fine",
                damage="minor",
                expected_revision=await revision(play, cid),
            ),
            principal_id="gm",
        )
    else:

        def mutate(state: PlayState) -> PlayState:
            return state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(
                                    update={"owner_id": "a"}
                                    if mutation == "owner"
                                    else {
                                        "ground": GroundPosition(
                                            encounter_id="fight", geometry="grid", x=1, y=2
                                        ),
                                        "equipped": False,
                                    }
                                )
                                if i.id == "tool-b"
                                else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )

        await change(play, cid, mutate)
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    command = begin(await revision(play, cid))
    if stage == "finish":
        command = command.model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        )
    with pytest.raises((ConflictError, ValidationError)):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert (
        await play.store.read(cid) == before
        and await play.store.stream(cid) == stream
        and play.rng.exhausted()
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_genesis_cannot_author_tool_facts_or_selections(tmp_path: Path, backend: str) -> None:
    from test_actions import campaign

    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    state = play._load(await play.store.read(cid))
    for prefix in ("armoury-tools:", "armoury-tool-selection:"):
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


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_spectator_cannot_observe_select_or_read_private_tools(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import build_runtime
    from test_combat_sensory_authority import change

    from wayfarer.engine.simulation.campaign.access import CampaignMember

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": state.members
                + (CampaignMember(principal_id="watcher", role="spectator"),)
            }
        ),
    )
    await observe(play, cid, "fine")
    await choose(play, cid)
    for command in (
        DeclareRepairTools(
            id="spy-observe",
            actor_id="gm",
            performer_id="b",
            tool_id="tool-b",
            quality="good",
            expected_revision=await revision(play, cid),
        ),
        SelectRepairTools(
            id="spy-select",
            actor_id="b",
            item_id="repair-target",
            tool_id="tool-b",
            start_command_id="other-repair",
            expected_revision=await revision(play, cid),
        ),
    ):
        saved = await play.store.read(cid)
        with pytest.raises((AuthorizationError, ValidationError)):
            await ArmouryService(play).execute(cid, command, principal_id="watcher")
        assert await play.store.read(cid) == saved
    view = json.dumps(await build_runtime(play).read(cid, principal_id="watcher"))
    assert "armoury-tools:" not in view and "armoury-tool-selection:" not in view


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_late_compare_and_set_conflict_commits_no_tool_receipt(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Callable

    from test_combat_sensory_authority import change

    from wayfarer.contracts import Campaign, CommandReceipt, TurnResult
    from wayfarer.persistence.events import CommandEntropy, CommandOrigin, CommandResolution

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    original = play.store.commit_turn
    winning: list[Campaign] = []

    async def race(
        campaign_id: str,
        request_id: str,
        revision_number: int,
        text: str,
        resolve: Callable[[Campaign], CommandReceipt | CommandResolution],
        *,
        actor_id: str = "system",
        entropy: CommandEntropy | None = None,
        recorded_at_us: int | None = None,
        origin: CommandOrigin | None = None,
    ) -> TurnResult:
        if request_id == "tools-facts":
            await change(play, cid, lambda state: state)
            winning.append(await play.store.read(cid))
        return await original(
            campaign_id,
            request_id,
            revision_number,
            text,
            resolve,
            actor_id=actor_id,
            entropy=entropy,
            recorded_at_us=recorded_at_us,
            origin=origin,
        )

    monkeypatch.setattr(play.store, "commit_turn", race)
    with pytest.raises(ConflictError):
        await observe(play, cid, "fine")
    assert winning and await play.store.read(cid) == winning[0]
    state = play._load(winning[0])
    assert not any(e.id.startswith("armoury-tools:") for e in state.resources.events)
    assert await play.store.duplicate(cid, "tools-facts", "unused") is None
