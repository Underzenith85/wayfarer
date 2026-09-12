# Mixed group activity evidence

The Wave 9 mixed-activity path now covers one normally created, profile-pinned
campaign whose authenticated actors split at the same authored scene. Two actors
enter Basic combat through `StartBasicEncounter`; the third investigates and then
travels through queued, typed commands. The combat explicitly migrates to an
authored hex battlefield and the returning actor joins at a synchronized boundary.
No test-only state mutation activates or routes any of those activities.

## Basic versus tactical behavior

| Surface | Disposition | Authoritative behavior | Executable evidence |
| --- | --- | --- | --- |
| Basic maneuvers, attacks and defenses | Supported subset | The server returns legal choices and commits typed combat commands without a map. Pending defenses, command receipts and turns survive reconnect/restart. | `tests/test_basic_tactical_api.py`, `frontend/src/play/tactical.test.tsx`, `frontend/tactical-tests/combat.spec.ts` |
| Basic distance, reach, visibility, cover, obstacles and retreat | Adjudicated spatial | Scenario facts seed the encounter. Later unknown or changed spatial facts require a GM declaration with provenance; clients cannot invent geometry. | `tests/test_basic_combat.py`, `tests/test_basic_tactical_api.py` |
| Hex movement, facing and visibility | Supported subset | An exact Basic-profile battlefield owns axial positions. Legal previews and commands use the tactical-v2 endpoint; explicit migration preserves turn state. | `tests/test_tactical.py`, `tests/test_reinforcements.py`, `tests/test_wave9.py` |
| Basic-to-hex and hex-to-Basic conversion | Supported subset | A GM invokes an explicit, validated conversion. Merely hiding the map never changes coordinate semantics. | `tests/test_reinforcements.py`, `tests/test_basic_conversion.py`, `tests/test_wave9.py` |
| Unimplemented Basic Set rules and uncited spatial edge cases | Unsupported rule | The capability registry remains partial and rejects unsupported profiles or combinations. This integration evidence does not certify the whole profile. | `wayfarer.engine.rules.conformance.CAPABILITIES`, `tests/test_gurps_conformance.py` |
| Existing square-grid encounters and frozen tactical v1 | Legacy behavior | Saved square coordinates and the v1 request schema remain unchanged. New Basic commands and representation fields are exposed only through additive tactical v2. | `contracts/tactical/v1/openapi.json`, `tests/test_tactical.py` |

## Shared-time and recovery contract

Combat advances only after every participant has completed a round. An independent
activity is committed only when every subgroup reaches its due time: investigation
does not reveal its private clue at time 1 when it is due at time 2, and two-second
travel remains at the origin until the combat subgroup reaches time 4. A scheduled
deadline at time 2 fires exactly once. Pausing and resuming the independent group
does not resolve a pending combat defense, and concurrent same-revision commands
leave exactly one committed winner.

The same scenario restarts from its SQLite checkpoint while a defense is pending,
replays the attack receipt without a second roll, rebinds the persisted hex
configuration after migration, preserves item ownership and quantities, and ends
with byte-equivalent event replay. The investigator's clue remains scoped to that
actor after joining combat.

This is cross-system evidence, not a whole-profile certification. The source and
catalog audits tracked by #121 and #122 remain independent gates.
