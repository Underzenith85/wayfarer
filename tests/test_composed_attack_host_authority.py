"""Private composed host: independent authority, replay, mutation and visibility gates."""

import asyncio
import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_combat_sensory_authority import change
from test_composed_attack_host import declare, defense, fixture
from test_composed_attacks import pick

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.commands import COMBAT_ADAPTER, ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult, PendingDefense
from wayfarer.engine.simulation.traits.composed_host import (
    AbandonComposedAttack,
    ComposedCommand,
    ContinueComposedCritical,
    ResistComposedAttack,
    UseComposedAttack,
)
from wayfarer.engine.simulation.traits.composed_resolution import (
    RESOLUTION_PREFIX,
    ComposedResolution,
)
from wayfarer.engine.simulation.traits.composed_sources import (
    BindComposedSource,
    validate_composed_state,
)
from wayfarer.engine.simulation.traits.innate_criticals import load_innate_critical
from wayfarer.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.orchestration.tactical_view.basic_choices import basic_choices


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_independent_service_race_commits_one_spent_attack(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    command = UseComposedAttack(
        id="race",
        kind="declare",
        actor_id="a",
        expected_revision=before.revision,
        encounter_id="fight",
        source_id=source,
        target_id="b",
    )
    other = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    results = await asyncio.gather(
        ComposedAttackService(play).execute(cid, command, principal_id="alice"),
        ComposedAttackService(other).execute(cid, command, principal_id="alice"),
    )
    assert results[0] == results[1]
    after = play._load(await play.store.read(cid))
    assert after.revision == before.revision + 1
    assert len([r for r in await play.store.history(cid) if r.command_id == "race"]) == 1
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp.injury and hp.injury.turn == 1
    with pytest.raises(ConflictError):
        await ComposedAttackService(play).execute(
            cid, command.model_copy(update={"target_id": "a"}), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await ComposedAttackService(play).execute(
            cid, command.model_copy(update={"id": "stale"}), principal_id="alice"
        )
    assert play._load(await play.store.read(cid)) == after
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_membership_is_rechecked_before_locked_use_and_exact_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    command = UseComposedAttack(
        id="planned",
        kind="declare",
        actor_id="a",
        expected_revision=before.revision,
        encounter_id="fight",
        source_id=source,
        target_id="b",
    )
    service = ComposedAttackService(play)
    plan = service.plan(play, before, command, principal_id="alice")
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={"members": tuple(m for m in state.members if m.principal_id != "alice")}
        ),
    )
    revoked = await play.store.read(cid)
    with pytest.raises(NotFoundError):
        await submit(play, cid, plan, principal_id="alice")
    assert await play.store.read(cid) == revoked
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": state.members
                + (CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),)
            }
        ),
    )
    committed = await declare(play, cid, source)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": tuple(
                    m.model_copy(update={"actor_ids": ("b",)}) if m.principal_id == "alice" else m
                    for m in state.members
                )
            }
        ),
    )
    with pytest.raises(AuthorizationError):
        await service.execute(cid, committed, principal_id="alice")
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


