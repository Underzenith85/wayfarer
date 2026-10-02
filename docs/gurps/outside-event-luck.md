# Outside-event Luck: environmental damage continuation (#870, partial)

The supplied *Characters*, fourth edition, third printing B66 and *Campaigns*,
fourth edition, fourth printing B378/B428/B433–434/B437–439 were checked directly. The private
source files remain outside the repository. This is a source-bound increment;
it does not complete #870, #854, or the engine audit.

## Implemented boundary

The existing `HazardService` records a scenario-bound environmental exposure.
`PrepareOutsideEvent` names that actual persisted schedule at its exact due
time. It does not accept an actor-supplied damage expression, target number,
original die result, list of affected people, or favorable direction. Preparation
requires the currently trusted seated GM and an explicit natural-environment
declaration naming the original exposure command and circumstances. The host
captures that immutable `HazardService(kind="enter")` input and verifies its
resource receipt digest, original consequence and canonical schedule identity.
Known spell-fire, crossing-fire and weapon-sprayer causes cannot be relabelled
as natural events. The exposure must actually affect the
named owner at the owner's current location, with matching living Basic Set HP
and FP pools. It currently requires noncombat play.

The admitted environmental sources are:

| Source | Existing random roll | Selected consequence |
| --- | --- | --- |
| B433 partial-turn ordinary flame | 1d−3 burning | Existing area-injury reducer, exposure cycle and next deadline |
| B433 full-turn ordinary flame | 1d−1 burning | Same canonical injury and schedule |
| B433 furnace/intense flame | 3d burning | Same, followed by independently required injury checks |
| B428 canonical strong-acid splash | 1d−3 corrosion | Existing corrosion injury and exposure completion |
| B428 canonical strong-acid immersion | 1d−1 corrosion | Existing corrosion injury and next exposure second |
| B434 ambient heat, after a critical personal HT failure | 1d FP | Actual fatigue, heat recovery debt and next 30-minute cycle |
| B439 arsenic or respiratory mustard, declared environmental | 1d toxic after failed personal resistance | Actual injury, cyclic healing restriction and exposure cycle |

Acid requires the canonical authored profile and explicit unsealed protection.
The supported flame expressions are not a permission to tag arbitrary success
checks as outside events. No new weather, encounter, general event, or party-wide
shared-roll engine is introduced. Each schedule affects one actual actor. Another
party member cannot spend Luck on that person's individual exposure.
This implements an outside roll affecting its Luck owner. It does not yet implement
a single outside-event roll affecting another current party member or the whole
party. An authoritative shared-event/party-membership continuation is absent from
this host; owner-only exposure evidence cannot certify that separate #870 scope.

The B439 cobra profile describes follow-up poison, so it cannot enter this
natural-event route merely because the causal creature is a natural animal.
Attack-caused poison and spell/weapon-caused fire require their proper causal
consumer. No implicit relabelling transfers another actor's damage roll.

The pending record freezes its source schedule, body pools, current actor and
location. A visible original is drawn once with no injury or schedule advance.
The fixed private `hazard-damage` Luck role chooses the lowest total; ordinary own
damage remains high-is-good. Luck draws two replacement attempts for an existing
visible original or three complete attempts for a secretly prepared event.
The B378 minimum of one point for these non-crushing damage rolls applies before
DR; B428 and B433 give no exception. Selection compares those actual basic-damage
results and retains the earliest attempt on ties. DR can still prevent all injury.

Only the selected dice enter `apply_hazard`. Actual injury, shock, incapacitation,
any required subsequent HT checks, the exposure cycle, Luck receipt and cooldown
commit together. A major-wound roll is later independent entropy; it is neither
rerolled as part of the outside event nor rolled for discarded candidate damage.
Previously committed damage is never reopened or restored.

## Personal resistance before outside damage

For the admitted resistible hazards, the canonical existing HT roll happens
first and is recorded once per actual exposure cycle and deadline. It is an
actor's own success check, not the outside-event Luck roll. The captured check
is reused unchanged by the canonical hazard reducer, including after a secret
damage opportunity is cancelled, prepared again, or completed by ordinary
`HazardService.resolve`. No real die is rolled to replay that prerequisite.
Its original disclosure cannot be weakened by cancellation. Ordinary continuation
retains the secret trace privately, returns an opaque check to the owner and
rechecks current control on fresh commands and exact retries. The enclosing
command receipt uses the same exact source-bound audience in campaign streams,
including historical pages read by an actor, spectator, or demoted GM. Raw
receipts and typed checks remain intact for the current trusted GM and replay.

Successful resistance, and ordinary heat failure's fixed one-FP loss, complete
their actual cycle without creating a no-dice Luck opportunity. Only source-called
random harm opens a pending choice. Heat-stroke selection therefore cannot undo
the preceding critical HT failure. Canonical cyclic-poison resolution installs
and accumulates the B438 restriction in the existing healing ledger across both
ordinary and selected cycles; natural, medical and magical HP recovery remains
blocked until the poison stops. Both successful resistance and the final exhausted
cycle release it. Historical committed state and exact receipt retries are not
retrospectively recomputed.

