# Roadside Watch native acceptance

**Current V5 evidence:** all 66 native campaign cases passed on SQLite and PostgreSQL at tested `7713b402f49e9ccbb42edd3227e53a66bf0dc339`, within the complete green regression. Exact current counts and artifact hashes are in [checkpoint evidence](roadside-watch-checkpoint-2026-10-29.json).

**Historical standalone evidence at 37583b90:** **66/66 native acceptance cases pass on both SQLite and PostgreSQL, with zero failures, errors or skips.** Runtime was438.04s. The exact66-case inventory matches JUnit without duplicates, omissions or unexpected cases. All1744 fingerprinted source/test/fixture files remained unchanged during the run; root independently matched them to its clean validation checkout.

**The complete V5 engine aggregate and unchanged normal-CI source-report-only release gates passed at tested `7713b402f49e9ccbb42edd3227e53a66bf0dc339`. The separate strict global source-completeness gate failed with 117 preserved blockers. Publication is not performed by this staging step.** Genuine source/capability counts are recorded separately in [checkpoint evidence](roadside-watch-checkpoint-2026-10-29.json); no local coverage-group count is a percentage of full-book implementation.

Historical external workspace evidence (not bundled in this repository): [result](/workspace/wayfarer-roadwatch-validation/architecture-v7-full66.result.json), [JUnit](/workspace/wayfarer-roadwatch-validation/architecture-v7-full66.xml), [exact case accounting](/workspace/wayfarer-roadwatch-validation/architecture-v7-full66-accounting.json), [source ZIP](/workspace/wayfarer-roadwatch-validation/architecture-v7-full66.source.zip), [per-file hashes](/workspace/wayfarer-roadwatch-validation/architecture-v7-full66.before.json), [log](/workspace/wayfarer-roadwatch-validation/architecture-v7-full66.log).

Historical standalone source digest: `7772689b32095d69a3b3807cec7c82f9a4ee722a713b24d4699b45ea67f03dea` before and after.

## Current coverage

**11 supported,5 partial,1 unsupported,1 unverified** reporting areas. “Supported” means the named bounded behavior has actual native evidence; it does not certify every rule variant in that subject.

| Area | Status | Actual evidence / qualification |
| --- | --- | --- |
| RW1: builds and setup | Supported | Authenticated public create/assign/ready/activate; both100-point builds, actual paid skills, HP/FP, speed/lift/load, human anatomy, sword hands and ordinary canonical gear. |
| RW2: GM discovery/history | Supported | Default GM200; player403, outsider404; malformed cursor/limit400; all six corrupt-history controls reject without state/entropy changes. |
| RW3: medical identity | Supported | Exact accepted retry200; changed choice/actor/revision409; outsider cached POST404; no extra randomness, state or history. |
| RW4: admission safety | Partial | Actual tested token/member/GM/magic/V1/live/wrong-actor and omitted-Parry-mode controls pass. Wider crafted-input combinations are not exhaustively certified. |
| RW5: complete clinical episode | Supported | Actual Observation→combat wound→shelter→Bandage/FirstAid, correct health and all postactivation original-seed replay on both stores. |
| RW6: failure/abandonment | Supported | Real failed Observation/Stealth checks, correct authored facts, player abandonment rejection and lawful GM abandonment before the return objective completes. |
| RW7: representative rare melee | Supported | Natural arm-strain15, ordinary sword breakage4/17 and explicit-Parry critical5/6 resolve without manufactured pending state; retry, severe-casualty GM ending and replay pass. This is not full-table certification. |
| RW8: FirstAid arithmetic | Supported | Bandaging is included in table totals2/3; critical maximum, ordinary failure and critical failure preserve exact HP and die consumption. |
| RW9: finite supplies | Partial | The real episode uses carried canonical basic bandages and durable wound reservations. Component controls cover unsuitable stock/capacity; wider native stock variants are qualified. No generic automatic inventory-depletion claim. |
| RW10: FirstAid Kit+1 | Unsupported in module | The selected item is genuinely basic+0 bandages. The ordinary FirstAid Kit+1 consumer is not claimed or hidden by renaming equipment. |
| RW11: battle FP/rest | Supported | Full and partial eleventh-second real combat: actual rolled participants lose1FP at battle end, mere presence does not; actual600sec rest restores the missing FP. Complete care and replay follow. Broader duration/roster/unarmed/weather variants are not certified here. |
| RW12: later-context retries | Partial | Accepted retry after its start choice disappears and fresh authority checks pass. Wider stock-loss, retirement and later-context native permutations are not exhausted. |
| RW13: reload | Supported | New application/runtime/store/client reopens a genuine wounded campaign, retains health/anatomy/history and returns an old exact retry without new effects. |
| RW14: CAS/exactly once | Supported | Independent app handles produce one competing-revision winner; identical concurrent IDs return two successes with one committed effect. |
| RW15: failed commit | Supported | A genuine injury candidateHP7 is computed, commit fails, persistedHP11/state/history remain unchanged, and lawful retry injures once. |
| RW16: replay boundary | Partial | Full public setup/genesis history folds correctly; all postactivation original-seed replay in the scripted episodes passes. **Setup/lobby seed reexecution remains unverified.** |
| RW17: GM evidence/fiction | Partial | Protected GM receipt reads and natural critical skill flags are verified. Initial module authoring is public. Human GM critical good/bad/reaction fiction is manual; B211 reaction consequences are not automatically persisted. |
| RW18: browser/GUI | Unverified | No browser, GUI or interactiveCLI acceptance claim. The demonstrated deliverable is the public API. |

