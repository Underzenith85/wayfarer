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
