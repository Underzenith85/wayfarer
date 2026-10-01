"""College casts reuse actual B235-241 energy, timing and effect reducers."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_spell_bindings import command, setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.colleges import CollegeSpellBinding
from wayfarer.engine.simulation.magic.colleges import dispatch_college_spell
from wayfarer.engine.simulation.magic.effects import dazed, illuminated
from wayfarer.engine.simulation.magic.spells import latest
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError

BINDINGS = (
    CollegeSpellBinding("light", "Light", 249, "light-darkness"),
    CollegeSpellBinding("daze", "Daze", 250, "mind-control"),
    CollegeSpellBinding("create-fire", "Create Fire", 246, "fire"),
    CollegeSpellBinding("fireball", "Fireball", 247, "fire"),
)


@pytest.mark.parametrize("success,expected_energy", [(True, 3), (False, 1)])
async def test_daze_dispatch_uses_approved_time_energy_and_effect(
    tmp_path: Path, success: bool, expected_energy: int
) -> None:
    cid, play = await setup(tmp_path, execution_version=2)
    before = play._load(await play.store.read(cid))
    start = command().model_copy(update={"spell_id": "daze", "channel_id": "daze"})
    runtime = replace(play.rules_context, rng=RecordedDice([]))
    casting, result = dispatch_college_spell(
        runtime, before, start, BINDINGS, authorized_actor_id="a"
    )
    assert result.outcome == "casting"
    effect = latest(casting.resources)["cast"]
    assert effect.ready_at == before.resources.game_time + 2  # B250, source skill 14.
    assert next(p.current for p in casting.resources.pools if p.id == "fp:a") == 10
    complete = command(1, "complete").model_copy(update={"spell_id": "daze", "channel_id": "daze"})
    with pytest.raises(ConflictError):
        dispatch_college_spell(runtime, casting, complete, BINDINGS, authorized_actor_id="a")
    second = casting.model_copy(
        update={"resources": casting.resources.model_copy(update={"game_time": 1})}
    )
    concentrate = complete.model_copy(update={"id": "second", "kind": "concentrate"})
    concentrated, _ = dispatch_college_spell(
        runtime, second, concentrate, BINDINGS, authorized_actor_id="a"
    )
    complete = complete.model_copy(update={"id": "complete", "expected_revision": 2})
    ready = concentrated.model_copy(
        update={
            "resources": concentrated.resources.model_copy(update={"game_time": effect.ready_at})
        }
    )
    runtime = replace(runtime, rng=RecordedDice([3, 3, 3, 6, 6, 5] if success else [5, 5, 5]))
    changed, result = dispatch_college_spell(
        runtime, ready, complete, BINDINGS, authorized_actor_id="a"
    )
    assert result.energy_spent == expected_energy
    assert (
        next(p.current for p in changed.resources.pools if p.id == "fp:a") == 10 - expected_energy
    )
    assert dazed(changed.resources, "b") is success
    assert dazed(changed.resources, "a") is False
    replay_runtime = replace(runtime, rng=RecordedDice([]))
    assert dispatch_college_spell(
        replay_runtime, changed, complete, BINDINGS, authorized_actor_id="a"
    ) == (changed, result)
    with pytest.raises(AuthorizationError):
        dispatch_college_spell(replay_runtime, changed, complete, BINDINGS, authorized_actor_id="b")


async def test_light_dispatch_changes_actual_illumination(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, execution_version=2)
    before = play._load(await play.store.read(cid))
    runtime = replace(play.rules_context, rng=RecordedDice([]))
    casting, _ = dispatch_college_spell(
        runtime, before, command(), BINDINGS, authorized_actor_id="a"
    )
    assert latest(casting.resources)["cast"].ready_at == 1
    ready = casting.model_copy(
        update={"resources": casting.resources.model_copy(update={"game_time": 1})}
    )
    changed, result = dispatch_college_spell(
        replace(runtime, rng=RecordedDice([3, 3, 3])),
        ready,
        command(1, "complete"),
        BINDINGS,
        authorized_actor_id="a",
    )
    assert result.energy_spent == 1
    assert illuminated(changed, "b") and not illuminated(changed, "a")
    assert changed.world == before.world  # Light does not reveal hidden facts.


async def test_insufficient_energy_is_atomic_and_unsupported_effect_cannot_succeed(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, execution_version=2)
    before = play._load(await play.store.read(cid))
    exhausted = before.model_copy(
        update={
            "resources": before.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                        for p in before.resources.pools
                    )
                }
            )
        }
    )
    runtime = replace(play.rules_context, rng=RecordedDice([]))
    with pytest.raises(ValidationError):
        dispatch_college_spell(runtime, exhausted, command(), BINDINGS, authorized_actor_id="a")
    assert exhausted.resources.receipts == before.resources.receipts
    assert latest(exhausted.resources) == {}
    unsupported = command().model_copy(update={"spell_id": "awaken"})
    binding = CollegeSpellBinding("awaken", "Awaken", 248, "healing")
    with pytest.raises(ValidationError, match="no executable effect"):
        dispatch_college_spell(runtime, before, unsupported, (binding,), authorized_actor_id="a")
    with pytest.raises(ValidationError, match="outside"):
        dispatch_college_spell(runtime, before, command(), (binding,), authorized_actor_id="a")
    with pytest.raises(ConflictError):
        dispatch_college_spell(runtime, before, command(5), BINDINGS, authorized_actor_id="a")
