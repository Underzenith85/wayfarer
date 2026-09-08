"""Independent B364-366/B385/B390/B412/B417 maneuver expectations for #152."""

from pathlib import Path

from test_gurps_maneuvers import defend, turn
from test_gurps_ranged import scene, weapon
from test_tactical import migration
from test_tactical import setup as tactical_setup

from wayfarer.models import Campaign, Event
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.combat import GridPoint
from wayfarer.simulation.hex_geometry import Hex
from wayfarer.simulation.resources import Item


async def test_variable_scope_and_bipod_bracing_add_independent_aim_bonuses(
    tmp_path: Path,
) -> None:
    ranged = weapon(bow=True).model_copy(
        update={"hands": 2, "brace_kind": "bipod", "scope_bonus": 2}
    )
    from test_gurps_melee import setup

    cid, play = await setup(tmp_path, ranged_mode=ranged, ranged_scene=scene())
    await turn(cid, play, "a", "change_posture", posture="prone")
    await turn(cid, play, "b", "do_nothing")
    await turn(
        cid,
        play,
        "a",
        "aim",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        braced=True,
    )
    actor = play._load(await play.store.read(cid)).encounters[0].participants[0]
    # Acc 2, first-second Aim +0, brace +1, variable scope +1.
    assert actor.maneuver_state.aim_bonus == 4


async def test_fixed_scope_requires_its_full_aim_time(tmp_path: Path) -> None:
    ranged = weapon(bow=True).model_copy(update={"scope_bonus": 2, "fixed_power_scope": True})
    from test_gurps_melee import setup

    cid, play = await setup(tmp_path, ranged_mode=ranged, ranged_scene=scene())
    await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
    actor = play._load(await play.store.read(cid)).encounters[0].participants[0]
    assert actor.maneuver_state.aim_bonus == 2
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
    actor = play._load(await play.store.read(cid)).encounters[0].participants[0]
    # Acc 2, second-second Aim +1, fixed scope +2.
    assert actor.maneuver_state.aim_bonus == 5


async def test_attack_then_step_is_deferred_until_defense_and_replays_once(tmp_path: Path) -> None:
    cid, play = await tactical_setup(tmp_path)
    command = TakeCombatTurn(
        id="attack-then-step",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        step_timing="after",
        hex_path=(Hex(q=-1, r=0),),
    )
    service = CombatService(play)
    await service.execute(cid, command, authenticated_actor_id="a")
    pending = play._load(await play.store.read(cid)).encounters[0]
    assert pending.participants[0].position == Hex(q=0, r=0)
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    saved = play._load(await play.store.read(cid)).encounters[0]
    assert saved.participants[0].position == Hex(q=-1, r=0)
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = CombatService(
        type(play)(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice(()))
    )
    result = await restarted.execute(cid, command, authenticated_actor_id="a")
    assert result.code == "combat.defense_required"


async def test_square_attack_then_step_applies_position_and_facing_after_defense(
    tmp_path: Path,
) -> None:
    from test_gurps_melee import setup

    cid, play = await setup(tmp_path)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        step_timing="after",
        destination={"x": 0, "y": 1},
        facing="north",
    )
    pending = play._load(await play.store.read(cid)).encounters[0]
    assert pending.participants[0].position == GridPoint(x=0, y=0)
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    saved = play._load(await play.store.read(cid)).encounters[0]
    assert saved.participants[0].position == GridPoint(x=0, y=1)
    assert saved.participants[0].facing == "north"


async def test_observable_zone_wait_persists_entry_and_resumes_once(tmp_path: Path) -> None:
    cid, play = await tactical_setup(tmp_path)
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "move",
            "zone": ((1, -1),),
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    result = await turn(cid, play, "b", "move", hex_path=({"q": 1, "r": -1},))
    assert result.code == "combat.wait_triggered"
    encounter = play._load(await play.store.read(cid)).encounters[0]
    assert encounter.participants[1].position == Hex(q=1, r=-1)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    encounter = play._load(await play.store.read(cid)).encounters[0]
    assert encounter.wait_interrupt is not None and encounter.wait_interrupt.ready


