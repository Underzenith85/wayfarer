# Social material and crowd outcomes

Issue #370 binds four social skill results to authoritative campaign state. The
social procedure still owns the roll; `campaign.economics` consumes its recorded
margin under the same command receipt.

- Carousing (B183, with drinking aftermath on B439-B440) transfers an authored
  evening outlay from the actor's account, records the evening duration, and
  schedules a failed intoxication HT check's delayed hangover. No Hangover and
  Horrible Hangovers are read from the approved build. The schedule stores its
  onset and duration on the resource ledger.
- Panhandling (B212) occupies one hour and conservatively transfers exactly
  `$2 × positive margin of success` from an authored donor account. Failure pays
  zero; critical success retains the source-required bonus as an explicit
  adjudication flag because B212 does not specify one universal item.
- Performance (B212) transfers scenario-authored pay from a payer account. Base
  pay and pay-per-margin are authored economic facts, not invented by the skill
  resolver. The recorded margin maps the named crowd to the existing reaction
  bands.
- Public Speaking (B216) maps the recorded margin across one explicit crowd.
  Crowd membership is validated as actors and stored with the aggregate reaction
  so the outcome cannot silently become a one-observer roll.

Empty bindings and outcome state are omitted from prerelease encodings, following
the engine-versioning policy; existing scenario and replay digests remain stable.