async def test_binding_requires_both_current_gm_seat_and_deployment_trust(tmp_path: Path) -> None:
    cid, play, _ = await fixture(tmp_path)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={"members": state.members + (CampaignMember(principal_id="visitor", role="gm"),)}
        ),
    )
    state = play._load(await play.store.read(cid))
    command = BindComposedSource(
        id="bad-bind",
        actor_id="a",
        expected_revision=state.revision,
        description="An untrusted description",
        specialty="gaze",
    )
    with pytest.raises(ValidationError, match="GM authority"):
        await ComposedAttackService(play).execute(cid, command, principal_id="visitor")
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={"members": tuple(m for m in state.members if m.principal_id != "gm")}
        ),
    )
    with pytest.raises(NotFoundError):
        await ComposedAttackService(play).execute(cid, command, principal_id="gm")
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "operation", ["bind", "aim", "declare", "defend", "resist", "abandon", "continue-critical"]
)
async def test_seed_only_reexecution_preserves_complete_host_state(
    tmp_path: Path, backend: str, operation: str
) -> None:
    modifiers = (pick("enhancement:malediction", option="1"),) if operation == "resist" else ()
    cid, play, source = await fixture(tmp_path / "original", backend, modifiers=modifiers)
    principal = "alice"
    command: ComposedCommand | ChooseDefense
    if operation in ("defend", "resist", "abandon", "continue-critical"):
        await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    if operation == "defend":
        principal = "bob"
        command = ChooseDefense(
            id="seed",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        )
    elif operation == "resist":
        assert pending
        principal = "bob"
        command = ResistComposedAttack(
            id="seed",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=True,
        )
    elif operation == "abandon":
        assert pending
        command = AbandonComposedAttack(
            id="seed",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
        )
    elif operation == "continue-critical":
        play.rng = RecordedDice((6, 6, 6, 3, 3, 3))
        await defense(play, cid)
        state = play._load(await play.store.read(cid))
        pending = state.encounters[0].pending_defense
        assert pending
        captured = next(
            ComposedResolution.model_validate_json(e.kind)
            for e in state.resources.events
            if e.id.startswith(RESOLUTION_PREFIX)
        )
        assert captured.critical_id
        record = load_innate_critical(state.resources, captured.critical_id)
        assert record
        principal = "gm"
        command = ContinueComposedCritical(
            id="seed",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            critical_id=captured.critical_id,
            context_digest=record.digest,
            policy_id="explicit-campaign-policy",
            reason="GM adopts a timed natural-power disable",
            effect="disable-source",
            duration_seconds=2,
        )
    elif operation == "bind":
        principal = "gm"
        command = BindComposedSource(
            id="seed",
            actor_id="a",
            expected_revision=state.revision,
            description="Second approved description",
            specialty="gaze",
        )
    else:
        command = UseComposedAttack.model_validate(
            {
                "id": "seed",
                "kind": operation,
                "actor_id": "a",
                "expected_revision": state.revision,
                "encounter_id": "fight",
                "source_id": source,
                "target_id": "b",
            }
        )
    before = await play.store.read(cid)
    play.rng = secrets
    if isinstance(command, ChooseDefense):
        await CombatService(play).execute(cid, command, principal_id=principal)
    else:
        await ComposedAttackService(play).execute(cid, command, principal_id=principal)
    recorded = (await play.store.history(cid))[-1]
    assert recorded.command_id == "seed" and recorded.reexecutable
    replay = build_play(
        tmp_path / "replay", play.engine, rng=secrets, backend=backend, filename="replay.sqlite"
    )
    # PostgreSQL campaign IDs are global within this test cluster, so use an isolated
    # SQLite replay store for the exact pre-command checkpoint of either backend.
    if backend == "postgres":
        replay = build_play(tmp_path / "replay", play.engine, rng=secrets, filename="replay.sqlite")
    await seed_campaign(replay.store, before)
    await execute_recorded(replay, recorded)
    assert replay._load(await replay.store.read(cid)) == play._load(await play.store.read(cid))
    assert (await replay.store.history(cid))[-1].event == recorded.event
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_legacy_pending_snapshot_and_frozen_commands_have_no_new_public_vocabulary() -> None:
    old = PendingDefense(
        id="old",
        attacker_id="a",
        defender_id="b",
        weapon_id="sword-a",
        allowed=("none", "dodge"),
        opened_round=1,
        opened_turn=0,
    )
    encoded = old.model_dump_json()
    assert "composed_attack_id" not in encoded
    assert PendingDefense.model_validate_json(encoded).model_dump_json() == encoded
    for schema in (COMBAT_ADAPTER.json_schema(), CombatResult.model_json_schema()):
        text = json.dumps(schema)
        assert "Composed" not in text and "composed_attack_id" not in text


async def test_pending_pointer_and_campaign_mismatch_fail_before_resolution(tmp_path: Path) -> None:
    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    pending = encounter.pending_defense
    assert pending
    for updated in (
        pending.model_copy(update={"composed_attack_id": "missing"}),
        pending.model_copy(update={"weapon_id": "sword-a"}),
        pending.model_copy(update={"spell_cast_id": "invented"}),
    ):
        malformed = state.model_copy(
            update={"encounters": (encounter.model_copy(update={"pending_defense": updated}),)}
        )
        with pytest.raises(ValidationError):
            play.engine.validate(malformed)
    with pytest.raises(ValidationError, match="campaign"):
        validate_composed_state(state.model_copy(update={"campaign_id": "another-campaign"}))
    choices = basic_choices(play, state, encounter, "b", frozenset({"a", "b"}))
    assert choices
    assert source not in json.dumps([c.model_dump(mode="json") for c in choices])
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


