"""Independent learning expectations; source certification remains partial."""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_statistics import gurps_draft

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import PackagePin, RulesCatalog
from wayfarer.rules.effects import Effect, Operation
from wayfarer.rules.gurps_magic import MAGERY, MAGERY_ZERO, PROFILE
from wayfarer.rules.profiles import (
    DEFAULT_REGISTRY,
    GURPS_BASIC_PROFILE,
    GURPS_MAGIC_PROFILE,
)


def compiler(
    *, ceiling: int = 30, supernatural: bool = True, effects: tuple[tuple[str, Effect], ...] = ()
) -> CharacterCompiler:
    profile = GURPS_MAGIC_PROFILE
    return CharacterCompiler(
        profile.catalog,
        profile.rules,
        replace(
            profile.policy, allow_supernatural=supernatural, point_budget=500, skill_ceiling=ceiling
        ),
        effects=effects,
        statistics_profile=PROFILE,
    )


def draft(*spells: tuple[str, int], magery: int = 0, iq: int = 12) -> CharacterDraft:
    traits: tuple[Purchase, ...] = (Purchase(definition_id=MAGERY_ZERO),) if magery >= 0 else ()
    if magery > 0:
        traits += (Purchase(definition_id=MAGERY, amount=magery),)
    result = gurps_draft(
        *traits, *(Purchase(definition_id="spell:" + k, amount=n) for k, n in spells)
    )
    return result.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": iq}) if p.definition_id == "attribute:iq" else p
                for p in result.purchases
            )
        }
    )


@pytest.mark.parametrize(
    "magery,points,level,cost",
    [
        (-1, 1, 10, 1),
        (0, 1, 10, 6),
        (1, 1, 11, 16),
        (2, 1, 12, 26),
        (3, 2, 14, 37),
        (1, 4, 13, 19),
        (1, 8, 14, 23),
    ],
)
def test_iq_hard_learning_and_magery_costs(magery: int, points: int, level: int, cost: int) -> None:
    result = compiler().compile(draft(("light", points), magery=magery))
    assert result.build is not None, result.diagnostics
    assert result.spent == 40 + cost  # IQ 12 costs 40; all other attributes are 10.
    values = {v.target: v.value for v in result.build.sheet.values}
    assert values["spell:light"] == level
    assert values["attribute:iq"] == 12  # Magery cannot raise ordinary IQ skills.
    assert "spell:daze" not in values  # Spells have no untrained defaults.


@pytest.mark.parametrize("magery,points,legal", [(0, 1, False), (0, 4, True), (2, 1, True)])
def test_prerequisite_requires_trained_level_twelve_including_magery(
    magery: int, points: int, legal: bool
) -> None:
    result = compiler().compile(draft(("foolishness", points), ("daze", 1), magery=magery))
    assert result.legal is legal
    if not legal:
        assert any(d.code == "skill.prerequisite" for d in result.diagnostics)


def test_iq_prerequisite_cannot_be_replaced_by_magery() -> None:
    result = compiler().compile(draft(("foolishness", 8), magery=3, iq=11))
    assert any(d.code == "spell.prerequisite" for d in result.diagnostics)


def test_fireball_requires_magery_and_both_trained_parent_spells() -> None:
    spells = (("ignite-fire", 4), ("create-fire", 4), ("shape-fire", 4), ("fireball", 1))
    assert compiler().compile(draft(*spells, magery=1)).legal
    for missing in ("ignite-fire", "create-fire", "shape-fire"):
        assert (
            not compiler().compile(draft(*(s for s in spells if s[0] != missing), magery=1)).legal
        )
    assert not compiler().compile(draft(*spells, magery=0)).legal


def test_no_magery_levels_without_magery_zero() -> None:
    value = draft(("light", 1), magery=1)
    value = value.model_copy(
        update={"purchases": tuple(p for p in value.purchases if p.definition_id != MAGERY_ZERO)}
    )
    assert not compiler().compile(value).legal


def test_magery_obeys_skill_ceiling_and_supernatural_policy() -> None:
    assert not compiler(ceiling=12).compile(draft(("light", 4), magery=1)).legal
    assert not compiler(supernatural=False).compile(draft(("light", 1))).legal


def test_catalog_metadata_cannot_be_forged_to_change_learning_rules() -> None:
    profile = GURPS_MAGIC_PROFILE
    package = replace(
        profile.packages[0],
        definitions=tuple(
            replace(d, point_cost=0) if d.id == MAGERY else d
            for d in profile.packages[0].definitions
        ),
    )
    rules = replace(
        profile.rules,
        packages=(
            PackagePin(package.id, package.version, package.digest),
            profile.rules.packages[1],
        ),
    )
    with pytest.raises(ValidationError, match="exact Basic Set catalog"):
        CharacterCompiler(
            RulesCatalog((package, profile.packages[1])),
            rules,
            profile.policy,
            statistics_profile=PROFILE,
        )


def test_magery_and_effect_bonus_apply_once_and_propagate_to_prerequisites() -> None:
    effect = Effect(
        id="bonus",
        source_id="spell:foolishness",
        source_version="0.4.0",
        target="spell:foolishness",
        operation=Operation.ADD,
        value=Decimal(1),
    )
    result = compiler(effects=(("spell:foolishness", effect),)).compile(
        draft(("foolishness", 1), ("daze", 1), magery=1)
    )
    assert result.build is not None, result.diagnostics
    values = {v.target: v.value for v in result.build.sheet.values}
    assert values["spell:foolishness"] == 12
    assert values["spell:daze"] == 11


def test_new_version_requires_explicit_selection_and_remains_uncertified() -> None:
    assert GURPS_BASIC_PROFILE.version == 3
    assert all(
        not d.id.startswith("spell:") for p in GURPS_BASIC_PROFILE.packages for d in p.definitions
    )
    assert DEFAULT_REGISTRY.get(GURPS_MAGIC_PROFILE.id, 4) == GURPS_MAGIC_PROFILE
    assert DEFAULT_REGISTRY.resolve(GURPS_BASIC_PROFILE.reference) == GURPS_BASIC_PROFILE
    with pytest.raises(ValidationError, match="not supported"):
        DEFAULT_REGISTRY.require_supported(GURPS_MAGIC_PROFILE.id, 4)
    assert "gurps.magic.spellcasting" in GURPS_MAGIC_PROFILE.unverified_capabilities
