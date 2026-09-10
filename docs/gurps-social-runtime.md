# Social runtime integration (#137 / #299)

Numeric references: Basic Set Campaigns, Fourth Edition, fourth printing,
B360-361 (fright consequences), B420 (stun), and Characters B120-121
(self-control). No rulebook prose is bundled.

## Explicit policy selection

Existing v1 scenario documents and their schemas are unchanged. Server-authored
campaigns opt into `SocialActionRules` with a `NPCSocialRules(version=2)` policy.
`NPCSocialPlan` accepts ordinary NPC actions or `NPCSocialAction` entries. The
additional `social` record selects reaction, Diplomacy influence, fright, or
self-control, a subject, bounded fact references, and a situation modifier.
This is trusted scenario configuration, not an LLM command or player roll target.

A trigger may also carry `NPCSocialStanding`: an authored Appearance level,
bounded Reputations with the classes that recognize them, and the audience the
subject presents. `rules.social_hooks` derives those modifiers, so authored data
selects standing rather than inventing an integer, and recognition rolls for a
reputation are drawn before the reaction or influence roll they modify. Standing
the observer cannot see or place is left out entirely. The same audience governs
the trait modifiers below, which keep coming from the initiator's approved build
rather than from the trigger.

`contracts/social/v2/schemas.json` specifies this opt-in policy and internal
director decisions. `python scripts/social_contracts.py --check` checks drift.
V1 scenario authoring cannot silently start accepting these new fields. V2
scenario document/transport adoption remains tracked in #299.

NPC occurrences use the plan ID, spent-action count and selected action ID as
their stable trigger identity. The existing NPC budget and decisions prevent
retries or restarts from drawing again. Fright requires explicit profile pools
and approved HT/Will. Self-control reads the approved purchase's options and
catalog metadata; unknown/unapproved traits reject before dice. Character
compilation still requires the exact runtime-hook capability, and this change
does not enable an uncertified trait catalog or profile.

Reaction and influence dispatch adds the modifiers the initiator's approved
build implies, from the pinned definition's implemented runtime binding
(#113, [selected trait inventory](gurps-mundane-traits.md)). A resolver that
supplies its own trait modifier is rejected, and an initiator without an
approved build contributes none.

Social disclosures must come from the subject's known facts and use the social
outcome policy. Ordinary unconditional NPC disclosures cannot be mixed into a
social action. Player characters cannot become reaction/influence subjects.

## Whole-entry social skill procedures (#345)

`rules.mundane_skills.social` carries one procedure per B168–B233 social skill:
Acting, Carousing, Diplomacy, Fast-Talk, Fortune-Telling, Gesture, Interrogation,
Intimidation, Leadership, Lip Reading, Panhandling, Performance, Politics,
Propaganda, Public Speaking, Savoir-Faire, Sex Appeal, Streetwise and Teaching.
Each declares the shape that decides it — an unopposed success roll, a Quick
Contest, a Regular Contest, or a B359 Influence roll — the contextual conditions
it cannot proceed without, the modifiers it derives itself, and a named effect for
every verdict that shape can reach. Numeric references are reconstructed from
model knowledge under the provisional policy and pinned in
`tests/fixtures/gurps/social_skills.json`, so frozen-source verification (#336)
and the printing delta audit (#191) are a data change rather than a rewrite.

Nothing here is a second engine. Success rolls and contests are scored by
`rules.gurps_checks`; influence procedures call the existing
`rules.gurps_social.influence_roll`, so the Diplomacy fallback and the Sex Appeal
outcome keep their #111 behaviour. Standing and trait reaction modifiers are
derived and rolled before the check that consumes them, and only for the
influence-shaped procedures B359 lets them reach; an unopposed procedure never
consumes the recognition dice it cannot use.

Conditions are named facts about the situation, never numbers. A trigger asserts
`audience-audible` or `credible-threat`; the procedure owns what each is worth. A
missing required condition rejects before dice, so Sex Appeal cannot run on an
audience nobody declared attracted and Interrogation cannot run on a subject who
is not held. `character.social_traits.skill_conditions` derives the build-supplied
conditions from approved purchases only, which is how the B97 Voice bonus reaches
Diplomacy, Fast-Talk, Leadership, Performance, Politics, Public Speaking and Sex
Appeal without any resolver supplying an integer. Gesture is paired: B198 uses the
less fluent party's level, and both parties need an approved level.

The `skill` command kind commits through the same receipt ledger as every other
social check. The public projection is the effect identifier alone; the verdict,
the dice, the derived modifiers and the contest rounds stay in the private
receipt. An effect that needs a ruling sets `requires_adjudication` rather than
choosing for anyone, and an authored disclosure may be gated on an effect
identifier exactly as it can be gated on a reaction band.

`NPCSocialTrigger(kind="skill")` names the procedure and its conditions and
rejects an undeclared identifier or circumstance before any dice are drawn. The
initiator's level comes from the approved build and the subject's Will from theirs,
so authoring selects a situation and never a roll target. Because only the pinned
catalog's skills can be purchased today, authored triggers reach Diplomacy alone;
activating the remaining catalog definitions is coordinated with #336, and the
procedures bind to them unchanged once it lands. Director dispatch through
`SocialService` reaches every procedure now, since the resolver supplies the
trusted level.

Ten rows keep their `runtime-procedure` blocker because a named part of the entry
is owned elsewhere — #366 specialties, #367 technology level, #368 coercion, #369
advancement and group activity, #370 audience reactions and income. `supported`
and `unsupported_scope` publish both halves to the scenario, character and LLM
validators, and the [mundane skill inventory](gurps-mundane-skills.md) reconciles
them against the accounting.

## Time and decisions

All live clock adapters pass their transaction's RNG to resource advancement.
Advancement visits each fright deadline in stable time/ID order and resolves new
deadlines from failed checks before reaching the requested frontier. Original
command receipts preserve one external revision. Other hazard, injury and
recovery deadlines still fail closed; fright cannot bypass their settlement.
Advancement without an RNG retains the explicit-deadline behavior for callers
that cannot authoritatively roll. Very long recovery runs are bounded to 10,000
iterations and must use shorter advances if that budget is exceeded.

Catatonia tracks separate daily-care and recovery deadlines. Unattended days
apply 1, 2, 3... HP through the injury service; care days do not add unattended
days. The director can change care prospectively using `FrightService` and a
`FrightDecision(kind="care")`. Past-due care cannot be rewritten. Upon recovery,
the recorded aftermath lasts as long as the entire catatonic episode.

For row-33 panic, `FrightDecision(kind="panic-response")` records a response
already adjudicated with the player, then checks recovery. A failed Will check
draws the next private severity. This does not execute a movement, attack,
character purchase, or any other player choice. Decisions require current GM
membership, pinned director authority, CAS and stable command receipts.

## Remaining limits

Permanent attribute losses and aftermath penalties remain explicit blockers, not
implemented arithmetic. Trait/quirk selection still needs approval-aware build
adjudication. Condition-specific retching and panic movement need fuller combat
integration. These limits remain visible under #299; neither #137 nor social
certification is marked complete.

`tests/test_live_social.py` exercises real campaign waits, NPC dispatch, automatic
failed recovery, SQLite restart replay, hidden traces, care decisions, coma
rescheduling, catatonia injury/duration, panic responses, and v2 contract drift.
