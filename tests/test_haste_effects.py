"""B237/B251 Haste: independent cost, clock and actual movement/defense values."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_lock_spell_persistence import prepare as lock_prepare
from test_spells import context, state

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import size_modifier_definition
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.melee.values import score_defense
from wayfarer.engine.simulation.magic.haste_effects import bonus
from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, apply_spell
from wayfarer.errors import ValidationError
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.play import PlayService


def command(identifier: str, revision: int, kind: str, *, levels: int = 3) -> RuntimeSpellCommand:
    return RuntimeSpellCommand.model_validate(
        dict(
            id=identifier,
            actor_id="a",
            expected_revision=revision,
            kind=kind,
            spell_id="haste",
            channel_id="haste",
            cast_id="haste",
            energy=levels,
        )
    )


@pytest.mark.parametrize("levels", [1, 2, 3])
def test_haste_source_cost_duration_and_magnitude(levels: int) -> None:
    bound = context().model_copy(
        update={
            "learned": ("haste",),
            "skill": 14,
            "energy": levels,
            "execution_version": 2,
            "execute_effects": True,
        }
    )
    resources, _ = apply_spell(
        state(),
        command("start", 0, "start", levels=levels),
        bound,
        system=True,
        rng=RecordedDice(()),
    )
    assert (
        latest(resources)["haste"].cost,
        latest(resources)["haste"].maintenance,
        latest(resources)["haste"].ready_at,
    ) == (2 * levels, levels, 2)
    resources = resources.model_copy(update={"game_time": 1})
    resources, _ = apply_spell(
        resources,
        command("concentrate", 1, "concentrate", levels=levels),
        bound,
        system=True,
        rng=RecordedDice(()),
    )
    resources = resources.model_copy(update={"game_time": 2})
    resources, result = apply_spell(
        resources,
        command("complete", 2, "complete", levels=levels),
        bound,
        system=True,
        rng=RecordedDice((3, 3, 3)),
    )
    assert result.energy_spent == 2 * levels
    assert bonus(resources, bound.target_id) == levels
    assert latest(resources)["haste"].expires_at == 62
    assert bonus(resources.model_copy(update={"game_time": 62}), bound.target_id) == 0


async def prepare(
    path: Path,
    backend: str = "sqlite",
    *,
    size_modifier: int = 0,
) -> tuple[str, PlayService]:
    cid, play = await lock_prepare(
        path,
        backend,
        combat=True,
        extra_definitions=(size_modifier_definition(),),
        extra_purchases=(Purchase(definition_id="spell:haste", amount=4),)
        + (
            (Purchase(definition_id="trait:size-modifier", amount=size_modifier),)
            if size_modifier > 0
            else ()
        ),
    )
    await HasteService(play).execute(
        cid,
        DeclareHasteChannel(
            id="channel",
            actor_id="gm",
            expected_revision=0,
            channel=HasteChannel(id="haste", actor_id="a", target_id="a", location_id="dock"),
        ),
        principal_id="gm",
    )
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_approved_cast_changes_real_move_and_dodge_and_expires(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    participant = Combatant(
        actor_id="a",
        initiative=5,
        initiative_dx=10,
        position=GridPoint(x=0, y=0),
        reach=1,
        movement_allowance=5,
    )
    baseline = score_defense(
        play.rules_context, before, participant, "dodge", targeted_weapon=False
    )[0]
    assert baseline is not None
    assert movement(play.rules_context, before, "a") == 5 and baseline.value == 8
    service = HasteService(play)
    await service.execute(cid, command("start", 1, "start"), principal_id="alice")
    await play.execute(
        cid, Wait(id="one", actor_id="a", expected_revision=2, ticks=1), principal_id="a"
    )
    await service.execute(cid, command("concentrate", 3, "concentrate"), principal_id="alice")
    await play.execute(
        cid, Wait(id="two", actor_id="a", expected_revision=4, ticks=1), principal_id="a"
    )
    play.rng = RecordedDice((3, 3, 3))
    complete = command("complete", 5, "complete")
    result = await service.execute(cid, complete, principal_id="alice")
    after = play._load(await play.store.read(cid))
    assert movement(play.rules_context, after, "a") == 8
    defense = score_defense(play.rules_context, after, participant, "dodge", targeted_weapon=False)[
        0
    ]
    assert defense is not None and defense.value == 11
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 4
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await HasteService(restarted).execute(cid, complete, principal_id="alice") == result
    await restarted.execute(
        cid, Wait(id="expiry", actor_id="a", expected_revision=6, ticks=60), principal_id="a"
    )
    expired = play._load(await play.store.read(cid))
    assert movement(play.rules_context, expired, "a") == 5
    assert (
        score_defense(play.rules_context, expired, participant, "dodge", targeted_weapon=False)[0]
        == baseline
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_haste_unsupported_levels_do_not_mutate() -> None:
    bound = context().model_copy(update={"learned": ("haste",), "energy": 4, "skill": 14})
    with pytest.raises(ValidationError, match="three levels"):
        apply_spell(
            state(),
            command("start", 0, "start", levels=4),
            bound,
            system=True,
            rng=RecordedDice(()),
        )


def test_personal_haste_failure_and_high_skill_costs_are_canonical() -> None:
    bound = context().model_copy(
        update={
            "learned": ("haste",),
            "skill": 20,
            "energy": 3,
            "execution_version": 2,
            "execute_effects": True,
        }
    )
    resources, _ = apply_spell(
        state(), command("start", 0, "start"), bound, system=True, rng=RecordedDice(())
    )
    effect = latest(resources)["haste"]
    assert (effect.cost, effect.maintenance, effect.ready_at) == (4, 1, 1)
    resources = resources.model_copy(update={"game_time": 1})
    resources, result = apply_spell(
        resources,
        command("complete", 1, "complete"),
        bound,
        system=True,
        rng=RecordedDice((6, 6, 5)),
    )
    assert result.outcome == "failed" and result.energy_spent == 1
    assert bonus(resources, bound.target_id) == 0


def test_runtime_haste_keeps_the_frozen_public_spell_command_closed() -> None:
    from pydantic import ValidationError as SchemaError

    from wayfarer.engine.simulation.magic.spells import SpellCommand

    with pytest.raises(SchemaError):
        SpellCommand.model_validate(command("start", 0, "start").model_dump())


def test_multiple_haste_instances_use_strongest_without_stacking() -> None:
    from wayfarer.engine.simulation.magic.spell_state import (
        RuntimeSpellEffect,
        RuntimeSpellEvent,
        SpellResult,
        event_id,
    )
    from wayfarer.engine.simulation.resources import ResourceEvent

    resources = state()
    for levels in (1, 3, 2):
        effect = RuntimeSpellEffect(
            cast_id=f"cast-{levels}",
            actor_id=f"caster-{levels}",
            target_id="a",
            spell_id="haste",
            build_revision="approved",
            phase="active",
            started_at=0,
            ready_at=0,
            expires_at=levels * 10,
            skill=14,
            cost=2 * levels,
            maintenance=levels,
            hp_at_start=10,
            energy=levels,
            execute_effects=True,
            execution_version=2,
        )
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=event_id(f"effect-{levels}", "haste"),
                        at=0,
                        target_id="a",
                        kind=RuntimeSpellEvent(
                            effect=effect, result=SpellResult(outcome="active")
                        ).model_dump_json(),
                    ),
                )
            }
        )
    assert bonus(resources, "a") == 3
    assert bonus(resources.model_copy(update={"game_time": 30}), "a") == 0
