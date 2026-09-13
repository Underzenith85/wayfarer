# Futuristic and anomalous artifacts

Issue #527 implements the engine-only artifact behavior reviewed from the selected
*Campaigns*, fourth printing, B478-B480. The implementation paraphrases the source;
no table prose is bundled.

Artifacts remain ordinary, pinned inventory items. `ArtifactDefinition` adds authored
apparent and actual functions, origin, technological procedures, properties,
capabilities, requirements, power cost, side effects, and campaign permission. It does
not modify the equipment catalog. Operation and repair contexts resolve the artifact's
native TL against the actor's current realm and field from the #504 world-context state,
so moving the same item can change the procedure modifier without changing its catalog
definition.

Discovery is actor-scoped. Public properties are visible initially; successful authored
analysis adds one named property to `ArtifactKnowledge`. Views derive capability IDs
only from visible properties and never expose the definition's actual function, origin,
or undiscovered properties. Each analysis takes at least one minute, records its check
and TL context, and applies the cumulative same-investigator retry penalty.

Operation fails closed before entropy or power settlement unless the artifact, identified
property, capability, every possible side effect, typed target, prerequisite, and effect
family are registered. The built-in adapters persist condition, malfunction, and
transformation markers through the active-effect ledger; injury through the injury
reducer; and object damage through the object reducer. Artifact charge costs are
preflighted and settled only for a successful activation.

Authored side effects are selected only from a bounded capability list using the command
random source. The selected ID, targets, damage dice, attempt, occurrence, resource
event, and receipt are persisted together. Reusing an identical command ID returns that
record without drawing entropy or applying the consequence again; a changed payload is
rejected.

Independent acceptance evidence lives in `tests/test_artifacts.py`, including partial
knowledge, local-TL procedure resolution, deterministic side-effect replay, and missing
adapter/capability rejection.
