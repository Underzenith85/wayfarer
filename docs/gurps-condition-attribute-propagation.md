# Temporary condition attributes in current checks

Direct review of the selected Campaigns fourth printing B421 requires temporary
IQ penalties to affect ordinary Will, Per and their governed skills. Defensive
reactions that require no maneuver are exempt from temporary attribute penalties.
B428 supplies the condition values; B439 supplies the named poison effects.

The shared check adapter now applies this contract to existing supported sources:

| Current source | Ordinary DX | Ordinary IQ, Will and Per |
| --- | ---: | ---: |
| Coughing Symptoms, tear gas/atmosphere coughing, generic toxin coughing | −3 | −1 |
| Tipsy | −1 | −1 |
| Drunk | −2 | −2 |
| Awake survival or toxin drowsiness | −2 | −2 |

Their modifiers are omitted when an existing consumer explicitly identifies a
defensive reaction. The existing Cobra venom DX penalties receive the same
defensive exemption; their damage thresholds and ordinary values are unchanged.
Fright-source Retching already supplied ordinary DX/IQ/Per/Will penalties and
the exemption, and retains its existing behavior. Direct B361 skill aftermath
remains distinct from attribute loss and is not removed by this correction.
The separate Acrobatic Dodge policy continues to exempt attribute penalties.

Approved purchases and base statistics stay immutable. The ordinary check's
canonical governing attribute selects its modifiers, so Per/Will skills receive
the same correction as direct checks. This does not lower HP, FP, Basic Speed,
Basic Move or active defense scores. Expiry and recovery continue to come from
each existing source's authoritative clock or causal damage debt.

## Captured generations and replay

Fresh commands capture private `symptom_attribute_generation: 2`. Generation 1
still selects the previously implemented Symptoms attribute projection; generation
2 additionally selects these condition-check rules. Missing generation and
generation 1 retain their exact old modifier behavior, input bytes and hashes.
Retries use the saved generation; replay does not reclassify a completed check.
Opaque, array and object command envelopes retain their original inner text.
Malformed and future versions fail closed through the existing digest-checked
metadata boundary. No public command or resource schema expands.

The command pipeline scopes the captured rule selection across feasibility and
transaction reduction, including typed task hosts and automatic checkpoint
consumers. A lower-tier context contains only this captured rule-version choice;
it contains no game facts, storage or entropy. This uses the existing scoped
generation pattern because the shared pure check adapter is also called without
a RulesContext. It avoids an engine import of orchestration. The scope resets on
failure and remains isolated between concurrent commands. Standalone canonical
reducers default to current rules and can explicitly select historical semantics
with `condition_generation(False)` for diagnostics.

The earlier per-exposure drowsy-toxin marker remains independent: sources created
through that corrected producer keep their previously captured behavior even
under generation 1. Fresh generation 2 checks also correct an existing unmarked
drowsy condition. They do not rewrite its exposure, old receipts, resistance
outcome or deadlines.

## Consequence evidence and boundaries

`tests/test_condition_attribute_generations.py` binds a real purchased coughing
attack, makes its actual 4-HP hit against HP10, and performs the ordinary B394
Hearing roll. Historical generations check at Per10−2 = 8; generation 2 also
applies coughing's −1 and checks at 7. Total 8 therefore fails to locate the foe.
The source binding, attack, debt activation and Hearing command reexecute from
the persisted pre-source campaign snapshot on SQLite and PostgreSQL. This does
not claim that the test's earlier setup/membership commands are reexecuted.

An independent actual natural-healing command restores 1 HP, reduces the source
debt from 4 to 3 and ends coughing. Hearing then succeeds at 8 with the same dice.
Actual drinking provides another consequence: a drunk feint victim retains
skill 13 for the defensive roll, instead of historical 11. The feint penalty
changes from 4 to 3; the later Parry checks at 7 instead of 6. Total 7 then
prevents the 4-HP wound. These numeric drinking/Parry cases check event folding;
they do not claim seeded reexecution of the drinking environment resolver.

`tests/test_condition_attribute_sources.py` independently checks the source values,
governed Per/Will skills, defensive exemptions and exact expiry using actual
canonical Symptoms, poison, toxin, drinking and survival producers. Those are
explicit reducer diagnostics, not additional persisted service proofs.
`tests/test_prepared_task_checks.py` separately checks old/new coughing semantics
with old/new Symptoms attribute projection and exact prepared/ordinary parity.

This correction does not admit new Symptoms or Affliction names, implement the
Shyness effects, add missing irritant producers, provide
the broader drowsiness sleep contract, repair every check consumer's reaction
classification, or certify the source families. Existing source and capability
statuses remain unchanged.

Subsequent [condition self-control work](gurps-condition-self-control.md) adds the
supported B428 self-control consumers and fresh Symptoms generation 3 duplicate
Drowsy normalization while preserving generation 2's existing propagation.
