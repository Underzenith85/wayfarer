"""Pure current default eligibility views; immutable approvals are never rewritten."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from wayfarer.engine.character.compiler import DerivedSheet, ValidatedBuild
from wayfarer.engine.character.skills import DefaultContext
from wayfarer.engine.simulation.campaign.default_knowledge import PROFILE, adopted, known_skills
from wayfarer.engine.simulation.campaign.default_knowledge_sources import current_skills

if TYPE_CHECKING:
    from wayfarer.engine.character.compiler import CharacterCompiler
    from wayfarer.engine.simulation.actions import PlayState


def project(
    compiler: CharacterCompiler,
    state: PlayState,
    actor_id: str,
    build: ValidatedBuild,
    *,
    required: bool = False,
) -> ValidatedBuild:
    if (
        compiler.statistics_profile != PROFILE
        or compiler.skills is None
        or (not required and not adopted(state, actor_id))
    ):
        return build
    paid = {p.definition_id for p in build.purchases}
    known = current_skills(state, actor_id) if required else known_skills(state, actor_id)
    values = tuple(
        value
        for value in build.sheet.values
        if value.target not in compiler.skills.specs
        or compiler.skills.specs[value.target].technique is not None
        or value.target in paid
        or value.target in known
    )
    return replace(build, sheet=DerivedSheet(values))


def for_check(
    compiler: CharacterCompiler,
    state: PlayState,
    actor_id: str,
    build: ValidatedBuild,
    *,
    acquisition: bool,
    enabled: bool,
    required: bool = False,
) -> DefaultContext | None:
    legacy = compiler.default_skill_context(build.purchases) if acquisition else None
    if (
        not enabled
        or compiler.statistics_profile != PROFILE
        or (not required and not adopted(state, actor_id))
    ):
        return legacy
    return replace(
        legacy or DefaultContext.empty(),
        known_default_skills=current_skills(state, actor_id)
        if required
        else known_skills(state, actor_id),
    )
