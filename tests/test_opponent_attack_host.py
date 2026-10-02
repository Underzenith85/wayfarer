"""B66 immediate opponent attack selection commits real B374 defense/injury."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_basic_combat import start_basic
from test_combat_sensory_authority import change
from test_composed_attack_host import declare
from test_gurps_melee import setup
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.rules.gurps_characters import source as character_source
from wayfarer.engine.rules.traits.attack_defense import RUNTIME_HOOKS, package
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.modifiers import ModifierSelection
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.traits.composed_sources import BindComposedSource
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, TaskResult, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock


async def fixture(
    path: Path,
    backend: str = "sqlite",
    *,
    points: int = 15,
    modifiers: tuple[ModifierSelection, ...] = (),
) -> tuple[str, PlayService, str]:
    luck = next(d for d in candidate_package().definitions if d.id == "trait:advantage:luck")
    luck = replace(luck, source_id=character_source("gurps-basic-set-4e-2004").id)
    innate = RuleDefinition(
        "skill:innate-attack-beam",
        DefinitionKind.SKILL,
        "Innate Attack (Beam)",
        luck.source_id,
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.DX, Difficulty.EASY, "B201"),
    )
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        trained=False,
        scene_bound=True,
        start_encounter=False,
        extra_definitions=package().definitions + (innate, luck),
        trait_runtime_hooks=RUNTIME_HOOKS | SUPPORTED_HOOKS,
        extra_purchases=(
            Purchase(
                definition_id="advantage:innate-attack",
                amount=2,
                trait=options(**{"damage-type": "burn"}).model_copy(
                    update={"attack_modifiers": modifiers}
                ),
            ),
            Purchase(definition_id=innate.id, amount=4),
            Purchase(
                definition_id=luck.id, trait=TraitOptions(parameters=(("point-cost", points),))
            ),
        ),
    )
    await CombatService(original).execute(cid, start_basic(2, ranged=True), principal_id="gm")
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "members": s.members
                + (
                    CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
                    CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
                )
            }
        ),
    )
    state = play._load(await play.store.read(cid))
    result = await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="bind",
            actor_id="a",
            expected_revision=state.revision,
            description="A directed natural burning beam",
            specialty="beam",
        ),
        principal_id="gm",
    )
    assert result.source_id
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(
            id="clock-start",
            actor_id="gm",
            expected_revision=state.revision,
            running=True,
        ),
        principal_id="gm",
    )
    return cid, play, result.source_id


async def begin(
    play: PlayService,
    cid: str,
    *,
    identifier: str = "original",
    principal: str = "gm",
    prepare_owner_damage: bool = False,
) -> tuple[BeginOpponentAttack, TaskResult]:
    state = play._load(await play.store.read(cid))
    attack = state.encounters[0].pending_defense
    assert attack
    command = BeginOpponentAttack(
        id=identifier,
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        attack_id=attack.id,
        prepare_owner_damage=prepare_owner_damage,
    )
    return command, await TaskService(play).execute(cid, command, principal_id=principal)


async def choose(
    play: PlayService,
    cid: str,
    pending_id: str | None,
    *,
    identifier: str = "choose",
    principal: str = "bob",
    luck: bool = True,
    defense: Literal["none", "dodge"] = "none",
) -> tuple[ChooseOpponentAttack, TaskResult]:
    assert pending_id
    state = play._load(await play.store.read(cid))
    command = ChooseOpponentAttack(
        id=identifier,
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending_id,
        choice="use-luck" if luck else "accept",
        response=ChooseDefense(
            id=identifier,
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense=defense,
        ),
    )
    return command, await TaskService(play).execute(cid, command, principal_id=principal)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "rerolls,defense,following,total,injury",
    [
        ((3, 3, 3, 5, 5, 5), "none", (), 15, 0),
        ((2, 2, 1, 3, 3, 3), "dodge", (2, 2, 2), 9, 0),
        ((1, 2, 2, 3, 3, 3), "none", (2, 2), 9, 4),
    ],
)
async def test_worst_attack_changes_actual_hit_defense_and_hp(
    tmp_path: Path,
    backend: str,
    rerolls: tuple[int, ...],
    defense: Literal["none", "dodge"],
    following: tuple[int, ...],
    total: int,
    injury: int,
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    declared = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 1))
    command, original = await begin(play, cid)
    assert original.check and original.check.effective_target == 12
    assert original.check.outcome is Outcome.CRITICAL_SUCCESS
    pending_state = play._load(await play.store.read(cid))
    assert pending_state.resources.pools == declared.resources.pools
    assert pending_state.encounters == declared.encounters
    assert play.rng.exhausted()
    play.rng = RecordedDice(rerolls + following)
    choice, result = await choose(play, cid, original.pending_id, defense=defense)
    assert result.check and result.check.total == total and result.luck and result.combat_json
    assert result.luck.chosen_index == 2
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.attack == result.check
    assert combat.injury.injury == injury and combat.injury.hp_after == 10 - injury
    assert (combat.injury.defense is not None) == (defense == "dodge")
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10 - injury
    assert state.encounters[0].pending_defense is None
    assert state.encounters[0].current_actor_id == "b"
    assert snapshot(state).pending is None and len(snapshot(state).luck.receipts) == 1
    assert real_play_clock(state).cooldowns[0].actor_id == "b"
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, choice, principal_id="bob") == result
    assert await TaskService(restarted).execute(cid, command, principal_id="gm") == original
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)
    with pytest.raises(ConflictError):
        await choose(restarted, cid, original.pending_id, identifier="late")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_requires_immediate_owner_choice_and_private_authority(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    play.rng = RecordedDice((3, 3, 3))
    with pytest.raises(ValidationError):
        await begin(play, cid, principal="alice")
    _, original = await begin(play, cid)
    saved = await play.store.read(cid)
    pending = play._load(saved)
    with pytest.raises(AuthorizationError):
        await TaskService(play).pending(cid, principal_id="alice")
    assert await TaskService(play).pending(cid, principal_id="bob")
    with pytest.raises(AuthorizationError):
        await choose(play, cid, original.pending_id, principal="gm")
    with pytest.raises(ConflictError, match="pending"):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="bypass",
                actor_id="b",
                expected_revision=pending.revision,
                encounter_id="fight",
                defense="none",
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == saved
    play.rng = RecordedDice((2, 2))
    _, accepted = await choose(play, cid, original.pending_id, principal="gm", luck=False)
    assert accepted.luck is None and accepted.combat_json
    accepted_combat = CombatResult.model_validate_json(accepted.combat_json)
    assert accepted_combat.injury and accepted_combat.injury.hp_after == 6
    assert real_play_clock(play._load(await play.store.read(cid))).cooldowns == ()


async def next_attack(play: PlayService, cid: str, source: str, identifier: str) -> None:
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="idle-" + identifier,
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    await declare(play, cid, source, identifier=identifier)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_opponent_choice_can_continue_into_attacker_own_damage_luck(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamageOutcome

    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    play.rng = RecordedDice((1, 1, 1))
    _, original = await begin(play, cid, prepare_owner_damage=True)
    assert original.pending_id
    state = play._load(await play.store.read(cid))
    command = ChooseOpponentAttack(
        id="both",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=original.pending_id,
        choice="use-luck",
        response=ChooseDefense(
            id="both",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )
    play.rng = RecordedDice((2, 2, 1, 3, 3, 3, 1, 1))
    chosen = await TaskService(play).execute(cid, command, principal_id="bob")
    assert chosen.status == "completed" and chosen.pending_id is None
    assert chosen.check and chosen.check.total == 9 and chosen.luck
    assert chosen.damage_json is None and chosen.combat_json is None
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    with pytest.raises(AuthorizationError):
        await TaskService(play).pending(cid, principal_id="bob")
    attacker = await TaskService(play).pending(cid, principal_id="alice")
    assert attacker and attacker.damage_json and attacker.pending_id
    assert OwnerDamageOutcome.model_validate_json(attacker.damage_json).dice == (1, 1)
    play.rng = RecordedDice((2, 2, 3, 3, 2, 2, 2))
    final = await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="own-damage",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=attacker.pending_id,
            choice="use-luck",
        ),
        principal_id="alice",
    )
    assert final.damage_json and final.luck
    outcome = OwnerDamageOutcome.model_validate_json(final.damage_json)
    assert outcome.dice == (3, 3) and outcome.combat and outcome.combat.injury
    assert outcome.combat.injury.hp_after == 4 and outcome.combat.injury.attack == chosen.check
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert snapshot(state).pending is None and len(snapshot(state).luck.receipts) == 2
    assert {c.actor_id for c in real_play_clock(state).cooldowns} == {"a", "b"}
    assert await play.store.read(cid) == await play.store.replay(cid)
