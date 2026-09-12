"""Derive background traits from approved purchases and campaign identity context."""

from collections.abc import Mapping

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.background_traits import (
    BackgroundTraits,
    LanguageAbility,
    comprehension,
    free_status,
)
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class BackgroundContext(Record):
    native_language: str = "trade"
    native_culture: str = "home"
    organization_memberships: tuple[str, ...] = ("watch",)
    wealth_grants_status: bool = True


DEFAULT_BACKGROUND_CONTEXT = BackgroundContext()


def background_traits(
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    context: BackgroundContext = DEFAULT_BACKGROUND_CONTEXT,
) -> BackgroundTraits:
    wealth = "average"
    status = 0
    talent = False
    ranks: list[tuple[str, int, str]] = []
    language_points: dict[str, dict[str, int]] = {
        context.native_language: {"spoken": 3, "written": 3}
    }
    cultures = {context.native_culture}
    for purchase in build.trait_purchases:
        definition = definitions.get(purchase.definition_id)
        if (
            definition is None
            or definition.trait_rules is None
            or definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.trait_rules.profile_id != "gurps-basic-set-4e-2004"
            or definition.trait_rules.runtime_hooks[0]
            not in {
                "trait.wealth",
                "trait.status",
                "trait.rank",
                "trait.language",
                "trait.language_talent",
                "trait.culture",
            }
        ):
            continue
        identifier = purchase.definition_id.removeprefix("trait:")
        if identifier.startswith("wealth-"):
            wealth = identifier.removeprefix("wealth-")
        elif identifier == "status":
            status += purchase.amount
        elif identifier == "low-status":
            status -= purchase.amount
        elif identifier == "language-talent":
            talent = True
        elif (
            identifier.startswith("rank-")
            or identifier.startswith("rank-replaces-status-")
            or identifier.startswith("courtesy-rank-")
        ):
            kind = (
                "replaces-status"
                if identifier.startswith("rank-replaces-status-")
                else "courtesy"
                if identifier.startswith("courtesy-rank-")
                else "ordinary"
            )
            organization = (
                identifier.split("-status-", 1)[-1]
                if kind == "replaces-status"
                else identifier.removeprefix("courtesy-rank-").removeprefix("rank-")
            )
            if organization not in context.organization_memberships:
                raise ValidationError("Rank requires campaign organization membership")
            ranks.append((organization, purchase.amount, kind))
        elif identifier.startswith("language-"):
            language, mode = identifier.removeprefix("language-").rsplit("-", 1)
            language_points.setdefault(language, {"spoken": 0, "written": 0})[mode] = (
                purchase.amount
            )
        elif identifier.startswith("culture-"):
            cultures.add(identifier.removeprefix("culture-"))
    if any(kind == "replaces-status" for _, _, kind in ranks):
        status = sum(level for _, level, kind in ranks if kind == "replaces-status")
        bonus = 0
    else:
        bonus = free_status(wealth, tuple(ranks), context.wealth_grants_status)
    languages = tuple(
        LanguageAbility(
            language_id=key,
            spoken=comprehension(value["spoken"], talent=talent and key != context.native_language),
            written=comprehension(
                value["written"], talent=talent and key != context.native_language
            ),
        )
        for key, value in sorted(language_points.items())
    )
    return BackgroundTraits(
        wealth=wealth,
        purchased_status=status,
        free_status=bonus,
        ranks=tuple(ranks),
        languages=languages,
        cultures=tuple(sorted(cultures)),
    )
