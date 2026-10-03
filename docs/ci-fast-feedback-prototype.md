# Additive test-only CI feedback

The package workflow supplies fast feedback for a conservative subset of **test-only modifications**, alongside the complete four-shard acceptance suite. It preserves the final package/release gate and prospective merge-tree verification. Selection does not accelerate source-changing engine PRs. Selected execution is partial feedback and cannot certify mechanics acceptance, release readiness or aggregate coverage.

From a checkout, supply a JSON array describing the caller’s actual exact-base-to-head changes:

```json
[{"status":"M","path":"tests/test_water_parcel_flow.py"}]
```

```sh
python scripts/select_ci_tests.py --changed-files /tmp/changed-files.json
```

The selector reads local files and emits deterministic JSON. It does not obtain Git history, credentials, a database or network access, execute tests, or publish check status. The caller owns correct merge-base/head provenance and complete status records. Missing history or failed diff must produce a full-required input outcome, never a guessed modified-file list. Renames may include `previous_path`; additions, deletions, renames, unknown statuses, missing files, malformed input, unsafe paths and empty changes require the full gate.

Only existing modified `tests/**/test_*.py` modules are eligible. The selected manifest unions all changed modules and follows reverse local Python imports recursively across tests, support and scripts. This includes fixtures imported inside functions and relative/package imports. Family names do not limit the graph: a Water fixture imported by Armoury and Haste tests selects all three. If the closure reaches a shared helper, support file, package initializer or script bridge, it requires the full gate instead of pretending that a partial fixture module is a complete consumer bundle.

Any changed source, shared test support/helper, configuration, workflow, dependency/lockfile, catalog, schema, persistence, script, documentation or unknown path requires the full gate. No source-family bundles are supplied: those need separately reviewed complete consumer manifests. Duplicate/ambiguous module names, unresolved explicit test/support imports, syntax errors, symlinks, dynamic imports, dynamic plugin registration and `eval`/`exec` in the dependency inventory also require it. Dynamic-loader names and attributes are refused even when passed or assigned without a visible call. Imports of `builtins`, `importlib` or `runpy` namespaces are themselves uncertain, as are loader lookups through `getattr`, literal dictionary indexing and global/local namespace introspection. This deliberately refuses alias chains instead of guessing their runtime target.

There is one explicit audit exception: the unchanged `tests/test_architecture.py` at SHA256 `f4adb653e33f24e5690342c798410a17ff804799ff2be8ca12b22039b490e4b0`. Its dynamic imports examine `wayfarer.engine.*` and the named `wayfarer` production packages; they do not dynamically discover test fixtures. The exact audited hash is recorded by the manifest. Changing even a comment invalidates the exception and requires the full gate. The hash is a narrow review anchor, not a generic dynamic-import whitelist.

A `selected` manifest offers `pytest_arguments` containing only `--no-cov` and complete module paths. It never uses `-k`, marker filtering, individual node IDs or backend filtering. The workflow's `run_ci_feedback` adapter records the actual requested base, merge base and head, admits at most five selected modules, provides PostgreSQL and rejects collection or runtime skips. Above the module budget it awaits the full gate without launching pytest. The count limit bounds repeated module admission; it does not guarantee a runtime. `--no-cov` belongs only to partial feedback; the whole-package coverage threshold stays with the full gate.

A `full-required` manifest supplies **no runnable pytest arguments**. Its fallback action is to await the existing full acceptance gate; do not launch a duplicate full suite just for fast feedback. A zero selector process exit code means a JSON manifest was emitted, not that tests passed. Callers must branch on `mode` and never interpret fallback, empty collection, skips, import/collection errors or selector errors as selected green evidence.

`tests/test_ci_selection.py` proves status/path fallbacks; changed, deleted and renamed files; malformed/empty input; deterministic union; recursive cross-family fixture imports; relative imports; script bridges; dynamic alias/plugin ambiguity, including the exact `b.__import__` and `loader=__import__` omitted-consumer regressions; changed audit hashes; and symlink refusal. Its isolated collection oracle compares selected node IDs with the matching portion of complete fixture-tree collection: all six nodes remain, including three SQLite and three PostgreSQL parametrizations. It executes no fixture scenario tests and runs no repository-wide suite. A separate collect-only check on the actual `test_water_parcel_flow.py`, compared with the matching portion of two-module collection including `test_water_construction.py`, preserves all 100 nodes: 50 SQLite and 50 PostgreSQL. No scenario tests were executed for that comparison.

Feedback artifacts retain manifests and diff provenance; subsequent real test-only changes can provide measured feedback timings. Static import analysis is not a complete runtime call graph. The complete full-suite gate remains mandatory because dynamic dispatch, arbitrary Python reflection and unreviewed source dependencies require broader execution evidence.
