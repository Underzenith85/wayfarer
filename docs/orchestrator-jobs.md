# Campaign sessions and provider jobs

The process-local session registry gives each active campaign a lock before the
store compare-and-set. Commands retain their explicit expected revision: a queued
stale command sees the winner's state and fails CAS instead of overwriting it.
Separate campaigns have independent locks. Idle sessions are evicted after fifteen
minutes, but neither active owners nor queued waiters are evicted.

Compiled engines are shared by configuration digest and immutable actor/GM bindings.
Those bindings remain part of the cache key because older configuration digests do
not contain them. Engines contain no campaign state, locks or provider workers.

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

Horizontal deployment partitions campaigns between runtimes. This is not a
multi-worker lease queue: two runtimes must not claim the same campaign partition.
The database CAS remains authoritative across processes.