async def test_current_symptom_projection_is_applied_once_and_defenses_keep_exemptions(
    tmp_path: Path,
) -> None:
    from test_symptom_attribute_consumers import attribute_penalty

    cid, play, source = await fixture(tmp_path)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={"resources": attribute_penalty(state.resources, "a", "dx", level=2)}
        ),
    )
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 1))
    _, result = await defense(play, cid)
    assert result.injury and result.injury.attack.effective_target == 4
    assert result.injury.injury == 0 and play.rng.exhausted()


async def test_b379_corrosion_rejects_binding_before_private_mutation(tmp_path: Path) -> None:
    cid, play, _ = await fixture(tmp_path, kind="cor", bind_source=False)
    before = await play.store.read(cid)
    state = play._load(before)
    with pytest.raises(ValidationError, match="B379 persistent DR degradation"):
        await ComposedAttackService(play).execute(
            cid,
            BindComposedSource(
                id="corrosion",
                actor_id="a",
                expected_revision=state.revision,
                description="Corrosive innate beam",
                specialty="beam",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_critical_self_hit_binds_current_limb_cyclic_debt(
    tmp_path: Path, backend: str
) -> None:
    from test_composed_cyclic_boundaries import cyclic

    from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, binding
    from wayfarer.engine.simulation.traits.innate_criticals import innate_critical_outcome

    cid, play, source = await fixture(
        tmp_path,
        backend,
        modifiers=(cyclic("burn"),),
        levels=1,
        dr=1,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )
    await declare(play, cid, source)
    # B556 ranged table5 rerolls once to6; right-arm self-hit basic5/2 ->2; DR1 ->1HP.
    play.rng = RecordedDice((6, 6, 6, 1, 1, 3, 1, 2, 3, 1, 1, 5))
    await defense(play, cid)
    state = play._load(await play.store.read(cid))
    resolution = next(
        ComposedResolution.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(RESOLUTION_PREFIX)
    )
    assert resolution.critical_id
    critical = innate_critical_outcome(state.resources, resolution.critical_id)
    assert critical.cyclic_attack_id and critical.location == "right-arm"
    occurrence = next(
        a for a in state.resources.cyclic_attacks if a.id == critical.cyclic_attack_id
    )
    assert (
        occurrence.actor_id,
        occurrence.attacker_id,
        occurrence.hp_debt,
        occurrence.remaining,
        occurrence.due,
    ) == ("a", "a", 1, 2, 10)
    bound = binding(state.resources, occurrence.id)
    assert bound and bound.location == "right-arm" and bound.source_id == source
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_new_cyclic_delivery_has_source_bound_initial_damage_and_recovery_debt(
    tmp_path: Path,
) -> None:
    from test_composed_cyclic_boundaries import cyclic

    from wayfarer.engine.rules.types.hazard import blocked_hp
    from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, binding

    cid, play, source = await fixture(
        tmp_path,
        modifiers=(cyclic("burn"),),
        levels=1,
        dr=1,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 3))
    await defense(play, cid)
    state = play._load(await play.store.read(cid))
    occurrence = state.resources.cyclic_attacks[0]
    assert (occurrence.hp_debt, occurrence.remaining, occurrence.due) == (2, 2, 10)
    assert blocked_hp(state.resources.illnesses, "b", "natural") == 2
    assert binding(state.resources, occurrence.id)
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 8
    assert play.rng.exhausted()


@pytest.mark.parametrize("kind", ["cr", "cut"])
async def test_b378_knockback_sources_refuse_hp_only_binding(tmp_path: Path, kind: str) -> None:
    cid, play, _ = await fixture(tmp_path, kind=kind, bind_source=False)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="B378 knockback continuation"):
        await ComposedAttackService(play).execute(
            cid,
            BindComposedSource(
                id="unsupported-kind",
                actor_id="a",
                expected_revision=play._load(before).revision,
                description="Natural impact source",
                specialty="projectile",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


async def test_b61_ordinary_toxic_machine_immunity_stops_initial_damage_and_cycles(
    tmp_path: Path,
) -> None:
    from test_composed_cyclic_boundaries import cyclic

    from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy

    cid, play, source = await fixture(
        tmp_path,
        kind="tox",
        modifiers=(cyclic("tox"),),
        levels=1,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )

    def machine(state: PlayState) -> PlayState:
        pools = tuple(
            p.model_copy(
                update={
                    "injury": p.injury.model_copy(update={"machine": True, "anatomy": "creature"})
                }
            )
            if p.id == "hp:b" and p.injury
            else p
            for p in state.resources.pools
        )
        return state.model_copy(
            update={"resources": state.resources.model_copy(update={"pools": pools})}
        )

    await change(play, cid, machine)
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2))
    await defense(play, cid)
    state = play._load(await play.store.read(cid))
    resolution = next(
        ComposedResolution.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(RESOLUTION_PREFIX)
    )
    assert resolution.outcome.outcome == "unaffected"
    assert not state.resources.cyclic_attacks and not state.resources.symptom_debts
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert play.rng.exhausted()


