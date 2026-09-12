"""Independent psionic power expectations, Characters B254-257."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.character.traits.psi_powers import PsiAllocation, PsiLoadout, psi_powers
from wayfarer.engine.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.rules.traits.attack_defense import RUNTIME_HOOKS as ATTACK_HOOKS
from wayfarer.engine.rules.traits.attack_defense import package as attack_package
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mental_spirit import RUNTIME_HOOKS as MENTAL_HOOKS
from wayfarer.engine.rules.traits.mental_spirit import package as mental_package
from wayfarer.engine.rules.traits.psi_powers import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.psi_powers import package as psi_package
from wayfarer.engine.rules.traits.world_travel import RUNTIME_HOOKS as TRAVEL_HOOKS
from wayfarer.engine.rules.traits.world_travel import package as travel_package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.abilities import AbilityContext, apply_ability
from wayfarer.engine.simulation.ability_types import AbilityChannel, AbilityCommand, AbilitySpec
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.psi_powers import (
    PsiInterference,
    PsiInterferenceCommand,
    apply_ability_context,
    apply_interference,
    history,
    power_is_blocked,
)
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError

POWER_IDS = {
    "power:antipsi",
    "power:esp",
    "power:psychic-healing",
    "power:psychokinesis",
    "power:telepathy",
    "power:teleportation",
}


def compiler() -> CharacterCompiler:
    base = profile_package(PROFILE)
    packages = (attack_package(), mental_package(), travel_package(), psi_package())
    combined = replace(
        base,
        id="package:test-psi-powers",
        definitions=base.definitions
        + tuple(definition for package in packages for definition in package.definitions),
    )
    policy = CampaignPolicy(
        "policy:psi-powers",
        1,
        10000,
        10000,
        20,
        20,
        frozenset(source.id for source in combined.sources),
        allow_supernatural=True,
    )
    rules = CampaignRules(
        combined.edition,
        (PackagePin(combined.id, combined.version, combined.digest),),
        policy.id,
        policy.version,
    )
    return CharacterCompiler(
        RulesCatalog((combined,)),
        rules,
        policy,
        statistics_profile=PROFILE,
        trait_runtime_hooks=RUNTIME_HOOKS | ATTACK_HOOKS | MENTAL_HOOKS | TRAVEL_HOOKS,
    )


def approved(*purchases: Purchase) -> tuple[ValidatedBuild, CharacterCompiler]:
    engine = compiler()
    result = engine.compile(gurps_draft(*purchases))
    assert result.build is not None, result.diagnostics
    return result.build, engine


def loadout(build: ValidatedBuild, *allocations: PsiAllocation) -> PsiLoadout:
    return PsiLoadout(build_revision=build.revision, allocations=allocations)


def test_registry_and_inventory_account_for_all_six_power_groups() -> None:
    assert {binding.id for binding in BINDINGS} == POWER_IDS
    assert all(
        binding.talent_cost == 5 and binding.power_modifier == -10
        for binding in BINDINGS
        if binding.id != "power:antipsi"
    )
    antipsi = next(binding for binding in BINDINGS if binding.id == "power:antipsi")
    assert (antipsi.talent_id, antipsi.talent_cost, antipsi.power_modifier) == (None, None, 0)
    rows = {row.id: row for row in inventory().entries if row.id in POWER_IDS}
    assert set(rows) == POWER_IDS
    assert all(row.blockers == (191,) for row in rows.values())
    assert all(row.evidence == ("tests/test_psi_powers.py",) for row in rows.values())


def test_talent_latency_cap_member_discount_and_learning_permission() -> None:
    build, engine = approved(Purchase(definition_id="advantage:teleportation-talent", amount=2))
    latent = psi_powers(build, engine.definitions, loadout(build))
    assert latent.talent("power:teleportation") == 2
    assert latent.has_power("power:teleportation")
    assert latent.can_learn("power:teleportation")
    assert not latent.can_learn("power:telepathy")
    assert latent.can_learn("power:telepathy", gm_permission=True)
    assert (
        compiler()
        .compile(gurps_draft(Purchase(definition_id="advantage:teleportation-talent", amount=5)))
        .build
        is None
    )

    warp, engine = approved(
        Purchase(
            definition_id="advantage:warp",
            trait=TraitOptions(parameters=(("reliability", 0),)),
        ),
        Purchase(definition_id="advantage:teleportation-talent", amount=2),
    )
    allocation = PsiAllocation(
        power_id="power:teleportation",
        ability_id="advantage:warp",
        power_modifier=-10,
    )
    powers = psi_powers(warp, engine.definitions, loadout(warp, allocation))
    assert powers.activation_bonus("power:teleportation", "advantage:warp") == 2
    assert powers.adjusted_cost("power:teleportation", "advantage:warp") == 90
    assert powers.point_credit() == 10


@pytest.mark.parametrize(
    "allocation",
    [
        PsiAllocation(
            power_id="power:telepathy",
            ability_id="advantage:warp",
            power_modifier=-10,
        ),
        PsiAllocation(
            power_id="power:teleportation",
            ability_id="advantage:warp",
            power_modifier=0,
        ),
        PsiAllocation(
            power_id="power:psychokinesis",
            ability_id="advantage:damage-resistance",
            power_modifier=-10,
        ),
    ],
)
def test_invalid_members_modifiers_and_required_variants_fail_closed(
    allocation: PsiAllocation,
) -> None:
    purchases = [
        Purchase(
            definition_id="advantage:warp",
            trait=TraitOptions(parameters=(("reliability", 0),)),
        )
    ]
    if allocation.ability_id == "advantage:damage-resistance":
        purchases = [Purchase(definition_id="advantage:damage-resistance")]
    build, engine = approved(*purchases)
    with pytest.raises(ValidationError, match="pinned power binding"):
        psi_powers(build, engine.definitions, loadout(build, allocation))
    with pytest.raises(ValidationError, match="approved build"):
        psi_powers(build, engine.definitions, PsiLoadout(build_revision="stale"))


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("jammer", EntityKind.ACTOR, "Jammer", "room"),
            Entity("psi", EntityKind.ACTOR, "Psi", "room"),
        ),
        facts=(Fact("visible", "jammer", "visible", "person"),),
        knowledge=(("jammer", "visible"), ("psi", "visible")),
    )


def test_antipsi_interference_is_authorized_timed_replayable_and_restart_safe() -> None:
    build, engine = approved(Purchase(definition_id="advantage:psi-static"))
    allocation = PsiAllocation(
        power_id="power:antipsi",
        ability_id="advantage:psi-static",
        power_modifier=0,
    )
    approved_loadout = loadout(build, allocation)
    interference = PsiInterference(
        id="jam",
        ability_id="advantage:psi-static",
        actor_id="jammer",
        location_id="room",
        blocked_power_id="power:telepathy",
        duration_seconds=10,
    )
    command = PsiInterferenceCommand(
        id="activate",
        actor_id="jammer",
        expected_revision=0,
        interference_id="jam",
        action="activate",
    )
    state, outcome = apply_interference(
        ResourceState(),
        world(),
        command,
        build,
        engine.definitions,
        approved_loadout,
        (interference,),
        authorized_actor_id="jammer",
        system=True,
    )
    assert outcome.action == "activated" and outcome.expires_at == 10
    assert power_is_blocked(
        state, world(), (interference,), actor_id="psi", power_id="power:telepathy"
    )
    assert not power_is_blocked(
        state, world(), (interference,), actor_id="psi", power_id="power:esp"
    )
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_interference(
        restarted,
        world(),
        command,
        build,
        engine.definitions,
        approved_loadout,
        (interference,),
        authorized_actor_id="jammer",
        system=True,
    ) == (restarted, outcome)
    assert history(restarted)[0].outcome == outcome
    with pytest.raises(ValidationError, match="authority"):
        apply_interference(
            ResourceState(),
            world(),
            command,
            build,
            engine.definitions,
            approved_loadout,
            (interference,),
            authorized_actor_id="psi",
            system=True,
        )
    with pytest.raises(ConflictError, match="revision"):
        apply_interference(
            ResourceState(revision=1),
            world(),
            command,
            build,
            engine.definitions,
            approved_loadout,
            (interference,),
            authorized_actor_id="jammer",
            system=True,
        )


def test_telepathy_talent_and_resistance_feed_existing_ability_service() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:mind-reading"),
        Purchase(definition_id="advantage:telepathy-talent", amount=2),
    )
    allocation = PsiAllocation(
        power_id="power:telepathy",
        ability_id="advantage:mind-reading",
        power_modifier=-10,
    )
    powers = psi_powers(build, engine.definitions, loadout(build, allocation))
    spec = AbilitySpec(
        definition_id="advantage:mind-reading",
        kind="mind-reading",
        modifiers=("telepathic",),
    )
    channel = AbilityChannel(
        id="channel",
        ability_id=spec.definition_id,
        actor_id="psi",
        target_id="jammer",
        location_id="room",
    )
    context = apply_ability_context(
        AbilityContext(
            PROFILE,
            1,
            TraitOptions(modifiers=("telepathic",)),
            12,
            12,
            12,
            10,
            target_will=10,
            channel=channel,
            build_revision=build.revision,
        ),
        spec,
        powers,
        power_id="power:telepathy",
        target_resistance=3,
    )
    assert (context.iq, context.will, context.per, context.target_will) == (14, 14, 14, 13)
    resources = ResourceState(
        pools=tuple(
            Pool(
                id="hp:" + actor,
                current=10,
                maximum=10,
                injury=InjuryStatus(profile_id=PROFILE),
            )
            for actor in ("psi", "jammer")
        )
        + (
            Pool(
                id="fp:psi",
                current=10,
                maximum=10,
                fatigue=FatigueStatus(profile_id=PROFILE),
            ),
        )
    )
    started, _, result = apply_ability(
        resources,
        world(),
        AbilityCommand(
            id="use",
            actor_id="psi",
            expected_revision=0,
            kind="activate",
            ability_id=spec.definition_id,
            channel_id="channel",
        ),
        spec,
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.outcome == "concentrating" and started.revision == 1
    with pytest.raises(ValidationError, match="living sentient"):
        apply_ability_context(
            context,
            spec,
            powers,
            power_id="power:telepathy",
            living_sentient_target=False,
        )
    with pytest.raises(ValidationError, match="suppressed"):
        apply_ability_context(
            context,
            spec,
            powers,
            power_id="power:telepathy",
            blocked=True,
        )
