# Rules profiles and explicit campaign migration

Issue #96 adds registered, versioned rules profiles on top of the existing
package/policy pins and the existing rules-migration ledger. It does not implement
GURPS mechanics, does not reproduce rulebook text, and does not change any saved
campaign.

## Registration

`wayfarer.engine.rules.profiles.ProfileRegistry` holds `RegisteredProfile` records. Each
profile is one exact selection: an ID and integer version, the `CampaignRules`
pins (edition, package ID/version/digest, policy ID/version), the matching
`CampaignPolicy`, the packages themselves, and optionally the GURPS conformance
profile it targets. Registration fails closed on:

- package pins that do not resolve to the listed packages by content digest;
- a pinned package whose dependency is not also pinned;
- packages whose edition differs from the profile's edition;
- policy pins that do not match the policy, or sources the policy does not permit;
- an unknown conformance profile, an unknown capability, a capability outside the
  conformance profile, or a GURPS profile that omits a required capability;
- any `optional_rules` entry (no optional rule is accepted until a reviewed
  profile revision names it individually);
- duplicate `(id, version)` registrations or two profiles with identical pins.

Selection is exact. There is no latest-version lookup, no case folding and no
fallback from a GURPS profile to the prototype package.

| Profile | Version | Edition | Packages | Status |
| --- | --- | --- | --- | --- |
| `profile:wayfarer-lite` | 1 | `wayfarer-lite` | `package:wayfarer-lite@1.0.0` | supported; pins identical to the pre-#96 default |
| `profile:gurps-lite-4e-2004` | 2 | `gurps-4e-2004` | `package:gurps-lite-4e-2004@0.2.0` | unsupported until every Lite capability is verified |
| `profile:gurps-basic-set-4e-2004` | 2 | `gurps-4e-2004` | `package:gurps-basic-set-characters-4e-2004@0.2.0`, `package:gurps-basic-set-campaigns-4e-2004@0.2.0` (depends on the Characters package) | unsupported until every Basic Set capability is verified |

The GURPS packages register identity, edition, source provenance and dependencies.
Version 0.2.0 of the Lite and Characters packages carries the #97 attribute and
secondary-characteristic definitions from `wayfarer.engine.rules.gurps_characters`
(identifiers and per-level costs only); later mechanics issues add skills, traits
and equipment as further package versions and profile versions. Version 1 of each
GURPS profile was never supported, so no campaign can reference it and it is not
kept registered. Sources cite the frozen artifacts from
`docs/gurps-conformance.md` with rights `user-supplied-reference`, and
`tests/test_profiles.py` checks those citations against the independent fixture
metadata. GURPS policy budgets and ceilings reuse the prototype defaults; they are
campaign policy, not rules. The runtime engine factory passes each profile's
conformance target to `CharacterCompiler(statistics_profile=...)`, so a GURPS
profile compiles characters through the statistics module and the prototype
profile keeps its original path.

A profile is **supported** only when every required conformance capability is
`verified`. The registry rejects `require_supported` for anything else and lists
the unverified capability IDs, so an unsupported profile can never build a play
engine, validate a scenario, compile a character, or receive an LLM proposal.
Existing generic hooks or manual rulings do not change that answer.

## Dispatch

`wayfarer.orchestration.profiles.ProfileRuntime` builds one `PlayService` per
supported profile from an engine factory and caches it. A campaign's saved
`rules_ref` is matched exactly against the registry; `PlayService.for_campaign`
dispatches mismatched pins to the owning profile's service before binding the
saved scenario graph. Without a runtime (tests or embedded services), mismatched
pins fail closed exactly as before.

`create_runtime_app` composes the default registry with the prototype profile as
the server default. Campaigns created before #96 already carry the prototype pins
and resolve to `profile:wayfarer-lite@1` with no stored change.

## Selection contracts

`contracts/profiles/v1/schemas.json` is the reviewed JSON Schema for the additive
setup endpoints; `test_profile_contract_schema_drift` keeps it in sync. The frozen
`/api/v1` contract is unchanged.

| Endpoint | Contract |
| --- | --- |
| `GET /setups/profiles` | `ProfileView[]`: every registered profile with edition, digest, package pins/dependencies/sources, policy pin, required and unverified capabilities and `supported`. Servers composed without a registry return `[]`. |
| `POST /setups` | Optional `rules_profile: ProfileSelection {id, version}`. Omitted means the server default. Unknown or unsupported profiles are rejected with 400 and nothing is created. A creation receipt without a selection keeps its pre-#96 payload bytes, so earlier retries still match. |
| `GET /setups/{cid}` | Adds `rules_profile` (`id`, `version`, `title`, `supported`) or `null` when the pins are not registered. |
| `GET /setups/{cid}/migration?profile_id=&version=` | `ProfileMigrationPreview` plus `applicable`. Host only; non-members see 404. |
| `POST /setups/{cid}/migration` | `MigrateProfile` body; returns the ledger `MigrationEntry` and the refreshed setup view. |

## Explicit migration

Migration reuses `MigrationService`, the `rules-migration` command receipts, the
`migrations` ledger in `PlayState`, revision compare-and-set and replay. The
additions are:

1. **Preview.** The host asks for a target profile. The service resolves the
   current profile from the saved pins, requires the target to be supported, and
   requires the game to be paused or completed so no turn is in flight. It then
   binds the saved scenario to the target engine and reports incompatibilities:
   - `scenario`: the saved graph does not bind to the target (for example a check
     pinned to a package the target does not include), or a pinned scenario
     document was published for other pins;
   - `character`: an actor is illegal or blocked under the target, or would need
     GM power approval when the approver is the host rather than a configured GM;
   - `resource`: an item's equipment definition is not implemented under the target.
   Legal actors are reported as build diffs.
2. **Apply.** `MigrateProfile` names the command ID, expected revision, target
   profile, expected source engine digest and a reason. The receipt is checked
   first, so an exact retry after success returns the original ledger entry even
   though the campaign now carries the new pins; a different payload under the
   same ID is a conflict. Any incompatibility, stale digest or stale revision
   rejects the command before any write. The write itself runs inside the
   existing `commit_turn` transaction: engine validation failures roll back with
   no receipt, so a retry of the same command after a failure applies once.
3. **Record.** The ledger entry stores `from_profile` and `to_profile`
   (`id@version`) alongside the engine digests. The campaign's `rules_ref` becomes
   the target pins and later requests dispatch to the target profile.

Authority: a configured GM records GM approvals as before. The setup host may
migrate their own table and only carries characters that stay within automatic
approval limits. Other seated players are refused; principals with no seat are
told the setup does not exist.

Drafts that have not been activated have no play state to migrate; create a new
game with the intended profile instead. Games pinned to a published scenario
document cannot change profile because the document's compatibility pins are part
of what was published; publish a revision for the target profile and create a
new game.

## What this does not claim

No capability moved to `verified`, and no GURPS mechanic executes. The coverage
matrix in `docs/gurps-conformance.md` is unchanged except for recording that
profile selection and migration now exist and fail closed. Certification (#121,
#122) still requires the full inventory with independent evidence.
