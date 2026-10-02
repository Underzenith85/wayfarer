"""B414-415 exact per-actor dice order before one selected fragment attack."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_area_attacks import grenade
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import scene
from test_opponent_attack_routes import enroll, luck_source

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.explosion import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.attack_visibility import (
    SecretAttackSource,
    conceal_attack_source,
)
from wayfarer.engine.simulation.combat.blast_phases import (
    FragmentContinuation,
    PreparedFragmentAttack,
    RecordedFragmentLaunch,
)
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion, TakeCombatTurn
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.engine.simulation.combat.fragment_state import (
    active_fragment,
    fragment_attack_id,
    save_fragment,
)
from wayfarer.engine.simulation.combat.thrown.explosions import (
    prepare_blast_fragments,
    resolve_blast,
    resume_fragment_attack,
)
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService


async def launched_blast(
    path: Path, backend: str = "sqlite", *, durable: bool = False
) -> tuple[str, PlayService, PlayState, BlastRecord]:
    definition, purchase = luck_source()
    cid, initial = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1, fragmentation_dice=1),
        durability=ObjectProfile(construction="unliving", hp=100, dr=100, ht=10, size_modifier=0)
        if durable
        else None,
        object_hp=100 if durable else None,
    )
    play = await enroll(path, backend, cid, initial)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="launch",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            area_aim_point=GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0),
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play, "b")
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    assert blast.due <= state.resources.game_time and not blast.resolved
    return cid, play, state, blast


def responses() -> tuple[BlastResponse, ...]:
    return tuple(BlastResponse(actor_id=actor, cover_dr=0, size_modifier=0) for actor in ("a", "b"))


async def test_fragment_phase_matches_ordinary_exact_draw_order_and_final_state(
    tmp_path: Path,
) -> None:
    _, play, before, blast = await launched_blast(tmp_path)
    following = (3, 3, 3, 1) * 3
    dice = (1, 5, 5, 6, 1, 3, 3, 3) + following
    legacy_rng = RecordedDice(dice)
    expected = resolve_blast(
        replace(play.rules_context, rng=legacy_rng),
        before,
        before.encounters[0],
        blast_id=blast.id,
        command_id="blast",
        responses=responses(),
        object_cover={},
        object_sizes={},
        center=None,
        environment="air",
        contact_actor_id=None,
        internal_actor_id=None,
    )
    rng = RecordedDice((1, 5, 5, 6, 1))
    prepared = prepare_blast_fragments(
        replace(play.rules_context, rng=rng),
        before,
        before.encounters[0],
        blast_id=blast.id,
        command_id="blast",
        responses=responses(),
        object_cover={},
        object_sizes={},
        center=None,
        environment="air",
        contact_actor_id=None,
        internal_actor_id=None,
        stop_for=("b",),
    )
    assert prepared.pending and rng.exhausted()
    assert prepared.pending.target.target == 15
    assert next(p.current for p in prepared.state.resources.pools if p.id == "hp:a") == 10
    assert next(p.current for p in prepared.state.resources.pools if p.id == "hp:b") == 9
    phase = PreparedFragmentAttack.model_validate_json(prepared.pending.model_dump_json())
    rng = RecordedDice((3, 3, 3) + following)
    original = phase.spec.roll(rng)
    finished = resume_fragment_attack(
        replace(play.rules_context, rng=rng), prepared.state, prepared.encounter, phase, original
    )
    assert finished.pending is None and rng.exhausted() and legacy_rng.exhausted()
    assert (finished.state, finished.encounter, finished.deferred_ticks) == expected
    assert next(p.current for p in finished.state.resources.pools if p.id == "hp:b") == 6
    with pytest.raises(ConflictError, match="unresolved blast"):
        resume_fragment_attack(
            replace(play.rules_context, rng=RecordedDice(())),
            finished.state,
            finished.encounter,
            phase,
            original,
        )


async def test_selected_fragment_miss_preserves_previous_blast_injury_and_actor_draws(
    tmp_path: Path,
) -> None:
    _, play, before, blast = await launched_blast(tmp_path)
    rng = RecordedDice((1, 5, 5, 6, 1))
    prepared = prepare_blast_fragments(
        replace(play.rules_context, rng=rng),
        before,
        before.encounters[0],
        blast_id=blast.id,
        command_id="blast",
        responses=responses(),
        object_cover={},
        object_sizes={},
        center=None,
        environment="air",
        contact_actor_id=None,
        internal_actor_id=None,
        stop_for=("b",),
    )
    assert prepared.pending and rng.exhausted()
    rng = RecordedDice(())
    finished = resume_fragment_attack(
        replace(play.rules_context, rng=rng),
        prepared.state,
        prepared.encounter,
        prepared.pending,
        prepared.pending.spec.score((5, 5, 6)),
    )
    assert finished.pending is None and rng.exhausted()
    assert next(p.current for p in finished.state.resources.pools if p.id == "hp:b") == 9
    assert next(p.current for p in finished.state.resources.pools if p.id == "hp:a") == 10
    assert blasts(finished.state.resources)[0].resolved
    assert prepared.state.resources.pools == finished.state.resources.pools


async def test_future_expended_object_uses_live_condition_without_restoring_captured_item(
    tmp_path: Path,
) -> None:
    _, play, before, blast = await launched_blast(tmp_path, durable=True)
    rng = RecordedDice((1, 5, 5, 6, 1))
    prepared = prepare_blast_fragments(
        replace(play.rules_context, rng=rng),
        before,
        before.encounters[0],
        blast_id=blast.id,
        command_id="blast",
        responses=responses(),
        object_cover={"sword-a": 0, "sword-b": 0, "shield-b": 0},
        object_sizes={"sword-a": 0, "sword-b": 0, "shield-b": 0},
        center=None,
        environment="air",
        contact_actor_id=None,
        internal_actor_id=None,
        stop_for=("b",),
    )
    assert prepared.pending and rng.exhausted()
    grenade_item = next(i for i in prepared.state.resources.expended_items if i.id == "sword-a")
    assert grenade_item.condition
    changed = grenade_item.model_copy(
        update={"condition": grenade_item.condition.model_copy(update={"hp": 51})}
    )
    live = prepared.state.model_copy(
        update={
            "resources": prepared.state.resources.model_copy(
                update={
                    "expended_items": tuple(
                        changed if i.id == changed.id else i
                        for i in prepared.state.resources.expended_items
                    )
                }
            )
        }
    )
    rng = RecordedDice((1, 5, 5, 6) * 3)
    finished = resume_fragment_attack(
        replace(play.rules_context, rng=rng),
        live,
        prepared.encounter,
        prepared.pending,
        prepared.pending.spec.score((5, 5, 6)),
    )
    assert finished.pending is None and rng.exhausted()
    actual = next(i for i in finished.state.resources.expended_items if i.id == "sword-a")
    assert actual.condition and actual.condition.hp == 51
    assert actual.firearm_failure and actual.firearm_failure.kind == "destroyed"


async def test_cancelled_fragment_preserves_amended_later_cover(tmp_path: Path) -> None:
    _, play, before, blast = await launched_blast(tmp_path)
    encounter = before.encounters[0].model_copy(
        update={"participants": tuple(reversed(before.encounters[0].participants))}
    )
    resolution = ResolveWeaponExplosion(
        id="blast",
        actor_id="gm",
        expected_revision=before.revision,
        encounter_id=encounter.id,
        blast_id=blast.id,
        responses=responses(),
        object_cover={},
        object_sizes={},
        environment="air",
    )
    rng = RecordedDice((1,))
    prepared = prepare_blast_fragments(
        replace(play.rules_context, rng=rng),
        before,
        encounter,
        blast_id=blast.id,
        command_id=resolution.id,
        responses=resolution.responses,
        object_cover={},
        object_sizes={},
        center=None,
        environment="air",
        contact_actor_id=None,
        internal_actor_id=None,
        stop_for=("b",),
    )
    assert prepared.pending and rng.exhausted()
    assert prepared.pending.progress.actor_ids == ("b", "a")
    launch = RecordedFragmentLaunch(
        campaign_id=before.campaign_id,
        command=TakeCombatTurn(
            id="launch",
            actor_id="a",
            expected_revision=0,
            encounter_id=encounter.id,
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
        ),
        payload_hash="0" * 64,
        attack_id=blast.id.rpartition(":blast:")[0],
        source_item_id=blast.source_item_id,
        producer_command_id="producer",
        producer_payload_hash="0" * 64,
    )
    cursor = FragmentContinuation(
        launch=launch,
        resolution=resolution,
        preparation=prepared.pending,
        secret=True,
        cancelled=True,
    )
    state = prepared.state.model_copy(
        update={
            "resources": conceal_attack_source(
                save_fragment(prepared.state.resources, cursor, "cancel"),
                SecretAttackSource(
                    encounter_id=encounter.id,
                    attack_id=fragment_attack_id(blast.id, "b"),
                    attacker_id="a",
                    target_id="b",
                ),
                system=True,
            )
        }
    )
    amended = tuple(
        response.model_copy(update={"cover_dr": 10, "covered_locations": ("torso",)})
        if response.actor_id == "a"
        else response
        for response in responses()
    )
    rng = RecordedDice((5, 5, 6, 6, 5, 5, 6))
    finished, _, _ = resolve_blast(
        replace(play.rules_context, rng=rng),
        state,
        prepared.encounter,
        blast_id=blast.id,
        command_id="resume",
        responses=amended,
        object_cover={},
        object_sizes={},
        center=None,
        environment="air",
        contact_actor_id=None,
        internal_actor_id=None,
    )
    assert rng.exhausted()
    assert next(pool.current for pool in finished.resources.pools if pool.id == "hp:b") == 9
    assert next(pool.current for pool in finished.resources.pools if pool.id == "hp:a") == 10
    resolved = blasts(finished.resources)[0]
    assert resolved.resolved and active_fragment(finished.resources, blast.id) is None
    evidence = json.loads(resolved.evidence)
    assert next(row for row in evidence["responses"] if row["actor_id"] == "a")["cover_dr"] == 10
