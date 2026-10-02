"""Independent resource-clock oracles for newly bound versus legacy occurrences."""

from pathlib import Path

import pytest
from test_cyclic_host import advance, current, expose_command, hp, prepare
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.cyclic import CyclicOccurrence
from wayfarer.engine.rules.types.hazard import blocked_hp
from wayfarer.engine.rules.types.symptoms import SymptomSpec
from wayfarer.engine.simulation.ability_types import AbilityEffect, AbilityEvent, AbilityOutcome
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.cyclic import save
from wayfarer.engine.simulation.health.cyclic_host_state import (
    BINDING_PREFIX,
    bind_occurrence,
    binding,
)
from wayfarer.engine.simulation.health.cyclic_observations import resolve
from wayfarer.engine.simulation.resources import Advance, ResourceEvent
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.orchestration.play import PlayService


def replace_build(
    play: PlayService, state: PlayState, actor_id: str, *, ht: int = 10, dr: int = 0, fit: int = 0
) -> PlayState:
    fitness = (
        (Purchase(definition_id="trait:fit" if fit == 1 else "trait:very-fit"),) if fit else ()
    )
    draft = gurps_draft(
        *fitness,
        *((Purchase(definition_id="advantage:damage-resistance", amount=dr),) if dr else ()),
    )
    proposal = CharacterProposal(
        draft=draft.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(update={"amount": ht}) if p.definition_id == "attribute:ht" else p
                    for p in draft.purchases
                )
            }
        )
    )
    approval = play.engine.reviewer.approve(
        proposal, campaign_id=state.campaign_id, actor_id=actor_id, revision=state.revision
    )
    return state.model_copy(
        update={
            "actors": tuple(
                actor.model_copy(update={"proposal": proposal, "approval": approval})
                if actor.actor_id == actor_id
                else actor
                for actor in state.actors
            )
        }
    )


def machine_body(state: PlayState, actor_id: str, *, machine: bool = True) -> PlayState:
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        pool.model_copy(
                            update={"injury": pool.injury.model_copy(update={"machine": machine})}
                        )
                        if pool.id == "hp:" + actor_id and pool.injury is not None
                        else pool
                        for pool in state.resources.pools
                    )
                }
            )
        }
    )


async def test_current_toxic_machine_immunity_refreshes_without_erasing_prior_debt(
    tmp_path: Path,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=10)
    first = advance(play, await current(play, cid), 10, (2,))
    assert hp(first) == 6 and first.resources.cyclic_attacks[0].hp_debt == 4
    ended = advance(play, machine_body(first, "b"), 20, ())
    attack = ended.resources.cyclic_attacks[0]
    assert hp(ended) == 6 and attack.hp_debt == 4 and not attack.active
    tick = CyclicOccurrence.model_validate_json(
        next(e.kind for e in ended.resources.events if e.id == "cyclic:" + occurrence + ":3")
    )
    assert tick.damage_dice == () and tick.hp_lost == tick.fp_lost == 0
    # Recorded legacy occurrences retain their old physiological snapshots.
    legacy = machine_body(first, "b")
    legacy = legacy.model_copy(
        update={
            "resources": legacy.resources.model_copy(
                update={
                    "events": tuple(
                        e for e in legacy.resources.events if not e.id.startswith(BINDING_PREFIX)
                    )
                }
            )
        }
    )
    assert hp(advance(play, legacy, 20, (2,))) == 4


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("infected", [False, True])
async def test_pending_contact_current_machine_immunity_is_not_permanent_disease_immunity(
    tmp_path: Path, backend: str, infected: bool
) -> None:
    cid, play, occurrence = await prepare(tmp_path, backend, contagious=True, interval=86400)
    await CyclicService(play).execute(cid, expose_command(occurrence), principal_id="gm")
    state = machine_body(await current(play, cid), "c")
    state, _ = resolve(
        play.rules_context,
        state,
        expose_command(occurrence, state.revision).model_copy(update={"id": "second-contact"}),
        principal_id="gm",
    )
    # Only the living carrier draws damage. Both contacts finish without a roll,
    # infection or debt on the subject whose body changed before this deadline.
    first = advance(play, state, 86400, (2,))
    assert len(first.resources.cyclic_exposures) == 2
    assert all(
        e.stage == "resisted" and e.check is None and not e.immune and e.infection_id is None
        for e in first.resources.cyclic_exposures
    )
    assert hp(first, "c") == 10 and blocked_hp(first.resources.illnesses, "c", "natural") == 0
    assert not any(a.actor_id == "c" for a in first.resources.cyclic_attacks)
    living = machine_body(first, "c", machine=False)
    living, _ = resolve(
        play.rules_context,
        living,
        expose_command(occurrence, living.revision).model_copy(update={"id": "living-contact"}),
        principal_id="gm",
    )
    ended = advance(play, living, 172800, (6, 6, 6, 2, 2) if infected else (1, 1, 1, 2))
    exposure = ended.resources.cyclic_exposures[-1]
    assert exposure.stage == ("infected" if infected else "resisted")
    assert exposure.check is not None and exposure.immune is not infected
    assert hp(ended, "c") == (8 if infected else 10)
    assert blocked_hp(ended.resources.illnesses, "c", "natural") == (2 if infected else 0)


