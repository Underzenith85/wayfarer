# Full-suite CI partitions and early feedback

The candidate workflow runs the complete pytest suite across four isolated PostgreSQL17 runners. It retains every parameter and parent test; dynamically executed subtests remain within their parent. An independent quality job runs every existing static, source and contracts gate. The final `package (3.14)` check always runs and explicitly requires successful planning, all four test shards, quality, and additive feedback before aggregation, certification, mechanics evidence, wheel installation and smoke. No individual shard authorizes merge.

All jobs check out the identical published PR head (or push SHA), rather than different moving refs. The planning manifest binds HEAD, tree, pyproject and frozen lock digests. Whole modules are assigned deterministically using measured historical JUnit totals. New/unmeasured modules receive at least the largest historical module weight (minimum 60 seconds), or one second per node when larger. Bundled advisory timings cite public run37142923045/artifact11282581938; they grant no execution credit.

The shard adapter compares actual collection to assigned nodes, then records setup/call/teardown and native pytest subtest outcomes and contexts. Unexpected skips, including unavailable PostgreSQL cases, block acceptance; only the existing exact optional authenticated Codex smoke exception remains. Raw JUnit properties and successful subtest counters survive aggregation. Dynamic subtest identities are captured during execution, not predeclared by collection; same-head benchmark comparison must check their totals and source-evidence properties.

Aggregation requires exactly four successful, distinct artifact directories, matching source/config/manifest/file digests, and exact parent phase/JUnit identity. It independently recollects the complete suite in a fresh interpreter and verifies the disjoint union. Missing, duplicate, unexpected, failed, skipped, malformed or cross-head results fail closed. Raw branch-coverage files must exist with portable relative paths and matching configuration. Supported `coverage combine --keep`, `report --fail-under=65`, and `xml` produce the aggregate original coverage gate; per-shard threshold zero never grants final acceptance.

The original branch, omit, and report settings are preserved. The generated coverage configuration adds `source=["wayfarer"]` and `relative_files=true` to transport raw data across runners, as required by [Coverage.py's configuration documentation](https://coverage.readthedocs.io/en/7.16.0/config.html#run-relative-files). Each shard has independent checkout, database, temporary files, fixture sessions/global state, and artifact names. No shared-database xdist is enabled. `--durations=20` exposes measured bottlenecks on future runs.

Additive feedback uses the separately reviewed conservative selector. It computes complete Git A/M/D/rename statuses from the actual base/head merge base. Selected test-only changes run complete modules with both backend parameters and no coverage; unavailable/uncertain/shared-source changes await the full gate without launching a duplicate full suite. Selected feedback admits at most five modules; larger selections record `feedback_budget_exceeded` and await the existing full gate without duplicate execution. This bounds module count, not runtime; unfamiliar tests may still be slow. A healthy PostgreSQL service is required for selected execution. No source authorization or acceptance scope derives from selection.

Local validation executes only bounded synthetic repositories: real pytest shards, native subtests, JUnit properties, branch coverage combination and threshold refusal; injected setup/subtest failures and PostgreSQL skips retain failure evidence. Actual collect-only parity is recorded separately. These checks prove adapter behavior, not full-suite remote parity or runner isolation across the complete suite.

The measured baseline was 4587.19 seconds of pytest (76.45 minutes). Four historical weighted bins model roughly 19 minutes of test work before collection/setup/coverage and runner variability. This is a projection, not observed speedup. Additional planning/quality/feedback/package jobs and repeated environments increase overhead and may increase total runner minutes; selected feedback deliberately repeats only a bounded useful subset. Root must measure the authorized exact-head benchmark and compare full node inventories, outcomes, dynamic subtests, optional skip, original source-evidence properties and aggregate coverage before merging/adopting. Keep exact-head CI and merge-tree verification. Existing serial runs are unaffected by this unpublished candidate.

Manual commands from a clean exact checkout:

```sh
uv run --frozen python -m scripts.plan_ci_shards --output manifest.json
uv run --frozen python -m scripts.run_ci_shard manifest.json 0
uv run --frozen python -m scripts.aggregate_ci_shards manifest.json shard-inputs artifacts
```

The manifest can also consume `--junit previous-pytest.xml`. `--inventory inventory.json` is for trusted bounded testing; the aggregator's independent complete recollection prevents an incomplete externally supplied inventory from becoming accepted execution evidence. A private workflow review copy remains in `runtime/CI-SHARDED-WORKFLOW-PROPOSAL.yml`; publication is coordinator-owned.