## Shared host rules

These commands join the existing task pending identity, campaign CAS transaction,
real-play clock, per-actor cooldown and command replay dispatcher. There is no
second pending gate or alternative time authority. Original-time eligibility
prevents waiting out a cooldown while inspecting a visible bad result. For an
unrolled secret event, eligibility is checked at declaration. Pause/resume keeps
the microsecond deadline and does not draw target dice.

Only the present controller may spend the affected actor's Luck. A trusted GM
may resolve the event normally. A secret opportunity exposes only its opaque
identity to the owner before declaration. Player results and projections omit
its private candidate dice, Luck receipt and prepared context; actual physical
injury remains ordinary actor state. Current control applies under the commit
lock and on exact retries. Changed payloads, stale revisions and post-commit
choices refuse.

A GM may cancel an unrolled secret opportunity. Cancellation consumes no target
dice or Luck and does not remove the already-existing environmental exposure.
Its due damage remains owed and continues to block ordinary advancement. A
visible rolled opportunity cannot be cancelled to avoid its consequences.
Any already recorded personal resistance remains fixed after cancellation.

## Existing caller map and remaining work

The complete outside-event family is still partial. The following existing
consumers have not been silently promoted by the environmental increment:

| Existing caller/effect | Remaining boundary |
| --- | --- |
| `health.hazards.apply_hazard` other poison/disease profiles | Source delivery/provenance classification and complete existing affliction/disease branches; admitted B434 heat and B439 environmental profiles now retain their prerequisite |
| The same reducer's lethal/localized electricity and explosive vacuum | Source-specific favorable result, actual electrical/air-deprivation follow-ons and post-damage checks |
| `apply_hazard(kind="enter")` random cycle count, including swallowed acid | Source-defined duration selection before the actual duration/deadline consequences |
| `health.disease` random ongoing damage; `health.toxins` HP/FP/duration; `health.survival` delayed injury | Individual source and exposure ownership review, plus continuation of their existing persistent illness/toxin/deprivation effects |
| `movement.physical` and vehicle collision/impact/ejection reducers | Identify each outside damage roll's actual affected actor(s), freeze prior movement/control, and resume body/occupant/position consequences without undoing them |
| Automatic spell/lingering-fire and combat hazard routes | Stop the actual shared-clock or combat consumer before its damage, with that route's current geometry and source timing |

The current repository has no general random-weather or random-encounter event
consumer to integrate. Success checks made by an actor remain that actor's own
checks; attacks and their hit/damage procedures remain the separately owned Luck
consumers. The entries above identify unresolved source work, not claims that all
random numbers are eligible or that every result has a simple scalar ordering.

Historical `HazardService` source-resolver commands retain their existing replay
limits. The new task continuations are seed-reexecutable from the actual persisted
exposure checkpoint; this does not claim a new reexecution handler for historical
resolver-only exposure creation.
New ordinary resolutions of an enrolled natural exposure capture a private
resume identity with the actual exposure command, source declaration, cycle and
deadline. They seed-reexecute against the current exposure state without a fresh
resolver or an old HP snapshot. Exact retries retain their original marker; an
older unmarked command does not acquire one just because later play enrolled
the exposure.
The new source-bound command uses B378's corrected minimum. Historical immediate
hazard commands retain their recorded zero-floor behavior; that inherited source
gap is not certified by the new continuation.

## Evidence for this increment

`test_outside_event_damage` checks independent source arithmetic and canonical
injury/schedule consequences. `test_outside_event_host` and
`test_outside_event_boundaries` exercise both SQLite and PostgreSQL, including:

- Flame attempts 6/2/4 with the B433 −1 modifier select 1 HP of actual injury,
  while the unselected original would have inflicted 5
- A furnace original of 18 remains consequence-free; selecting 6 leaves a
  10-HP actor at 4 HP and only then draws the distinct successful HT total of 9
- B428 splash and immersion both select one point of actual injury from
  the same attempts, with independent exposure completion/cycle expectations
- A fixed critical HT total of18 yields a separate secret heat-stroke choice;
  selected damage2 changes FP10→8 and records two points of heat recovery debt
- Environmental arsenic retains its HT−2 failure, selects actual toxic injury,
  and blocks healing until a later successful resistance ends the exposure
- Secret seeded attempts 3/6/6 select 2 points of actual flame injury;
  cancellation keeps the due exposure and inflicts no injury
- All three cooldown tiers retain exact microsecond deadlines across pause and
  resume; an unavailable visible original cannot become legal by waiting
- Live ownership/GM/build changes, stale context, wrong affected actor,
  independent-store races, post-commit choices, exact retry after restart,
  candidate-commit rollback, complete event folds and secret seed continuation

The source inventory and family certification remain unchanged. Active, Aspected
and Defensive purchases explicitly refuse this consumer and remain #855 work.
