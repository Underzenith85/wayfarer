"""B174/B34/B74 approved trait bonuses change actual B375 Dodge consequences."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_combat_sensory_authority import change
from test_gurps_melee import attack, setup
from test_mastery_combat import purchase
from test_statistics import profile_package

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.rules.types.special_combat import PersonalFlightState
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.play import PlayService


async def reaction_fixture(
    tmp_path: Path, backend: str, flying: bool, traits: tuple[str, ...]
) -> tuple[str, PlayService]:
    source = profile_package("gurps-basic-set-4e-2004").sources[0].id
    definitions = tuple(
        replace(d, source_id=source)
        for d in candidate_package().definitions
        if d.id in {"trait:advantage:" + t for t in traits}
    )
    skill = RuleDefinition(
        "skill:aerobatics" if flying else "skill:acrobatics",
        DefinitionKind.SKILL,
        "Acrobatics",
        source,
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.DX, Difficulty.HARD, "B174"),
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        extra_definitions=(*definitions, skill),
        extra_purchases=(
            *(purchase(t) for t in traits),
            Purchase(definition_id=skill.id, amount=1),
        ),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    play.rng = RecordedDice([3, 3, 3])
    await attack(cid, play)
    if flying:

        def flight(state: PlayState) -> PlayState:
            encounter = state.encounters[0]
            return state.model_copy(
                update={
                    "encounters": (
                        encounter.model_copy(
                            update={
                                "participants": tuple(
                                    p.model_copy(
                                        update={
                                            "personal_flight": PersonalFlightState(
                                                altitude=1, basic_air_move=5, top_air_speed=10
                                            )
                                        }
                                    )
                                    if p.actor_id == "b"
                                    else p
                                    for p in encounter.participants
                                )
                            }
                        ),
                    )
                }
            )

        await change(play, cid, flight)
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "flying,traits,bonus,acro",
    [
        (False, ("perfect-balance",), 1, (3, 3, 3)),
        (True, ("perfect-balance",), 1, (3, 3, 3)),
        (True, ("3d-spatial-sense",), 2, (3, 3, 4)),
        (True, ("perfect-balance", "3d-spatial-sense"), 3, (3, 4, 4)),
        (False, ("3d-spatial-sense",), 0, (2, 3, 3)),
        (False, (), 0, (2, 3, 3)),
    ],
)
async def test_approved_task_bonuses_settle_actual_dodge(
    tmp_path: Path,
    backend: str,
    flying: bool,
    traits: tuple[str, ...],
    bonus: int,
    acro: tuple[int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generations, "ACTIVE", frozenset({"grenade-fuse", "acrobatic-trait-bonuses"})
    )
    cid, play = await reaction_fixture(tmp_path, backend, flying, traits)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="acro",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale-acro", "expected_revision": state.revision - 1}
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()
    play.rng = RecordedDice([*acro, 3, 3, 3, 3, 3, 3, 1])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    updated = play._load(await play.store.read(cid))
    trace = updated.encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace is not None and trace.effective_target == 8 + bonus
    assert trace.outcome.succeeded
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 11 and result.injury.injury == 0
    assert result.injury.hp_before == result.injury.hp_after == 10
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    assert play.rng.exhausted()
