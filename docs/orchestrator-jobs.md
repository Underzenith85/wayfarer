# Campaign sessions and provider jobs

`CampaignRuntime` (`orchestration/runtime.py`) is the container every adapter
receives. It owns the store handles, the session registry behind the play services
and the provider-job worker for its partition; transport is handed the runtime and
constructs none of them. Nothing here is a module global, so two runtimes in one
process share no lock, no compiled-engine cache and no job partition.

The session registry gives each active campaign a lock, keyed by campaign id, before
the store compare-and-set. Commands retain their explicit expected revision: a queued
stale command sees the winner's state and fails CAS instead of overwriting it.
Separate campaigns have independent locks. Idle sessions are evicted after fifteen
minutes, but neither active owners nor queued waiters are evicted. Every service
derived from a play service — a scenario binding, a profile switch, a map migration —
shares its registry, so two handles onto one campaign take the same lock.

Compiled engines are shared by configuration digest and immutable actor/GM bindings.
Those bindings remain part of the cache key because older configuration digests do
not contain them. Engines contain no campaign state, locks or provider workers. The
engine factory is a registry constructor argument, so a test counts construction by
injecting one rather than by patching the module.

Wall-clock instants and command seeds are injected sources too (`instants`, `seeds`
on the play service). Production passes `capture_instant` and a 256-bit token; a test
passes a scripted clock and a counting seed source. `commit_command` reads both
through the service that owns the command, never from the module.

Narration and interpretation/director/NPC proposals use durable provider jobs.
Provider execution happens outside database transactions, with eight concurrent
provider calls and a five-minute worker timeout. Each terminal transition atomically
publishes an audience-scoped outbox row. Narration identity includes campaign,
revision, principal and actor, so retries do not generate duplicate speech and
another audience cannot read the result. Proposal identity additionally includes
the command or clarification generation.

Committed projections return without waiting for narration. Director retries can
read completed narration from the outbox. The frozen v1 live narration messages
deliver the durable result separately from committed projection events; reconnects
reuse results rather than generating a second narration.

A runtime owns one worker for its campaign partition. On restart, its queued or
running jobs become explicit terminal failures, published through the same outbox.
Completed jobs are retained. Shutdown cancels workers and records terminal failures.
No provider output or worker status participates in engine state or deterministic
replay. Provider errors retain allowlisted diagnostics, never raw exception text.

Horizontal deployment partitions campaigns between runtimes. Set `WAYFARER_PARTITION`
to name the partition a process owns; restart recovery only fails the work of its own
partition, so a second process never terminates another's in-flight jobs. This is not a
multi-worker lease queue: two runtimes must not claim the same campaign partition.
The database CAS remains authoritative across processes.

## Composition

`wayfarer/runtime.py` and `adventures/runtime.py` are the production composition
roots: they open the store, build the profile runtime and hand `CampaignRuntime` to
`create_campaign_app`. Tests compose the same objects through
`tests/support/runtime.py` — `build_play`, `build_runtime`, `job_worker`, `open_store`
— passing fakes as constructor arguments. `unittest.mock` is banned by Ruff; a seam
that a test needs to control is a constructor argument, not a patched module.
