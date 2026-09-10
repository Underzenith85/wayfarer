"""Executable mundane trait effects, independent of the catalog under test.

Expected modifiers come from the selected numeric references (Characters,
Fourth Edition, third printing, B28 Status, B41 Charisma, B97 Voice) and are
written here as literals, never read back from the binding table. Reaction and
influence resolution itself stays in the existing GURPS social services.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from test_actions import campaign
from test_mundane_traits import combined_package, runtime_compiler
from test_social_dispatch import command, world
from test_statistics import gurps_draft, profile_compiler

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Purchase, ValidatedBuild
from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.character.social_traits import reaction_modifiers
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.catalog import ImplementationStatus, RuleDefinition, RulesCatalog
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.gurps_social import ReactionModifier
from wayfarer.rules.mundane_traits import PROFILE
from wayfarer.rules.mundane_traits.runtime import Audience, Check
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.actions import ActionEngine, ActionRules, ActorSetup, PlayState
from wayfarer.simulation.resources import Owner, ResourceEngine, ResourceState
from wayfarer.simulation.social import SocialCommand, SocialContext, apply_social


def approved(*purchases: Purchase) -> tuple[ValidatedBuild, CharacterCompiler]:
    engine = runtime_compiler()
    compilation = engine.compile(gurps_draft(*purchases))
    assert compilation.build is not None, compilation.diagnostics
    return compilation.build, engine


@pytest.mark.parametrize(
    ("identifier", "levels", "check", "expected"),
    [
        ("trait:charisma", 3, "reaction", 3),  # B41: +1 per level.
        ("trait:charisma", 3, "influence", 3),
        ("trait:voice", 1, "reaction", 2),  # B97: +2 to those who hear it.
        ("trait:voice", 1, "influence", None),  # Its influence skill bonus is unimplemented.
        ("trait:status", 2, "reaction", 2),  # B28: +1 per level.
        ("trait:status", 2, "influence", 2),
        ("trait:low-status", 2, "reaction", -2),
        ("trait:night-vision", 2, "reaction", None),  # Unbound: never purchasable.
    ],
)
def test_bound_modifiers_match_their_selected_source_values(
    identifier: str, levels: int, check: Check, expected: int | None
) -> None:
    engine = runtime_compiler()
    compilation = engine.compile(gurps_draft(Purchase(definition_id=identifier, amount=levels)))
    build = compilation.build
    if build is None:
        assert expected is None
        assert "definition.not_implemented" in {d.code for d in compilation.diagnostics}
        return
    modifiers = reaction_modifiers(build, engine.definitions, check)
    assert modifiers == (
        () if expected is None else (ReactionModifier("trait", expected, identifier),)
    )


def test_each_binding_keeps_its_own_audience() -> None:
    build, engine = approved(
        Purchase(definition_id="trait:charisma", amount=2),
        Purchase(definition_id="trait:voice"),
        Purchase(definition_id="trait:status"),
    )
    everyone = reaction_modifiers(build, engine.definitions, "reaction")
    assert [(m.value, m.source_id) for m in everyone] == [
        (2, "trait:charisma"),
        (1, "trait:status"),
        (2, "trait:voice"),
    ]
    unseen = reaction_modifiers(
        build, engine.definitions, "reaction", Audience(perceptible=False, audible=False)
    )
    assert [m.source_id for m in unseen] == ["trait:status"]
    unknown = reaction_modifiers(
        build, engine.definitions, "reaction", Audience(recognizes_status=False)
    )
    assert [m.source_id for m in unknown] == ["trait:charisma", "trait:voice"]


def test_a_reused_identifier_without_the_pinned_binding_contributes_nothing() -> None:
    build, engine = approved(Purchase(definition_id="trait:charisma", amount=2))
    definition = engine.definitions["trait:charisma"]
    assert definition.trait_rules is not None
    unsupported = dict(engine.definitions)
    unsupported["trait:charisma"] = replace(definition, status=ImplementationStatus.UNSUPPORTED)
    assert reaction_modifiers(build, unsupported, "reaction") == ()
    unhooked = dict(engine.definitions)
    unhooked["trait:charisma"] = replace(
        definition, trait_rules=replace(definition.trait_rules, runtime_hooks=("trait.other",))
    )
    assert reaction_modifiers(build, unhooked, "reaction") == ()
    assert reaction_modifiers(build, {}, "reaction") == ()


def test_manual_obligations_and_unbound_effects_cannot_activate() -> None:
    engine = runtime_compiler()
    for identifier, options in (
        ("trait:honesty", TraitOptions(self_control=12)),  # Self-control plus legal obligations.
        ("trait:code-of-honor-soldier", None),
        ("trait:rank-watch", None),
        ("trait:ally-associate", None),
    ):
        result = engine.compile(gurps_draft(Purchase(definition_id=identifier, trait=options)))
        assert result.build is None
        assert "definition.not_implemented" in {d.code for d in result.diagnostics}


def test_approved_self_control_disadvantage_uses_the_existing_roll() -> None:
    build, engine = approved(
        Purchase(definition_id="trait:bad-temper", trait=TraitOptions(self_control=12))
    )
    purchase = next(p for p in build.trait_purchases if p.definition_id == "trait:bad-temper")
    definition = engine.definitions["trait:bad-temper"]
    assert definition.point_cost == -10 and definition.trait_rules is not None
    context = SocialContext(
        PROFILE,
        10,
        trait_base=definition.point_cost,
        trait_levels=purchase.amount,
        trait_options=purchase.trait,
        trait_rules=definition.trait_rules,
    )
    value = SocialCommand(
        id="temper",
        actor_id="a",
        subject_id="a",
        kind="self-control",
        trigger_id="insult",
        expected_revision=0,
    )
    _, resisted = apply_social(
        ResourceState(), world(), value, context, rng=RecordedDice([3, 3, 3]), system=True
    )
    assert resisted.outcome == "resisted"
    _, triggered = apply_social(
        ResourceState(),
        world(),
        value.model_copy(update={"id": "temper-again"}),
        context,
        rng=RecordedDice([6, 6, 6]),
        system=True,
    )
    assert triggered.outcome == "triggered"


async def prepare(
    path: Path, *purchases: Purchase, extra_definitions: tuple[RuleDefinition, ...] = ()
) -> tuple[str, PlayService]:
    path.mkdir(parents=True, exist_ok=True)
    package = combined_package()
    package = replace(package, definitions=package.definitions + extra_definitions)
    base = profile_compiler(PROFILE, package=package)
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        base.rules,
        base.policy,
        statistics_profile=PROFILE,
        trait_runtime_hooks=runtime_compiler().trait_runtime_hooks,
    )
    reviewer = PowerReviewer(compiler, PowerPolicy(id="trait-social", version=1), frozenset({"gm"}))
    engine = ActionEngine(
        reviewer,
        ResourceEngine(world(), RulesCatalog((package,)), compiler.rules, compiler.policy, ()),
        ActionRules(id="trait-social", version=1),
    )
    play = PlayService(
        AsyncSQLiteStore(path / "traits.sqlite", 10), engine, rng=RecordedDice([5] * 3)
    )
    initial = campaign(engine)
    proposal = CharacterProposal(
        draft=CharacterDraft(
            name="Envoy",
            purchases=tuple(
                Purchase(definition_id="attribute:" + key, amount=10)
                for key in ("st", "dx", "iq", "ht")
            )
            + purchases,
        )
    )
    await play.create(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal, aware_of=("npc",)),),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    return initial["id"], play


def plain(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
    return ResolvedInteraction(SocialContext(PROFILE, 10))


async def test_dispatch_applies_the_approved_build_and_refuses_supplied_trait_modifiers(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path / "plain")
    # The same recorded 3d6 reaches the existing table with and without traits.
    plain_outcome = await SocialService(play, plain).execute(
        cid, command(), authenticated_gm_id="gm"
    )
    assert plain_outcome.outcome == "good"
    charismatic, second = await prepare(
        tmp_path / "charisma", Purchase(definition_id="trait:charisma", amount=2)
    )
    improved = await SocialService(second, plain).execute(
        charismatic, command(), authenticated_gm_id="gm"
    )
    assert improved.outcome == "very-good"

    def supplied(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(
            SocialContext(PROFILE, 10, modifiers=(ReactionModifier("trait", 5, "invented"),))
        )

    blocked, third = await prepare(tmp_path / "supplied")
    with pytest.raises(ValidationError, match="derived from approved builds"):
        await SocialService(third, supplied).execute(blocked, command(), authenticated_gm_id="gm")
