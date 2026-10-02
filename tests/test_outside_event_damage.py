"""Independent B428/B433 damage oracles before any B66 host integration."""

from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_luck import approved, command
from test_medical_service import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.environmental_hazards import acid_spec
from wayfarer.engine.rules.types.hazard import HazardProtection, HazardSchedule, HazardSpec
from wayfarer.engine.simulation.health.hazard_damage import (
    HazardDamageSelection,
    prepare_hazard_damage,
)
from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.luck import LuckRoll, LuckState, apply_luck
from wayfarer.errors import ConflictError, ValidationError


def flame(dice: int = 1, add: int = -1) -> HazardSpec:
    return HazardSpec(
        id="flame",
        scene_id="dock",
        kind="fire",
        interval=1,
        cycles=3,
        damage_dice=dice,
        damage_add=add,
        resistible=False,
        reference="B433",
    )


async def exposed(path: Path, spec: HazardSpec) -> ResourceState:
    cid, play, _ = await setup(path)
    resources = play._load(await play.store.read(cid)).resources
    return resources.model_copy(
        update={
            "pools": tuple(
                pool.model_copy(update={"current": pool.maximum}) for pool in resources.pools
            ),
            "hazards": (
                HazardSchedule(
                    id="exposure",
                    actor_id="a",
                    spec=spec,
                    started=0,
                    due=0,
                    remaining=spec.cycles,
                    ht=10,
                    will=10,
                    swimming=6,
                ),
            ),
        }
    )


@pytest.mark.parametrize(
    "spec,face,damage",
    [
        (flame(1, -3), 2, 1),
        (flame(), 5, 4),
        (acid_spec("splash", id="acid", scene_id="dock", protection=HazardProtection()), 5, 2),
        (acid_spec("immersion", id="acid", scene_id="dock", protection=HazardProtection()), 3, 2),
    ],
)
async def test_selected_faces_resume_real_source_damage_once(
    tmp_path: Path,
    spec: HazardSpec,
    face: int,
    damage: int,
) -> None:
    before = await exposed(tmp_path, spec)
    prepared = prepare_hazard_damage(before, actor_id="a", schedule_id="exposure")
    selected = HazardDamageSelection(preparation=prepared, dice=(face,))
    request = HazardCommand(
        id="resolved", actor_id="a", expected_revision=0, kind="resolve", hazard_id=spec.id
    )
    rng = RecordedDice(())
    after, result = apply_hazard(
        before, request, prepared.schedule, rng=rng, system=True, selected_damage=selected
    )
    assert result.hp_lost == damage
    assert next(pool.current for pool in after.pools if pool.id == "hp:a") == 10 - damage
    assert after.hazards[0].cycle == 1 and after.hazards[0].remaining == spec.cycles - 1
    assert after.hazards[0].due == 1
    assert apply_hazard(
        after, request, prepared.schedule, rng=rng, system=True, selected_damage=selected
    ) == (after, result)
    with pytest.raises(ConflictError):
        apply_hazard(
            after,
            request.model_copy(update={"id": "late", "expected_revision": 1}),
            prepared.schedule,
            rng=rng,
            system=True,
            selected_damage=selected,
        )


async def test_major_wound_roll_happens_after_selected_damage_and_is_not_replaced(
    tmp_path: Path,
) -> None:
    before = await exposed(tmp_path, flame(3, 0))
    prepared = prepare_hazard_damage(before, actor_id="a", schedule_id="exposure")
    rng = RecordedDice((3, 3, 3))
    after, result = apply_hazard(
        before,
        HazardCommand(
            id="furnace", actor_id="a", expected_revision=0, kind="resolve", hazard_id="flame"
        ),
        prepared.schedule,
        rng=rng,
        system=True,
        selected_damage=HazardDamageSelection(preparation=prepared, dice=(2, 2, 2)),
    )
    hp = next(pool for pool in after.pools if pool.id == "hp:a")
    assert result.hp_lost == 6 and hp.current == 4
    assert hp.injury is not None and not hp.injury.stunned and not hp.injury.unconscious
    assert rng.exhausted()


async def test_actual_owner_current_source_and_profile_are_required(tmp_path: Path) -> None:
    state = await exposed(tmp_path, flame())
    with pytest.raises(ValidationError, match="does not affect"):
        prepare_hazard_damage(state, actor_id="b", schedule_id="exposure")
    with pytest.raises(ConflictError, match="deadline"):
        prepare_hazard_damage(
            state.model_copy(update={"game_time": 1}), actor_id="a", schedule_id="exposure"
        )
    for spec in (
        flame(2, 0),
        flame().model_copy(update={"resistible": True}),
        acid_spec("swallowed", id="acid", scene_id="dock", protection=HazardProtection()),
        acid_spec("splash", id="acid", scene_id="dock", protection=HazardProtection(sealed=True)),
    ):
        changed = state.model_copy(
            update={"hazards": (state.hazards[0].model_copy(update={"spec": spec}),)}
        )
        with pytest.raises(ValidationError):
            prepare_hazard_damage(changed, actor_id="a", schedule_id="exposure")
    prepared = prepare_hazard_damage(state, actor_id="a", schedule_id="exposure")
    changed = state.model_copy(
        update={
            "pools": tuple(
                pool.model_copy(update={"current": 9}) if pool.id == "hp:a" else pool
                for pool in state.pools
            )
        }
    )
    with pytest.raises(ConflictError, match="no longer matches"):
        apply_hazard(
            changed,
            HazardCommand(
                id="stale", actor_id="a", expected_revision=0, kind="resolve", hazard_id="flame"
            ),
            prepared.schedule,
            rng=RecordedDice(()),
            system=True,
            selected_damage=HazardDamageSelection(preparation=prepared, dice=(2,)),
        )


@pytest.mark.parametrize("secret", [False, True])
def test_outside_damage_selects_low_with_fixed_semantics(secret: bool) -> None:
    build, definitions = approved()
    roll = LuckRoll(
        id="check",
        actor_id="inventor",
        kind="hazard-damage",
        scope="party-event",
        affected_actor_ids=("inventor",),
        dice_count=1,
        modifier=-1,
        original=None if secret else (6,),
        secret=secret,
    )
    rng = RecordedDice((6, 2, 4) if secret else (2, 4))
    after, receipt = apply_luck(
        LuckState(rolls=(roll,), pending_roll_id=roll.id),
        command(),
        build,
        definitions,
        real_time=0,
        authorized_actor_id="inventor",
        system=True,
        rng=rng,
    )
    assert receipt.attempts == ((6,), (2,), (4,)) and receipt.chosen_index == 1
    assert after.rolls[0].chosen_total == 1 and rng.exhausted()
    with pytest.raises(SchemaError, match="actual affected"):
        LuckRoll(id="bad", actor_id="inventor", kind="hazard-damage", dice_count=1, original=(6,))


def test_equal_source_damage_keeps_original_after_the_noncrushing_floor() -> None:
    build, definitions = approved()
    roll = LuckRoll(
        id="check",
        actor_id="inventor",
        kind="hazard-damage",
        scope="party-event",
        affected_actor_ids=("inventor",),
        dice_count=1,
        modifier=-3,
        original=(2,),
    )
    after, receipt = apply_luck(
        LuckState(rolls=(roll,), pending_roll_id=roll.id),
        command(),
        build,
        definitions,
        real_time=0,
        authorized_actor_id="inventor",
        system=True,
        rng=RecordedDice((1, 6)),
    )
    assert receipt.attempts == ((2,), (1,), (6,)) and receipt.chosen_index == 0
    assert after.rolls[0].chosen_total == 1
