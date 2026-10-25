"""B173 joins for native procedures that historically supplied numeric defaults."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.default_knowledge import PROFILE, adopted, known_skills
from wayfarer.engine.simulation.campaign.default_knowledge_policy import active
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def optional_skill(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    build: ValidatedBuild,
    key: str,
    legacy_default: int,
    *,
    fresh: bool = False,
) -> int | None:
    target = "skill:" + key
    value = next((v.value for v in build.sheet.values if v.target == target), None)
    enabled = (
        runtime.reviewer.compiler.statistics_profile == PROFILE
        and runtime.correct_default_knowledge
        and (runtime.require_default_knowledge or adopted(state, actor_id))
        if fresh
        else active(runtime, state, actor_id)
    )
    if not enabled:
        return int(value) if value is not None else legacy_default
    learned = any(p.definition_id == target for p in build.purchases)
    if not learned and target not in known_skills(state, actor_id):
        return None
    # A true societal observation supplies no missing catalog edge, acquisition
    # prerequisite, background payment or unsupported definition permission.
    return int(value) if value is not None else None


def require_skill(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    build: ValidatedBuild,
    key: str,
    legacy_default: int,
    *,
    fresh: bool = False,
) -> int:
    value = optional_skill(runtime, state, actor_id, build, key, legacy_default, fresh=fresh)
    if value is None:
        raise ValidationError(
            "Physical or care skill has no learned or society-known legal default"
        )
    return value
