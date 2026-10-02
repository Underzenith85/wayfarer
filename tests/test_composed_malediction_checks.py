"""B106/B361/B421: actual Malediction checks retain current general modifiers."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_composed_attack_host import declare, fixture
from test_composed_attacks import pick
from test_symptom_attribute_consumers import attribute_penalty

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.fright import save
from wayfarer.engine.simulation.health.fright_state import TimedFright
from wayfarer.engine.simulation.traits.composed_host import ResistComposedAttack
from wayfarer.orchestration.composed_attacks import ComposedAttackService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("resist", [False, True])
async def test_current_aftermath_reaches_attacker_and_resister_without_double_attributes(
    tmp_path: Path, backend: str, resist: bool
) -> None:
    cid, play, source = await fixture(
        tmp_path, backend, modifiers=(pick("enhancement:malediction", option="1"),)
    )

    def current_penalties(state: PlayState) -> PlayState:
        resources = state.resources
        for actor in ("a", "b"):
            resources = save(
                resources,
                TimedFright(
                    id="prior-coma:" + actor,
                    actor_id=actor,
                    trigger_id="coma:" + actor,
                    started=0,
                    due=None,
                    active=False,
                    recovery_target=10,
                    aftermath_until=21600,
                    effect=FrightEffect(
                        table_total=28, aftermath_penalty=-2, aftermath_seconds=21600
                    ),
                ),
                "recovered:" + actor,
            )
        resources = attribute_penalty(resources, "b", "iq", level=4)
        return state.model_copy(update={"resources": resources})

    await change(play, cid, current_penalties)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    response = ResistComposedAttack(
        id="response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        pending_id=pending.id,
        resist=resist,
    )
    # Attacking Will10 − range2 − aftermath2 =6. Consent doesn't erase
    # that check: roll7 misses. Resisting Will remains10 under B421 despite
    # Symptoms IQ−4; its separate general aftermath makes effective8.
    play.rng = RecordedDice((2, 2, 2, 3, 3, 3, 2, 2) if resist else (2, 2, 3))
    result = await ComposedAttackService(play).execute(cid, response, principal_id="bob")
    assert result.attack is not None
    attack = result.attack.checks[0]
    assert (attack.base_target, attack.effective_target) == (10, 6)
    if resist:
        defense = result.attack.checks[1]
        assert (defense.base_target, defense.effective_target) == (10, 8)
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (6 if resist else 10)
    assert state.encounters[0].pending_defense is None
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    saved = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert (
        await ComposedAttackService(restarted).execute(cid, response, principal_id="bob") == result
    )
    assert await restarted.store.read(cid) == saved == await restarted.store.replay(cid)
