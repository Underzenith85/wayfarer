"""Shared lifecycle state evidence from Characters 3p B236–241/B249–251."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_college_dispatch import BINDINGS
from test_spell_bindings import command, setup
from test_spells import command as raw_command
from test_spells import context, state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_checks import Contestant, resolve_quick_contest
from wayfarer.engine.simulation.magic.colleges import dispatch_college_spell
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.effects import illuminated
from wayfarer.engine.simulation.magic.spell_state import interrupt_spells
from wayfarer.engine.simulation.magic.spells import PROFILE, apply_spell, latest
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError


async def test_maintenance_expiry_cancel_and_retry_change_actual_light(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, execution_version=2)
    initial = play._load(await play.store.read(cid))
    runtime = replace(play.rules_context, rng=RecordedDice([]))
    casting, _ = dispatch_college_spell(
        runtime, initial, command(), BINDINGS, authorized_actor_id="a"
    )
    ready = casting.model_copy(
        update={"resources": casting.resources.model_copy(update={"game_time": 1})}
    )
    active, _ = dispatch_college_spell(
        replace(runtime, rng=RecordedDice([3, 3, 3])),
        ready,
        command(1, "complete"),
        BINDINGS,
        authorized_actor_id="a",
    )
    assert illuminated(active, "b")
    assert latest(active.resources)["cast"].expires_at == 61
    due = active.model_copy(
        update={"resources": active.resources.model_copy(update={"game_time": 61})}
    )
    assert not illuminated(due, "b")  # Expiry already changes the consumer.
    maintain = command(2, "maintain")
    empty = due.model_copy(
        update={
            "resources": due.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                        for p in due.resources.pools
                    )
                }
            )
        }
    )
    before_json = empty.model_dump_json()
    with pytest.raises(ConflictError):
        dispatch_college_spell(runtime, empty, maintain, BINDINGS, authorized_actor_id="a")
    assert empty.model_dump_json() == before_json and not illuminated(empty, "b")
    maintained, result = dispatch_college_spell(
        runtime, due, maintain, BINDINGS, authorized_actor_id="a"
    )
    assert result.energy_spent == 1
    assert latest(maintained.resources)["cast"].expires_at == 121
    assert next(p.current for p in maintained.resources.pools if p.id == "fp:a") == 8
    assert illuminated(maintained, "b")
    restarted = maintained.model_copy(
        update={
            "resources": ResourceState.model_validate_json(maintained.resources.model_dump_json())
        }
    )
    assert dispatch_college_spell(
        runtime, restarted, maintain, BINDINGS, authorized_actor_id="a"
    ) == (restarted, result)
    with pytest.raises(ConflictError):
        dispatch_college_spell(
            runtime,
            restarted,
            maintain.model_copy(update={"hp_energy": 1}),
            BINDINGS,
            authorized_actor_id="a",
        )
    ended, result = dispatch_college_spell(
        runtime, restarted, command(3, "cancel"), BINDINGS, authorized_actor_id="a"
    )
    assert result.energy_spent == 1 and not illuminated(ended, "b")
    assert next(p.current for p in ended.resources.pools if p.id == "fp:a") == 7
    assert dispatch_college_spell(
        runtime, ended, command(3, "cancel"), BINDINGS, authorized_actor_id="a"
    ) == (ended, result)


def test_interruption_releases_actual_casting_commitment_without_charge() -> None:
    casting, _ = apply_spell(
        state(), raw_command(spell="daze"), context(), rng=RecordedDice([]), system=True
    )
    with pytest.raises(ConflictError):
        require_idle_concentration(casting, "a")
    interrupted = interrupt_spells(casting, "a", "other-maneuver")
    require_idle_concentration(interrupted, "a")
    assert latest(interrupted)["cast"].phase == "ended"
    assert interrupted.pools == casting.pools
    assert interrupt_spells(interrupted, "a", "other-maneuver") == interrupted


@pytest.mark.parametrize(
    "target_ht,cast_roll,resist_roll,resisted",
    [
        (16, (3, 3, 3), (3, 3, 3), True),  # Rule of 16 tie goes to defender.
        (20, (4, 4, 4), (3, 3, 3), True),  # Defender >16 raises the cap.
        (10, (3, 3, 3), (4, 4, 4), False),
    ],
)
def test_shared_resistance_contest_reuses_the_casting_roll(
    target_ht: int,
    cast_roll: tuple[int, int, int],
    resist_roll: tuple[int, int, int],
    resisted: bool,
) -> None:
    ctx = context().model_copy(update={"skill": 30, "target_ht": target_ht})
    started, _ = apply_spell(
        state(), raw_command(spell="daze"), ctx, rng=RecordedDice([]), system=True
    )
    rng = RecordedDice([*cast_roll, *resist_roll])
    changed, result = apply_spell(
        started.model_copy(update={"game_time": 1}),
        raw_command(1, kind="complete", spell="daze"),
        ctx,
        rng=rng,
        system=True,
    )
    assert rng.exhausted() and len(result.checks) == 2
    assert (result.outcome == "resisted") is resisted
    expected = resolve_quick_contest(
        PROFILE,
        Contestant("caster", min(30, max(16, target_ht))),
        Contestant("subject", target_ht),
        first_dice=cast_roll,
        second_dice=resist_roll,
    )
    assert (expected.winner != "caster") is resisted
    assert latest(changed)["cast"].phase == ("ended" if resisted else "active")
