"""B109/B124/B364/B394 independent oracles through actual armed combat commands.

Acute blindness is -10, ordinary nonvisual location is not certainty, and aware
defenders receive one -4. Geometry and an old pending choice confer no senses.
"""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Final, Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_basic_combat import start_basic
from test_combat_sensory_authority import change, declaration, symptoms
from test_combat_settlement import condition
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import scene, weapon
from test_symptoms import heal, hit

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import BasicMove
from wayfarer.engine.simulation.combat.maneuvers import WaitTrigger
from wayfarer.engine.simulation.combat.sensory_state import evidence
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, Placement
from wayfarer.engine.simulation.equipment.catalog import RangedMode, SmartgunSpec
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.world import Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.combat_senses import CombatSensesService
from wayfarer.orchestration.play import PlayService

PROFILE: Final = "gurps-basic-set-4e-2004"
Context = Literal["basic", "square", "hex"]


async def prepare(
    path: Path,
    backend: str,
    context: Context = "square",
    *,
    ranged: bool = False,
    ranged_mode: RangedMode | None = None,
    darkness: int = 0,
) -> tuple[str, PlayService]:
    board = (
        HexBattlefield(
            id="dock",
            coordinate_system="hex-axial-v1",
            profile_id=PROFILE,
            baseline_id=BASELINE_ID,
            location_id="dock",
            cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(4) for r in range(3)),
        )
        if context == "hex"
        else None
    )
    selected = ranged_mode or (weapon(thrown=True) if ranged else None)
    situations = scene(1) if selected else ()
    if selected and selected.smartgun:
        situations = tuple(
            s.model_copy(update={"laser_visible_to_firer": True, "laser_visible_to_target": True})
            for s in situations
        )
    cid, original = await setup(
        path / "source",
        PROFILE,
        human=True,
        darkness_penalty=darkness,
        ranged_mode=selected,
        ranged_scene=situations,
        scene_bound=context == "basic",
        start_encounter=context != "basic",
        battlefield=board,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        )
        if context == "hex"
        else None,
    )
    if context == "basic":
        await CombatService(original).execute(
            cid, start_basic(1, ranged=selected is not None), principal_id="gm"
        )
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    if context == "hex":
        await change(
            play,
            cid,
            lambda s: s.model_copy(
                update={
                    "world": replace(
                        s.world,
                        facts=s.world.facts
                        + (
                            Fact("seen-a", "a", "visible", "yes"),
                            Fact("seen-b", "b", "visible", "yes"),
                        ),
                        knowledge=s.world.knowledge + (("a", "seen-b"), ("b", "seen-a")),
                    )
                }
            ),
        )
    return cid, play


def blind(state: PlayState, actor: str = "b", *, active: bool = True) -> PlayState:
    updated = symptoms(state, active=active)
    return updated.model_copy(
        update={
            "resources": updated.resources.model_copy(
                update={
                    "symptom_effects": tuple(
                        e.model_copy(update={"actor_id": actor, "pool_id": f"hp:{actor}"})
                        for e in updated.resources.symptom_effects
                    ),
                    "symptom_debts": tuple(
                        d.model_copy(update={"pool_id": f"hp:{actor}"})
                        for d in updated.resources.symptom_debts
                    ),
                }
            )
        }
    )


async def sense(
    play: PlayService,
    cid: str,
    actor: str,
    *,
    located: bool = True,
    aware: bool = True,
    exact: bool = False,
) -> None:
    state = play._load(await play.store.read(cid))
    command = declaration(state.revision, f"sense-{state.revision}", located=located, exact=exact)
    command = command.model_copy(
        update={
            "actor_id": actor,
            "target_id": "b" if actor == "a" else "a",
            "observation": command.observation.model_copy(
                update={"attack_awareness": command.observation.attack_awareness if aware else None}
            ),
        }
    )
    await CombatSensesService(play).execute(cid, command, principal_id="gm")


