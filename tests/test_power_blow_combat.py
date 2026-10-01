"""B215 Power Blow changes actual immediate melee damage, once."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_gurps_melee import choice, setup
from test_mastery_combat import DEFINITIONS, purchase
from test_statistics import profile_package

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.cinematic import package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.skills.power_blow import PowerBlowCommand, activate_power_blow
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn, TakeUnarmedTurn

POWER = tuple(
    replace(d, source_id=profile_package("gurps-basic-set-4e-2004").sources[0].id)
    for d in package().definitions
    if d.id == "skill:power-blow"
)


@pytest.mark.parametrize("attack_id", ["attack", "a" * 200])
@pytest.mark.parametrize("success,basic", [(True, 9), (False, 3)])
@pytest.mark.parametrize("unarmed", [False, True])
@pytest.mark.parametrize("matching", [False, True])
async def test_power_blow_changes_real_weapon_damage_and_failure_still_costs_fp(
    tmp_path: Path,
    success: bool,
    basic: int,
    unarmed: bool,
    matching: bool,
    attack_id: str,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=DEFINITIONS + POWER,
        unarmed_fixture=unarmed,
        human=unarmed,
        free_defender_hand=unarmed,
        extra_purchases=(
            purchase("trained-by-a-master"),
            Purchase(definition_id="skill:power-blow", amount=48),
        ),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    play.rng = RecordedDice([2, 2, 2] if success else [4, 4, 4])

    def activate(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        build = play.rules_context.approved_build(state, "a")
        changed, result = activate_power_blow(
            play.rules_context,
            state,
            PowerBlowCommand(
                id="power",
                actor_id="a",
                expected_revision=state.resources.revision,
                build_revision=build.revision,
                encounter_id="fight",
                attack_command_id=attack_id if matching else "another-attack",
            ),
            authorized_actor_id="a",
        )
        retry = PowerBlowCommand(
            id="power",
            actor_id="a",
            expected_revision=state.resources.revision,
            build_revision=build.revision,
            encounter_id="fight",
            attack_command_id=attack_id if matching else "another-attack",
        )
        assert activate_power_blow(play.rules_context, changed, retry, authorized_actor_id="a") == (
            changed,
            result,
        )
        with pytest.raises(AuthorizationError):
            activate_power_blow(play.rules_context, changed, retry, authorized_actor_id="b")
        with pytest.raises(ConflictError):
            activate_power_blow(
                play.rules_context,
                changed,
                retry.model_copy(
                    update={"id": "stack", "expected_revision": changed.resources.revision}
                ),
                authorized_actor_id="a",
            )
        assert result.fatigue_spent == 1
        assert next(p.current for p in changed.resources.pools if p.id == "fp:a") == 9
        campaign["play_json"] = changed.model_copy(
            update={"revision": state.revision + 1}
        ).model_dump_json()
        campaign["revision"] += 1
        return CommandReceipt(action="combat", outcome="power prepared")

    await play.store.commit_turn(cid, "power-fixture", 1, "fixture", activate)
    if unarmed:
        await CombatService(play).execute(
            cid,
            TakeUnarmedTurn(
                id=attack_id,
                actor_id="a",
                expected_revision=2,
                encounter_id="fight",
                maneuver="attack",
                action="punch",
                target_id="b",
                hands=("left-hand",),
                enter_close_combat=True,
            ),
            principal_id="a",
        )
    else:
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id=attack_id,
                actor_id="a",
                expected_revision=2,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="swing",
                target_id="b",
            ),
            principal_id="a",
        )
    play.rng = RecordedDice(
        [3, 3, 3]
        + (
            ([2, 2] if success and matching else [2])
            if unarmed
            else ([2, 2, 2, 3, 3, 3] if success and matching else [2])
        )
    )
    defense = choice().model_copy(update={"expected_revision": 3})
    result = await CombatService(play).execute(cid, defense, principal_id="b")
    if unarmed:
        assert result.unarmed is not None and result.unarmed.basic_damage == (
            2 if success and matching else 0
        )
    else:
        assert result.injury is not None and result.injury.basic_damage == (
            basic if matching else 3
        )
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, defense, principal_id="b") == result


@pytest.mark.parametrize("lift_id", ["lift", "l" * 200])
@pytest.mark.parametrize("success,capacity", [(True, "640"), (False, "160")])
async def test_power_blow_changes_actual_authored_lift_capacity(
    tmp_path: Path, success: bool, capacity: str, lift_id: str
) -> None:
    from decimal import Decimal

    from wayfarer.engine.simulation.skills.power_blow import (
        PowerBlowLiftCommand,
        activate_power_blow_lift,
    )
    from wayfarer.orchestration.physical import PhysicalCommand, PhysicalRoute, PhysicalService

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        start_encounter=False,
        extra_definitions=DEFINITIONS + POWER,
        extra_purchases=(
            purchase("trained-by-a-master"),
            Purchase(definition_id="skill:power-blow", amount=48),
        ),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    campaign = await play.store.read(cid)
    before = play._load(campaign)
    scene = next(e.location_id for e in before.world.entities if e.id == "a")
    assert scene is not None
    route = PhysicalRoute(id="heavy-object", scene_id=scene, pounds=Decimal(300))
    play.rng = RecordedDice([2, 2, 2] if success else [4, 4, 4])

    def activate(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        build = play.rules_context.approved_build(state, "a")
        changed, result = activate_power_blow_lift(
            play.rules_context,
            state,
            PowerBlowLiftCommand(
                id="power",
                actor_id="a",
                expected_revision=state.resources.revision,
                build_revision=build.revision,
                route_id=route.id,
                lift_command_id=lift_id,
            ),
            route,
            authorized_actor_id="a",
        )
        assert result.fatigue_spent == 1
        campaign["revision"] += 1
        campaign["play_json"] = changed.model_copy(
            update={"revision": campaign["revision"]}
        ).model_dump_json()
        return CommandReceipt(action="combat", outcome="power prepared")

    await play.store.commit_turn(cid, "power-fixture", campaign["revision"], "fixture", activate)
    campaign = await play.store.read(cid)
    play.rng = RecordedDice([])
    service = PhysicalService(play, lambda *_: route)
    command = PhysicalCommand(
        id=lift_id,
        actor_id="a",
        expected_revision=campaign["revision"],
        kind="lift",
        route_id=route.id,
    )
    result = await service.execute(cid, command, principal_id="a")
    assert (
        result.capacity == capacity and result.succeeded == success and result.completed == success
    )
    assert await service.execute(cid, command, principal_id="a") == result
