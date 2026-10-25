"""B549/B551 shared scorers retain posture across composed, spell and Push callers."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_composed_attack_host import declare, defense, fixture
from test_gurps_maneuvers import turn
from test_push_combat import declaration, game, response
from test_spell_bindings import command, idle, start_fight
from test_spell_bindings import setup as spell_setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.combat import Posture
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.unarmed.injury import drop_held
from wayfarer.engine.simulation.skills.push import declare_push, defend_push
from wayfarer.orchestration import defense_posture_generations, ranged_target_posture_generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.command_inputs import DEFENSE_POSTURE_KEY


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture,target", [("sitting", 7), ("crawling", 6)])
@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_composed_defense_keeps_posture_independent_of_acrobatic_protocol(
    tmp_path: Path,
    backend: str,
    posture: Posture,
    target: int,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play, source = await fixture(tmp_path, backend, skill_points=4)
    # This older defense fixture tests the independent defense policy; its
    # crawling attack intentionally retains historical target-posture rules.
    monkeypatch.setattr(ranged_target_posture_generations, "ACTIVE", False)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture=posture)
    await declare(play, cid, source)
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", enabled)
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3] + ([1, 1] if enabled else []))
    selected, result = await defense(play, cid, selected="dodge")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == (target if enabled else 9)
    assert result.injury.injury == (2 if enabled else 0) and play.rng.exhausted()
    final = await play.store.read(cid)
    raw = (await play.store.history(cid))[-1].command_input
    assert raw
    envelope = json.loads(raw)
    assert envelope["operation"] == "composed-defense"
    assert envelope["combat_protocol_features"] == [
        "acrobatic-condition-checks",
        "acrobatic-reaction-attributes",
        "acrobatic-skill-minimum",
        "acrobatic-trait-bonuses",
    ]
    assert envelope.get(DEFENSE_POSTURE_KEY) == (1 if enabled else None)
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", not enabled)
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, selected, principal_id="bob") == result
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture,target", [("sitting", 6), ("crawling", 5)])
async def test_actual_spell_release_and_following_dodge_share_current_posture_generation(
    tmp_path: Path, backend: str, posture: Posture, target: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ranged_target_posture_generations, "ACTIVE", False)
    cid, play = await spell_setup(
        tmp_path,
        combat=True,
        backend=backend,
        human_targets=True,
        extra_purchases=(Purchase(definition_id="skill:innate-attack-projectile", amount=4),),
    )
    await start_fight(cid, play)
    await idle(cid, play, "a")
    await turn(cid, play, "b", "change_posture", posture=posture)
    state = play._load(await play.store.read(cid))
    start = command(state.revision).model_copy(
        update={"spell_id": "fireball", "channel_id": "fireball"}
    )
    service = SpellService(play)
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(
        cid,
        start.model_copy(
            update={"id": "finish", "kind": "complete", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(
        cid,
        start.model_copy(
            update={"id": "release", "kind": "release", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    raw = (await play.store.history(cid))[-1].command_input
    assert raw and json.loads(raw)[DEFENSE_POSTURE_KEY] == 1
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming and incoming.attack_roll and incoming.attack_roll.outcome.succeeded
    play.rng = RecordedDice([2, 2, 3, 2])
    selected = ChooseDefense(
        id="posture-spell-dodge",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
    )
    result = await CombatService(play).execute(cid, selected, principal_id="b")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == target
    assert result.injury.injury == 2 and result.injury.hp_after == 8 and play.rng.exhausted()
    final = await play.store.read(cid)
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, selected, principal_id="b") == result
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("posture,target", [("sitting", 6), ("crawling", 5)])
@pytest.mark.parametrize("enabled", [False, True])
async def test_push_reducer_consumes_explicit_posture_policy_and_actual_authored_posture(
    tmp_path: Path, posture: Posture, target: int, enabled: bool
) -> None:
    cid, play, _, _ = await game(tmp_path)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture=posture)
    original = play._load(await play.store.read(cid))
    state, encounter = drop_held(original, original.encounters[0], "a")
    state = state.model_copy(update={"encounters": (encounter,)})
    play.rng = RecordedDice([3, 3, 3])
    declared, opened = declare_push(
        play.rules_context, state, declaration(play, state), authorized_actor_id="a"
    )
    assert opened.attack and opened.attack.outcome.succeeded
    play.rng = RecordedDice([2, 2, 3] + ([5, 5, 3, 3, 3] if enabled else []))
    runtime = replace(play.rules_context, correct_defense_postures=enabled)
    updated, result = defend_push(
        runtime, declared, response(play, declared, defense="dodge"), authorized_actor_id="b"
    )
    assert result.defense and result.defense.effective_target == (target if enabled else 8)
    assert result.defense.outcome.succeeded == (not enabled)
    actor = updated.encounters[0].participants[1]
    assert actor.position == GridPoint(x=3 if enabled else 1, y=0)
    assert next(pool.current for pool in updated.resources.pools if pool.id == "hp:b") == 10
    assert play.rng.exhausted()
    # Push has no orchestration host; this is its real reducer boundary, not an API claim.
