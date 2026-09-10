# Social runtime integration (#137 / #299)

Numeric references: Basic Set Campaigns, Fourth Edition, fourth printing,
B360-361 (fright consequences), B420-421 (stun and temporary attributes), B428 (retching), and Characters B120-121
(self-control). No rulebook prose is bundled.

## Explicit policy selection

Existing v1 scenario documents and their schemas are unchanged. Server-authored
campaigns opt into `SocialActionRules` with a `NPCSocialRules(version=2)` policy.
`NPCSocialPlan` accepts ordinary NPC actions or `NPCSocialAction` entries. The
additional `social` record selects reaction, influence, fright, or
self-control, a subject, bounded fact references, and a situation modifier.
This is trusted scenario configuration, not an LLM command or player roll target.

Influence selects its procedure from the approved `skill_id`: Diplomacy,
Fast-Talk, Intimidation, Savoir-Faire (including specialized IDs), Sex Appeal,
or Streetwise. The campaign must pin an implemented skill definition and the
initiator must have a compiled trained or legal default level. A built subject's
Will comes from its approved build; `npc_will` supplies only an unbuilt NPC's Will.
The authored `specious_intimidation` flag produces a Very Bad reaction on a loss
or tie. It is invalid for any other procedure. Existing profile package pins are
unchanged; broader skill catalog population remains #112.

