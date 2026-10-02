# Aggregate Reputation cap for corrected reaction consumers

This bounded B28 correction supports the existing open #868 reaction-consumer
work. It does not certify the whole social-trait family or close host acceptance.

Source: supplied private Characters fourth edition, third printing, B28 (PDF
page 30), SHA-256
`872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`.
The source limits the total reaction contribution of applicable reputations to
−4 through +4. It does not require clipping after each individual reputation.
No source text or PDF is committed.

The old helper clipped each recognized reputation while iterating. For
recognized +4, +4, −4, that incorrectly produced 0; changing the order could
produce +4. The corrected helper keeps every signed source contribution and
caps their sum once. It also includes explicitly authored modifiers whose kind
is `reputation`; standing +4, +4 and authored −4 still contributes +4. Appearance,
Charisma and situational modifiers are outside this category cap.

When the raw sum exceeds a bound, `reputation-cap:aggregate` records the exact
correction as a distinct Reputation modifier. It is hidden if a contributing
reputation is hidden. This preserves the original source rows and their privacy
flags. The final canonical join removes an earlier generated correction before
recomputing the combined total, so derived standing is not prematurely capped
before authored sources join it. Authored use of that reserved generated source
ID rejects before dice.

`standing_modifiers(..., correct_reputation_cap=False)` retains historical
per-row clipping and trace bytes by default. The existing prepared-reaction
path opts into corrected aggregation. Immediate social execution already has
its separate `correct_reactions=True` generation switch; that path shares the
same prepared standing resolver and receives this correction. Old default
commands and committed receipt retries retain their original results. There is
no public schema version change or fixture regeneration.

Independent tests cover all permutations of positive/negative sources, both
bounds and cancellation to zero; recognized versus unrecognized sources; mixed
standing/authored sources; non-reputation categories; hidden provenance and
reserved-ID refusal. Through the real campaign reducer, all six permutations of
recognized +4, +4, −4 with raw 9 create a hireling contract with loyalty 13 after
exactly one recognition check per uncertain source. The immutable original
source receipt prevents reapplication. Domain returned-state checks do not by
themselves prove database persistence; the task host owns store and replay
acceptance.
