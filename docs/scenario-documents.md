# Portable scenario documents, schema v1

The portable document is the common authoring/import/export contract for #82.
`ScenarioDocument` and `parse_document` are the shared Python consumer type and
strict decoder; `ScenarioDocuments.validate` is the common authored/generated
domain validator. Other languages consume the JSON Schemas in
`contracts/scenarios/v1`. The existing `ScenarioGraph` remains the runtime
mechanics model. `ScenarioContent` factors its authored fields so the portable
graph and runtime graph cannot develop independent predicate/action definitions.

This change supplies persistence-ready draft/published aggregates and document
activation. The durable catalog, catalog HTTP routes and import/export transport
are #83; the assisted-authoring workflow is #84. Existing graph-based setup and
generation APIs remain adapters/consumers to migrate in those issues; this does
not change the frozen `/api/v1` contract.

## Envelope and field semantics

All object fields are strict and unknown fields are rejected. JSON Schema lists
every required field, default, limit and enum. Optional collections default empty;
optional integrations default null. A document is an author export and is private
by default, including its metadata. No credential, principal, membership,
approval, campaign ID or campaign checkpoint field is part of the format.

| Field | Contract |
| --- | --- |
| `schema_version` | Required integer `1`; format version, independent of revisions and engine rules. |
| `scenario_id` | Stable opaque identity, 1–200 characters; equals `graph.id`. |
| `revision_id`, `revision` | Required immutable publication identity and positive author revision sequence. Neither is `graph.version`, which versions the existing graph definition. |
| `public` | Required, explicitly author-approved title, summary, tags and setup brief. Never inferred from private content. |
| `public.setup` | Premise, genre, tone, duration in minutes, difficulty (`gentle`, `standard`, `hard`) and content boundaries in `restrictions`. These are presentation/estimates, not engine instructions. |
| `public.opening_prompt` | Player-safe initial guidance; separate from private graph opening/ending text. |
| `provenance` | Required `authored`, `generated` or `adapted` origin and author label. Optional source, generator identifier and SHA-256 source digest. These labels are attribution, never authorization. |
| `compatibility` | Exact `CampaignRules` edition, package IDs/versions/content digests, policy ID/version, engine configuration digest and declared capabilities. |
| `party` | Required fixed set of scenario-local actor slots, inclusive point range, per-slot required catalog purchases, initial awareness and conditions. Party size is exactly the number of slots; author another revision/variant for another size. |
| `pregenerated` | Optional legal character proposals keyed by slot; no approvals, identities or mutable character state. A partial set is allowed, but publication needs a complete validation party. |
| `npc_actors` | Optional authored `ActorSetup` records; IDs must exactly match `graph.npc_actor_ids`, distinct from player slots. NPC legality requires an explicit server NPC reviewer. |
| `graph` | Shared typed world, initial resources, actions, scenes, objectives and optional integrations; contains no player builds. |
| `gm_notes` | Optional private presentation text; never executable and never in player exports. |

The engine digest covers the validator contract version, player/NPC compiler
policies, power policies, compilation effect bindings, exact rules pins and
equipment specs. Sets in policy configuration are sorted. Trusted GM account IDs
are intentionally excluded: they are deployment authorization, not game mechanics.
Changing policies without incrementing their version still invalidates validation.

Supported capabilities are `actions-v1`, `scenes-v1`, `objectives-v1`, `combat-v1`,
`noncombat-v1`, `npc-plans-v1`, `recovery-v1`, `split-party-v1` and
`adjudication-v1`. The first three are always required, and all used optional
capabilities must be declared. Unknown capability IDs are rejected, not silently
ignored. Adjudication uses the existing typed ruling policy and authorization.

## Mechanics and references

Use existing `SceneRules` for locations, exits, encounter transitions, discoveries,
revelation gates and obstacles; `NPCRules` for factions, NPC goals, budgets and
clocks; `NoncombatRules` for supported approaches; `RecoveryRules` for setbacks and
capture/escape/rescue branches; and `PartyRules` for cross-scene effects. Mechanics
refer to existing implemented catalog definitions. Freeform prose cannot add a
skill, predicate, action, effect, reward or executable script.

`ObjectiveRules` defines conjunctions of typed predicates for objectives, an OR
of failure predicates, a deadline, partial completion and escrow-backed rewards.
The existing outcome precedence is failure, deadline (partial-success or failure),
abandonment, then success. Predicate kinds remain `fact`, `known`, `item`,
`condition`, `location`, `time`, `event`, `custody`; negation and integer minimums
have their engine meaning. There is no separate ending expression language.

IDs are opaque and case-sensitive. Definition IDs must be unique within their
collection/namespace; entity and fact references resolve against the graph world,
scene references against scenes, and catalog references against exact pins.
Identical strings in different namespaces do not imply the same object. Actor
references in rewards, knowledge, objectives, NPC plans, recovery and cross-scene
effects refer to the same scenario-local actor IDs. Dangling references cannot
pass runtime validation.

`graph.resources` holds initial items, owners, pools, active effects and authored
schedules. Resource revision and elapsed time are fixed at zero; receipts, fired
schedule IDs and event history must be empty. Initial captivity, wounds and known
facts are authored starting conditions, not imported campaign history. Schedules,
deadlines, delays and action durations use the engine's integer game-time ticks;
`duration_minutes` is only a real-world session estimate. Resource weights and
capacities use the existing integer catalog units; do not convert them as prose.

The top-level graph integration fields are authoritative. Redundant nested
`actions.scenes/objectives/...` copies must be absent/null or identical; nested
combat profiles must be empty or identical. Legacy serializers omit these copies,
so canonical output keeps the authoritative top-level fields. Combat profiles
without enabled combat rules are rejected.

