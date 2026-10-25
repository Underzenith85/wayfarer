"""Drawn native default attacks remain immutable through Luck and paid missile impact."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_default_knowledge_hosts import adopt
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_haste_manufacture_power_composition import _canonical_campaign
from test_live_skill_prerequisite_context import reexecute
from test_opponent_attack_host import begin, choose
from test_spell_bindings import command as spell_command
from test_spell_bindings import idle, start_fight
from test_spell_bindings import setup as spell_setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.errors import ConflictError
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.combat import CombatService, StartEncounter
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_opponent_luck_keeps_adopted_default_original_and_source_target(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    luck = next(d for d in candidate_package().definitions if d.id == "trait:advantage:luck")
    luck = replace(luck, source_id=source("gurps-basic-set-4e-2004").id)
    cid, original = await setup(
        tmp_path / "foundation",
        "gurps-basic-set-4e-2004",
        human=True,
        trained=False,
        start_encounter=False,
        allow_supernatural=True,
        extra_definitions=(luck,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        extra_purchases=(
            Purchase(definition_id=luck.id, trait=TraitOptions(parameters=(("point-cost", 15),))),
        ),
    )
    initial = await original.store.read(cid)
    play = build_play(
        tmp_path / "actual",
        original.engine,
        backend=backend,
        rng=secrets,
        seeds=lambda: f"{11:064x}",
    )
    await seed_campaign(play.store, initial)
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    await adopt(play, cid, ("skill:broadsword",))
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    _, opened = await begin(play, cid)
    assert opened.check and opened.check.base_target == opened.check.total == 5
    original_state = await play.store.read(cid)
    state = play._load(original_state)
    with pytest.raises(ConflictError, match="pending task"):
        await DefaultKnowledgeService(play).execute(
            cid,
            DeclareDefaultKnowledge(
                id="forbidden-change",
                actor_id="gm",
                expected_revision=state.revision,
                performer_id="a",
                skill_ids=("skill:broadsword",),
                society_known=False,
                reason="A current pending original cannot be replaced by another command",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == original_state
    monkeypatch.setattr(generations, "ACTIVE", False)
    _, selected = await choose(play, cid, opened.pending_id, principal="b")
    assert selected.check and selected.check.base_target == 5 and selected.combat_json
    combat = CombatResult.model_validate_json(selected.combat_json)
    assert combat.injury and combat.injury.attack == selected.check
    assert selected.check.total > 5 and combat.injury.injury == 0
    await reexecute(initial, play, tmp_path / "seed-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_default_projectile_original_survives_later_gm_revocation(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await spell_setup(tmp_path, combat=True, backend=backend, human_targets=True)
    initial = await play.store.read(cid)
    play.rng, play.seeds = secrets, lambda: f"{11:064x}"
    await start_fight(cid, play)
    await idle(cid, play, "a")
    await idle(cid, play, "b")
    state = play._load(await play.store.read(cid))
    start = spell_command(state.revision).model_copy(
        update={"spell_id": "fireball", "channel_id": "fireball"}
    )
    service = SpellService(play)
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    state = play._load(await play.store.read(cid))
    await service.execute(
        cid,
        start.model_copy(
            update={"id": "finish", "kind": "complete", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    await adopt(play, cid, ("skill:innate-attack-projectile",))
    state = play._load(await play.store.read(cid))
    await service.execute(
        cid,
        start.model_copy(
            update={"id": "release", "kind": "release", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.attack_roll
    original = pending.attack_roll
    assert original.base_target == 6 and original.total == 5
    paid_fp = next(pool.current for pool in state.resources.pools if pool.id == "fp:a")
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="projectile-unknown",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            skill_ids=("skill:innate-attack-projectile",),
            society_known=False,
            reason="Future defaults are denied; this paid projectile already has its original",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack == original and result.injury.injury == 1
    final = play._load(await play.store.read(cid))
    assert next(pool.current for pool in final.resources.pools if pool.id == "fp:a") == paid_fp
    current = await play.store.read(cid)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-replay"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(current)
    assert current == await play.store.replay(cid)
