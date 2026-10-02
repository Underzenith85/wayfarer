# Private campaign reaction continuations

This is domain evidence for the existing open #868 contract, not a claim that
all host consumers or persistence checks have passed. No new API, scenario
schema version, transaction engine, cooldown ledger, or public command family
is introduced. Shared Luck evidence remains partial until integrated acceptance.

## Source decisions

Directly checked supplied private Basic Set Characters fourth edition, third
printing B21/B27–28/B41/B66 (PDF pages 23/29–30/43/68), SHA-256
`872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`;
and Campaigns fourth edition, fourth printing B506–508/B518–519/B560–561
(PDF pages 171–173/183–184/225–226), SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
Private PDFs and extracted source prose are not repository artifacts.

- B41 Charisma requires active interaction with a sapient reacting observer.
  Purchased bonuses come from the currently activated approved build. Passive
  observation can still use B21 visible Appearance; audible Voice uses the shared
  canonical binding. An absent recipient or their proxy supplies neither the
  recipient's looks nor their audible/personal influence. Applicable Reputation
  may still be recognized by name (B27), scoped to this actual actor and NPC
- Initial hireling loyalty is a reaction after the distinct search. The source
  command represents the authored decision to hire that candidate; a selected
  reaction cannot decide whom to hire or replay the search
- A qualifying B519 rescue uses the printed +3 when the existing modifiers tuple
  is empty. Explicit components supply a total of +3 or higher, counted once.
  Good or better saves the greater of the old loyalty and modified reaction
  total. Other reactions leave it unchanged. Ordinary loyalty target/pay rules
  do not replace this reaction, even when old loyalty is 20 or higher
- B519's additional permanent bonus for a seriously injured or killed PC is an
  optional, separate `RescuePermanentBonus`. The amount, applicability condition
  and reason are explicit trusted GM decisions. It is added after the normal
  reaction-dependent base rating, never mixed into the reaction total. The known
  rescuer identity is bound to this source command; present HP, consciousness,
  build or control assignment does not decide whether a former PC made that
  sacrifice. No automatic injury threshold or source-mandated fixed bonus exists
- B508 unilateral judge decisions require a visible, trusted authored outcome
  policy and reason. There is no universal source rule making Neutral a win
- The supported B508 adversarial policy records the defense/prosecution Quick
  Contest first. Its signed victory margin modifies the reaction; Good or better
  acquits and below Neutral convicts. Neutral follows the contest winner, with
  a tie acquitting. Targets and acquittal/conviction transitions must already be
  authored on the reaction-mode trial procedure
- The administration bridge binds an existing knowledge source and explicitly
  allowed reaction bands. The reacting NPC must know every bound fact. The
  existing source determines actor or campaign audience. For a complex complete
  answer, Good-or-better is an example policy; simple answers may include Neutral

## Host interface

`campaign/reactions.py` provides these typed private records:

- `CampaignReactionSource(kind="campaign", role=..., command=...)`, containing
  exactly one existing `FindHireling`, `CheckLoyalty`, `ResolveLawCase`, or
  `ResolveReaction` command. The role must match its command
- Every source requires `interaction=CampaignReactionInteraction(actor_id, mode,
  audience, standing, sapient, proxy_actor_id)`. Its actor is the actual reaction
  recipient and must match the command; `mode` is `active`, `passive`, `absent` or
  `proxy`. Only proxy mode accepts/requires a distinct known proxy. Presence is
  never inferred merely because a player owns the source command. Here `proxy`
  specifically means an absent recipient represented by the named actor; a
  present nonparticipating recipient uses `passive`, retaining perceived looks. `Audience` and
  optional authored `Standing` reuse canonical types; supplied trait bonuses or
  duplicate purchased standing are rejected
- `ReactionKnowledgeBinding(source_id, outcomes)` is required for administration
- `JudgeReactionPolicy(outcomes, reason)` or `AdversarialReactionPolicy()` plus
  an explicit `npc_id` is required for law
- `rescue_bonus=RescuePermanentBonus(amount, rescuer_actor_id, loss, applies_on,
  reason)` is optional only on a rescue source. `amount` must be a positive
  integer, `loss` is `serious-injury` or `death`, and `applies_on` must explicitly
  select `grateful` or `any-reaction`. The source leaves that discretionary
  condition to the GM; neither is asserted as a universal rule
- `PreparedCampaignReaction` stores the source, receiving actor, NPC, preparation
  identity, canonical profile/modifiers, bound context digest, and any frozen
  search or contest. Its `reaction: PreparedReaction` binds the canonical social
  identity, approved trait/standing context and remembered recognition, including
  verified legacy command sources. `modifiers` is the base before uncertain
  recognition. It has no callback, RNG/provider or selected target dice

`CampaignProcedureEngine` exposes `prepare_reaction`, `validate_reaction`,
`recognize_reaction`, `resolve_reaction`, and `cancel_reaction`; these dispatch
the corresponding named module functions. Prepare/validate/resolve require the current authoritative `player_actor_ids` keyword from the
host. NPCs may have compiled builds in `PlayState.actors`; those builds do not
make them player-controlled. Current membership, not build presence, excludes a
player as the reacting NPC.

