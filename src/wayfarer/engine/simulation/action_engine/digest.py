"""The configuration digest a campaign is pinned to."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.simulation.actions import ActionRules

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


def _configuration_digest(
    reviewer: PowerReviewer, resources: ResourceEngine, rules: ActionRules
) -> str:
    """Pin the rules, policy, effects and equipment a checkpoint was produced under."""
    # Preserve the exact Wave 7 digest for campaigns that have not enabled
    # adjudication. Enabling or changing policy requires explicit migration.
    excluded = {
        field
        for field, disabled in (
            ("adjudication", rules.adjudication is None),
            ("combat", rules.combat is None),
            ("scenes", rules.scenes is None),
        )
        if disabled
    }
    encoded_rules = rules.model_dump_json(exclude=excluded)
    payload = (
        encoded_rules
        + (rules.scenes.model_dump_json() if rules.scenes is not None else "")
        + (
            "".join(
                p.model_dump_json()
                for p in (
                    *rules.combat.attacks,
                    *rules.combat.protection,
                    *rules.combat.consequences,
                )
            )
            if rules.combat is not None
            else ""
        )
        + (rules.objectives.model_dump_json() if rules.objectives else "")
        + (
            rules.combat.gurps_equipment.model_dump_json()
            if rules.combat and rules.combat.gurps_equipment
            else ""
        )
        + (rules.noncombat.model_dump_json() if rules.noncombat else "")
        + (rules.party.model_dump_json() if rules.party else "")
        + (rules.npcs.model_dump_json() if rules.npcs else "")
        + (rules.recovery.model_dump_json() if rules.recovery else "")
        + (rules.abilities.model_dump_json() if rules.abilities else "")
        + (rules.spells.model_dump_json() if rules.spells else "")
        + (rules.administration.model_dump_json() if rules.administration else "")
        + (rules.law.model_dump_json() if rules.law else "")
        + reviewer.policy.digest
        + repr(resources.rules)
        + repr(reviewer.compiler.effects)
        + "".join(spec.model_dump_json() for _, spec in sorted(resources.specs.items()))
    )
    return hashlib.sha256(payload.encode()).hexdigest()
