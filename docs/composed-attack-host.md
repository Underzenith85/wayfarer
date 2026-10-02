# Persisted approved composed attacks

`ComposedAttackService` is a private campaign service, not a public command family
or HTTP/UI vocabulary extension. A seated, deployment-trusted GM binds an approved
Innate Attack purchase to a specialty and description. Players request that stable
source for their currently controlled actor. The service activates canonical
approvals under the campaign write lock; it accepts no damage, skill, protection,
range, Aim duration, or another actor's defense decision.

New crushing/cutting bindings are rejected pending the B378 knockback/continuation
consumer; their HP-only fallback would be incorrect. Ordinary toxic damage is
ineffective against current machine physiology (B62); special machine-affecting
exceptions have no private override in this host.

New corrosion bindings are rejected before any source mutation because B379 persistent
DR destruction has no supported consumer. This concrete residual remains in the
existing #761/#764 scope; older helper/creature behavior is unchanged.

The current supported delivery is an ordinary torso ranged attack (Dodge or no
active defense), or Malediction 1 with optional Vision-Based delivery and a separate
target-controlled resistance response. Modifier admission delegates to the existing
`cyclic_profile` envelope and rejects unsupported variants visibly. Legacy string
modifiers (including `melee` and `armor-divisor`) have no typed projection in this
new host and reject before binding; their existing helper behavior is preserved. Accurate,
Inaccurate, Armor Divisor, Increased/Reduced Range, Cyclic and typed Symptoms remain
purchase-defined. The source identity is actor plus purchase identity; individual
uses and changing approvals do not create fresh cumulative Symptoms causes.

## Canonical state changes

Private declaration spends the normal injury/exertion turn and records the actual
Attack, Aim or Concentrate commitment. Aim is source/target/version-specific and
uses the existing maneuver state. No attack or damage dice are drawn at declaration.
The optional omitted-default `PendingDefense.composed_attack_id` points to one
private immutable binding and enables non-inventory dispatch. Unknown, mismatched,
retired, spell-mixed or suppression-mixed pointers fail validation. Old snapshots
without the pointer keep their inventory/spell meaning.

Ordinary `ChooseDefense` retains its existing shape and now routes composed sources
through current campaign control. Current visibility, range, source approval, target
eligibility and legal defense are checked before optional-defense dice or effects.
Dodge uses canonical current posture, encumbrance, shields, injury and fatigue;
B394 visibility contributes once. Current Malediction Will rolls also consume
general check modifiers such as B361 Fright aftermath; the defensive build keeps
B421 temporary-attribute exemptions without erasing those separate penalties. Worn, natural and active protection are composed
once before the purchased divisor. The consequence reducer receives raw approved
builds, while scoring uses current Symptoms views once.

Resolution uses canonical injury/FP, actual held-item drops, Symptoms/Cyclic
registration, end-of-turn handling, encounter settlement and the shared clock.
Cyclic occurrences receive their private approved stopping/context binding in the
same commit. Fractional canonical range is preserved at the 1/2D and Max boundaries;
Malediction 1's negative range modifier is rounded down, `-ceil(distance)` (B9/B106).

Critical hits consume the B556 body table and change actual damage/protection,
knockdown/shock and held custody. Critical Dodge failure causes prone. Ranged
failure-by-ten classification follows B382. Inventory-free critical misses use the
separate typed innate critical reducer. Undefined natural-source drop/break/readiness
substitutions pause with immutable dice and captured semantics. An explicit seated,
trusted-GM continuation applies a recorded campaign policy and a real consequence
once; the result is labelled GM adjudication, not selected-printing certification.

Changed unrolled source, sight or range leaves the spent pending commitment
available for attacker-controlled abandonment. The private abandonment route also
serves the existing `AbandonPendingAttackService`. Recorded critical rolls cannot
be abandoned or re-created. Exact retries use their own durable results, after
current membership/control is checked, even after later commands or restart.

## Independent evidence and remaining envelope

`tests/test_composed_malediction_checks.py` covers current Will modifiers,
consenting targets and defensive attribute exemptions.
`tests/test_composed_attack_host.py` exercises actual persisted declaration,
defender authority, injury and turn advancement; natural plus worn protection;
current Dodge encumbrance; spent Aim and accuracy; fractional range; target-owned
Malediction; abandonment after changed range; all B556 body rows; ranged critical
classification; Dodge prone; and immutable natural-critical continuation/retry.
The numeric expectations cite Characters third printing B9/B61/B102/B106/B201 and
Campaigns fourth printing B375/B378/B381-382/B556-557. Private source PDFs are not
repository artifacts.

This is a bounded engine host, not all-modifier or all-87 certification. Wait
interruptions, interposition/catching, non-torso targeting, exotic natural delivery
classes, unsupported modifier variants and broader source suppression need their
own existing-family consumers. Admission rejects these combinations before
spending the intended attack. Cyclic due/contact/stop context and shared-clock
coverage are jointly owned by the existing #761/#762 integration. Complete source
certification must also distinguish natural critical campaign policies from printed
rules; a resumable adjudication is playable continuation, not a printed substitution.


The whole Innate Attack and Cyclic source rows remain partial with live completion
owner #764. The implemented noncontagious and transmission contracts are reviewed
under #761/#762; the shared corrosion degradation gap and broader delivery envelope
are not erased by those narrower tests or any later issue reconciliation. Historical
#114 is not used as a closed completion owner for that still-missing consequence.
The full source-row verdict is distinct from the bounded issue contract verdict.
