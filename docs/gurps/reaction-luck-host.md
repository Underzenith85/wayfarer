# Private reaction choices

This implementation binds Characters B66 Luck to the existing reaction consumers.
It uses the task host's one transaction, private pending gate, captured command
random source, actor authority and microsecond real-play clock. There is no public
API, scenario schema or version change, and no separate reaction cooldown.

## Source and selection

Campaigns B494 and B560–561 reactions use modified 3d totals, with higher results
better. A trusted GM prepares an unrolled opportunity. The receiving character's
current controller can then declare Luck; a current trusted seated GM can instead
resolve once or cancel. The owner cannot supply target dice, modifiers, recognition
or the selected outcome. A completed secret roll cannot be reopened.

Recognition runs once after current context, source, authority, limitation and
cooldown admission. Its modifiers are reused for all three Luck candidates. Equal
best totals retain the first candidate. Ordinary resolution uses one target roll;
cancellation uses none and spends no Luck. A legal but unhelpful Luck selection
still consumes its one use.

`AuthoredSocialReaction` and `NPCReactionSource` require private declarations of
active interaction and a sapient audience. These are distinct from visibility,
audibility and perceptibility. Passive visible observation retains applicable
Appearance while excluding Charisma. Approved character builds remain the source
of purchased modifiers; authored trait modifiers are rejected.

B28 reputation recognition belongs to the reacting NPC and the particular
character, not every character who purchased the same definition. The corrected
path totals all recognized and authored Reputation contributions, then caps the
aggregate at ±4. It retains component provenance and records a cap adjustment when
necessary. Standing does not discard existing authored retribution modifiers.

## Actual consumers

- `SocialService.prepare_reaction` records the complete authored source and joins
  the canonical social receipt and `World.learn` consequence. Disclosure outcomes
  remain configurable. Neutral can permit a simple answer; a complete answer to
  a complex request can require Good. Empty disclosure is supported, but is not
  evidence of a material consequence
- Both direct Influence/Diplomacy and `skill:diplomacy` freeze their preceding
  recognition and skill/Will contest. Only the still-unrolled fallback receives
  the reaction choice. The best combined reaction controls reaction-band
  disclosure, while the original skill verdict/effect remains private. The
  existing voluntary-action guard applies before prerequisite dice
- `NPCService.prepare_reaction_command` enrolls an actual authored occurrence.
  The canonical checkpoint can stop before its exact plan/index, after earlier
  tied occurrences and their pre/post checkpoint consequences. Earlier timestamps
  use ordinary chronological progression. The target keeps its action cost,
  occurrence identity and finite decision budget; selected completion or
  cancellation records one terminal decision
- Initial hireling loyalty freezes the successful search and stores the selected
  reaction total in the actual contract. Failed searches create no target
  opportunity. Cancelled source receipts prevent ordinary dispatch from rerunning
  a frozen search
- B519 rescue reactions use the explicitly authored +3-or-greater rescue modifier,
  without ordinary pay bonuses or the loyalty-20 automatic success rule. Good or
  better sets base loyalty to the greater of its prior value and the selected
  total. An optional explicit GM permanent bonus for a named injured/killed
  rescuer is a separate, once-only consequence
- Reaction-mode law binds the actual case and declared judge or adversarial policy.
  A preceding adversarial contest remains fixed. Selected verdict, case history,
  declared consequence and procedure time commit together. A punishment dispatch
  string does not certify execution of that punishment
- Administration binds a specific existing knowledge source and accepted reaction
  bands. Selection changes that bounded knowledge state, in addition to recording
  the administration outcome

Every campaign role declares the actual reaction recipient and an
active/passive/absent/proxy context. Current approved traits, standing and audience
facts are rebound before target dice. An absent recipient does not lend personal
Charisma, Voice or visible appearance to a proxy. Canonical recognition records
are shared with subsequent reaction consumers for the same actor/NPC pair.

## Persistence, privacy and older histories

The private task result, chosen social/campaign trace, actual consequence,
recognition, shared Luck use/deadline and cleared pending record commit together.
Actor projections exclude reaction bands, dice, attempts, modifiers and recognition;
a legitimately learned fact still follows the actor's perspective. Historical
retries use current membership and trust.

Cancellation preserves prerequisites already committed at preparation and closes
both the task opportunity and original consumer identity. It remains available
when the current build, source or world context is no longer valid.

New immediate social commands use a recorded private generation for corrected
standing and final Diplomacy outcomes. Original inputs retain their byte identity;
old recorded generations retain their original semantics. New captured social
inputs reconstruct the resolver context and disclosure for seed reexecution.
Historical social inputs lacking that context remain explicitly fold-only, while
exact receipt retries remain available.

Where an old recognition event has a retained original SocialCommand, the new host
records that immutable command and verifies its receipt digest/event identity.
For genuinely missing identity, `AttributeReactionRecognition` lets only a current
trusted seated GM append a specific, reasoned attribution. It preserves the old
recognition roll and event bytes, prevents contradictory attribution, and makes
subsequent normal play/replay possible without silently rerolling that decision.

## Verification and limits

The focused domain and host tests use independent raw-dice/actual-state oracles,
SQLite and PostgreSQL transactions, source/control/trust changes, rollback,
competing stores, exact retries, secret views, cross-consumer microsecond cooldowns,
full event folding and seed-only command reexecution. Captured pre-change ordinary,
NPC and task command histories are also reexecuted; no old fixtures are regenerated.

These are bounded consequences, not source-wide NPC behavior. General B560–562
behaviors without a typed consequence, including surrender, flight, actual aid,
commerce and extra volunteered information, are not inferred from a receipt or one
learned fact. The overall Luck source row remains partial under the existing audit
owners. This document does not itself close an audit issue or certify other Luck
consumers or the remaining variant work.

Sources inspected: supplied Characters fourth edition, third printing B27–28,
B41 and B66; Campaigns fourth edition, fourth printing B359, B494–495, B508,
B518–519 and B560–561. Copyrighted source PDFs and extracted source prose remain
outside the repository.
