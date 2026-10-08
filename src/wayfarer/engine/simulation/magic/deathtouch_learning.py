"""Two pinned B244–245 purchased prerequisite routes for personal Deathtouch."""

from collections.abc import Mapping

from wayfarer.engine.rules.magic.body_control import package as body_package
from wayfarer.engine.rules.magic.movement import package as movement_package
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError

COMMON = ("itch", "spasm", "pain", "hinder", "paralyze-limb", "wither-limb", "deathtouch")
ROUTES = ((*COMMON, "clumsiness"), (*COMMON, "haste", "rooted-feet"))


def require_learning(runtime: RulesContext, purchases: Mapping[str, int]) -> None:
    """Haste qualifies Hinder; Rooted Feet supplies its missing college witness.

    Current approved-build admission remains responsible for the compiler's
    acyclic five-other-Body-Control and Magery prerequisites. This bounded
    consumer accepts only these two complete, genuinely purchased routes.
    """
    expected = {d.id: d for p in (body_package(), movement_package()) for d in p.definitions}
    definitions = runtime.reviewer.compiler.definitions
    for route in ROUTES:
        if not all(purchases.get("spell:" + key, 0) > 0 for key in route):
            continue
        if any(definitions.get("spell:" + key) != expected["spell:" + key] for key in route):
            raise ValidationError("Deathtouch requires exact source-bound Body Control learning")
        return
    raise ValidationError("Bounded Deathtouch requires its genuinely purchased source chain")
