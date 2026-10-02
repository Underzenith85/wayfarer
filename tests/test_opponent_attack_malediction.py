"""B66/B106/B348: caster worst check before one actual resistance and injury."""

from pathlib import Path

import pytest
from test_composed_attack_host import declare
from test_composed_attacks import pick
from test_opponent_attack_host import fixture
from test_opponent_attack_secret import private_result

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.traits.composed_records import ResistComposedAttack
from wayfarer.errors import ConflictError
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "resist,selected,following,damage",
    [
        (False, (2, 2, 2), (2, 2), 4),
        (False, (5, 5, 5), (), 0),
        (True, (2, 2, 2), (2, 3, 3), 0),
        (True, (2, 2, 2), (4, 4, 4, 2, 2), 4),
        (True, (5, 5, 5), (), 0),
    ],
)
@pytest.mark.parametrize("secret", [False, True])
async def test_selected_caster_check_controls_real_resistance_and_injury(
    tmp_path: Path,
    backend: str,
    resist: bool,
    selected: tuple[int, int, int],
    following: tuple[int, ...],
    damage: int,
    secret: bool,
) -> None:
    cid, play, source = await fixture(
        tmp_path, backend, modifiers=(pick("enhancement:malediction", option="1"),)
    )
    await declare(play, cid, source)
    before = play._load(await play.store.read(cid))
    attack = before.encounters[0].pending_defense
    assert attack
    play.rng = RecordedDice(()) if secret else RecordedDice((1, 1, 1))
    begun = await TaskService(play).execute(
        cid,
        BeginOpponentAttack(
            id="caster",
            actor_id="b",
            expected_revision=before.revision,
            encounter_id="fight",
            attack_id=attack.id,
            resist=resist,
            visibility="secret" if secret else "public",
        ),
        principal_id="gm",
    )
    assert begun.pending_id and play.rng.exhausted()
    if secret:
        assert begun.check is None
    else:
        assert begun.check and begun.check.effective_target == 8
    state = play._load(await play.store.read(cid))
    assert state.encounters == before.encounters
    command = ChooseOpponentAttack(
        id="caster-choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=begun.pending_id,
        choice="use-luck",
        response=ResistComposedAttack(
            id="caster-choice",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=attack.id,
            resist=resist,
        ),
    )
    assert isinstance(command.response, ResistComposedAttack)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="resistance declaration"):
        await TaskService(play).execute(
            cid,
            command.model_copy(
                update={"response": command.response.model_copy(update={"resist": not resist})}
            ),
            principal_id="bob",
        )
    play.rng = RecordedDice(((1, 1, 1) if secret else ()) + (1, 1, 2) + selected + following)
    result = await TaskService(play).execute(cid, command, principal_id="bob")
    private = private_result(play, await play.store.read(cid), command.id)
    assert (
        private.check
        and private.check.total == sum(selected)
        and private.check.effective_target == 8
    )
    assert private.luck and private.luck.chosen_index == 2 and private.combat_json
    combat = CombatResult.model_validate_json(private.combat_json)
    assert (
        combat.injury and combat.injury.injury == damage and combat.injury.attack == private.check
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10 - damage
    assert (
        state.encounters[0].pending_defense is None and state.encounters[0].current_actor_id == "b"
    )
    assert not state.encounters[0].participants[0].maneuver_state.concentrating
    assert snapshot(state).pending is None and play.rng.exhausted()
    if secret:
        assert result.check is None and result.combat_json is None and result.luck is None
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "resist,target,following,damage", [(True, 16, (2, 2, 3), 0), (False, 18, (2, 2), 4)]
)
async def test_rule_of_16_is_captured_only_for_declared_resistance(
    tmp_path: Path,
    backend: str,
    resist: bool,
    target: int,
    following: tuple[int, ...],
    damage: int,
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.engine.simulation.traits.composed_records import BindComposedSource
    from wayfarer.orchestration.composed_attacks import ComposedAttackService

    cid, play, source = await fixture(
        tmp_path, backend, modifiers=(pick("enhancement:malediction", option="1"),)
    )

    def stronger_will(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "a")
        proposal = actor.proposal.model_copy(
            update={
                "draft": actor.proposal.draft.model_copy(
                    update={
                        "purchases": actor.proposal.draft.purchases
                        + (Purchase(definition_id="secondary:will", amount=20),)
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
            reason="Independent B349 Rule of 16 fixture",
        )
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"proposal": proposal, "approval": approval})
                    if a.actor_id == "a"
                    else a
                    for a in state.actors
                ),
                "approvals": state.approvals + (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            owner.model_copy(
                                update={"definitions": owner.definitions + ("secondary:will",)}
                            )
                            if owner.actor_id == "a"
                            else owner
                            for owner in state.resources.owners
                        )
                    }
                ),
            }
        )

    await change(play, cid, stronger_will)
    state = play._load(await play.store.read(cid))
    await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="rebind",
            actor_id="a",
            expected_revision=state.revision,
            description="A directed natural burning beam",
            specialty="beam",
        ),
        principal_id="gm",
    )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    attack = state.encounters[0].pending_defense
    assert attack
    play.rng = RecordedDice((1, 1, 1))
    begun = await TaskService(play).execute(
        cid,
        BeginOpponentAttack(
            id="caster",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=attack.id,
            resist=resist,
        ),
        principal_id="gm",
    )
    assert (
        begun.pending_id
        and begun.check
        and begun.check.base_target == 20
        and begun.check.effective_target == target
    )
    assert sum(m.value for m in begun.check.modifiers) == target - 20
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2, 5, 5, 5) + following)
    result = await TaskService(play).execute(
        cid,
        ChooseOpponentAttack(
            id="choice",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=begun.pending_id,
            choice="use-luck",
            response=ResistComposedAttack(
                id="choice",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                pending_id=attack.id,
                resist=resist,
            ),
        ),
        principal_id="bob",
    )
    assert result.check and result.check.effective_target == target and result.check.total == 15
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.injury == damage
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


