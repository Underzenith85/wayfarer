"""Immutable adventure endings and actor-scoped, evidence-only epilogues."""

from dataclasses import asdict

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.models import Record


class AdventureSnapshot(Record):
    graph: ScenarioGraph
    state: PlayState

    def project(self, actor_ids: tuple[str, ...]) -> dict[str, object]:
        state = self.state
        known = {f for a, f in state.world.knowledge if a in actor_ids}
        objectives = {o.id: o for o in self.graph.objectives.objectives}
        evidence = [
            {
                "id": e.objective_id,
                "title": objectives[e.objective_id].title,
                "satisfied": e.satisfied,
                "predicates": list(e.predicates),
            }
            for e in state.objectives.evidence
            if not e.visible_to or set(e.visible_to) & set(actor_ids)
        ]
        return {
            "adventure_id": self.graph.id,
            "title": self.graph.title,
            "outcome": state.objectives.outcome,
            "revision": state.revision,
            "at": state.objectives.at,
            "evidence": evidence,
            "discoveries": [asdict(f) for f in state.world.facts if f.id in known],
            "casualties": [a for a in state.recovery.dead_actor_ids if a in actor_ids],
            "commitments": [
                asdict(c)
                for c in state.world.commitments
                if (c.reveal_fact_id is not None and c.reveal_fact_id in known)
                or (
                    c.reveal_fact_id is None
                    and (c.debtor_id in actor_ids or c.creditor_id in actor_ids)
                )
            ],
            "rewards": [
                r.model_dump(mode="json")
                for r in self.graph.objectives.rewards
                if r.id in state.objectives.settled_reward_ids and r.actor_id in actor_ids
            ],
            "advancement": [
                e.model_dump(mode="json") for e in state.advancement if e.actor_id in actor_ids
            ],
            "pools": [
                p.model_dump(mode="json")
                for p in state.resources.pools
                if p.id in {f"{kind}:{a}" for a in actor_ids for kind in ("hp", "fp")}
            ],
        }