async def test_machine_repeat_skips_resistance_without_curing_an_established_toxic_debt(
    tmp_path: Path,
) -> None:
    cid, play, _ = await prepare(tmp_path, contagious=True, interval=10, resistible=True)
    state = machine_body(await current(play, cid), "b")
    first = advance(play, state, 10, ())
    assert hp(first) == 8 and first.resources.cyclic_attacks[0].active
    assert blocked_hp(first.resources.illnesses, "b", "natural") == 2
    ended = advance(play, machine_body(first, "b", machine=False), 20, (6, 6, 6, 2))
    assert hp(ended) == 6 and ended.resources.cyclic_attacks[0].hp_debt == 4


async def test_legacy_pending_machine_contact_keeps_recorded_infection_behavior(
    tmp_path: Path,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=86400)
    await CyclicService(play).execute(cid, expose_command(occurrence), principal_id="gm")
    state = machine_body(await current(play, cid), "c")
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "events": tuple(
                        e for e in state.resources.events if not e.id.startswith(BINDING_PREFIX)
                    )
                }
            )
        }
    )
    ended = advance(play, state, 86400, (6, 6, 6, 2))
    assert ended.resources.cyclic_exposures[0].stage == "infected"
    infection = next(a for a in ended.resources.cyclic_attacks if a.actor_id == "c")
    assert infection.cycle == 0 and infection.due == 172800


async def test_immune_machine_contact_does_not_become_a_later_living_exposure(
    tmp_path: Path,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=86400)
    state = machine_body(await current(play, cid), "c")
    state, _ = resolve(
        play.rules_context,
        state,
        expose_command(occurrence).model_copy(update={"contact": "intimate-contact"}),
        principal_id="gm",
    )
    exposure = state.resources.cyclic_exposures[0]
    assert exposure.stage == "resisted" and exposure.check is None and not exposure.immune
    state = machine_body(state, "c", machine=False)
    state, _ = resolve(
        play.rules_context,
        state,
        expose_command(occurrence, state.revision).model_copy(
            update={"id": "living-close-contact"}
        ),
        principal_id="gm",
    )
    ended = advance(play, state, 86400, (4, 4, 4, 2))
    assert ended.resources.cyclic_exposures[0] == exposure
    living = ended.resources.cyclic_exposures[-1]
    assert living.check is not None and living.check.base_target == 12
    assert living.stage == "resisted" and hp(ended, "c") == 10
    assert not any(a.actor_id == "c" for a in ended.resources.cyclic_attacks)


