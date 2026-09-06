# Proposed extensions — not frozen v1 operations

These interfaces scope the remaining UI features. They are deliberately excluded
from `openapi.json`: consumers must not generate production calls from this table
or advertise availability. Each promotion must add complete JSON Schemas, examples,
authorization and contract tests before frontend/backend implementation is coupled.
All paths below are relative to `/api/v1`. All writes inherit command identity,
concurrency, authorization and error semantics from README.md. `id` is the shared
Id, `version` the shared opaque Version, and timestamps are UTC wall time unless
explicitly GameTime. These are typed design decisions, not arbitrary JSON blobs.

| Area / proposed operation | Request and response contract | Authorization / lifecycle / owner |
|---|---|---|
| POST `/campaigns` | command_id, name, premise, pinned rules_package_id/version, policy_id → Campaign in draft state | Authenticated creator becomes GM; no playable actors until validated activation. #40 |
| POST `/campaigns/{campaign_id}/lifecycle` | command_id, expected_version, transition enum ready/activate/pause/resume/archive, optional selected_scenario_revision → Campaign | GM only; activation atomically selects legal party + scenario + opening scene; reject incompatible transitions. #40 |
| POST `/campaigns/{campaign_id}/members/{principal_id}/control` | command_id, expected_membership_version, actor_ids, transfer_consent_id when policy requires → Membership | GM policy plus affected-player consent; atomically revoke old controller and invalidate private caches/streams. #45/#40 |
| POST `/campaigns/{campaign_id}/readiness` | command_id, expected_membership_version, ready boolean → readiness record (principal_id, ready, version) | Self only; readiness cannot select consequential actions for disconnected players. #40/#45 |
| POST `/campaigns/{campaign_id}/character-drafts` | command_id, prompt, budget, rules_package_id/version → GenerationJob | Member creating own draft; request budget constrained to campaign policy, never authoritative. #21 |
| GET `/campaigns/{campaign_id}/generation-jobs/{job_id}` | no body → job id, version, kind character/scenario/epilogue, status queued/running/needs_review/succeeded/failed/cancelled, permitted draft_id or structured error, timestamps | Creator or authorized GM. Failed jobs do not overwrite newer drafts. #21/#22/#41 |
| GET/PUT `/campaigns/{campaign_id}/character-drafts/{draft_id}` | PUT command_id, expected_version and full draft: name, attributes list (definition_id, level), traits list (definition_id, level, option_ids), skills list (definition_id, points), equipment selection IDs → draft id/version, rules pin, submitted build, cost breakdown and validation report | Owner/GM, no arbitrary mechanical modifiers. Proposed build never directly mutates active actor. #21/#8/#9 |
| POST `/campaigns/{campaign_id}/character-drafts/{draft_id}/validation` | command_id, expected_version → legal boolean, budget/spent/remaining points, diagnostics list (path, code, message, severity error/warning), power_review_status automatic/pending/approved/rejected | Owner/GM; engine compiler is authoritative and legality is distinct from power approval. #8/#9/#21 |
| POST `/campaigns/{campaign_id}/character-drafts/{draft_id}/finalization` | command_id, expected_version, exact approval_id if required → Character | Own character or authorized GM; engine recompiles pinned build and checks current approval, creates once. #21 |
| POST `/campaigns/{campaign_id}/scenario-drafts` | command_id, premise, tone, duration_minutes, difficulty enum easy/standard/hard, party_actor_ids → GenerationJob | GM only; generated graph validated for rules/party compatibility. #22 |
| GET `/campaigns/{campaign_id}/scenario-drafts/{draft_id}` | → id/version, premise, rules pin, compatibility diagnostics, permitted opening preview; GM-only graph contains scenes/edges/objectives/outcomes | Players never receive secret graph or hidden win/lose conditions. Activation uses exact approved revision. #22/#40 |
| GET `/campaigns/{campaign_id}/encounters/{encounter_id}` | → id/version, scene_id, status active/completed, round, visible participants, active_actor_id if visible, legal choices (id/label/required target kind), pending decision id/owner/choices, permitted position/range data | Scene-authorized perspective; legal-choice IDs resolve server-side. No raw hidden combat state. #16/#17/#36 |
| Extended `Intent` variants | attack(target_id, weapon_id), maneuver(choice_id, optional target_id/path), defend(decision_id, choice_id), equip(item_id, slot_id), drop(item_id, quantity), transfer(item_id, quantity, recipient_actor_id), store(item_id, quantity, container_id) → same Action union | Owner, scene/reachability and required resource versions; no free-form result/effect fields. Closed Intent union change requires version review. #12/#16/#17/#50 |
| GET `/campaigns/{campaign_id}/journal` | cursor, limit, optional query, kind npc/location/clue/commitment → paginated entries (id, kind, title, text, learned_at GameTime, related visible IDs) | Caller knowledge only, including search snippets/counts. No globally computed totals. #11/#34 |
| GET `/campaigns/{campaign_id}/objectives` | cursor, limit → visible objectives (id/version/title, status active/succeeded/failed, known progress text, visible evidence IDs, optional known deadline GameTime) | Never serialize secret evaluation predicates or undiscovered outcomes. #35 |
| GET `/campaigns/{campaign_id}/recap` | since opaque caller-scoped checkpoint → summary, visible changes, next checkpoint | Revocation invalidates checkpoint; recap from committed permitted events. Does not invent canonical facts. #39/#41 |
| POST `/campaigns/{campaign_id}/groups/commands` | command_id, expected_group_versions, kind split/transfer/rejoin, controlled actor IDs, destination group or scene → typed group receipt with affected visible versions | Authoritative travel/shared-time checks; no world fork or secret sharing. #45 |
| GET `/campaigns/{campaign_id}/characters/{actor_id}/advancement` | → version, earned/spent/available points, paginated ledger records (id, amount, reason, rules pin) | Owner/GM only; deterministic ledger. #18 |
| POST `/campaigns/{campaign_id}/characters/{actor_id}/advancement` | command_id, expected_character_version, expected_ledger_version, proposed definition purchases → validated Action/advancement receipt | Recompile entire build and consume points once. No client reward grant. #18 |
| GET `/campaigns/{campaign_id}/conclusion` | → adventure_id, outcome success/partial_success/failure, permitted evidence, grounded epilogue, settled reward receipt IDs, continuation options | Completed adventure, member perspective only. No secret failed objectives unless revealed. #35/#41 |
| POST `/campaigns/{campaign_id}/continuation` | command_id, expected_campaign_version, completed_adventure_id, selected_next_draft_id/version → Campaign | GM, no repeat rewards; carry lasting injuries/items/commitments. #38/#41 |
| POST `/campaigns/{campaign_id}/voice-sessions` | command_id, actor_id, scene_id, locale, input_mode push_to_talk, narration_enabled boolean → id, version, expires_at, negotiated media type, scoped ephemeral transport credential | Own actor/current scene; provider secrets never returned. Browser codecs and media transport need #24 review. #24/#30 |
| DELETE `/campaigns/{campaign_id}/voice-sessions/{voice_session_id}` | command_id, expected_version → closed receipt id/status | Owner only; stops capture/playback but never cancels or reverses committed actions. #24 |
| Voice transcript submission / narration interruption | final reviewed transcript enters frozen text Intent with one command ID; interruption references voice_session_id + narration_id + scene_id → stopped acknowledgement | Partial transcripts never execute; cancellation cannot affect another player/group. #24/#48 |

Session start/end and session-history pagination require explicit policy in #40;
only current-session reads are frozen here. Host/GM authority is separate from
player character control. Currency needs campaign-defined units and integer minor
amounts in #12/#50. Item containers need capacity and access rules before extending
inventory commands. Maps must use the engine's actual coordinate/range model;
artwork is presentation only. WebSocket framing, narration tokens and audio
transport remain #48/#24; this HTTP contract does not pretend to specify them.