Preparation returns `(prerequisite_state, prepared_or_none, terminal_or_none)`.
A successful hireling search and an adversarial contest consume their own dice
once but have no existing independent world consequences. Their complete traces
are stored in the private preparation. Failed search returns the real terminal
`not-found` economics receipt with no reaction opportunity. The host must retain
source identity and frozen prerequisites when cancellation closes an opportunity;
it must prevent reusing that source identity to reroll a cancelled search/contest.
`cancel_prepared_campaign_reaction` also writes a terminal `cancelled` receipt
through the ordinary source receipt/event functions. This prevents a later direct
legacy campaign command from bypassing the host identity gate and rerolling the
search or contest. It applies no target effect or elapsed time and remains usable
after source/build loss; the host still checks current GM authority and pending
identity. Cancellation closes both the canonical social identity and the ordinary
campaign source receipt, at one outer revision.

The source command's revision is checked at preparation. Its substantive actor,
role, rules, case/contract, knowledge, world, current build and resources are bound
for revalidation before the unresolved target dice. Resource revision, clock and
receipt/event bookkeeping do not invalidate a valid pending source. Preserve the
original source command, including its revision, when settling: the ordinary
source receipt uses that original identity. The continuation does not require
normalizing the command to a later task-clock revision.

Approved standing and legacy recognition provenance are validated without dice
before search/contest prerequisites. Preparation accepts the host-recorded
`recognition_sources` tuple. After current authority, source, Luck variant and
cooldown admission, the host calls `recognize_reaction(prepared, rng=...)` exactly
once, selects all candidates using its `ResolvedReactionContext.modifiers`, then
passes `recognized=` into resolution. Unknown recognition cannot be skipped by
omitting that context: the no-context fallback permits only RNG-free standing.

Resolve re-scores selected dice through canonical `evaluate_reaction` and rejects
a forged trace. It does not roll the named target or reroll a prerequisite. It
commits the canonical actor-attributed recognition/social receipt alongside the
actual source consequence. Subsequent generic or campaign reactions to that actor
by the same NPC reuse the same recognition memory; another actor with the same
reputation purchase does not. Legacy recognition is accepted only through the
canonical immutable-command or explicit-GM-attribution proof. Both social and
ordinary source receipts are normalized to one outer revision. It uses the same
existing contract, loyalty-check, case/time and knowledge reducers
as ordinary campaign procedures. A law procedure's later shared-clock effects
may still consume the caller's RNG after the selected verdict. Host preparation
and selected resolution are intended to run inside its registered private task
command transaction; these pure returned states are not database commits.

The task host still owns seated GM/owner authority, current approvals and Luck,
secret projections, source-identity consumption, global pending gate, clock and
cooldown, canonical attempt drawing/selection, rollback, races, persisted retries
and full seed-only command reexecution. Direct legacy committed source receipts
are returned unchanged and cannot become a new pending reaction.

## Evidence and limits

Dedicated tests use literal state expectations: initial selected 15 versus
ordinary 9; rescue old 14/raw 15/+3 becomes 18, raw 9/+3 leaves 14, and selected
13 cannot lower old 16; Good/Bad change the case to its authored state exactly
once; administration changes the bound actor's actual knowledge. Preparation
leaves dependent effects untouched, typed JSON restores frozen prerequisites,
clock revision drift is allowed, and foreign/stale/forged contexts reject.
Additional independent state oracles cover active approved Charisma1 turning raw
12 into actual initial loyalty13, reaction-based acquittal/knowledge/rescue effects,
passive/absent/proxy exclusions, separate Appearance/Voice conditions, successful
and failed reputation recognition, cross-role memory reuse, cross-actor isolation,
verified legacy attribution, forged trait refusal and current approval loss. Five literal snapshot hashes and RNG
continuation values were captured separately from unchanged source commit
`40c191f2e80f1a31e7a38894a8efa2bd0fb83bee` for ordinary hire/trade/knowledge/reaction/
law execution; the continuation branch reproduces all five.

Ordinary economics, administration and law procedures retain their original
serialization and draw order. Existing contest-only law mode is left unchanged;
it is not certified here as a complete B508 contest-plus-verdict procedure. The
new adversarial continuation requires an explicitly authored reaction-mode trial.
Two-party comparative judge disputes, arbitrary NPC behaviors and execution of
punishment dispatch strings
are not implemented by this seam. The optional permanent rescue addition has separate numeric, current-source,
JSON, exact-retry and cancellation checks; cancellation never applies the bonus. A below-Good reaction with an explicitly
authorized unconditional bonus reports `loyalty-adjusted`, retaining a false
gratitude flag while recording the increased permanent rating.
State-return and receipt tests do not certify
host/store privacy, atomicity or command replay.


The corrected standing generation applies the B28 Reputation cap once to the
combined recognized and explicitly authored Reputation contribution, preserving
source rows. See [aggregate Reputation cap](reputation-aggregate-cap.md) for the
legacy-generation boundary and independent contract/order oracles.