async def test_each_deadline_reopens_expiring_current_protection(tmp_path: Path) -> None:
    cid, play, _ = await prepare(tmp_path)
    state = await current(play, cid)
    effect = AbilityEffect(
        actor_id="b",
        ability_id="temporary-dr",
        kind="damage-resistance",
        target_id="b",
        started_at=0,
        expires_at=15,
        level=3,
        build_revision=build(play.rules_context, state, "b").revision,
    )
    event = AbilityEvent(
        actor_id="b",
        ability_id=effect.ability_id,
        target_id="b",
        effect=effect,
        outcome=AbilityOutcome(outcome="active"),
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "events": state.resources.events
                    + (
                        ResourceEvent(
                            id="ability:prior-defense",
                            at=0,
                            target_id="b",
                            kind=event.model_dump_json(),
                        ),
                    ),
                }
            )
        }
    )
    ended = advance(play, state, 20, (3, 3))
    # At10 current DR3 absorbs3. At20 the defense expired at15: loss3.
    assert hp(ended) == 5 and ended.resources.cyclic_attacks[0].hp_debt == 5
    assert ended.resources.cyclic_attacks[0].resistance == 0


async def test_current_natural_dr_refreshes_and_legacy_snapshots_are_unchanged(
    tmp_path: Path,
) -> None:
    cid, play, _ = await prepare(tmp_path)
    state = replace_build(play, await current(play, cid), "b", dr=4)
    first = advance(play, state, 10, (3,))
    assert hp(first) == 8 and first.resources.cyclic_attacks[0].resistance == 4
    second = advance(play, replace_build(play, first, "b"), 20, (3,))
    assert hp(second) == 5 and second.resources.cyclic_attacks[0].resistance == 0
    legacy = state.resources.model_copy(
        update={
            "events": tuple(
                e for e in state.resources.events if not e.id.startswith(BINDING_PREFIX)
            )
        }
    )
    replayed = play.engine.resources.apply(
        legacy,
        Advance(id="legacy-clock", actor_id="a", expected_revision=legacy.revision, to=10),
        system=True,
        rng=RecordedDice((3,)),
    )
    assert next(p.current for p in replayed.pools if p.id == "hp:b") == 5


async def test_resistance_uses_current_permanent_ht_and_b421_symptom_exemption(
    tmp_path: Path,
) -> None:
    cid, play, _ = await prepare(tmp_path)
    state = replace_build(play, await current(play, cid), "b", ht=14)
    attack = state.resources.cyclic_attacks[0].model_copy(
        update={
            "resistance_modifier": 0,
            "symptom_spec": SymptomSpec(
                kind="attribute-penalty", attribute="ht", level=6, denominator=3
            ),
            "symptom_source_id": "attack-symptoms",
        }
    )
    state = state.model_copy(update={"resources": save(state.resources, attack)})
    # Roll15 fails against the current14; damage2 raises this source's Symptoms
    # debt above1/3 only after the initial2 has also been registered below.
    from wayfarer.engine.simulation.health.symptoms import register

    assert attack.symptom_spec is not None
    resources = register(
        state.resources,
        actor_id="b",
        source_id="attack-symptoms",
        injury_id="initial-symptom",
        amount=2,
        pool_id="hp:b",
        spec=attack.symptom_spec,
        restriction_id=attack.id,
    )
    state = state.model_copy(update={"resources": resources})
    ended = advance(play, state, 20, (5, 5, 5, 2, 4, 4, 5))
    # The next resistance13 succeeds against14 despite Symptoms HT-6.
    assert hp(ended) == 6 and not ended.resources.cyclic_attacks[0].active
    ordinary = build(play.rules_context, ended, "b").statistics
    defensive = build(play.rules_context, ended, "b", defensive=True).statistics
    assert ordinary is not None and defensive is not None
    assert ordinary.ht == 8 and defensive.ht == 14


async def test_exposure_resistance_uses_current_subject_ht_without_temporary_penalties(
    tmp_path: Path,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=86400)
    await CyclicService(play).execute(cid, expose_command(occurrence), principal_id="gm")
    state = replace_build(play, await current(play, cid), "c", ht=13)
    # The recorded exposure captured HT10. Current HT13 + close contact2 =15.
    ended = advance(play, state, 86400, (4, 5, 5, 2))
    exposure = ended.resources.cyclic_exposures[0]
    assert exposure.stage == "resisted" and exposure.check is not None
    assert exposure.check.effective_target == 15
    assert hp(ended, "c") == 10


