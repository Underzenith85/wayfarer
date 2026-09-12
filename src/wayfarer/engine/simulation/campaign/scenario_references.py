"""Pure verification of scenario pins carried by campaign stream checkpoints.

A checkpoint reaches the engine as a mapping of the campaign envelope's fields;
its typed shape is an application contract the engine does not import.
"""

import json
from collections.abc import Mapping

from wayfarer import validation
from wayfarer.engine.simulation.campaign.scenario_document import digest_json
from wayfarer.engine.simulation.campaign.scenario_loading import ScenarioBoundary, parse_document
from wayfarer.errors import ValidationError


def boundary(campaign: Mapping[str, object]) -> ScenarioBoundary | None:
    raw = campaign.get("scenario_reference_json")
    return ScenarioBoundary.model_validate_json(validation.string(raw)) if raw is not None else None


def verify(campaign: Mapping[str, object], *, runtime_digest: str | None = None) -> None:
    pin = boundary(campaign)
    if pin is None:
        return  # Explicit legacy boundary; new activations always carry a pin.
    prefix = f"Campaign {campaign['id']} scenario revision {pin.reference.revision}"
    try:
        graph = json.loads(validation.string(campaign["scenario_graph_json"]))
        if digest_json(graph) != pin.graph_digest:
            raise ValueError("cached graph digest differs")
        if pin.published is not None:
            document = parse_document(validation.string(campaign["scenario_document_json"]))
            published = pin.published
            if (
                document.canonical() != published.content_json
                or document.digest != pin.reference.content_digest
                or published.revision != pin.reference.revision
                or document.compatibility.engine_digest != pin.reference.engine_digest
            ):
                raise ValueError("published source differs from reference")
        elif pin.reference.content_digest != pin.graph_digest:
            raise ValueError("synthetic source digest differs")
        if runtime_digest is not None and runtime_digest != pin.runtime_digest:
            raise ValueError("runtime engine digest differs")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValidationError(f"{prefix}: {exc}") from exc