async def attack(cid: str, play: PlayService, *, ranged: bool = False, **options: object) -> None:
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged" if ranged else "swing",
        **options,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_blind_mapped_or_visually_known_target_does_not_grant_location(
    tmp_path: Path, backend: str, context: Context, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, context, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a"))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="nonvisual|location|unavailable"):
        await attack(cid, play, ranged=ranged)
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize(("exact", "penalty"), [(False, -10), (True, -4)])
async def test_source_distinguishes_located_from_certain_armed_target(
    tmp_path: Path, backend: str, context: Context, ranged: bool, exact: bool, penalty: int
) -> None:
    cid, play = await prepare(tmp_path, backend, context, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a"))
    await sense(play, cid, "a", aware=False, exact=exact)
    await attack(cid, play, ranged=ranged)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    assert pending.hit_location == "random"
    assert pending.visibility_attack_penalty == penalty
    # Roll 10 misses both targets (3/9), avoiding critical or wound side effects.
    play.rng = RecordedDice((3, 3, 4))
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13 + penalty
    assert result.injury.attack.dice == (3, 3, 4)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


def blinded_and_eyes_disabled(state: PlayState) -> PlayState:
    state = blind(state, "a")
    pools = []
    for pool in state.resources.pools:
        if pool.id == "hp:a":
            assert pool.injury is not None
            pool = pool.model_copy(
                update={
                    "injury": pool.injury.model_copy(
                        update={
                            "lasting_injuries": tuple(
                                LastingInjury(
                                    id=f"disabled-{side}",
                                    location=side,
                                    kind="disabled",
                                    duration="permanent",
                                    inflicted_at=0,
                                    injury=0,
                                )
                                for side in ("left-eye", "right-eye")
                            )
                        }
                    )
                }
            )
        pools.append(pool)
    return state.model_copy(
        update={"resources": state.resources.model_copy(update={"pools": tuple(pools)})}
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_blindness_darkness_and_disabled_eyes_share_one_visual_penalty(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged, darkness=-7)
    await change(play, cid, blinded_and_eyes_disabled)
    await sense(play, cid, "a")
    await attack(cid, play, ranged=ranged)
    play.rng = RecordedDice((3, 3, 4))
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.attack.effective_target == 3
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("onset_after_aim", [False, True])
async def test_nonvisual_aim_keeps_accuracy_but_never_scope_or_laser_bonus(
    tmp_path: Path, backend: str, onset_after_aim: bool
) -> None:
    selected = weapon(thrown=True).model_copy(update={"scope_bonus": 2, "smartgun": SmartgunSpec()})
    cid, play = await prepare(tmp_path, backend, ranged_mode=selected)
    if not onset_after_aim:
        await change(play, cid, lambda s: blind(s, "a"))
        await sense(play, cid, "a")
    await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
    state = play._load(await play.store.read(cid))
    aiming = state.encounters[0].participants[0].maneuver_state
    assert aiming.aim_accuracy == 2
    assert aiming.aim_sight_bonus == int(onset_after_aim)
    await turn(cid, play, "b", "do_nothing")
    if onset_after_aim:
        await change(play, cid, lambda s: blind(s, "a"))
        await sense(play, cid, "a")
    await attack(cid, play, ranged=True, laser_sight=True)
    play.rng = RecordedDice((3, 3, 4))
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 5  # 13 + Acc 2 - acute blindness 10
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_aim_needs_detection_evaluate_needs_vision_and_feint_needs_observer(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=True)
    await change(play, cid, lambda s: blind(s, "a"))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
    assert await play.store.read(cid) == before
    await sense(play, cid, "a", exact=True)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="visible"):
        await turn(cid, play, "a", "evaluate", target_id="b")
    assert await play.store.read(cid) == before
    # A sighted attacker cannot Feint the now-blind intended observer.
    await change(play, cid, blind)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="observe"):
        await turn(cid, play, "a", "feint", item_id="sword-a", target_id="b", mode_id="swing")
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("damage", "healing", "blind_now"), [(5, 0, False), (6, 0, True), (6, 1, True), (6, 2, False)]
)
async def test_real_symptom_damage_and_healing_threshold_changes_armed_defense(
    tmp_path: Path, backend: str, damage: int, healing: int, blind_now: bool
) -> None:
    cid, play = await prepare(tmp_path, backend)

    def source_outcome(state: PlayState) -> PlayState:
        resources = hit(state.resources, damage=damage)
        if healing:
            resources = heal(resources, healing)
        return state.model_copy(update={"resources": resources})

    await change(play, cid, source_outcome)
    state = play._load(await play.store.read(cid))
    assert state.resources.symptom_effects[0].active == blind_now
    if blind_now:
        await sense(play, cid, "b", located=False)
    await attack(cid, play)
    # Defender's source damage can cause shock, but shock does not penalize defense.
    play.rng = RecordedDice((3, 3, 3, 1, 2, 2))
    result = await defend(cid, play, "b", "dodge")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == (5 if blind_now else 9)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_random_default_location_is_rolled_and_exact_retry_is_historical(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a"))
    await sense(play, cid, "a", exact=True)
    await attack(cid, play, ranged=ranged)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="resolve-random",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    # Attack 7 succeeds at 9, random location 9 is torso, damage is one die.
    play.rng = RecordedDice((2, 2, 3, 3, 3, 3, 1))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 9
    assert result.injury.location == "torso"
    assert result.injury.location_dice == (3, 3, 3)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await change(play, cid, lambda s: blind(s, "a", active=False))
    before = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    service = CombatService(restarted)
    assert await service.execute(cid, command, principal_id="b") == result
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"defense": "dodge"}), principal_id="b"
        )
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"id": "stale-new-choice"}), principal_id="b"
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize(
    "options", [{"hit_location": "neck"}, {"target_item_id": "sword-b"}, {"armor_chink": True}]
)
async def test_blind_armed_aimed_location_object_or_chink_rejected_atomically(
    tmp_path: Path, backend: str, ranged: bool, options: dict[str, object]
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a"))
    await sense(play, cid, "a", exact=True)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="[Bb]lind|random"):
        await attack(cid, play, ranged=ranged, **options)
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("located", "aware", "allowed"),
    [
        (False, True, {"none", "dodge"}),
        (True, False, {"none"}),
        (True, True, {"none", "dodge", "parry", "block"}),
    ],
)
async def test_defense_awareness_is_separate_from_nonvisual_location(
    tmp_path: Path, backend: str, located: bool, aware: bool, allowed: set[str]
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, blind)
    await sense(play, cid, "b", located=located, aware=aware)
    await attack(cid, play)
    before = await play.store.read(cid)
    state = play._load(before)
    pending = state.encounters[0].pending_defense
    assert pending is not None and set(pending.allowed) == allowed
    for denied in {"dodge", "parry", "block"} - allowed:
        with pytest.raises(ValidationError):
            await defend(cid, play, "b", denied)
        assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(("defense", "target"), [("dodge", 5), ("parry", 6), ("block", 6)])
