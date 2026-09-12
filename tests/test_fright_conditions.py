"""B361/B421/B428 checks across live combat and forced injury/fatigue paths."""

from pathlib import Path
from typing import Literal

import pytest
from test_fright_builds import install
from test_gurps_melee import setup
from test_social_completion import with_aftermath

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import ContinueExertion, apply_fatigue
from wayfarer.engine.simulation.health.fright import effects
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def aftermath(cid: str, play: PlayService) -> None:
    state = play._load(await play.store.read(cid))

    def reduce(campaign: Campaign) -> CommandReceipt:
        revision = state.revision + 1
        resources = with_aftermath(state.resources).model_copy(update={"revision": revision})
        updated = state.model_copy(update={"revision": revision, "resources": resources})
        campaign["revision"], campaign["play_json"] = revision, updated.model_dump_json()
        return CommandReceipt(action="npc", outcome="recovered")

    await play.store.commit_turn(
        cid, "aftermath", state.revision, "aftermath", reduce, actor_id="gm"
    )


@pytest.mark.parametrize("condition,target", [("aftermath", 11), ("retching", 8)])
async def test_live_attack_penalty_without_defense_or_build_changes(
    tmp_path: Path, condition: str, target: int
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    before = play._load(await play.store.read(cid))
    if condition == "aftermath":
        await aftermath(cid, play)
    else:
        await install(
            cid,
            play,
            FrightEffect(
                table_total=12,
                condition="retching",
                recovery_attribute="ht",
                recovery_interval_seconds=1,
            ),
        )
    state = play._load(await play.store.read(cid))
    actor = state.encounters[0].participants[0]
    value, _ = defense_value(play.rules_context, state, actor, "dodge")
    assert value is not None and value.value == 8
    assert state.actors == before.actors
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        authenticated_actor_id="a",
    )
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(
        AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([3, 3, 3, 1])
    )
    result = await CombatService(restarted).execute(
        cid,
        ChooseDefense(
            id="defense", actor_id="b", expected_revision=3, encounter_id="fight", defense="none"
        ),
        authenticated_actor_id="b",
    )
    assert result.injury is not None and result.injury.attack.effective_target == target
    assert result.injury.attack.modifiers[0].value == (-2 if condition == "aftermath" else -5)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("condition", ["stunned", "panic", "retching"])
async def test_condition_specific_maneuvers(
    tmp_path: Path, condition: Literal["stunned", "panic", "retching"]
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await install(
        cid,
        play,
        FrightEffect(
            table_total={"stunned": 7, "panic": 21, "retching": 12}[condition],
            condition=condition,
            recovery_attribute="will",
            recovery_interval_seconds=1,
        ),
    )
    state = play._load(await play.store.read(cid))
    blocked = TakeCombatTurn(
        id="invalid",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="concentrate",
    )
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="does not permit"):
        await CombatService(play).execute(cid, blocked, authenticated_actor_id="a")
    assert await play.store.read(cid) == saved
    if condition == "panic":
        await CombatService(play).execute(
            cid,
            blocked.model_copy(
                update={"id": "flee", "maneuver": "move", "destination": GridPoint(x=0, y=1)}
            ),
            authenticated_actor_id="a",
        )
        after = play._load(await play.store.read(cid))
        assert after.encounters[0].participants[0].position == GridPoint(x=0, y=1)
    elif condition == "stunned":
        await CombatService(play).execute(
            cid,
            blocked.model_copy(update={"id": "wait-turn", "maneuver": "do_nothing"}),
            authenticated_actor_id="a",
        )
    else:
        assert [m.value for m in check_modifiers(state.resources, "a", "will")] == [-5]
        assert check_modifiers(state.resources, "a", "will", defensive=True) == ()
        assert check_modifiers(state.resources, "a", "ht") == ()


async def test_aftermath_applies_once_to_injury_and_exertion_at_check_time(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    resources = with_aftermath(play._load(await play.store.read(cid)).resources)
    wounded, result = apply_injury(
        resources,
        Wound(
            id="major",
            actor_id="a",
            expected_revision=resources.revision,
            basic_damage=6,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.checks[0].check.effective_target == 8
    assert result.checks[0].check.margin == -1
    # Use the original uninjured state so consciousness/shock does not mask exertion.
    depleted = resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                for p in resources.pools
            )
        }
    )
    _, fatigue = apply_fatigue(
        depleted,
        ContinueExertion(id="effort", actor_id="a", expected_revision=depleted.revision),
        ht=10,
        will=10,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert not fatigue.allowed and fatigue.checks[0].effective_target == 8
    expired = depleted.model_copy(update={"game_time": 21600})
    assert check_modifiers(expired, "a", "will") == ()
    assert effects(wounded)[0].aftermath_until == 21600


@pytest.mark.parametrize(
    "skill,penalty,expected", [(20, -2, 9), (12, -2, 6), (20, -5, 9), (12, -5, 3)]
)
def test_b365_move_and_attack_cap_is_after_condition_penalties(
    skill: int, penalty: int, expected: int
) -> None:
    from wayfarer.engine.simulation.combat.maneuvers import ManeuverState, attack_modifier

    maneuver = ManeuverState(attack_bonus=-4, attack_cap=9)
    base = attack_modifier(maneuver, "target", skill, check_adjustment=penalty)
    assert base + penalty == expected


@pytest.mark.parametrize(
    "trait,kind,expected", [("acute-vision", "sense", 9), ("very-fit", "ht", 10)]
)
async def test_aftermath_combines_with_new_physical_trait_checks(
    tmp_path: Path, trait: str, kind: Literal["sense", "ht"], expected: int
) -> None:
    import json

    from test_mundane_trait_runtime import prepare

    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.orchestration.physical_checks import (
        PhysicalCheck,
        PhysicalCheckCommand,
        PhysicalCheckService,
    )

    cid, play = await prepare(tmp_path, Purchase(definition_id="trait:" + trait))
    await aftermath(cid, play)
    service = PhysicalCheckService(play, lambda *_: PhysicalCheck(kind))
    command = PhysicalCheckCommand(
        id="observe", actor_id="a", expected_revision=1, trigger_id="scene-check"
    )
    await service.execute(cid, command, gm_id="gm")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("physical-check:"))
    assert json.loads(event.kind)["effective_target"] == expected
    assert await play.store.read(cid) == await play.store.replay(cid)
