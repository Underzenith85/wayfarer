"""B556 action ban reaches ordinary turns and genuinely free unarmed actions."""

import pytest
from test_attack_defense_traits import world
from test_innate_criticals import context, fight, initial, miss, table
from test_resources import engine as resource_engine

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.unarmed.fighters import guard_control
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.innate_criticals import (
    require_innate_actor_action,
    resolve_innate_miss,
)
from wayfarer.errors import ValidationError


def lost_balance() -> tuple[ResourceState, Encounter, PlayState]:
    resources, encounter, _ = resolve_innate_miss(
        initial(), fight(), context(), miss(), rng=RecordedDice(table(16)), system=True
    )
    encounter = encounter.model_copy(update={"pending_defense": None})
    state = PlayState(
        campaign_id="campaign",
        configuration_digest="fixture",
        world=world(),
        resources=resources,
        encounters=(encounter,),
        actors=(),
    )
    return resources, encounter, state


def engine() -> CombatEngine:
    return CombatEngine(
        CombatRules(
            id="guard",
            version=1,
            battlefields=(Battlefield(id="room", location_id="room", width=10, height=10),),
        ),
        resource_engine(),
    )


@pytest.mark.parametrize("action", ["release", "lock_damage", "punch"])
def test_innate_balance_blocks_unarmed_actions_before_grip_lookup(action: str) -> None:
    _, encounter, state = lost_balance()
    command = TakeUnarmedTurn.model_validate(
        {
            "id": action,
            "actor_id": "a",
            "target_id": "b",
            "encounter_id": "fight",
            "expected_revision": 0,
            "action": action,
            "grip_id": "unused",
        }
    )
    with pytest.raises(ValidationError, match="even free actions"):
        guard_control(encounter, command, state)


@pytest.mark.parametrize(
    "changes",
    [
        {"maneuver": "attack"},
        {"crouch": "before"},
        {"facing": "east"},
        {"relinquish_stuck_weapon_id": "unused"},
    ],
)
def test_innate_balance_blocks_maneuver_and_disguised_free_actions(changes: dict[str, str]) -> None:
    _, encounter, state = lost_balance()
    command = TakeCombatTurn.model_validate(
        {
            "id": "turn",
            "actor_id": "a",
            "encounter_id": "fight",
            "expected_revision": 0,
            "maneuver": "do_nothing",
            **changes,
        }
    )
    with pytest.raises(ValidationError, match="even free actions"):
        guard_control(encounter, command, state)


def test_innate_balance_preserves_pure_do_nothing_and_active_defense() -> None:
    resources, encounter, state = lost_balance()
    passive = TakeCombatTurn(
        id="idle", actor_id="a", encounter_id="fight", expected_revision=0, maneuver="do_nothing"
    )
    guard_control(encounter, passive, state)
    defense = ChooseDefense(
        id="defend", actor_id="a", encounter_id="fight", expected_revision=0, defense="dodge"
    )
    guard_control(encounter, defense, state)
    assert encounter.participants[0].defense_penalty == -2
    after, _, result = engine().take_turn(
        encounter, resources=resources, actor_id="a", maneuver="do_nothing", command_id="idle"
    )
    assert result.current_actor_id == "b" and after.current_actor_id == "b"


def test_direct_combat_engine_cannot_bypass_guard_before_posture_or_movement() -> None:
    resources, encounter, _ = lost_balance()
    reducer = engine()
    with pytest.raises(ValidationError, match="even free actions"):
        reducer.take_turn(
            encounter,
            resources=resources,
            actor_id="a",
            maneuver="move",
            destination=GridPoint(x=1, y=2),
            command_id="move",
        )
    with pytest.raises(ValidationError, match="even free actions"):
        reducer.take_turn(
            encounter,
            resources=resources,
            actor_id="a",
            maneuver="do_nothing",
            crouch="before",
            command_id="free-crouch",
        )


def test_first_next_turn_move_is_legal_without_injury_turn_increment() -> None:
    resources, encounter, state = lost_balance()
    encounter = CombatEngine._advance(CombatEngine._advance(encounter))
    command = TakeCombatTurn(
        id="move",
        actor_id="a",
        encounter_id="fight",
        expected_revision=0,
        maneuver="move",
        destination=GridPoint(x=1, y=2),
    )
    guard_control(encounter, command, state.model_copy(update={"encounters": (encounter,)}))
    after, _, result = engine().take_turn(
        encounter,
        resources=resources,
        actor_id="a",
        maneuver="move",
        destination=command.destination,
        command_id="move",
    )
    assert result.current_actor_id == "b"
    assert after.participants[0].position == GridPoint(x=1, y=2)
    assert after.participants[0].defense_penalty == 0


@pytest.mark.parametrize("removed", [False, True])
def test_ended_encounter_balance_cannot_be_evaded_or_become_permanent(removed: bool) -> None:
    resources, encounter, state = lost_balance()
    ended = encounter.model_copy(update={"status": "completed", "completion_reason": "fixture"})
    state = state.model_copy(update={"encounters": () if removed else (ended,)})
    with pytest.raises(ValidationError, match="even free actions"):
        require_innate_actor_action(state, "a")
    # A legitimate time-only Wait/Do Nothing may advance the ordinary world clock.
    state = state.model_copy(update={"resources": resources.model_copy(update={"game_time": 1})})
    require_innate_actor_action(state, "a")
    assert next(p for p in state.resources.pools if p.id == "hp:a").injury.turn == 0  # type: ignore[union-attr]