async def test_blind_aware_located_defenses_have_one_minus_four(
    tmp_path: Path, backend: str, defense: str, target: int
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, blind)
    await sense(play, cid, "b")
    await attack(cid, play)
    # Ordinary attack success, ordinary defense failure, one harmless damage die.
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3, 1))
    result = await defend(cid, play, "b", defense)
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == target
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_blindness_onset_rejects_old_active_defense_before_dice(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await attack(cid, play, ranged=ranged)
    await change(play, cid, blind)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await defend(cid, play, "b", "dodge")
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_recovery_restores_defense_excluded_at_declaration(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await change(play, cid, blind)
    await attack(cid, play, ranged=ranged)
    pending = play._load(await play.store.read(cid)).encounters[0].pending_defense
    assert pending is not None and pending.allowed == ("none",)
    await change(play, cid, lambda s: blind(s, active=False))
    play.rng = RecordedDice((3, 3, 3, 2, 2, 2))
    result = await defend(cid, play, "b", "dodge")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 9
    assert result.injury.defense.outcome.succeeded
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_current_incapacity_rejects_defense_without_rewriting_original_offer(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await attack(cid, play, ranged=ranged)
    declared = play._load(await play.store.read(cid)).encounters[0].pending_defense
    assert declared is not None and "dodge" in declared.allowed
    await change(play, cid, lambda s: condition(s, "b", "collapsed"))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="current sensory and physical conditions"):
        await defend(cid, play, "b", "dodge")
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()

    play.rng = RecordedDice((3, 3, 3, 1))
    await defend(cid, play, "b", "none")
    after = play._load(await play.store.read(cid))
    history = after.encounters[0].defense_history[-1]
    assert history.selected == "none"
    assert history.pending.allowed == declared.allowed
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "hex"])
async def test_atomic_retreat_keeps_admitted_blind_defense_then_expires_evidence(
    tmp_path: Path, backend: str, context: Context
) -> None:
    cid, play = await prepare(tmp_path, backend, context)
    await change(play, cid, blind)
    await sense(play, cid, "b")
    await attack(cid, play)
    options = {"basic_retreat": True} if context == "basic" else {"retreat": Hex(q=2, r=0)}
    play.rng = RecordedDice((3, 3, 3, 2, 2, 2))
    result = await defend(cid, play, "b", "dodge", **options)
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 8  # 9 - 4 blindness + 3 retreat
    state = play._load(await play.store.read(cid))
    assert evidence(state, state.encounters[0], "b", "a") is None
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_attacker_onset_requires_fresh_location_before_pending_attack_roll(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await attack(cid, play, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a"))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="nonvisual|location"):
        await defend(cid, play, "b")
    assert await play.store.read(cid) == before
    await sense(play, cid, "a", exact=True)
    play.rng = RecordedDice((2, 2, 3, 3, 3, 3, 1))
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 9
    assert result.injury.location_dice == (3, 3, 3)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_attacker_recovery_removes_cached_penalty_before_pending_attack_roll(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a"))
    await sense(play, cid, "a")
    await attack(cid, play, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a", active=False))
    play.rng = RecordedDice((4, 5, 5))
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.attack.effective_target == 13
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_legal_pending_named_body_attack_becomes_random_after_blindness_onset(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, ranged=ranged)
    await attack(cid, play, ranged=ranged, hit_location="neck")
    pending = play._load(await play.store.read(cid)).encounters[0].pending_defense
    assert pending is not None and pending.hit_location == "neck" and pending.attack_roll is None
    await change(play, cid, lambda s: blind(s, "a"))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="nonvisual|location"):
        await defend(cid, play, "b")
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await sense(play, cid, "a", exact=True)
    # The legal declaration is already pending, but no attack has happened.
    # Current B394 blindness replaces its named body intent with random location.
    play.rng = RecordedDice((2, 2, 3, 3, 3, 3, 1))
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 9  # 13 - 4 certainty; no neck penalty
    assert result.injury.location == "torso" and result.injury.location_dice == (3, 3, 3)
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is None
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_torso_chink_loses_aim_penalty_and_dr_benefit_after_blindness(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.equipment.catalog import (
        LITE_SOURCE,
        Armor,
        Damage,
        EquipmentProfile,
        MeleeMode,
        Parry,
    )
    from wayfarer.engine.simulation.resources import Item

    armor = EquipmentProfile(
        definition_id="equipment:test-mail",
        provenance=LITE_SOURCE,
        weight_millipounds=10000,
        price=100,
        technology_level=3,
        slot="armor",
        armor=Armor(locations=("torso",), dr=4),
    )
    cid, original = await setup(
        tmp_path / "source",
        PROFILE,
        human=True,
        melee_modes=(
            MeleeMode(
                id="thrust",
                skill_id="skill:broadsword",
                minimum_st=10,
                damage=Damage(basis="thrust", adds=1, damage_type="imp"),
                reach=(1,),
                parry=Parry(),
            ),
        ),
        extra_equipment=(armor,),
        extra_items=(
            Item(
                id="mail-b",
                definition_id=armor.definition_id,
                owner_id="b",
                equipped=True,
            ),
        ),
    )
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="thrust",
        hit_location="torso",
        armor_chink=True,
    )
    pending = play._load(await play.store.read(cid)).encounters[0].pending_defense
    assert pending is not None and pending.armor_chink and pending.attack_roll is None
    await change(play, cid, lambda s: blind(s, "a"))
    await sense(play, cid, "a", exact=True)
    play.rng = RecordedDice((2, 2, 3, 3, 3, 3, 4))
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 9  # 13 - 4; no retained chink -8
    assert result.injury.location == "torso" and result.injury.location_dice == (3, 3, 3)
    assert result.injury.basic_damage == 3
    assert result.injury.resistance == 4  # Full DR; the former chink cannot halve it.
    assert result.injury.injury == 0 and result.injury.hp_after == 10
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is None
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_second_defense_needs_current_location_and_each_penalty_applies_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, blind)
    await sense(play, cid, "b", located=False)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "all_out_defense", defense_option="double")
    await attack(cid, play)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await defend(cid, play, "b", "dodge", second_defense="parry", second_item_id="sword-b")
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await sense(play, cid, "b")
    # The Dodge fails at 5; the newly permitted Parry succeeds at 6.
    play.rng = RecordedDice((3, 3, 3, 2, 2, 2, 1, 2, 2))
    result = await defend(cid, play, "b", "dodge", second_defense="parry", second_item_id="sword-b")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.second_defense is not None
    assert result.injury.defense.effective_target == 5
    assert result.injury.second_defense.effective_target == 6
    assert result.injury.second_defense.outcome.succeeded
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_competing_blind_defenses_commit_one_command_under_cas(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, blind)
    await sense(play, cid, "b", located=False)
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="defense-left",
        actor_id="b",
        encounter_id="fight",
        expected_revision=state.revision,
        defense="dodge",
    )
    other = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice((3, 3, 3, 1, 2, 2))
    )
    play.rng = RecordedDice((3, 3, 3, 1, 2, 2))
    before = len(await play.store.history(cid))
    results = await asyncio.gather(
        CombatService(play).execute(cid, command, principal_id="b"),
        CombatService(other).execute(
            cid, command.model_copy(update={"id": "defense-right"}), principal_id="b"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    successful = [result for result in results if not isinstance(result, BaseException)]
    assert len(successful) == 1
    assert successful[0].injury is not None and successful[0].injury.defense is not None
    assert successful[0].injury.defense.effective_target == 5
    assert len(await play.store.history(cid)) == before + 1
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fireball_release_needs_nonvisual_location_and_uses_blind_attack_penalty(
    tmp_path: Path, backend: str
) -> None:
    from test_spell_bindings import command, idle, start_fight
    from test_spell_bindings import setup as spell_setup

    from wayfarer.orchestration.spells import SpellService

    cid, play = await spell_setup(
        tmp_path, combat=True, execution_version=2, human_targets=True, backend=backend
    )
    await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    play.rng = RecordedDice((3, 3, 3))
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    await change(play, cid, lambda s: blind(s, "a"))
    state = play._load(await play.store.read(cid))
    release = start.model_copy(
        update={"id": "blind-release", "kind": "release", "expected_revision": state.revision}
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await service.execute(cid, release, principal_id="a")
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await sense(play, cid, "a", exact=True)
    state = play._load(await play.store.read(cid))
    release = release.model_copy(update={"expected_revision": state.revision})
    with pytest.raises(ValidationError, match="[Bb]lind|random"):
        await service.execute(
            cid, release.model_copy(update={"hit_location": "neck"}), principal_id="a"
        )
    await service.execute(cid, release, principal_id="a")
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None and pending.hit_location == "random"
    assert pending.visibility_attack_penalty == -4
    play.rng = RecordedDice((2, 2, 2))
    result = await defend(cid, play, "b")
    # Innate Attack projectile DX default is 6; B394 certainty costs four.
    assert result.injury is not None and result.injury.attack.effective_target == 2
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_composed_blind_defender_needs_independent_awareness_before_attack_dice() -> None:
    from test_composed_attacks import attacker, resolve

    from wayfarer.engine.simulation.traits.composed_attacks import AttackCompositionContext

    state = hit(damage=6)
    before = state.model_dump_json()
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=2, defense="dodge"
    )
    with pytest.raises(ValidationError, match="awareness"):
        resolve(attacker(), state=state, context=context, dice=[])
    assert state.model_dump_json() == before


def test_composed_aware_blind_defender_dodges_at_four_not_eight() -> None:
    from test_composed_attacks import attacker, resolve

    from wayfarer.engine.simulation.traits.composed_attacks import AttackCompositionContext

    state = hit(damage=6)
    context = AttackCompositionContext(
        target_id="b",
        location_id="room",
        distance_yards=2,
        defense="dodge",
        defender_attack_awareness="The defender hears the incoming projectile's approach",
    )
    # Attack 9 succeeds at 10. Dodge 6 fails at 8-4; two purchased damage dice follow.
    updated, result = resolve(
        attacker(), state=state, context=context, dice=[3, 3, 3, 2, 2, 2, 1, 1]
    )
    assert result.outcome == "injured"
    assert [check.effective_target for check in result.checks] == [10, 4]
    assert result.damage_dice == (1, 1)
    assert next(p.current for p in updated.pools if p.id == "hp:b") == 2


def test_composed_unaware_blind_target_can_decline_defense_without_a_defense_roll() -> None:
    from test_composed_attacks import attacker, resolve

    state = hit(damage=6)
    updated, result = resolve(attacker(), state=state, defense="none", dice=[3, 3, 3, 1, 1])
    assert result.outcome == "injured"
    assert len(result.checks) == 1
    assert result.checks[0].effective_target == 10
    assert result.damage_dice == (1, 1)
    assert next(p.current for p in updated.pools if p.id == "hp:b") == 2


def test_composed_blind_defense_exact_retry_after_recovery_retains_original_score() -> None:
    from test_attack_defense_traits import approved, command, world
    from test_composed_attacks import attacker

    from wayfarer.engine.simulation.traits.composed_attacks import (
        AttackCompositionContext,
        apply_composed_attack,
    )

    state = hit(damage=6)
    attack_build = attacker()
    target, compiler = approved()
    declared = command(revision=state.revision).model_copy(update={"id": "composed-blind-hit"})
    context = AttackCompositionContext(
        target_id="b",
        location_id="room",
        distance_yards=2,
        defense="dodge",
        defender_attack_awareness="The defender hears the incoming projectile's approach",
    )
    state, result = apply_composed_attack(
        state,
        world(),
        declared,
        attack_build,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice((3, 3, 3, 2, 2, 2, 1, 1)),
        authorized_actor_id="a",
        system=True,
    )
    recovered = heal(state, 3)
    assert not recovered.symptom_effects[0].active
    assert result.checks[1].effective_target == 4
    assert apply_composed_attack(
        recovered,
        world(),
        declared,
        attack_build,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    ) == (recovered, result)


def test_composed_blind_ordinary_attacker_remains_explicitly_unsupported() -> None:
    from test_attack_defense_traits import approved, command, world
    from test_composed_attacks import attacker

    from wayfarer.engine.simulation.traits.composed_attacks import (
        AttackCompositionContext,
        apply_composed_attack,
    )

    state = hit(damage=6)
    before = state.model_dump_json()
    target, compiler = approved()
    with pytest.raises(ValidationError, match="Blind ordinary composed attacks"):
        apply_composed_attack(
            state,
            world(),
            command(revision=state.revision).model_copy(
                update={"id": "blind-source", "actor_id": "b"}
            ),
            attacker(),
            target,
            compiler.definitions,
            AttackCompositionContext(
                target_id="a", location_id="room", distance_yards=1, defense="none"
            ),
            rng=RecordedDice(()),
            authorized_actor_id="b",
            system=True,
        )
    assert state.model_dump_json() == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize("located", [False, True])
async def test_sighted_interposer_cannot_bypass_pending_attacker_blindness(
    tmp_path: Path, backend: str, ranged: bool, located: bool
) -> None:
    cid, original = await setup(
        tmp_path / "source",
        PROFILE,
        human=True,
        third_actor=True,
        ranged_mode=weapon(thrown=True) if ranged else None,
        ranged_scene=scene(1) if ranged else (),
    )
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await attack(cid, play, ranged=ranged)
    await change(play, cid, lambda s: blind(s, "a"))
    if located:
        await sense(play, cid, "a", exact=True)
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    # Missing detection must fail; detected blindness requires a random location,
    # which the existing ordinary Sacrificial Dodge consumer explicitly excludes.
    with pytest.raises(ValidationError, match="nonvisual|Blind|random|specialized"):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="protect-after-onset",
                actor_id="c",
                expected_revision=state.revision,
                encounter_id="fight",
                defense="dodge",
                sacrificial_for="b",
            ),
            principal_id="c",
        )
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_sighted_interposer_uses_own_defense_when_original_target_is_blind(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, original = await setup(
        tmp_path / "source",
        PROFILE,
        human=True,
        third_actor=True,
        ranged_mode=weapon(thrown=True) if ranged else None,
        ranged_scene=scene(1) if ranged else (),
    )
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await change(play, cid, blind)
    await attack(cid, play, ranged=ranged)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None and pending.allowed == ("none",)
    hp_before = {p.id: p.current for p in state.resources.pools}
    play.rng = RecordedDice((3, 3, 3, 2, 2, 2, 1))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="sighted-protector",
            actor_id="c",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            sacrificial_for="b",
        ),
        principal_id="c",
    )
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.attack.effective_target == 13
    assert result.injury.defense.effective_target == 8
    assert result.injury.injury > 0
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == hp_before["hp:b"]
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") < hp_before["hp:c"]
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
@pytest.mark.parametrize("onset_after_wait", [False, True])
async def test_blind_armed_waiter_cannot_observe_a_movement_trigger_from_geometry(
    tmp_path: Path, backend: str, context: Context, onset_after_wait: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, context)
    if context == "basic":

        def permit_mover(state: PlayState) -> PlayState:
            encounter = state.encounters[0]
            spatial = encounter.spatial
            assert isinstance(spatial, BasicSpatialContext)
            obstacle = next(f for f in spatial.facts if f.kind == "obstacle")
            spatial = spatial.model_copy(
                update={
                    "facts": spatial.facts
                    + (obstacle.model_copy(update={"subject_id": "b", "object_id": "a"}),)
                }
            )
            return state.model_copy(
                update={"encounters": (encounter.model_copy(update={"spatial_context": spatial}),)}
            )

        await change(play, cid, permit_mover)
    if not onset_after_wait:
        await change(play, cid, lambda s: blind(s, "a"))
    if context == "hex" and not onset_after_wait:
        before = await play.store.read(cid)
        with pytest.raises(ValidationError, match="unavailable"):
            await turn(
                cid,
                play,
                "a",
                "wait",
                wait_trigger=WaitTrigger(
                    actor_id="b", action="move", reaction="ready", item_id="sword-a"
                ),
            )
        assert await play.store.read(cid) == before
        assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
        return
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger=WaitTrigger(actor_id="b", action="move", reaction="ready", item_id="sword-a"),
    )
    if onset_after_wait:
        await change(play, cid, lambda s: blind(s, "a"))
    # Even a valid one-time location declaration does not prove observation of
    # a later movement event; no nonvisual Wait-trigger consumer is bound.
    await sense(play, cid, "a", exact=True)
    options: dict[str, object] = (
        {"basic_move": BasicMove(reference_actor_id="a", direction="withdraw")}
        if context == "basic"
        else {"hex_path": (Hex(q=2, r=0),)}
        if context == "hex"
        else {"destination": GridPoint(x=2, y=0)}
    )
    result = await turn(cid, play, "b", "move", **options)
    assert result.code != "combat.wait_triggered"
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].wait_interrupt is None
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_blindness_after_committed_interposition_attack_preserves_historical_roll(
    tmp_path: Path, backend: str, ranged: bool
) -> None:
    cid, original = await setup(
        tmp_path / "source",
        PROFILE,
        human=True,
        third_actor=True,
        ranged_mode=weapon(thrown=True) if ranged else None,
        ranged_scene=scene(1) if ranged else (),
    )
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await attack(cid, play, ranged=ranged)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3, 4, 4, 4))
    failed = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="failed-interposition",
            actor_id="c",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            sacrificial_for="b",
        ),
        principal_id="c",
    )
    assert failed.code == "combat.sacrificial_failed" and failed.injury is not None
    historical_attack = failed.injury.attack
    assert historical_attack.effective_target == 13 and historical_attack.dice == (3, 3, 3)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None and pending.attack_roll == historical_attack
    await change(play, cid, lambda s: blind(s, "a"))
    # This attack has already succeeded. Later blindness neither demands a new
    # targeting fact nor retroactively changes its score, location or attack dice.
    play.rng = RecordedDice((1,))
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack == historical_attack
    assert result.injury.location is None and result.injury.location_dice == ()
    assert result.injury.injury == (1 if ranged else 3)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)
