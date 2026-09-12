"""Independent B21, B26-28 construction and runtime examples (Characters 4e).

The selected third-printing values are literal expectations. First-printing
errata reconciliation remains a separate certification blocker.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from test_mundane_trait_runtime import approved, plain, prepare
from test_mundane_traits import runtime_compiler
from test_social_dispatch import command
from test_statistics import gurps_draft

from wayfarer.character.compiler import Purchase
from wayfarer.character.social_traits import bind_standing
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.rules.catalog import ImplementationStatus
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.gurps_social import ReactionModifier
from wayfarer.rules.mundane_traits import PROFILE
from wayfarer.rules.mundane_traits.runtime import Audience
from wayfarer.rules.social_hooks import Reputation, Standing, standing_modifiers
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.social import SocialCommand, SocialContext


@pytest.mark.parametrize(
    ("level", "expected", "attracted"),
    [
        ("hideous", -4, False),
        ("ugly", -2, False),
        ("unattractive", -1, False),
        ("average", 0, False),
        ("attractive", 1, False),
        ("handsome", 2, False),
        ("handsome", 4, True),
    ],
)
def test_purchased_appearance(level: str, expected: int, attracted: bool) -> None:
    build, compiler = approved(Purchase(definition_id=f"trait:appearance-{level}"))
    value = bind_standing(build, compiler.definitions, None)
    assert value is not None
    rng = RecordedDice([])
    trace = standing_modifiers(PROFILE, value, Audience(attracted=attracted), rng=rng)
    assert trace.total == expected
    assert rng.exhausted()


@pytest.mark.parametrize(
    "audience",
    [
        Audience(perceptible=False),
        Audience(visible=False),
        Audience(appearance_applicable=False),
    ],
)
def test_appearance_requires_sight_and_an_affected_race(audience: Audience) -> None:
    build, compiler = approved(Purchase(definition_id="trait:appearance-handsome"))
    standing = bind_standing(build, compiler.definitions, None)
    assert standing is not None
    assert standing_modifiers(PROFILE, standing, audience, rng=RecordedDice([])).total == 0


@pytest.mark.parametrize(
    ("detail", "level", "expected"),
    [
        ("bravery", 1, 1),
        ("bravery", 4, 4),
        ("cruelty", 1, -1),
        ("cruelty", 4, -4),
    ],
)
def test_purchased_reputation_needs_no_recognition_dice(
    detail: str, level: int, expected: int
) -> None:
    build, compiler = approved(Purchase(definition_id=f"trait:reputation-{detail}", amount=level))
    standing = bind_standing(build, compiler.definitions, None)
    assert standing is not None
    trace = standing_modifiers(PROFILE, standing, Audience(visible=False), rng=RecordedDice([]))
    assert trace.total == expected
    assert trace.recognition == ()


@pytest.mark.parametrize(
    ("identifier", "authored", "modifiers"),
    [
        ("appearance-handsome", Standing(appearance="handsome"), ()),
        ("appearance-average", Standing(appearance="ugly"), ()),
        ("appearance-handsome", None, (ReactionModifier("appearance", 4, "invented"),)),
        ("reputation-bravery", Standing(reputations=(Reputation("alias", 1),)), ()),
        ("reputation-bravery", None, (ReactionModifier("reputation", 1, "invented"),)),
    ],
)
def test_purchased_sources_cannot_be_duplicated_or_overridden(
    identifier: str,
    authored: Standing | None,
    modifiers: tuple[ReactionModifier, ...],
) -> None:
    build, compiler = approved(Purchase(definition_id=f"trait:{identifier}"))
    with pytest.raises(ValidationError, match="cannot also be supplied"):
        bind_standing(build, compiler.definitions, authored, modifiers)


def test_legacy_standing_and_unrelated_sources_are_preserved() -> None:
    authored = Standing(reputations=(Reputation("secret", -2, hidden=True),))
    build, compiler = approved()
    assert bind_standing(build, compiler.definitions, authored) is authored
    build, compiler = approved(Purchase(definition_id="trait:appearance-handsome"))
    result = bind_standing(build, compiler.definitions, authored)
    assert result == Standing("handsome", authored.reputations)
    assert standing_modifiers(PROFILE, result, rng=RecordedDice([])).public_modifiers == (
        ReactionModifier("appearance", 2, "appearance:handsome"),
    )


@pytest.mark.parametrize("identifier", ["appearance-handsome", "reputation-bravery"])
def test_definition_must_pin_the_implemented_hook(identifier: str) -> None:
    key = f"trait:{identifier}"
    build, compiler = approved(Purchase(definition_id=key))
    definition = compiler.definitions[key]
    assert definition.trait_rules is not None
    for replacement in (
        replace(definition, status=ImplementationStatus.UNSUPPORTED),
        replace(definition, trait_rules=replace(definition.trait_rules, runtime_hooks=())),
    ):
        definitions = dict(compiler.definitions)
        definitions[key] = replacement
        assert bind_standing(build, definitions, None) is None
    assert bind_standing(build, {}, None) is None


def test_very_handsome_activates_only_with_its_completed_binding() -> None:
    result = runtime_compiler().compile(
        gurps_draft(Purchase(definition_id="trait:appearance-very-handsome"))
    )
    assert result.build is not None
    standing = bind_standing(result.build, runtime_compiler().definitions, None)
    assert standing is not None and standing.appearance == "very-handsome"


async def test_dispatch_combines_purchases_once_and_replays_without_resolving(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(
        tmp_path,
        Purchase(definition_id="trait:appearance-handsome"),
        Purchase(definition_id="trait:reputation-bravery", amount=2),
    )
    service = SocialService(play, plain)
    outcome = await service.execute(cid, command(), authenticated_gm_id="gm")
    # B21/B27: 15 + 2 (appearance) + 2 (reputation) = 19, Excellent.
    assert outcome.outcome == "excellent"

    def fail(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        raise AssertionError("A replay must not evaluate standing or consume dice")

    repeated = await SocialService(play, fail).execute(cid, command(), authenticated_gm_id="gm")
    assert repeated == outcome
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


async def test_dispatch_rejects_duplicate_standing_before_rolling(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, Purchase(definition_id="trait:appearance-handsome"))

    def duplicate(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext(PROFILE, 10, standing=Standing("handsome")))

    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="cannot also be supplied"):
        await SocialService(play, duplicate).execute(cid, command(), authenticated_gm_id="gm")
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and not play.rng.exhausted()


def test_composed_template_prices_and_activates_through_the_existing_compiler() -> None:
    from wayfarer.character.templates import TemplateCatalog, representative_templates

    compiler = runtime_compiler()
    templates = TemplateCatalog(representative_templates(), compiler)
    result = templates.preview(gurps_draft(), ("template:celebrated-envoy",))
    # Original Envoy [20] + Handsome [12] + Reputation +2 [10].
    assert result.compilation.spent == 42
    build = result.compilation.build
    assert build is not None
    standing = bind_standing(build, compiler.definitions, None)
    assert standing == Standing("handsome", (Reputation("trait:reputation-bravery", 2),))
    conflicting = templates.preview(
        gurps_draft(Purchase(definition_id="trait:appearance-ugly")),
        ("template:celebrated-envoy",),
    )
    assert conflicting.compilation.build is None
    assert "purchase.exclusion" in {d.code for d in conflicting.compilation.diagnostics}


def test_item_audit_retains_inventory_and_concrete_runtime_owners() -> None:
    from wayfarer.rules.mundane_traits import inventory
    from wayfarer.source_audit import inventory as source_inventory

    rows = {row.id: row for row in source_inventory() if row.scope == "mundane-traits"}
    for entry in inventory():
        assert rows[entry.id].blockers == entry.followup_issues
        assert 113 in entry.followup_issues
        if not entry.implemented:
            assert set(entry.followup_issues) & {332, 333, 334, 335}
    assert rows["trait:appearance-very-handsome"].blockers == (113,)