## Exact scenario and source oracles

One party principal controls both ordinary human PCs, Mira(`a`) and Iven(`b`); a separate trusted GM controls the human brigand. TL3, fixed approved builds, ordinary swords, leather armor and basic bandages. No magic, shields, ranged weapons, unusual bodies, character advancement, commerce, invasive medicine or V1 writes/live sessions are admitted in this module.

Mira: attributes80+skills20=100points; Broadsword14, Stealth13, Observation13; HP11/FP11, Speed5.75, Move5, Dodge8, sword Parry10, Basic Lift24lb, load13lb. Iven: attributes70+skills30=100; FirstAid13, Broadsword12, Observation15, Stealth14; HP10/FP11, Speed5.5, Move5, Dodge8, Parry9, Basic Lift20lb, load15lb. Canonical resources use millipounds internally; the source-pound expectations remain13/15.

The ordinary real guard hit uses literal seed1: attack9, damage die4, ST10 swing+1=5raw, torsoDR2 leaves3, cutting×1.5 rounded down gives4 injury (Mira11→7). Bandage60sec givesHP8. B424 TL3 shock treatment takes1800sec and restores1d−3,min1 **including prior bandaging**. Literal die6 gives3total (HP7→8→10), die5 gives2total (HP7→8→9), and critical maximum3 also ends atHP10. After bandaging, ordinary failure staysHP8; critical failure loses2 toHP6. The actual checks consume the independent expected dice.

B426–427 is exercised with genuine initiative turns and real attack/defense rolls: FP11 remains during combat, becomes10 at the end of a full or partial eleventh combat second, then actual self-rest600sec restores11. An actor who never rolls attack/defense stays atFP11. HP recovery is separately tested; no fake deficit, Feverish Defense or zero-delta rest counts as proof.

Representative rare outcomes use literal independent streams. Ordinary sword misses4/17 break the weapon without a resistant confirmation and permit legal continuation/ending. Miss15 disables the actual wielding right arm1800sec. Explicit swing Parry critical5 produces capped left-leg self-injury6 plus incoming6, finalHP−1, with19draws; critical6 produces right-leg self-injury4 plus incoming6, finalHP1, with16draws. Both preserve real prone/stunned casualties and allow an honest GM abandonment without resetting health. See the independent [parry oracle](/workspace/wayfarer-roadwatch-critical-parry-oracle.json).

Sources are Characters third printing2008 and Campaigns fourth printing2008. B424 was reread from the hash-matched Campaigns PDF (`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`). B289 independently states basic bandages serve six wounds at$10/2lb; a FirstAid Kit is a separate+1 item. B274 supports the ordinary-quality local sword qualification; B556 supplies the critical consequences.

