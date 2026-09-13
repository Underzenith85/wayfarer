# GURPS economics, employment, and hirelings (#503)

Issue #503 adds an engine-owned campaign economics checkpoint for the Basic Set
procedures on B513-B519, with the related Money and Jobs guidance on B291-B294.
The implementation is authored data plus deterministic reducers; it does not add
a UI, a new profile, or a rules-engine version increment.

## Money and property

`EconomicsRules` authors currencies, world-local price contexts, fixed trade and
exchange offers, and financial profiles. `EconomicsState` owns integer money
accounts and an append-only economic ledger. A trade moves money and the item in
one campaign procedure. Cross-world exchange uses two independently conserved
currency legs and may require both player accounts to represent portable wealth.
Command receipts make retries idempotent and stale revisions fail closed.

Cost of living is bound to the compiled actor's Wealth and Status. Authored Rank,
Ally, Contact, and Dependent IDs must agree with the actor's purchased traits.
Each living-cost settlement has a durable campaign-period ID, so a second command
cannot charge the same period. Declining the payment records the authored
living-standard consequence without creating or destroying money.

## Employment

Jobs author prerequisites, accounts, monthly pay, settlement size, status,
advertising, work duration, income shape, and critical-failure consequence. Job
search uses the compiled IQ and a purchased prerequisite; defaults cannot satisfy
the prerequisite. Search attempts are recorded and limited to one per week.

Income can settle only against an existing `SettleTimeUse` record containing the
job's authored activity for the whole work interval. The time-use ID is then
consumed by the job period, preventing duplicate pay. If the activity grants
study credit while working, its authored fraction is resolved by the existing
Time Use engine; no second advancement path is introduced.

## Hirelings and privacy

Hireling rules author availability checks, parties, accounts, competence, normal
and offered pay, and reaction modifiers. A successful search persists a contract
and its initial reaction-based loyalty. Loyalty checks name an authored danger,
temptation, rescue, service, or competence circumstance and use the stored score,
including the source-defined bonus for extra pay.

Private motives and loyalty traces remain in the authoritative checkpoint. Public
resource events contain only the public outcome, and validation rejects a motive
that appears in that event stream.

## Evidence boundary

The acceptance tests in `tests/test_economics.py` cover conserved item and money
transfers, cross-world exchange and serialization, retry and conflict behavior,
Time Use-bound job income, one-settlement-per-period living costs, and persisted
hireling loyalty without private-event leakage. Making goods, slavery, and travel
to other planes remain outside #503 and retain their existing completion owners.
