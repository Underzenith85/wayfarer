"""Opt-in server-authored v2 policy; frozen v1 scenario documents are unchanged."""

import json
from typing import Literal

from pydantic import Field

from wayfarer.simulation.actions import ActionRules
from wayfarer.simulation.npcs import NPCSocialRules
from wayfarer.simulation.scenario_document import PortableGraph, ScenarioDocumentBase
from wayfarer.simulation.studio import ScenarioGraph


class SocialActionRules(ActionRules):
    npcs: NPCSocialRules | None = Field(default=None, exclude=True)


# Opt-in document/graph models keep frozen v1 schemas narrow. The policy version
# is also the saved graph's explicit discriminator when rebinding after restart.


class SocialPortableGraph(PortableGraph):
    actions: SocialActionRules
    npcs: NPCSocialRules


class SocialScenarioDocument(ScenarioDocumentBase):
    schema_version: Literal[2]
    graph: SocialPortableGraph


class SocialScenarioGraph(ScenarioGraph):
    actions: SocialActionRules
    npcs: NPCSocialRules


def parse_graph(source: str) -> ScenarioGraph:
    raw = json.loads(source)
    policy = raw.get("npcs") if isinstance(raw, dict) else None
    model = (
        SocialScenarioGraph
        if isinstance(policy, dict) and policy.get("version") == 2
        else ScenarioGraph
    )
    return model.model_validate_json(source)