## Party instantiation

`bind_party(document, party)` accepts exactly one character proposal for every
slot. Omit `party` to select the optional pregens; supplying an incomplete party
never silently fills missing slots. The compiler and power reviewer enforce
legality, then party point/purchase constraints and the scenario validator check
the actual party. Structural validity alone never establishes playable status.

Binding keeps slot actor IDs unchanged as campaign-local actor IDs. It replaces
only player builds, retaining authored awareness/conditions and starting each
player's availability at tick zero. It does not rewrite arbitrary strings, prose,
pool IDs, catalog IDs or graph references. Runtime authenticated principals are
assigned these actor IDs through separate `CampaignMember` records. Existing
characters with other actor IDs must explicitly select a slot and supply their
proposal; this is new-game creation, not migration of mutable campaign state.

## Validation, drafts and publication

| Layer | Checks and result |
| --- | --- |
| JSON Schema / strict parser | Types, enums, bounds, extra fields, version and runtime-field exclusions. Decoder additionally rejects duplicate object keys and non-finite numbers. |
| Document/domain | Duplicate identities, references, exact pins, declared capabilities, legal pregens and party constraints. |
| Studio/runtime | The existing compiler and initial-state validation, references, reachable scenes, clue bottlenecks, ending consistency, reward escrow and supported recovery/capture branches. Requires a concrete compatible party. |

`DocumentReport.status` is `invalid`, `needs-party` or `playable`. Without a
complete party, validation does not claim to have run all runtime checks.
Playability is conditional on the validation party and pinned engine, and does
**not** guarantee solvability, tactical balance, fairness or a particular outcome.
The studio's challenge warning is retained.

`save_draft` returns a serializable `DraftRevision` retaining exact source text,
source digest, edit counter and diagnostics, even for invalid JSON. Persist the
aggregate atomically in the catalog. `previous`/`expected_edit` implement the pure
compare-and-swap transition; the storage adapter must lock/check that same current
edit during its write. Validation is tied to the exact normalized content digest,
engine digest and bound party digest. Invalid source uses its raw UTF-8 digest.
No caller-supplied or stale report is trusted at publication or activation.

`publish` revalidates and produces `PublishedRevision` containing immutable
canonical JSON, identity and validation evidence. A published snapshot has no edit
method; deserialize into a new document to start a draft, retain `scenario_id`,
allocate a new `revision_id` and increment `revision`. The catalog must enforce
unique `(scenario_id, revision_id)` and `(scenario_id, revision)` keys, permit
idempotent identical publication, and reject replacement with different content.
Published revision snapshots must remain available while referenced by campaigns.

`ScenarioDocuments.activate` requires trusted author authority and revalidates
against the current engine and actual party. It persists the exact accepted
document in `Campaign.scenario_document_json` alongside the bound
`scenario_graph_json` and runtime state. Retrying a campaign with another document,
graph or membership fails. Editing a draft or publishing later content cannot
alter this snapshot or the running game. Existing graph-only records remain
supported; adapting them is explicit.

## Export authorization and evolution

`author_export` requires a trusted GM/author principal and returns the complete
canonical document. `player_export` constructs a separate `PlayerScenarioExport`
allowlist: export kind, schema version, scenario/revision IDs, public brief and
party size. It never copies graph facts, objectives, ending predicates, rewards,
NPCs, pregens, notes, provenance or validation diagnostics. This public projection
needs no author privilege; catalog transport still controls whether a scenario
itself is discoverable. Authors are responsible for reviewing the explicitly
public prose. Runtime discoveries use the existing actor-scoped campaign views,
not the reusable player brief.

Unknown versions fail closed. There is no automatic migration or "best effort"
reinterpretation. Future migrations require a named source/target version adapter,
new revision identity, retained original bytes and source digest, and fresh domain
validation. `adapt_graph` accepts only the current `ScenarioGraph` initial-state
contract, requires an explicit public brief, records a source digest and separates
player proposals into optional pregens. It rejects elapsed runtime history. The
caller must retain the original source; obsolete prototype formats are not a
compatibility commitment.

## Canonical serialization and consumers

Decode strictly, then serialize all model defaults in JSON mode. Sort object keys
by Unicode code point, preserve every array order and string exactly, use UTF-8
with non-ASCII characters unescaped, no insignificant whitespace or trailing
newline, and reject NaN/Infinity. SHA-256 of these bytes is the content digest.
This is Wayfarer's versioned Python JSON convention, **not** a claim of RFC 8785
compatibility. Whitespace/escape spelling of an import does not change its digest;
array reordering, identity, provenance and all accepted content do. Published
reports live outside the digest to avoid a recursive hash. Draft `source_digest`
separately hashes the exact input bytes. Cross-language consumers should use the
server's digest until they implement this same convention.

```python
from wayfarer.orchestration.scenario_documents import parse_document, ScenarioDocuments

document = parse_document(source_json)
service = ScenarioDocuments(studio)  # server-configured compiler/rules/author authority
draft = service.save_draft(source_json, draft_id="draft-1", principal_id="gm")
published = service.publish(draft, principal_id="gm")
player_brief = service.player_export(published)
```

Run `uv run python scripts/scenario_contracts.py` to regenerate schemas and the
authored/generated-shaped Last Lantern examples; `--check` verifies drift. The
generated example is synthetic and makes no live LLM call. Both examples use the
same decoder and domain validation. The schemas cover full documents, player
exports and persistence aggregates; consumers must never treat schema validation
as equivalent to engine/party validation.