## Manual and terminal qualifications

Observation/Stealth natural critical success/failure flags, exact currently authored facts, GM read access and replay pass eight actual cases. The engine does not invent bonuses, surprise, injury, false information or persisted reactions for those checks. A human GM supplies critical good/bad/reaction fiction. Already announced hostility is not proof that B211's noticed/poor-reaction consequence was automatically stored. This remains **partial automation**.

Returning both PCs to shelter wins the authored objective automatically. Medical care remains available and is validated after arrival/success; care is not an additional win condition. Later explicit objective evaluation reports the existing result. The abandonment test therefore acts on the road before satisfying the return objective. Severe incapacitation/crippling may legitimately end the episode; resurrection, carrying and long-term rehabilitation are outside the fixed module.

Replay verification copies only authentic accepted before-images into an isolated official store, refuses fresh entropy/time, and uses official `execute_recorded`/`verify_commands`. These copies never choose gameplay or seed played health. All gameplay uses authenticated production HTTP; critical information comes from the actual GM route. Full setup folding and postactivation seed replay are distinct claims.

## Public route checklist

Use separate `party` and `gm` bearer identities. `GET /setups/templates` supplies the exact graph/brief. Create with `POST /setups` and a fresh UUID; use the server-returned campaign ID. Assign `[a,b]` to`party`, then ready and activate via `/setups/{cid}` using current revisions.

Read `/campaigns/{cid}` for revision and opaque recovery choices. New legacy commands use `/campaigns/{cid}/commands`, fresh`id`, controlled`actor_id`, current`expected_revision`, native`kind` and fields. Medical callers submit an offered opaque choice, wait the native interval, then rediscover the offered Finish choice. They do not submit healing, skill, TL or invented task IDs.

Character reads use `/api/v1/campaigns/{cid}/characters/{actor}`. GM receipt discovery uses `/roadwatch/campaigns/{cid}/gm` with default cursor0. Actual TacticalV2 GET and wrapped`{command:...}` POST defenses pass; Parry must specify the selected swing/thrust mode. V1 writes and live are refused. An exact accepted retry keeps the entire original payload/revision and returns the current projection without extra effects.

## Preserved history and remaining release work

Earlier REDs are retained under the validation directory: GM setup-history400, missing story identity knowledge, changed medical intention200, missing Bandage care capture, care movement remaining pending, and absent objectives replay dispatch. Each received a bounded source-supported fix and actual regression proof. Test-only mistakes were separately classified: pounds/millipounds, hidden404/live403, cursor-excluded genesis, encodedJSON key order, abandonment after automatic success, and standard-library gather's list result. No production assertion was weakened to hide a rules failure.

The original standalone66-case run is source-bound to37583b90. A second fresh complete66-case campaign module also passed at a561within V4, independently verified from its raw JUnit; see `/workspace/wayfarer-roadwatch-v4-native66-proof.json`. V4 completed FAILED:13,208parents passed,7failed,1optional-provider skip and16,191architecture subtests passed in59.68minutes. Its two pre-existing reaction fixture failures and five pre-existing Wave14 continuation production failures are separately classified and repaired. V2 remains infrastructure-invalid; V3 remained prepared-only.

## Current tested candidate

Final official V5 evidence at `7713b402f49e9ccbb42edd3227e53a66bf0dc339`: 13224 parents, 13223 passes, 1 allowed optional-provider skips; 16191 architecture subtests, 66 campaign cases and 22 static/extra gates. Combined branch-enabled coverage passed the original 65 percent gate; exact counters are in the checkpoint manifest. The added strict global source-completeness gate failed with 117 preserved blockers; normal CI checks source-audit integrity without requiring global completeness. Full Basic Set certification remains incomplete.

Local scope remains 11 supported / 5 partial / 1 unsupported / 1 unverified. Generic native continuation fixes do not admit sequels through the fixed Roadwatch API. Source-ambiguous reactive concentrationWill defensive9 interpretation preserves its original oracle/history but gives no new magic coverage. Magic, browser, setup seed execution and other listed exclusions remain unclaimed. Publication is not performed by this generator; actual pushed commitSHA is recorded externally after commit/push verification.
