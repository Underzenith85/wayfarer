"""Characters 3p B36/B109; Campaigns 4p B421, actual attribute consumers."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_attack_defense_traits import approved, command, resources, world
from test_combat_sensory_authority import change
from test_composed_attacks import attacker, pick
from test_gurps_melee import attack, setup
from test_social_dispatch import prepare as social_prepare

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.skill import (
    ControllingAttribute,
    Difficulty,
    SkillDefault,
    SkillSpec,
)
from wayfarer.engine.rules.types.symptoms import SymptomDebt, SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.npcs import NPCSocialTrigger
from wayfarer.engine.simulation.combat.melee.resolution import resolve_melee
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.unarmed.defense import parry_candidates
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.health.symptom_state import projected_build
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.attack_defense import TraitAttackOutcome
from wayfarer.engine.simulation.traits.composed_attacks import (
    AttackCompositionContext,
    apply_composed_attack,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.npcs import social_occurrence
from wayfarer.orchestration.play import PlayService

Attribute = Literal["st", "dx", "iq", "ht"]


def attribute_penalty(
    state: ResourceState, actor: str, attribute: Attribute, *, level: int = 4
) -> ResourceState:
    source = f"source:{actor}:{attribute}"
    return state.model_copy(
        update={
            "symptom_effects": state.symptom_effects
            + (
                SymptomEffect(
                    id=source,
                    pool_id=f"hp:{actor}",
                    source_id=source,
                    actor_id=actor,
                    active=True,
                    spec=SymptomSpec(kind="attribute-penalty", attribute=attribute, level=level),
                ),
            ),
            "symptom_debts": state.symptom_debts
            + (SymptomDebt(id=source, pool_id=f"hp:{actor}", source_id=source, remaining=6),),
        }
    )


def penalize(state: PlayState, actor: str, attribute: Attribute = "dx") -> PlayState:
    return state.model_copy(
        update={"resources": attribute_penalty(state.resources, actor, attribute)}
    )


@pytest.mark.parametrize("trained", [False, True])
async def test_all_canonical_governing_attributes_and_defaults(
    tmp_path: Path, trained: bool
) -> None:
    definitions = tuple(
        RuleDefinition(
            f"skill:probe-{attribute.name.lower()}",
            DefinitionKind.SKILL,
            attribute.name,
            "sjg:basic-set-characters-4e-2004",
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "check.target"),
            skill=SkillSpec(
                attribute,
                Difficulty.AVERAGE,
                "B421",
                defaults=(SkillDefault(attribute, -5),),
            ),
        )
        for attribute in ControllingAttribute
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=definitions,
        extra_purchases=tuple(Purchase(definition_id=d.id, amount=4) for d in definitions)
        if trained
        else (),
    )
    state = play._load(await play.store.read(cid))
    original = build(play.rules_context, state, "a")
    for attribute in ("st", "dx", "iq", "ht"):
        state = penalize(state, "a", attribute)
    ordinary = build(play.rules_context, state, "a")
    defensive = build(play.rules_context, state, "a", defensive=True)
    for definition in definitions:
        before = next(v.value for v in original.sheet.values if v.target == definition.id)
        assert before == (11 if trained else 5)
        assert (
            next(v.value for v in ordinary.sheet.values if v.target == definition.id) == before - 4
        )
        assert next(v.value for v in defensive.sheet.values if v.target == definition.id) == before
    assert original.statistics and ordinary.statistics and defensive.statistics
    assert (ordinary.statistics.will, ordinary.statistics.per) == (6, 6)
    assert (defensive.statistics.will, defensive.statistics.per) == (10, 10)
    for name in ("hp", "fp", "basic_speed", "basic_move", "dodge"):
        assert getattr(ordinary.statistics, name) == getattr(original.statistics, name)
    # The existing B36 ST/lift projection still governs equipment eligibility.
    assert defensive.statistics.st == ordinary.statistics.st == 6
    assert defensive.statistics.basic_lift == ordinary.statistics.basic_lift


@pytest.mark.parametrize("selected,expected", [("dodge", 9), ("parry", 10), ("block", 10)])
async def test_actual_melee_roll_and_active_defense_targets(
    tmp_path: Path, selected: Defense, expected: int
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    # The unresolved attack was declared before either effect appeared.
    current = penalize(penalize(state, "a"), "b")
    runtime = replace(play.rules_context, rng=RecordedDice([3] * 60))
    _, _, trace = resolve_melee(runtime, current, current.encounters[0], selected, None)
    assert trace.attack.effective_target == 9
    assert trace.attack.dice == (3, 3, 3)
    assert trace.defense and trace.defense.effective_target == expected
    recovered = current.model_copy(
        update={
            "resources": current.resources.model_copy(
                update={
                    "symptom_effects": tuple(
                        e.model_copy(update={"active": False})
                        for e in current.resources.symptom_effects
                    )
                }
            )
        }
    )
    _, _, trace = resolve_melee(
        replace(runtime, rng=RecordedDice([3] * 60)),
        recovered,
        recovered.encounters[0],
        selected,
        None,
    )
    assert trace.attack.effective_target == 13
    assert trace.defense and trace.defense.effective_target == expected


async def test_unarmed_dx_and_trained_parry_inputs_are_exempt(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, unarmed_fixture=True)
    state = play._load(await play.store.read(cid))
    current = penalize(state, "b")
    assert parry_candidates(
        play.rules_context, current, current.encounters[0], "b"
    ) == parry_candidates(play.rules_context, state, state.encounters[0], "b")
    assert (8, "attribute:dx") in parry_candidates(
        play.rules_context, current, current.encounters[0], "b"
    )


async def test_exemption_preserves_blindness_fatigue_injury_and_equipment(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    state = penalize(play._load(await play.store.read(cid)), "b")
    defender = state.encounters[0].participants[1]
    blind = SymptomEffect(
        id="blind",
        pool_id="hp:b",
        source_id="blind",
        actor_id="b",
        active=True,
        spec=SymptomSpec(kind="blindness"),
    )
    blind_state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"symptom_effects": state.resources.symptom_effects + (blind,)}
            )
        }
    )
    value, _ = standard_defense_value(play.rules_context, blind_state, defender, "parry")
    assert value and value.value == 6
    for pool_id in ("hp:b", "fp:b"):
        impaired = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 2}) if p.id == pool_id else p
                            for p in state.resources.pools
                        )
                    }
                )
            }
        )
        value, _ = standard_defense_value(play.rules_context, impaired, defender, "dodge")
        assert value and value.value == 5
    empty = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ready": False}) for i in state.resources.items
                    )
                }
            )
        }
    )
    with pytest.raises(ValidationError, match="No available"):
        standard_defense_value(play.rules_context, empty, defender, "parry")


async def test_authored_fright_keeps_approved_will_with_current_iq_penalty(tmp_path: Path) -> None:
    cid, play = await social_prepare(tmp_path)
    state = penalize(play._load(await play.store.read(cid)), "a", "iq")
    play.rng = RecordedDice([3] * 30)
    updated = social_occurrence(
        play, state, "npc", NPCSocialTrigger(kind="fright", subject_id="a"), "fear"
    )
    result = next(
        json.loads(e.kind) for e in updated.resources.events if e.id.startswith("social:")
    )
    assert result["private"]["check"]["effective_target"] == 10
    assert result["private"]["check"]["outcome"] == "success"
    ordinary = build(play.rules_context, state, "a")
    assert ordinary.statistics and ordinary.statistics.will == 6


@pytest.mark.parametrize("actor,targets", [("a", (6, 10)), ("b", (10, 10))])
def test_malediction_exempts_only_resisting_will_and_reuses_completed_rolls(
    actor: str, targets: tuple[int, int]
) -> None:
    attacking = attacker(pick("enhancement:malediction", option="1"), kind="tox")
    target, compiler = approved()
    state = attribute_penalty(resources(), actor, "iq")
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=0, maneuver="concentrate", defense="none"
    )
    action = command()
    updated, result = apply_composed_attack(
        state,
        world(),
        action,
        attacking,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice([3] * 30),
        authorized_actor_id="a",
        system=True,
    )
    assert tuple(c.effective_target for c in result.checks) == targets
    assert result.outcome == "resisted"
    healed = updated.model_copy(update={"symptom_effects": ()})
    assert apply_composed_attack(
        healed,
        world(),
        action,
        attacking,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    ) == (healed, result)
    with pytest.raises(ValidationError, match="authority"):
        apply_composed_attack(
            state,
            world(),
            action,
            attacking,
            target,
            compiler.definitions,
            context,
            rng=RecordedDice([]),
            authorized_actor_id="b",
            system=True,
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_attack_uses_current_symptoms_then_exact_retry_is_frozen(
    tmp_path: Path, backend: str
) -> None:
    cid, original = await setup(tmp_path / "source", "gurps-basic-set-4e-2004", human=True)
    await attack(cid, original)
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice([3] * 60))
    await seed_campaign(play.store, await original.store.read(cid))
    await change(play, cid, lambda state: penalize(penalize(state, "a"), "b"))
    before = await play.store.read(cid)
    value = ChooseDefense(
        id="defend",
        actor_id="b",
        expected_revision=before["revision"],
        encounter_id="fight",
        defense="parry",
    )
    service = CombatService(play)
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, value, principal_id="a")
    assert await play.store.read(cid) == before
    result = await service.execute(cid, value, principal_id="b")
    assert result.injury and result.injury.attack.effective_target == 9
    assert result.injury.defense and result.injury.defense.effective_target == 10
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"symptom_effects": (), "symptom_debts": ()}
                )
            }
        ),
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    restarted = PlayService(play.store, play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, value, principal_id="b") == result
    with pytest.raises(ConflictError, match="different input"):
        await CombatService(restarted).execute(
            cid, value.model_copy(update={"defense": "dodge"}), principal_id="b"
        )
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == stream


def test_pre_correction_malediction_receipt_keeps_recorded_target_and_outcome() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/gurps/symptom-malediction-legacy.json").read_text()
    )
    assert fixture["source_commit"] == "3589468dcae9d92641ad2942ae8a725c52644145"
    state = ResourceState.model_validate_json(json.dumps(fixture["state_after"]))
    expected = TraitAttackOutcome.model_validate_json(json.dumps(fixture["result"]))
    assert [c.effective_target for c in expected.checks] == [10, 6]
    assert expected.outcome == "injured"
    target, compiler = approved()
    attacking = attacker(pick("enhancement:malediction", option="1"), kind="tox")
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=0, maneuver="concentrate", defense="none"
    )
    assert apply_composed_attack(
        state,
        world(),
        command(),
        attacking,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    ) == (state, expected)


async def test_defensive_projection_keeps_permanent_purchased_attribute_levels(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    state = play._load(await play.store.read(cid))
    actor = state.actors[0]
    draft = actor.proposal.draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 8}) if p.definition_id == "attribute:dx" else p
                for p in actor.proposal.draft.purchases
            )
        }
    )
    compiler = play.engine.reviewer.compiler
    permanent = compiler.compile(draft)
    assert permanent.build and permanent.build.statistics
    current = projected_build(
        attribute_penalty(state.resources, "a", "dx"),
        "a",
        permanent.build,
        compiler.definitions,
        defensive=True,
    )
    assert current.statistics and current.statistics.dx == permanent.build.statistics.dx == 8
    assert next(v.value for v in current.sheet.values if v.target == "skill:broadsword") == 11