async def change_will(play: PlayService, cid: str, actor_id: str, will: int) -> None:
    from test_combat_sensory_authority import change

    def revise(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == actor_id)
        proposal = actor.proposal.model_copy(
            update={
                "draft": actor.proposal.draft.model_copy(
                    update={
                        "purchases": actor.proposal.draft.purchases
                        + (Purchase(definition_id="secondary:will", amount=will),)
                    }
                )
            }
        )
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=cid,
            actor_id=actor_id,
            revision=state.revision,
            approver_id="gm",
            reason="Current unresolved resistance source fixture",
        )
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"proposal": proposal, "approval": approval})
                    if a.actor_id == actor_id
                    else a
                    for a in state.actors
                ),
                "approvals": state.approvals + (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            o.model_copy(
                                update={"definitions": o.definitions + ("secondary:will",)}
                            )
                            if o.actor_id == actor_id
                            else o
                            for o in state.resources.owners
                        )
                    }
                ),
            }
        )

    await change(play, cid, revise)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "caster_will,target_will,caster_target,resistance_dice",
    [
        (10, 14, 8, (4, 4, 4)),
        (20, 20, 16, (3, 3, 4)),
    ],
)
async def test_later_resistance_refreshes_without_rescoring_captured_caster(
    tmp_path: Path,
    backend: str,
    caster_will: int,
    target_will: int,
    caster_target: int,
    resistance_dice: tuple[int, int, int],
) -> None:
    from support.runtime import build_play

    from wayfarer.engine.simulation.traits.attack_defense import history
    from wayfarer.engine.simulation.traits.composed_records import BindComposedSource
    from wayfarer.orchestration.composed_attacks import ComposedAttackService

    cid, play, source = await fixture(
        tmp_path, backend, modifiers=(pick("enhancement:malediction", option="1"),)
    )
    if caster_will != 10:
        await change_will(play, cid, "a", caster_will)
        state = play._load(await play.store.read(cid))
        await ComposedAttackService(play).execute(
            cid,
            BindComposedSource(
                id="rebind",
                actor_id="a",
                expected_revision=state.revision,
                description="A directed natural burning beam",
                specialty="beam",
            ),
            principal_id="gm",
        )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    attack = state.encounters[0].pending_defense
    assert attack
    play.rng = RecordedDice((2, 2, 2))
    begun = await TaskService(play).execute(
        cid,
        BeginOpponentAttack(
            id="caster",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=attack.id,
            resist=True,
        ),
        principal_id="gm",
    )
    assert begun.pending_id and begun.check and begun.check.effective_target == caster_target
    await change_will(play, cid, "b", target_will)
    state = play._load(await play.store.read(cid))
    command = ChooseOpponentAttack(
        id="resist",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=begun.pending_id,
        choice="accept",
        response=ResistComposedAttack(
            id="resist",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=attack.id,
            resist=True,
        ),
    )
    play.rng = RecordedDice(resistance_dice)
    result = await TaskService(play).execute(cid, command, principal_id="bob")
    after = play._load(await play.store.read(cid))
    checks = history(after.resources)[-1].outcome.checks
    assert checks[0] == begun.check == result.check
    assert checks[1].effective_target == target_will
    assert checks[0].margin == checks[1].margin
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    assert play.rng.exhausted() and snapshot(after).pending is None
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, command, principal_id="bob") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("option", ["2", "3"])
async def test_other_malediction_ranges_have_no_approved_executable_source(
    tmp_path: Path, option: str
) -> None:
    cid, play, _ = await fixture(tmp_path)
    state = play._load(await play.store.read(cid))
    actor = next(a for a in state.actors if a.actor_id == "a")
    draft = actor.proposal.draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(
                    update={
                        "trait": p.trait.model_copy(
                            update={
                                "attack_modifiers": (
                                    pick("enhancement:malediction", option=option),
                                )
                            }
                        )
                    }
                )
                if p.definition_id == "advantage:innate-attack" and p.trait is not None
                else p
                for p in actor.proposal.draft.purchases
            )
        }
    )
    compiled = play.engine.reviewer.compiler.compile(draft)
    assert compiled.build is None and not compiled.legal
    assert any("Malediction 1" in error.message for error in compiled.diagnostics)
