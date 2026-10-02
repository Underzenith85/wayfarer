"""Only a trusted source requests the later damage phase; it reveals no hidden Luck."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_composed_attack_host import declare
from test_opponent_attack_host import begin, fixture
from test_opponent_attack_secret import prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.opponent_attack_records import ChooseOpponentAttack
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


async def revise_attacker(play: PlayService, cid: str, mode: str) -> None:
    def revise(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "a")
        if mode == "approval":
            updated = actor.model_copy(update={"approval": None})
            approvals = state.approvals
        else:
            proposal = actor.proposal.model_copy(
                update={
                    "draft": actor.proposal.draft.model_copy(
                        update={
                            "purchases": tuple(
                                p
                                for p in actor.proposal.draft.purchases
                                if mode != "luck" or p.definition_id != "trait:advantage:luck"
                            )
                        }
                    )
                }
            )
            approval = play.engine.reviewer.approve(
                proposal,
                campaign_id=cid,
                actor_id="a",
                revision=state.revision,
                approver_id="gm",
                reason="Private attacker trait boundary fixture",
            )
            updated = actor.model_copy(update={"proposal": proposal, "approval": approval})
            approvals = state.approvals + (approval,)
        return state.model_copy(
            update={
                "actors": tuple(updated if a.actor_id == "a" else a for a in state.actors),
                "approvals": approvals,
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            o.model_copy(
                                update={
                                    "definitions": tuple(
                                        d for d in o.definitions if d != "trait:advantage:luck"
                                    )
                                }
                            )
                            if o.actor_id == "a" and mode == "luck"
                            else o
                            for o in state.resources.owners
                        )
                    }
                ),
            }
        )

    await change(play, cid, revise)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_identical_victim_view_survives_hidden_luck_and_approval_changes(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    visible = []
    for mode in ("unchanged", "luck", "approval"):
        path = tmp_path / mode
        cid, play, source = await fixture(path, backend)
        await declare(play, cid, source)
        play.rng = RecordedDice(()) if secret else RecordedDice((1, 1, 1))
        _, opened = await (prepare if secret else begin)(play, cid, prepare_owner_damage=True)
        assert opened.pending_id
        if not secret:
            await revise_attacker(play, cid, mode)
        state = play._load(await play.store.read(cid))
        source_actor = next(a for a in state.actors if a.actor_id == "a")
        command = ChooseOpponentAttack(
            id="victim",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=opened.pending_id,
            choice="use-luck",
            response=ChooseDefense(
                id="victim",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                defense="none",
            ),
        )
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError):
            await TaskService(play).execute(
                cid,
                {
                    **command.model_dump(mode="json"),
                    "prepare_owner_damage": True,
                },
                principal_id="bob",
            )
        assert play.rng.exhausted()
        play.rng = RecordedDice(
            ((1, 1, 1) if secret else ()) + (2, 2, 2, 3, 3, 3) + (() if secret else (1, 1))
        )
        result = await TaskService(play).execute(cid, command, principal_id="bob")
        assert result.status == "completed" and result.pending_id is None
        assert result.damage_json is None and result.combat_json is None
        visible.append((result.status, result.pending_id, result.check, result.secret))
        state = play._load(await play.store.read(cid))
        assert next(a for a in state.actors if a.actor_id == "a") == source_actor
        assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
        with pytest.raises(AuthorizationError):
            await TaskService(play).pending(cid, principal_id="bob")
        owner = await TaskService(play).pending(cid, principal_id="alice")
        assert owner and owner.pending_id
        if secret:
            await revise_attacker(play, cid, mode)
            source_actor = next(
                a for a in play._load(await play.store.read(cid)).actors if a.actor_id == "a"
            )
        fresh = build_play(
            path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
        )
        assert await TaskService(fresh).execute(cid, command, principal_id="bob") == result
        state = fresh._load(await fresh.store.read(cid))
        use = ChooseOwnerDamage(
            id="owner",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=owner.pending_id,
            choice="use-luck",
        )
        if mode != "unchanged":
            before = await fresh.store.read(cid)
            with pytest.raises(ValidationError):
                await TaskService(fresh).execute(cid, use, principal_id="alice")
            assert await fresh.store.read(cid) == before
        fresh.rng = RecordedDice((1, 1)) if secret else RecordedDice(())
        accepted = await TaskService(fresh).execute(
            cid, use.model_copy(update={"choice": "accept"}), principal_id="gm"
        )
        assert accepted.status == "completed"
        after = fresh._load(await fresh.store.read(cid))
        assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 8
        assert next(a for a in after.actors if a.actor_id == "a") == source_actor
        assert snapshot(after).pending is None and fresh.rng.exhausted()
        assert await fresh.store.read(cid) == await fresh.store.replay(cid)
    assert visible[0] == visible[1] == visible[2]
