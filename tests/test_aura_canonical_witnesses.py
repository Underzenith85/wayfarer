"""Canonical reducer diagnostics; these are not health/control host producer claims."""

from pathlib import Path

import pytest
from support.aura import fixture, observe
from test_mental_spirit_traits import approved, channel, command

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.aura_admission import current_subject, observation
from wayfarer.engine.simulation.magic.aura_state import observations
from wayfarer.engine.simulation.traits.mental_control import control_grants
from wayfarer.engine.simulation.traits.mental_spirit import apply_mental_use
from wayfarer.errors import ConflictError


async def test_canonical_lethal_injury_cannot_be_declared_living(tmp_path: Path) -> None:
    cid, play, _ = await fixture(tmp_path, "sqlite")
    await observe(play, cid)
    state = play._load(await play.store.read(cid))
    resources, _ = apply_injury(
        state.resources,
        Wound(
            id="fatal",
            actor_id="b",
            expected_revision=state.resources.revision,
            basic_damage=100,
            resistance=0,
            damage_type="cr",
            location="torso",
        ),
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    hp = next(p for p in resources.pools if p.id == "hp:b")
    assert hp.injury is not None and hp.injury.dead
    dead = state.model_copy(update={"resources": resources})
    with pytest.raises(ConflictError, match="living"):
        current_subject(play.rules_context, dead, "subject", "c")
    assert await play.store.read(cid) is not None


@pytest.mark.parametrize("kind", ["influence", "possession"])
async def test_actual_canonical_control_grant_changes_observation_witness(
    tmp_path: Path, kind: str
) -> None:
    cid, play, _ = await fixture(tmp_path, "sqlite")
    await observe(play, cid)
    state = play._load(await play.store.read(cid))
    definition = "advantage:mind-control" if kind == "influence" else "advantage:possession"
    build, compiler = approved(Purchase(definition_id=definition))
    physical = channel(
        definition_id=definition,
        kind=kind,
        actor_id="a",
        target_id="b",
        location_id="dock",
        fatigue_cost=0,
        fact_ids=(),
        duration_seconds=60,
        touching_target=True,
    )
    use = command().model_copy(
        update={
            "definition_id": definition,
            "channel_id": physical.id,
            "expected_revision": state.resources.revision,
        }
    )
    resources, world, outcome = apply_mental_use(
        state.resources,
        state.world,
        use,
        build,
        compiler.definitions,
        (physical,),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "successful"
    assert control_grants(resources)[0].kind == kind
    controlled = state.model_copy(update={"resources": resources, "world": world})
    old = observations(state.resources)[0]
    with pytest.raises(ConflictError, match="witness changed"):
        current_subject(play.rules_context, controlled, "subject", "c")
    fresh = observation(play.rules_context, controlled, old.subject, "new")
    assert fresh.control_json and fresh.control_json != old.control_json
