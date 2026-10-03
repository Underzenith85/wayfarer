"""B239 named knowledge and B366 selected Step form one actual casting consumer."""

from pathlib import Path

import pytest
from test_great_haste_named_subject import prepare_named as prepare_original
from test_power_maintenance_lifecycle import change

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.great_haste_named import origin
from wayfarer.engine.simulation.magic.great_haste_step_state import (
    CastingStep,
    NamedStepCastGreatHaste,
    leases,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService


async def prepare_named(
    path: Path, backend: str, *, blocked: bool, amount: int = 16, basic_move: int | None = None
) -> tuple[str, PlayService]:
    cid, play = await prepare_original(path, backend, blocked=blocked)

    def trained(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "a")
        draft = actor.proposal.draft.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(update={"amount": amount})
                    if p.definition_id == "spell:great-haste"
                    else p
                    for p in actor.proposal.draft.purchases
                )
                + (
                    (Purchase(definition_id="secondary:basic-move", amount=basic_move),)
                    if basic_move is not None
                    else ()
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
            reason="B237 trained ritual movement fixture",
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
                                    "definitions": tuple(p.definition_id for p in draft.purchases)
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

    await change(cid, play, "ritual-trained-caster", trained)
    return cid, play


def command(
    revision: int, index: int, *, fact: str = "named-subject", blocked: bool = True
) -> NamedStepCastGreatHaste:
    return NamedStepCastGreatHaste(
        id="named-step-" + str(index),
        actor_id="a",
        expected_revision=revision,
        operation="start" if index == 0 else "concentrate",
        channel_id="great-haste",
        cast_id="named-step",
        known_fact_id=fact,
        step=CastingStep(
            hex_path=(
                Hex(q=1, r=0)
                if index == 2 and not blocked
                else Hex(q=0, r=1 if index % 2 == 0 else 0),
            )
        ),
    )


async def other_turns(cid: str, play: PlayService) -> None:
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
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


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("blocked", [False, True])
async def test_named_step_moves_before_cast_and_refreshes_actual_unseen_range(
    tmp_path: Path, backend: str, blocked: bool
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=blocked)
    service = GreatHasteService(play)
    first_state = play._load(await play.store.read(cid))
    initial_fp = next(p.current for p in first_state.resources.pools if p.id == "fp:a")
    for index in range(2):
        state = play._load(await play.store.read(cid))
        receipt = await service.execute(cid, command(state.revision, index), principal_id="alice")
        assert receipt.outcome == "casting" and receipt.energy_spent == 0
        state = play._load(await play.store.read(cid))
        assert next(
            p.position for p in state.encounters[0].participants if p.actor_id == "a"
        ) == Hex(q=0, r=1 if index == 0 else 0)
        accepted = origin(state, "named-step")
        lease = leases(state.resources)["named-step-" + str(index)]
        assert (
            accepted and lease.completed and lease.named_origin_json == accepted.model_dump_json()
        )
        assert latest(state.resources)["named-step"].target_id == "b"
    await other_turns(cid, play)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([1, 1, 3])
    final_command = command(state.revision, 2, blocked=blocked)
    receipt = await service.execute(cid, final_command, principal_id="alice")
    assert receipt.outcome == "active" and receipt.energy_spent == 4
    final = await play.store.read(cid)
    state = play._load(final)
    effect = latest(state.resources)["named-step"]
    assert (
        effect.target_id == "b"
        and effect.skill == (8 if blocked else 14)
        and effect.concentration_seconds == 3
    )
    assert effect.expires_at == state.resources.game_time + 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == initial_fp - 4
    assert play.rng.exhausted()
    restarted = PlayService(play.store, play.engine)
    restarted.rng = RecordedDice([])
    assert (
        await GreatHasteService(restarted).execute(cid, final_command, principal_id="alice")
        == receipt
    )
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_step_authority_stale_and_unknown_fact_fail_before_movement(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=True)
    before = await play.store.read(cid)
    state = play._load(before)
    play.rng = RecordedDice([])
    service = GreatHasteService(play)
    with pytest.raises(AuthorizationError):
        await service.execute(
            cid,
            command(state.revision, 0).model_copy(update={"actor_id": "b"}),
            principal_id="alice",
        )
    with pytest.raises(ConflictError):
        await service.execute(cid, command(state.revision - 1, 0), principal_id="alice")
    with pytest.raises(ValidationError, match="explicit known subject"):
        await service.execute(cid, command(state.revision, 0, fact="missing"), principal_id="alice")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_step_last_cast_entropy_rollback_and_actual_failure(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=False)
    service = GreatHasteService(play)
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await service.execute(cid, command(state.revision, index), principal_id="alice")
    await other_turns(cid, play)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    selected = command(state.revision, 2, blocked=False)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="dice"):
        await service.execute(cid, selected, principal_id="alice")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([5, 5, 5])
    receipt = await service.execute(cid, selected, principal_id="alice")
    state = play._load(await play.store.read(cid))
    assert receipt.outcome == "failed" and receipt.energy_spent == 1
    assert latest(state.resources)["named-step"].phase == "ended"
    assert next(p.position for p in state.encounters[0].participants if p.actor_id == "a") == Hex(
        q=1, r=0
    )
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_step_absent_generation_and_illegal_path_are_atomic(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.orchestration.pipeline import submit

    cid, play = await prepare_named(tmp_path, backend, blocked=False)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    state = play._load(before)
    service = GreatHasteService(play)
    selected = command(state.revision, 0)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="authenticated casting generation"):
        await submit(
            play,
            cid,
            service.plan(state, selected, "alice", combat_casting=False),
            principal_id="alice",
        )
    illegal = selected.model_copy(
        update={"step": CastingStep(hex_path=(Hex(q=1, r=0), Hex(q=2, r=0)))}
    )
    with pytest.raises(ValidationError):
        await service.execute(cid, illegal, principal_id="alice")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert play.rng.exhausted()