async def test_later_natural_three_does_not_create_first_attempt_immunity(tmp_path: Path) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=86400)
    state, _ = resolve(
        play.rules_context, await current(play, cid), expose_command(occurrence), principal_id="gm"
    )
    state = advance(play, state, 86400, (3, 3, 3, 2))
    assert not state.resources.cyclic_exposures[0].immune
    state, _ = resolve(
        play.rules_context,
        state,
        expose_command(occurrence, state.revision).model_copy(update={"id": "second-day"}),
        principal_id="gm",
    )
    ended = advance(play, state, 172800, (1, 1, 1, 2))
    assert all(e.stage == "resisted" and not e.immune for e in ended.resources.cyclic_exposures)


@pytest.mark.parametrize("machine", [False, True])
async def test_existing_independent_infection_is_not_reset_by_pending_contact(
    tmp_path: Path,
    machine: bool,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=86400)
    state, _ = resolve(
        play.rules_context, await current(play, cid), expose_command(occurrence), principal_id="gm"
    )
    source = state.resources.cyclic_attacks[0]
    established = source.model_copy(
        update={"id": "independent-infection", "actor_id": "c", "due": 100000, "hp_debt": 4}
    )
    resources = save(state.resources, established)
    policy = binding(resources, occurrence)
    assert policy is not None
    resources = bind_occurrence(
        resources,
        established.id,
        source_id=source.attack_id,
        source_revision=policy.source_revision,
        policy=policy.policy,
        bypass_dr=True,
        disease=True,
    )
    state = state.model_copy(update={"resources": resources})
    state = machine_body(state, "c", machine=machine)
    # Only the original carrier's scheduled damage draws. Pending contact adds
    # no second roll or replacement occurrence when infection is established.
    ended = advance(play, state, 86400, (2,))
    assert next(a for a in ended.resources.cyclic_attacks if a.id == established.id) == established
    assert len([a for a in ended.resources.cyclic_attacks if a.actor_id == "c"]) == 1
    assert ended.resources.cyclic_exposures[0].infection_id == (None if machine else established.id)


async def test_current_general_check_penalties_apply_without_reducing_resistance_attributes(
    tmp_path: Path,
) -> None:
    from test_social_completion import with_aftermath

    from wayfarer.engine.rules.types.cyclic import CyclicOccurrence

    cid, play, occurrence = await prepare(tmp_path, resistible=True)
    state = await current(play, cid)
    state = state.model_copy(update={"resources": with_aftermath(state.resources, "b")})
    ended = advance(play, state, 20, (3, 3, 3, 1, 2, 2, 3))
    # Current HT10 remains10; B361's separate -2 to checks makes roll9 fail.
    assert hp(ended) == 7 and not ended.resources.cyclic_attacks[0].active
    tick = CyclicOccurrence.model_validate_json(
        next(e.kind for e in ended.resources.events if e.id == "cyclic:" + occurrence + ":2")
    )
    assert (
        tick.check is not None and tick.check.base_target == 10 and tick.check.effective_target == 8
    )


@pytest.mark.parametrize("fitness", [1, 2])
async def test_current_fit_bonus_changes_resistance_without_changing_ht(
    tmp_path: Path, fitness: int
) -> None:
    cid, play, _ = await prepare(tmp_path, resistible=True)
    state = replace_build(play, await current(play, cid), "b", fit=fitness)
    ended = advance(play, state, 10, (4, 4, 2 + fitness))
    assert hp(ended) == 8 and not ended.resources.cyclic_attacks[0].active
    current_stats = build(play.rules_context, ended, "b").statistics
    assert current_stats is not None and current_stats.ht == 10