async def test_stop_thrust_interrupts_charge_and_adds_one_per_two_yards(tmp_path: Path) -> None:
    cid, play = await tactical_setup(tmp_path, migrate=False)
    moved_migration = migration().model_copy(
        update={
            "placements": tuple(
                placement.model_copy(
                    update={"pose": placement.pose.model_copy(update={"position": Hex(q=3, r=0)})}
                )
                if placement.actor_id == "b"
                else placement
                for placement in migration().placements
            )
        }
    )
    await CombatService(play).execute(cid, moved_migration, authenticated_actor_id="gm")
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "attack",
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "thrust",
            "stop_thrust": True,
        },
    )

    def set_declared_reach(campaign: Campaign) -> Event:
        state = play._load(campaign)
        encounter = state.encounters[0]
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    participant.model_copy(update={"reach": 2})
                    if participant.actor_id == "a"
                    else participant
                    for participant in encounter.participants
                )
            }
        )
        campaign["play_json"] = state.model_copy(
            update={"encounters": (encounter,)}
        ).model_dump_json()
        return Event(input="fixture", action="combat", outcome="declared reach", roll=None)

    await play.store.commit_turn(cid, "declared-reach", 3, "declared-reach", set_declared_reach)
    prepared = play._load(await play.store.read(cid)).encounters[0]
    assert prepared.participants[0].reach == 2
    result = await turn(
        cid,
        play,
        "b",
        "move_and_attack",
        item_id="sword-b",
        target_id="a",
        mode_id="swing",
        hex_path=({"q": 2, "r": 0}, {"q": 1, "r": 0}),
    )
    assert result.code == "combat.wait_triggered"
    encounter = play._load(await play.store.read(cid)).encounters[0]
    assert encounter.participants[1].position == Hex(q=1, r=0)
    assert encounter.participants[0].maneuver_state.stop_thrust_damage_bonus == 1
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="thrust")
    play.rng = RecordedDice((4, 4, 4, 2))
    resolved = await defend(cid, play, "b")
    assert resolved.injury is not None and resolved.injury.basic_damage == 2


async def two_weapon_double(tmp_path: Path, *, traits: tuple[str, ...] = ()) -> int:
    """Resolve an All-Out Attack (Double) and return the off-hand attack's target."""
    from test_gurps_melee import setup

    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, traits=traits)

    def add_left_weapon(campaign: Campaign) -> Event:
        state = play._load(campaign)
        item = Item(
            id="sword-a-left",
            definition_id="equipment:broadsword",
            owner_id="a",
            equipped=True,
            ready=True,
        )
        encounter = state.encounters[0]
        actor = encounter.participants[0]
        actor = actor.model_copy(
            update={
                "ready_item_ids": actor.ready_item_ids + (item.id,),
                "hand_bindings": actor.hand_bindings + ((item.id, "left-hand"),),
            }
        )
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    actor if participant.actor_id == "a" else participant
                    for participant in encounter.participants
                )
            }
        )
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"items": state.resources.items + (item,)}
                ),
                "encounters": (encounter,),
                "actors": tuple(
                    setup_actor.model_copy(
                        update={
                            "held_item_hands": setup_actor.held_item_hands
                            + ((item.id, "left-hand"),)
                        }
                    )
                    if setup_actor.actor_id == "a"
                    else setup_actor
                    for setup_actor in state.actors
                ),
            }
        )
        campaign["play_json"] = state.model_dump_json()
        return Event(input="fixture", action="combat", outcome="left weapon", roll=None)

    await play.store.commit_turn(cid, "left-weapon", 1, "left-weapon", add_left_weapon)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="double",
        second_item_id="sword-a-left",
        second_target_id="b",
        second_mode_id="swing",
    )
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    pending = play._load(await play.store.read(cid)).encounters[0].pending_defense
    assert pending is not None and pending.weapon_id == "sword-a-left"
    play.rng = RecordedDice((4, 4, 4, 2))
    result = await defend(cid, play, "b")
    assert result.injury is not None
    return result.injury.attack.effective_target


async def test_two_weapon_double_uses_declared_off_hand_with_minus_four(tmp_path: Path) -> None:
    assert await two_weapon_double(tmp_path) == 9  # Broadsword 13, off hand at -4.


async def test_ambidexterity_removes_the_off_hand_penalty(tmp_path: Path) -> None:
    """B39 Ambidexterity [5]: no -4 for the off hand, so both attacks use skill 13."""
    assert await two_weapon_double(tmp_path, traits=("trait:ambidexterity",)) == 13