async def test_changed_worn_protection_is_recomputed_after_declaration(tmp_path: Path) -> None:
    cid, play, source = await fixture(
        tmp_path, dr=4, armor=True, modifiers=(pick("enhancement:armor-divisor", option="2"),)
    )
    await declare(play, cid, source)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"equipped": False}) if i.id == "armor-b" else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        ),
    )
    play.rng = RecordedDice((2, 2, 2, 3, 3))
    _, result = await defense(play, cid)
    assert result.injury and (result.injury.resistance, result.injury.injury) == (2, 4)
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    ("row", "dice", "fp", "hp", "prone", "shock"),
    [
        (5, (3,), 5, 10, False, 0),
        (7, (3, 3, 4, 4), 8, 10, True, 0),
        (8, (6, 6), -1, 9, False, 2),
    ],
)
async def test_fatigue_critical_consequences_change_real_pools_without_fake_hp_damage(
    tmp_path: Path, row: int, dice: tuple[int, ...], fp: int, hp: int, prone: bool, shock: int
) -> None:
    from test_composed_attack_host import table_dice

    levels = 2 if row == 8 else 1
    cid, play, source = await fixture(tmp_path, kind="fat", dr=1, levels=levels)
    await declare(play, cid, source)
    play.rng = RecordedDice((1, 1, 1) + table_dice(row) + dice)
    await defense(play, cid)
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == fp
    health = next(p for p in state.resources.pools if p.id == "hp:b")
    assert health.current == hp and health.injury
    assert (health.injury.prone, health.injury.shock) == (prone, shock)
    assert play.rng.exhausted()


async def test_critical_drop_applies_even_when_dr_prevents_all_damage(tmp_path: Path) -> None:
    from test_composed_attack_host import table_dice

    cid, play, source = await fixture(tmp_path, levels=1, dr=20)
    await declare(play, cid, source)
    play.rng = RecordedDice((1, 1, 1) + table_dice(12) + (3,))
    _, result = await defense(play, cid)
    state = play._load(await play.store.read(cid))
    assert result.injury and result.injury.basic_damage == 3 and result.injury.injury == 0
    assert {i.id for i in state.resources.items if i.world_ground_location_id == "dock"} == {
        "sword-b",
        "shield-b",
    }
    assert play.rng.exhausted()


async def test_recorded_family_dispatch_uses_indexed_validated_input(tmp_path: Path) -> None:
    from wayfarer.orchestration.composed_attacks import recorded_operation
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
    from wayfarer.persistence.events import CommandRecord

    class IndexedOnlyStore(AsyncSQLiteStore):
        async def history(self, cid: str) -> list[CommandRecord]:
            raise AssertionError("Family dispatch must not fold full campaign history")

    cid, play, _ = await fixture(tmp_path)
    indexed = build_play(
        tmp_path, play.engine, store=IndexedOnlyStore(tmp_path / "runtime.sqlite", 10)
    )
    assert await recorded_operation(indexed, cid, "bind", "composed-attack")
    assert not await recorded_operation(indexed, cid, "bind", "composed-defense")
    assert not await recorded_operation(indexed, cid, "missing", "composed-attack")


async def test_recovery_lock_rejects_composed_turn_before_spending_or_dice(tmp_path: Path) -> None:
    cid, play, source = await fixture(tmp_path)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"available_at": 1}) if a.actor_id == "a" else a
                    for a in state.actors
                )
            }
        ),
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="recovering from injury"):
        await declare(play, cid, source)
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("modifier", ["melee", "armor-divisor"])
async def test_legacy_modifier_never_silently_becomes_an_unmodified_ranged_source(
    tmp_path: Path, modifier: str
) -> None:
    cid, play, _ = await fixture(tmp_path, legacy_modifiers=(modifier,), bind_source=False)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Legacy Innate Attack modifiers"):
        await ComposedAttackService(play).execute(
            cid,
            BindComposedSource(
                id="legacy",
                actor_id="a",
                expected_revision=play._load(before).revision,
                description="Legacy modified purchase",
                specialty="beam",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
