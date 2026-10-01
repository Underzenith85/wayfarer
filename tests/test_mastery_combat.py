"""B93/B99/B376 consequences from canonical approved mastery purchases."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_gurps_melee import attack, choice, setup
from test_statistics import profile_package

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import ChooseDefense, CombatService

DEFINITIONS = tuple(
    replace(d, source_id=profile_package("gurps-basic-set-4e-2004").sources[0].id)
    for d in candidate_package().definitions
    if d.id in {"trait:advantage:weapon-master", "trait:advantage:trained-by-a-master"}
)


def purchase(master: str) -> Purchase:
    return Purchase(
        definition_id="trait:advantage:" + master,
        trait=TraitOptions(
            parameters=(("point-cost", 20), ("weapon-scope", "broadsword"))
            if master == "weapon-master"
            else ()
        ),
    )


@pytest.mark.parametrize(
    ("master", "damage"), [(None, 3), ("trained-by-a-master", 3), ("weapon-master", 5)]
)
async def test_weapon_master_changes_actual_injury_only_for_purchased_weapon(
    tmp_path: Path, master: str | None, damage: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=DEFINITIONS,
        extra_purchases=() if master is None else (purchase(master),),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    await attack(cid, play)
    play.rng = RecordedDice([3, 3, 3, 2] + ([3, 3, 3] if master == "weapon-master" else []))
    command = choice()
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.basic_damage == damage
    assert result.injury.injury == damage * 3 // 2
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"defense": "dodge"}), principal_id="b"
        )


@pytest.mark.parametrize(
    ("master", "expected"), [(None, 6), ("trained-by-a-master", 8), ("weapon-master", 8)]
)
async def test_multiple_weapon_parry_uses_printed_master_penalty(
    tmp_path: Path, master: str | None, expected: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=DEFINITIONS,
        extra_purchases=() if master is None else (purchase(master),),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1].model_copy(update={"parries": ("sword-b",)})
    value, item = standard_defense_value(play.rules_context, state, defender, "parry", "sword-b")
    assert value is not None and int(value.value) == expected
    assert item == "sword-b"


@pytest.mark.parametrize(
    ("master", "target"), [(None, 7), ("trained-by-a-master", 10), ("weapon-master", 10)]
)
async def test_rapid_strike_two_actual_attacks_keep_defenses_and_master_penalty(
    tmp_path: Path, master: str | None, target: int
) -> None:
    from test_gurps_maneuvers import defend, turn

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=DEFINITIONS,
        extra_purchases=() if master is None else (purchase(master),),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="rapid-strike",
    )
    state = play._load(await play.store.read(cid))
    actor = state.encounters[0].participants[0]
    assert (
        actor.maneuver_state.attacks_remaining == 1 and not actor.maneuver_state.defense_forbidden
    )
    for index in range(2):
        # A roll of 8 misses ordinary Rapid Strike, hits masters without criticals.
        play.rng = RecordedDice([2, 3, 3] + ([1, 3, 3, 3, 3, 3, 3] if master is not None else []))
        result = await defend(cid, play, "b", "none")
        assert result.injury and result.injury.attack.effective_target == target
        assert result.injury.basic_damage == (
            4 if master == "weapon-master" else 2 if master else 0
        )
        state = play._load(await play.store.read(cid))
        assert state.encounters[0].current_actor_id == ("a" if index == 0 else "b")
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(
    ("cost", "scope", "legal"),
    [
        (20, "broadsword", True),
        (25, "rapier-and-main-gauche", True),
        (30, "fencing", True),
        (35, "swords", True),
        (40, "bladed", True),
        (45, "all", True),
        (20, "all", False),
        (45, "broadsword", False),
        (20, "invented", False),
    ],
)
def test_scope_price_is_enforced_by_actual_compiler(cost: int, scope: str, legal: bool) -> None:
    from test_mundane_traits import runtime_compiler
    from test_statistics import gurps_draft

    result = runtime_compiler().compile(
        gurps_draft(
            Purchase(
                definition_id="trait:advantage:weapon-master",
                trait=TraitOptions(parameters=(("point-cost", cost), ("weapon-scope", scope))),
            )
        )
    )
    assert result.legal is legal, result.diagnostics
    if legal:
        assert result.build is not None and result.build.spent == cost


async def test_weapon_master_does_not_cover_unpurchased_weapon_class(tmp_path: Path) -> None:
    scoped = Purchase(
        definition_id="trait:advantage:weapon-master",
        trait=TraitOptions(parameters=(("point-cost", 20), ("weapon-scope", "rapier"))),
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=DEFINITIONS,
        extra_purchases=(scoped,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    await attack(cid, play)
    play.rng = RecordedDice([3, 3, 3, 2])
    result = await CombatService(play).execute(cid, choice(), principal_id="b")
    assert result.injury and result.injury.basic_damage == 3 and result.injury.injury == 4
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1].model_copy(update={"parries": ("sword-b",)})
    value, _ = standard_defense_value(play.rules_context, state, defender, "parry", "sword-b")
    assert value is not None and value.value == 6


@pytest.mark.parametrize(
    ("master", "target", "injury"), [(None, 4, 0), ("trained-by-a-master", 7, 1)]
)
async def test_unarmed_rapid_strike_master_changes_both_actual_punches(
    tmp_path: Path, master: str | None, target: int, injury: int
) -> None:
    from test_gurps_maneuvers import turn
    from test_unarmed import state_of

    from wayfarer.orchestration.combat import TakeUnarmedTurn

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        free_defender_hand=True,
        extra_definitions=DEFINITIONS,
        extra_purchases=() if master is None else (purchase(master),),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    await turn(cid, play, "a", "do_nothing")
    state = await state_of(cid, play)
    command = TakeUnarmedTurn(
        id="rapid-punch",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        action="punch",
        target_id="a",
        hands=("left-hand",),
        enter_close_combat=True,
        maneuver="attack",
        attack_option="rapid-strike",
    )
    await CombatService(play).execute(cid, command, principal_id="b")
    for _ in range(2):
        play.rng = RecordedDice([2, 2, 2] + ([4] if master else []))
        state = await state_of(cid, play)
        pending = state.encounters[0].pending_unarmed
        assert pending is not None
        defense = ChooseDefense(
            id="defend:" + pending.id,
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        )
        await CombatService(play).execute(cid, defense, principal_id="a")
        state = await state_of(cid, play)
        trace = state.encounters[0].unarmed_history[-1]
        assert trace.checks[0].effective_target == target
        assert trace.injury == injury
    state = await state_of(cid, play)
    assert state.encounters[0].current_actor_id == "a"
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10 - 2 * injury


@pytest.mark.parametrize(
    ("master", "target"), [(None, 4), ("trained-by-a-master", 6), ("weapon-master", 4)]
)
async def test_unarmed_multiple_parry_belongs_only_to_trained_by_master(
    tmp_path: Path, master: str | None, target: int
) -> None:
    from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        free_defender_hand=True,
        extra_definitions=DEFINITIONS,
        extra_purchases=() if master is None else (purchase(master),),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1].model_copy(update={"parries": ("left-hand",)})
    encounter = state.encounters[0].model_copy(
        update={"participants": (state.encounters[0].participants[0], defender)}
    )
    value, hand = unarmed_defense(play.rules_context, state, encounter, "b", "parry", "left-hand")
    assert value == target and hand == "left-hand"
