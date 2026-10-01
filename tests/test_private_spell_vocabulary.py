"""Runtime extension boundaries, not Lockmaster/Magelock effect certification."""

import pytest
from pydantic import ValidationError as SchemaError
from test_actions import engine, seed

from wayfarer.engine.simulation.events import GMAudience, ResourceChanged, SpellResolved, play_facts
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEffect,
    RuntimeSpellEvent,
    SpellEffect,
    SpellEvent,
    SpellResult,
    active_spells,
    event_id,
    interrupt_spells,
    latest,
)
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellCommand
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError


def private_record(*, phase: str = "casting") -> RuntimeSpellEffect:
    # Synthetic deadlines isolate serialization and concentration bookkeeping.
    return RuntimeSpellEffect.model_validate(
        dict(
            cast_id="cast",
            actor_id="a",
            target_id="door",
            spell_id="magelock",
            build_revision="build",
            phase=phase,
            started_at=0,
            ready_at=4,
            expires_at=10 if phase == "active" else None,
            skill=14,
            cost=3,
            maintenance=2,
            hp_at_start=10,
        )
    )


@pytest.mark.parametrize("spell_id", ["lockmaster", "magelock"])
def test_new_vocabulary_is_private_and_legacy_commands_remain_closed(spell_id: str) -> None:
    payload = dict(
        id="cast",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id=spell_id,
        cast_id="cast",
    )
    assert RuntimeSpellCommand.model_validate(payload).spell_id == spell_id
    with pytest.raises(SchemaError):
        SpellCommand.model_validate(payload)
    with pytest.raises(SchemaError):
        RuntimeSpellCommand.model_validate({**payload, "spell_id": "unreviewed-spell"})
    with pytest.raises(SchemaError):
        SpellEffect.model_validate(private_record().model_dump())


def test_private_casts_share_concentration_expiry_and_gm_only_projection() -> None:
    before = seed(engine())
    effect = private_record()
    event = ResourceEvent(
        id=event_id("cast", effect.spell_id),
        at=0,
        target_id="a",
        kind=RuntimeSpellEvent(
            effect=effect, result=SpellResult(outcome="casting")
        ).model_dump_json(),
    )
    resources = before.resources.model_copy(update={"events": (event,)})
    assert event.id.startswith("runtime-spell:")
    assert latest(resources)["cast"] == effect
    with pytest.raises(ConflictError, match="already concentrating"):
        require_idle_concentration(resources, "a")
    after = before.model_copy(update={"resources": resources})
    projected = play_facts(before, after, actor_id="a")
    assert len(projected) == 1 and isinstance(projected[0], ResourceChanged)
    assert projected[0].audience == GMAudience()
    assert not any(isinstance(e, SpellResolved) for e in projected)
    interrupted = interrupt_spells(resources, "a", "move")
    assert interrupted.events[-1].id.startswith("runtime-spell:")
    assert latest(interrupted)["cast"].phase == "ended"
    require_idle_concentration(interrupted, "a")
    active = private_record(phase="active")
    active_state = ResourceState(
        events=(
            event.model_copy(
                update={
                    "kind": RuntimeSpellEvent(
                        effect=active, result=SpellResult(outcome="active")
                    ).model_dump_json()
                }
            ),
        ),
        game_time=9,
    )
    assert active_spells(active_state) == (active,)
    assert not active_spells(active_state.model_copy(update={"game_time": 10}))


def test_legacy_records_retain_their_model_and_event_vocabulary() -> None:
    effect = SpellEffect.model_validate({**private_record().model_dump(), "spell_id": "light"})
    event = ResourceEvent(
        id=event_id("cast", "light"),
        at=0,
        target_id="a",
        kind=SpellEvent(effect=effect, result=SpellResult(outcome="casting")).model_dump_json(),
    )
    parsed = latest(ResourceState(events=(event,)))["cast"]
    assert type(parsed) is SpellEffect and parsed == effect
    assert event.id == event_id("cast") and event.id.startswith("spell:")
