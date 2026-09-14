# Basic Set trait procedures

Issue #700 completes the residual cross-catalog procedures from *Basic Set:
Characters*, third printing. The implementation is pinned to the Basic Set profile and
does not change the engine or package version.

`wayfarer.engine.rules.traits.procedures` supplies frozen typed records and pure
transitions for B33 advantage origins and potential advantages, B34 activation, B46
limited defenses, B61 alternative attacks, and B120-121 secret disadvantages,
self-control decisions, self-imposed disadvantages, and disadvantage buyoff.

B36 Frequency of Appearance remains in `traits.mental`, where associated-NPC
construction applies the source multiplier and resolution consumes one recorded 3d
roll (or no roll for constant appearance).

Procedure states and receipts inherit from `Record`, so storage rejects extra or
ill-typed fields and JSON round trips preserve results. Random procedures accept an
explicit `RandomSource`; replay uses `RecordedDice` and fails closed if entropy is
absent. Independent source-derived expectations live in
`tests/test_trait_procedures.py`.
