"""B375/B377 optional Dodge is an authoritative, replayable transaction."""

from pathlib import Path

import pytest
from test_gurps_melee import attack, setup
from test_gurps_ranged import scene

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.rules.types.special_combat import PersonalFlightState
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.tactical_transitions import prepare_defense
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn


@pytest.mark.parametrize("flying", [False, True])
@pytest.mark.parametrize(("roll", "bonus"), [(1, 2), (5, -2)])
async def test_acrobatic_dodge_records_one_roll_and_adjusts_actual_defense(
    tmp_path: Path, roll: int, bonus: int, flying: bool
) -> None:
    definition = RuleDefinition(
        "skill:aerobatics" if flying else "skill:acrobatics",
        DefinitionKind.SKILL,
        "Acrobatics",
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.DX, Difficulty.HARD, "B174"),
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        extra_definitions=(definition,),
        extra_purchases=(Purchase(definition_id=definition.id, amount=1),),
    )
    play.rng = RecordedDice([3, 3, 3])
    await attack(cid, play)
    seed = play._load(await play.store.read(cid))
    if flying:
        from wayfarer.engine.simulation.combat.engine import CombatEngine

        encounter = CombatEngine._replace(
            seed.encounters[0],
            seed.encounters[0]
            .participants[1]
            .model_copy(
                update={
                    "personal_flight": PersonalFlightState(
                        altitude=1, basic_air_move=5, top_air_speed=10
                    )
                }
            ),
        )
        seed = seed.model_copy(update={"encounters": (encounter,)})

        def install_flight(campaign: Campaign) -> CommandReceipt:
            updated = seed.model_copy(
                update={
                    "revision": seed.revision + 1,
                    "resources": seed.resources.model_copy(update={"revision": seed.revision + 1}),
                }
            )
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return CommandReceipt(action="combat", outcome="flight-fixture")

        await play.store.commit_turn(
            cid, "flight-fixture", seed.revision, "flight-fixture", install_flight
        )
        seed = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="acro",
        actor_id="b",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    play.rng = RecordedDice([])
    # Eligibility preview must not consume a skill roll or change persisted state.
    preview = prepare_defense(play.rules_context, seed, seed.encounters[0], command)
    assert preview.participants[1].acrobatic_dodge_trace is None
    play.rng = RecordedDice([roll, roll, roll])
    prepared = prepare_defense(
        play.rules_context, seed, seed.encounters[0], command, resolve_options=True
    )
    defender = prepared.participants[1]
    assert defender.acrobatic_dodge_trace is not None
    assert defender.acrobatic_dodge_trace.dice == (roll, roll, roll)
    score, _ = defense_value(play.rules_context, seed, defender, "dodge")
    assert score is not None and score.value == 9 + bonus
    with pytest.raises(ValidationError, match="already attempted"):
        prepare_defense(play.rules_context, seed, prepared, command)
    play.rng = RecordedDice([roll, roll, roll, 3, 3, 3, 2, 2, 2])
    service = CombatService(play)
    result = await service.execute(cid, command, principal_id="b")
    stored = play._load(await play.store.read(cid))
    trace = stored.encounters[0].participants[1].acrobatic_dodge_trace
    saved_trace = stored.encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert saved_trace is not None and saved_trace.dice == (roll, roll, roll)
    # If the encounter advances directly to b's next turn the marker resets.
    if trace is not None:
        assert trace.dice == (roll, roll, roll)
    assert play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await service.execute(cid, command, principal_id="b") == result


async def test_dodge_and_drop_has_ranged_bonus_then_persistent_prone_posture(
    tmp_path: Path,
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, ranged_fixture=True, ranged_scene=scene()
    )
    play.rng = RecordedDice([3, 3, 3])
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="throw",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="throw-fixture",
            target_id="b",
        ),
        principal_id="a",
    )
    seed = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="drop",
        actor_id="b",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        dodge_and_drop=True,
    )
    play.rng = RecordedDice([])
    prepared = prepare_defense(
        play.rules_context, seed, seed.encounters[0], command, resolve_options=True
    )
    defender = prepared.participants[1]
    assert defender.posture == "standing"
    score, _ = defense_value(play.rules_context, seed, defender, "dodge")
    assert score is not None and score.value == 12
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    stored = play._load(await play.store.read(cid))
    assert stored.encounters[0].participants[1].posture == "prone"
    assert stored.encounters[0].participants[1].tactical_defense_bonus == 0
    assert play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="b") == result


async def test_optional_dodge_rejects_absent_purchase_and_melee_drop_without_randomness(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    play.rng = RecordedDice([3, 3, 3])
    await attack(cid, play)
    seed = play._load(await play.store.read(cid))
    play.rng = RecordedDice([])
    for option, message in (
        ("acrobatic_dodge", "purchased Acrobatics"),
        ("dodge_and_drop", "ranged attacks"),
    ):
        command = ChooseDefense.model_validate(
            {
                "id": option,
                "actor_id": "b",
                "expected_revision": seed.revision,
                "encounter_id": "fight",
                "defense": "dodge",
                option: True,
            }
        )
        with pytest.raises(ValidationError, match=message):
            await CombatService(play).execute(cid, command, principal_id="b")
    assert play._load(await play.store.read(cid)).model_dump_json() == seed.model_dump_json()
    assert play.rng.exhausted()