async def test_bound_limb_repeat_keeps_location_and_current_held_item_consequences(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
    from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext

    cid, play, _ = await prepare(tmp_path, location="right-arm")
    state = await current(play, cid)
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"ready": True, "equipped": True, "quantity": 1})
                for i in state.resources.items
            )
        }
    )
    held = Combatant(
        actor_id="b",
        initiative=5,
        reach=1,
        movement_allowance=5,
        ready_item_ids=("dose",),
        hand_bindings=(("dose", "right-hand"),),
    )
    state = state.model_copy(
        update={
            "resources": resources,
            "encounters": (
                Encounter(
                    id="current-hands",
                    participants=(held,),
                    turn_order=("b",),
                    spatial_context=BasicSpatialContext(),
                ),
            ),
        }
    )
    ended = advance(play, state, 10, (6, 3, 3, 3))
    injury = next(p.injury for p in ended.resources.pools if p.id == "hp:b")
    assert injury is not None and hp(ended) == 2
    assert any(w.location == "right-arm" for w in injury.lasting_injuries)
    assert not ended.resources.items[0].ready and not ended.resources.items[0].equipped


async def test_equipped_supply_outside_combat_catalog_is_not_armor(tmp_path: Path) -> None:
    from dataclasses import replace

    from wayfarer.engine.simulation.combat.profiles import CombatRules
    from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
    from wayfarer.engine.simulation.health.cyclic_context import resolver

    cid, play, _ = await prepare(tmp_path)
    state = await current(play, cid)
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"equipped": True, "quantity": 1})
                for i in state.resources.items
            )
        }
    )
    runtime = replace(
        play.rules_context,
        rules=play.rules_context.rules.model_copy(
            update={
                "combat": CombatRules(
                    id="empty-armor",
                    version=1,
                    gurps_equipment=EquipmentCatalog(
                        profile_id="gurps-basic-set-4e-2004", entries=()
                    ),
                ),
            }
        ),
    )
    context = resolver(runtime, state)(resources, resources.cyclic_attacks[0], "b")
    assert context.resistance == 0


async def test_first_attempt_natural_immunity_persists_without_another_resistance_roll(
    tmp_path: Path,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=86400)
    state, _ = resolve(
        play.rules_context, await current(play, cid), expose_command(occurrence), principal_id="gm"
    )
    state = advance(play, state, 86400, (1, 1, 1, 2))
    first = state.resources.cyclic_exposures[0]
    assert first.immune and first.stage == "resisted" and hp(state, "c") == 10
    state, _ = resolve(
        play.rules_context,
        state,
        expose_command(occurrence, state.revision).model_copy(update={"id": "immune-second-day"}),
        principal_id="gm",
    )
    # The only draw is the carrier's final damage; natural immunity gets no new roll.
    ended = advance(play, state, 172800, (2,))
    second = ended.resources.cyclic_exposures[-1]
    assert second.immune and second.check == first.check and hp(ended, "c") == 10


async def test_secondary_cure_preserves_prior_tertiary_exposure_and_independent_onset(
    tmp_path: Path,
) -> None:
    from test_cyclic_host import stop_command

    cid, play, occurrence = await prepare(tmp_path, contagious=True, interval=86400)
    state, _ = resolve(
        play.rules_context, await current(play, cid), expose_command(occurrence), principal_id="gm"
    )
    state, _ = resolve(
        play.rules_context, state, stop_command(occurrence, state.revision), principal_id="gm"
    )
    state = advance(play, state, 86400, (6, 6, 6, 2))
    secondary = next(a for a in state.resources.cyclic_attacks if a.actor_id == "c")
    state, _ = resolve(
        play.rules_context,
        state,
        expose_command(secondary.id, state.revision).model_copy(
            update={"id": "tertiary-contact", "actor_id": "a"}
        ),
        principal_id="gm",
    )
    state, _ = resolve(
        play.rules_context,
        state,
        stop_command(secondary.id, state.revision).model_copy(
            update={"id": "secondary-stop", "actor_id": "c"}
        ),
        principal_id="gm",
    )
    ended = advance(play, state, 172800, (6, 6, 6, 2))
    tertiary = next(a for a in ended.resources.cyclic_attacks if a.actor_id == "a")
    assert tertiary.cycle == 1 and tertiary.remaining == 2 and tertiary.active
    assert hp(ended, "a") == hp(ended, "b") == hp(ended, "c") == 8
    assert not next(a for a in ended.resources.cyclic_attacks if a.id == secondary.id).active