Trusted `SocialContext.influence_conditions` supports the B359 Indomitable,
appropriate Empathy, Unfazeable, and Slave Mentality cases. The director's resolver
must bind these from authoritative subject/initiator traits and circumstances.
Automatic wins/losses skip contest dice; Diplomacy still compares its ordinary
reaction. Their reasons and all roll traces stay private. Contradictory automatic
outcomes fail before randomness. These conditions do not create trait catalog
entries or certify unimplemented trait runtime bindings (#113).

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
V1 scenario authoring cannot silently start accepting these new fields. `SocialScenarioDocument(schema_version=2)` provides explicit portable authoring.
Import, publication, party binding and saved-campaign rebinding retain the v2
policy. The existing authenticated authoring endpoint accepts its JSON content;
v1 documents and their frozen schemas remain unchanged.

NPC occurrences use the plan ID, spent-action count and selected action ID as
their stable trigger identity. The existing NPC budget and decisions prevent
retries or restarts from drawing again. Fright requires explicit profile pools
and approved HT/Will. Self-control reads the approved purchase's options and
catalog metadata; unknown/unapproved traits reject before dice. Character
compilation still requires the exact runtime-hook capability, and this change
does not enable an uncertified trait catalog or profile.

Self-control now applies the trigger's situation modifier to the approved
self-control rating, independently of Will (Characters B121). The private trace
retains the rating, modifier and triggering occurrence. Replaying an occurrence
does not draw again or change the approved character.

Reaction and influence dispatch adds the modifiers the initiator's approved
build implies, from the pinned definition's implemented runtime binding
(#113, [selected trait inventory](gurps-mundane-traits.md)). A resolver that
supplies its own trait modifier is rejected, and an initiator without an
approved build contributes none.

Social disclosures must come from the subject's known facts and use the social
outcome policy. Ordinary unconditional NPC disclosures cannot be mixed into a
social action. Player characters cannot become reaction/influence subjects.

## Whole-entry social skill procedures (#345)

`rules.mundane_skills.social` carries one procedure per B168–B233 social skill.
Each declares the shape that decides it — an unopposed success roll, a Quick
Contest, a Regular Contest, or a B359 Influence roll — the contextual conditions
it cannot proceed without, the modifiers it derives itself, and a named effect for
every verdict that shape can reach. Numeric references are reconstructed from
model knowledge under the provisional policy and pinned in
`tests/fixtures/gurps/social_skills.json`, so frozen-source verification (#336)
and the printing delta audit (#191) are a data change rather than a rewrite. The
coverage matrix, including what each row transfers, is
[the mundane skill inventory](gurps-mundane-skills.md).

Nothing here is a second engine. Success rolls and contests are scored by
`rules.gurps_checks`; influence procedures call the existing
`rules.gurps_social.influence_roll`, so the Diplomacy fallback, the Sex Appeal
outcome and the B359 trait exceptions keep their #111 behaviour, including an
attempt a trait settles with no contest to read a winner from. Standing and trait
reaction modifiers are derived and rolled before the check that consumes them, and
only for the influence-shaped procedures B359 lets them reach; an unopposed
procedure never consumes the recognition dice it cannot use.

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
initiator's level comes from the approved build and the subject's Will from
theirs, so authoring selects a situation and never a roll target. Director
dispatch through `SocialService` reaches the same procedures with a resolver-
supplied level. `require_procedure` fails closed on the three rows that are not
bound — Fortune-Telling and Savoir-Faire await their specialties (#366) and
Propaganda its technology level (#367) — naming the child that owns each, and
`supported` publishes the bound set to the scenario, character and LLM validators.

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

Recovered coma/catatonia applies its recorded penalty to skill and attribute
checks across social, combat attacks/contests, physical feats, injury, fatigue,
hazards, medicine, spells, abilities and transport control. Each episode expires
at its own deadline, evaluated when the roll occurs. This modifies checks, not
purchased statistics, HT-based durations, reaction totals, self-control ratings
or active defenses. Medical contests select the better effective patient HT or
physician skill after applying each person's conditions.

Retching permits action at -5 to DX/IQ/Per and dependent Will-based skills.
B421 defensive reactions are exempt from this temporary attribute penalty.
Concentration is unavailable, while combat attacks and active defenses use their
normal paths. Stun permits Do Nothing and defenses at -4; unconsciousness,
catatonia and seizures permit no active defense. Panic permits player-selected
movement or Do Nothing; ordinary attack maneuvers are unavailable. When retching
ends, B428's 1 FP loss commits once through fatigue in the recovery receipt.

For row-33 panic, `FrightDecision(kind="panic-response")` records a response
already adjudicated with the player, then checks recovery. A failed Will check
draws the next private severity. This does not execute a movement, attack,
character purchase, or any other player choice. Decisions require current GM
membership, pinned director authority, CAS and stable command receipts.

## Campaign presentation and dispatch

Campaign reads and resumable event projections include a `fright` collection.
Players see only their controlled actors; directors see all consequences.
Trait choices, point requirements and permanent attribute losses stay visible
after temporary recovery. `propose_fright_build` accepts an owner or director's
complete proposed draft, the current build revision, a reason, and optionally a
related self-control trait. It does not change the approved character.
`approve_fright_build` requires a current director and the exact proposal ID.

Approval enforces the table's point value or exact one-step self-control change,
required HT/IQ loss, and absence of unrelated edits. The director confirms that
the selected trait's mental/physical/delusion classification fits the event.
Only implemented, pinned catalog purchases can be approved, under the existing
campaign compiler and power policy. Missing catalog entries are not invented.
The approved build recalculates dependent attributes and skills, preserves HP/FP
deficits, refreshes stored medical/hazard/fright recovery inputs, and grants no
spendable point refund. Existing spell/ability build pins still require
cancellation if their approved build changed. Unapproved permanent losses remain
action blockers; approved consequences stop producing choice prompts. Stale
builds/proposals and changed retry payloads reject without mutation.

References are opaque hashes, excluding authored trigger identities, table rolls,
recovery targets and private responses from player projections.

The campaign command endpoint routes `care` and `panic-response` decisions to
`FrightService` using the authenticated principal. Directors can use the projected
reference as `fright_id`; the existing internal IDs remain accepted. These
administrative decisions bypass the incapacitated actor action guard, while the
service still enforces director authority, active consequences, CAS and retries.

`tests/test_fright_access.py` covers owner and spectator visibility, lasting
requirements after recovery/restart, event projection privacy, unchanged builds,
panic command replay, and care through the authenticated HTTP endpoint.

## Evidence and certification

`tests/test_fright_builds.py` covers lasting trait/stat proposals, exact director
approval, unauthorized/stale requests, derived statistics, no point refunds,
owner privacy and restart replay. `tests/test_fright_conditions.py` covers live
combat attack penalties, defense exclusions, maneuver restrictions and forced
injury/fatigue checks. `tests/test_social_scenario_v2.py` imports, publishes,
activates and restarts a real social scenario before resolving its NPC occurrence
once. Existing live-social suites cover automatic recovery, catatonia care and
neglect, timed aftermath, panic decisions and private traces.

These runtime integrations complete #299. Social capability certification stays
partial: the separate frozen-source/errata review and broader catalog population
are not promoted by runtime tests.
